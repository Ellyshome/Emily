"""WorkItemAgent —— 全局单例，异步处理所有 WorkItem（蓝图 §5.3）。

核心设计：不是每个 WorkItem 创建独立 Agent，而是全局唯一 Agent 实例，
异步处理所有 WorkItem。新 WorkItem 进来时，KnowledgeInjector 增量注入
执行该 WorkItem 缺失的知识（SOP/工具/schema），最小化上下文污染。

Phase C 实现（蓝图 §12.2）：
  - 节点1 [意图验证+注入]：路由已在 SessionAgent 完成，节点1 仅验证 + 增量注入
  - 节点2 [计划+标准]：EMILY_PLANNER_MODE=real 时 LLM 动态规划，否则 MockPlanner
  - 节点3 [执行+验收]：EMILY_EXECUTOR_MODE=real 时真实执行引擎（M14 工具直调）
  - 节点4 [成果总结]：EMILY_GUARDIAN_MODE=review|agent 时真实守护审核，移除 [Mock] 前缀

节点 ↔ 大脑映射（蓝图 §5.4 + Phase C）：
  wi_node1 [意图验证+注入]  ← KnowledgeInjector + RouteDecision 构建
  wi_node2 [计划+标准]      ← LLM Planning | MockPlanner
  wi_node3 [执行+验收]      ← RealExecutor (M14) | MockWorkAgent + GuardianReview/MockGuardian
  wi_node4 [成果总结]        ← 组装 result_text + GuardianReview/MockGuardian.review_reply()
"""

from __future__ import annotations

import logging
import time as _time

from .injector import KnowledgeInjector
from .pipeline.context import BusContext
from .pipeline.interfaces.routing import RouteDecision, SubTask
from .pipeline.interfaces.planning import ExecutionPlan, PlanStep
from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
from .pipeline.interfaces.guardian import GuardianVerdict
from .pipeline.interfaces.auth import AuthResult, AuthDecision
from .pipeline.mocks import (
    MockPlanner,
    MockWorkAgent,
    MockGuardian,
)

logger = logging.getLogger("emily.workitem_agent")

# ══════════════════════════════════════════════════════════════════════════════
# Phase B/C: LLM 规划器 System Prompt
# ══════════════════════════════════════════════════════════════════════════════

_PLANNER_SYSTEM_PROMPT = """你是 Emily 的执行规划器。根据业务流程（SOP）和用户输入，制定逐步的执行计划。

## SOP 参考
{sop_text}

## 用户输入
{user_input}

## 规划规则
1. 根据 SOP 中定义的流程阶段拆解步骤（通常 2-5 步）
2. 每个步骤绑定一个具体的工具（tool_name），如 record_event, record_task, record_meeting, record_file, query_data 等
3. 步骤间如有依赖关系，在 depends_on 中标明
4. 评估整体风险等级：L1(低风险-查询类) / L2(中风险-录入类) / L3(高风险-删除/批量修改)
5. 对于需要工具调用的步骤，在 tool_params 中提供完整的参数对象

## 输出格式
仅输出一个 JSON 对象（不要包含其他文字）：
{{"risk_level": "L1|L2|L3", "steps": [{{"step_id": "step-01", "description": "步骤描述", "tool_name": "record_event|null", "tool_params": {{"title": "事件标题", "event_type": "施工节点", "description": "详细描述"}}, "expected_output": "预期产出", "depends_on": []}}], "acceptance_criteria": ["验收标准1"], "estimated_steps": N}}
"""


def _fallback_steps() -> list[PlanStep]:
    """LLM 规划失败时的回退计划。"""
    return [
        PlanStep("step-01", "解析用户输入并确认意图"),
        PlanStep("step-02", "执行核心业务操作"),
        PlanStep("step-03", "确认结果并生成回复"),
    ]


class WorkItemAgent:
    """全局单例 WorkItem-Agent —— 提供公共 BUS 的 4 节点 handler。

    Phase C 升级：
      - 节点3 支持真实执行引擎（EMILY_EXECUTOR_MODE=real）
      - 节点3/4 支持 GuardianReview（EMILY_GUARDIAN_MODE=review）
      - 节点4 支持 GuardianAgent 深度审计（EMILY_GUARDIAN_MODE=agent）
      - 全部真实模式时自动移除 [Mock 模式] 前缀
    """

    def __init__(
        self,
        injector: KnowledgeInjector | None = None,
        # Phase B: 真实大脑依赖
        llm_client=None,
        config=None,
        # Phase C: 执行和守护依赖
        business_flow_tools=None,
        guardian_review=None,
        sop_intent_registry=None,
        rag_provider=None,
    ):
        self.injector = injector or KnowledgeInjector()
        self.config = config

        # LLM 依赖
        self._llm = llm_client

        # Phase C: 执行依赖
        self._business_flow_tools = business_flow_tools

        # Phase C: 守护依赖
        self._guardian_review = guardian_review

        # Phase C: 鉴权依赖
        self._sop_intent_registry = sop_intent_registry

        # Phase C: RAG 依赖
        self._rag_provider = rag_provider

        # Mock 大脑（Phase C 保留作为 fallback）
        self._planner = MockPlanner()
        self._work_agent = MockWorkAgent()
        self._guardian = MockGuardian()

    # ── 模式解析 ──

    def _resolve_mode(self, component: str) -> str:
        """解析组件模式，mock 为默认回退。"""
        if self.config is None:
            return "mock"
        mode = getattr(self.config, f"{component}_mode", "mock") or "mock"
        if mode in ("real", "review", "agent") and self._llm is None:
            logger.warning(
                "Component '%s' mode=%s but no LLM client — falling back to mock",
                component, mode,
            )
            return "mock"
        return mode

    # ── 公共 Pipeline BUS 节点 handler ──

    async def node1_intent(self, context: BusContext) -> None:
        """Node 1 [意图验证+注入] —— 路由已在 SessionAgent 完成。"""
        wi = context.work_item

        # 增量灌注
        self.injector.analyze(wi)

        # SessionAgent 已设置 sop_id 和 intent_type，节点1 仅验证
        if wi.route_decision is None:
            wi.route_decision = RouteDecision(
                intent_type=getattr(wi, "intent_type", "fallback") or "fallback",
                sop_id=wi.sop_id or None,
                confidence="medium" if wi.sop_id else "none",
                is_compound=False,
                sub_tasks=[
                    SubTask(
                        id="subtask-001",
                        sop_id=wi.sop_id or "",
                        user_input=wi.user_input,
                    )
                ] if wi.sop_id else [],
                _source="session_agent",
            )

        wi.llm_call_count += 1
        context.intent = wi.route_decision
        logger.debug("WI %s node1: sop=%s intent=%s _source=%s",
                     wi.id, wi.sop_id, wi.route_decision.intent_type,
                     getattr(wi.route_decision, "_source", ""))

    async def node2_plan(self, context: BusContext) -> None:
        """Node 2 [计划+标准] —— LLM 动态规划 或 MockPlanner fallback。"""
        wi = context.work_item

        planner_mode = self._resolve_mode("planner")

        if planner_mode == "real" and self._llm:
            plan = await self._llm_plan(wi, context)
        else:
            plan = await self._planner.plan(wi.route_decision, context)

        wi.execution_plan = plan
        wi.risk_level = plan.risk_level
        wi.acceptance_criteria = list(getattr(plan, "acceptance_criteria", []))
        wi.llm_call_count += 1
        logger.debug("WI %s node2: risk=%s steps=%d _source=%s",
                     wi.id, plan.risk_level, getattr(plan, "estimated_steps", 0),
                     getattr(plan, "_source", "mock"))

    async def _llm_plan(self, wi, context) -> ExecutionPlan:
        """LLM 动态规划 —— 从 SOP 全文和用户输入生成 ExecutionPlan。"""
        sop_text = ""
        if hasattr(self.injector, 'get_context_text'):
            sop_text = self.injector.get_context_text()

        prompt = _PLANNER_SYSTEM_PROMPT.format(
            sop_text=sop_text[:4000] if sop_text else f"SOP: {wi.sop_id or '未知'}（全文未加载）",
            user_input=wi.user_input,
        )

        try:
            data = await self._llm.chat_json(prompt, f"Plan for: {wi.user_input[:200]}")
            logger.debug("LLM planner response: %s", data)
        except Exception as e:
            logger.error("LLM planner failed: %s, falling back to MockPlanner", e)
            return await self._planner.plan(wi.route_decision, context)

        return self._map_to_execution_plan(data)

    @staticmethod
    def _map_to_execution_plan(data: dict) -> ExecutionPlan:
        """将 LLM JSON 输出映射为 ExecutionPlan 协议对象。"""
        steps = []
        for i, s in enumerate(data.get("steps", [])):
            steps.append(PlanStep(
                step_id=s.get("step_id", f"step-{i+1:02d}"),
                description=s.get("description", ""),
                tool_name=s.get("tool_name"),
                tool_params=s.get("tool_params", {}),
                expected_output=s.get("expected_output", ""),
                depends_on=s.get("depends_on", []),
            ))

        if len(steps) > 8:
            logger.warning("Plan has %d steps, truncating to 8", len(steps))
            steps = steps[:8]

        return ExecutionPlan(
            risk_level=data.get("risk_level", "L2"),
            steps=steps if steps else _fallback_steps(),
            acceptance_criteria=data.get("acceptance_criteria", []),
            estimated_steps=data.get("estimated_steps", len(steps)),
            _source="llm_planner",
        )

    # ── Phase C: Node 3 真实执行引擎 ──

    async def node3_execute(self, context: BusContext) -> None:
        """Node 3 [执行+验收] —— Phase C: 真实执行引擎 + 守护验收。"""
        wi = context.work_item
        if wi.execution_plan is None:
            return

        executor_mode = self._resolve_mode("executor")

        if executor_mode == "real":
            step_results = await self._real_execute(wi.execution_plan, context)
        else:
            step_results = await self._work_agent.execute(wi.execution_plan, context)

        # 逐步守护验收（陪跑模式）
        guardian_mode = self._resolve_mode("guardian")
        criteria = wi.acceptance_criteria
        for sr in step_results:
            try:
                if guardian_mode == "review":
                    await self._guardian_review_step(sr, None, criteria)
                elif guardian_mode == "agent":
                    await self._guardian_agent_step(sr, None, criteria)
                else:
                    await self._guardian.review_step(sr, None, criteria)
            except Exception as e:
                logger.warning("WI %s node3 guardian review_step failed: %s", wi.id, e)
            wi.add_step_result(sr)

        wi.llm_call_count += len(step_results)
        if step_results:
            context.agent_result = step_results[-1]
            context.agent_reply = step_results[-1].output
        logger.debug("WI %s node3: %d steps, executor=%s guardian=%s",
                     wi.id, len(step_results), executor_mode, guardian_mode)

    async def _real_execute(self, plan: ExecutionPlan, context: BusContext) -> list[StepResult]:
        """Phase C: 真实执行引擎 —— 按 PlanStep 调用 M14 工具 handler。

        对有 tool_name 且在 BusinessFlowToolRegistry 中注册的步骤，
        调用 handler(tool_params) 直接执行；其他步骤返回纯文本结果。
        """
        if self._business_flow_tools is None:
            logger.warning("RealExecutor: no BusinessFlowToolRegistry, falling back to MockWorkAgent")
            return await self._work_agent.execute(plan, context)

        results: list[StepResult] = []

        for step in plan.steps:
            t_start = _time.monotonic()
            tool_name = step.tool_name
            tool_params = getattr(step, 'tool_params', {}) or {}

            try:
                if tool_name and tool_name in self._business_flow_tools:
                    # M14: 框架直接调用 handler
                    tool = self._business_flow_tools.get(tool_name)
                    handler_result = await tool.handler(tool_params)
                    handler_dict = handler_result if isinstance(handler_result, dict) else {}
                    ...
                elif tool_name == "knowledge_search" and self._rag_provider:
                    # Phase C: RAG 知识库检索 — 内联执行
                    import asyncio
                    try:
                        from ..providers.rag.base import RagSearchResponse
                        rag_result = await self._rag_provider.search(
                            query=tool_params.get("query", step.description),
                            top_k=tool_params.get("top_k", 5),
                        )
                        rag_chunks = [
                            type('RagChunk', (), {'content': r.content, 'score': r.score, 'doc_name': r.source_document})()
                            for r in rag_result.results
                        ] if hasattr(rag_result, 'results') else []
                        rag_obj = type('RagResult', (), {
                            'query': tool_params.get("query", ""),
                            'provider': rag_result.provider_name,
                            'chunks': rag_chunks,
                            'hit_count': len(rag_chunks),
                            'elapsed_ms': 0,
                        })()
                        sr = StepResult(
                            step_id=step.step_id,
                            success=True,
                            output=rag_result.context_text or "未找到相关知识",
                            rag_results=[rag_obj],
                        )
                    except Exception as e:
                        logger.warning("RAG knowledge_search failed: %s", e)
                        sr = StepResult(step_id=step.step_id, success=False, output=f"知识库检索失败: {e}")

                    tool_calls = [ToolCallRecord(
                        tool_name=tool_name,
                        tool_input=tool_params,
                        tool_output=handler_dict,
                        success=handler_dict.get("success", True),
                        elapsed_ms=int((_time.monotonic() - t_start) * 1000),
                    )]

                    db_results = []
                    object_id = handler_dict.get("object_id", "") or ""
                    if object_id:
                        db_results.append(DbResult(
                            operation="insert",
                            table=tool_name.replace("record_", "") + "s",
                            affected_rows=1,
                            result_data=handler_dict,
                        ))

                    output = handler_dict.get("reply", step.description)
                    success = handler_dict.get("success", True)

                    sr = StepResult(
                        step_id=step.step_id,
                        success=success,
                        output=str(output),
                        tool_calls=tool_calls,
                        db_results=db_results,
                        business_data=handler_dict,
                    )
                elif tool_name:
                    # 工具未注册
                    sr = StepResult(
                        step_id=step.step_id,
                        success=False,
                        output=f"工具 '{tool_name}' 未在 BusinessFlowToolRegistry 中注册",
                    )
                else:
                    # 无工具步骤
                    sr = StepResult(
                        step_id=step.step_id,
                        success=True,
                        output=step.description,
                    )
            except Exception as e:
                logger.error("Step %s failed: %s", step.step_id, e)
                sr = StepResult(
                    step_id=step.step_id,
                    success=False,
                    output=f"步骤执行异常: {e}",
                )

            results.append(sr)

            if not sr.success:
                break  # 失败即停止

        return results

    # ── Phase C: 守护审核方法 ──

    async def _guardian_review_step(self, step_result, plan_step=None, criteria=None) -> None:
        """Phase C: 轻量 Guardian 逐步审核 —— GuardianReview.review_record()。

        Returns None（内部处理，异常捕获后默认 PASS）。
        """
        if self._guardian_review is None:
            return

        try:
            tool_calls = getattr(step_result, 'tool_calls', []) or []
            for tc in tool_calls:
                result = await self._guardian_review.review_record(
                    tool_name=tc.tool_name if hasattr(tc, 'tool_name') else 'unknown',
                    data={
                        'output': getattr(step_result, 'output', ''),
                        'success': getattr(step_result, 'success', True),
                        'step_id': getattr(step_result, 'step_id', '?'),
                    },
                )
                if not result.passed:
                    logger.info("GuardianReview FLAG step: %s findings=%s",
                                getattr(step_result, 'step_id', '?'), result.findings[:80])
        except Exception as e:
            logger.warning("GuardianReview review_step failed: %s (defaulting to PASS)", e)

    async def _guardian_agent_step(self, step_result, plan_step=None, criteria=None) -> None:
        """Phase C: 深度审计逐步审核 —— GuardianAgent.investigate()。

        注意：每步骤都创建 GuardianAgent 成本高，推荐在 DeepAuditHook 中做全量审计。
        """
        if self._guardian_review is None:
            return
        # Fallback to light review for per-step; deep audit is done via Hook
        await self._guardian_review_step(step_result, plan_step, criteria)

    # ── Phase C: Node 4 成果总结 ──

    async def node4_summary(self, context: BusContext) -> None:
        """Node 4 [成果总结] —— Phase C: 移除 Mock 前缀 + 真实守护审核。"""
        wi = context.work_item
        summary = wi.to_summary()
        steps = summary.get("steps_executed", 0)
        rag_hits = summary.get("rag_hits", 0)
        tool_calls = summary.get("tool_calls", 0)

        # Phase C: executor_mode=real 时无 Mock 前缀
        executor_mode = self._resolve_mode("executor")
        mock_prefix = "" if executor_mode == "real" else "[Mock 模式] "

        if rag_hits > 0:
            rag_texts = []
            for sr in wi.step_results:
                for rag_result in getattr(sr, "rag_results", []):
                    for chunk in getattr(rag_result, "chunks", []):
                        rag_texts.append(f"• {chunk.content}")
            if rag_texts:
                draft = mock_prefix + "根据知识库检索，找到以下相关信息：\n\n" + "\n".join(rag_texts[:5])
            else:
                draft = mock_prefix + f"已完成知识库查询，共找到 {rag_hits} 条相关信息。"
        elif tool_calls > 0:
            draft = (
                f"{mock_prefix}操作已完成！共执行 {steps} 个步骤，"
                f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
            )
        else:
            draft = mock_prefix + "Emily 已处理完毕。"

        # 出站审核
        guardian_mode = self._resolve_mode("guardian")
        try:
            if guardian_mode in ("review", "agent") and self._guardian_review:
                review_result = await self._guardian_review.review_reply(
                    reply_text=draft,
                    user_message=wi.user_input,
                )
                verdict_val = "pass" if review_result.passed else "flag"
            else:
                verdict = await self._guardian.review_reply(draft, wi)
                verdict_val = getattr(verdict, "value", "pass")
        except Exception as e:
            logger.warning("WI %s node4 guardian review_reply failed: %s", wi.id, e)
            verdict_val = "pass"

        if verdict_val == "pass":
            wi.result_text = draft
        elif verdict_val == "flag":
            wi.result_text = draft + "\n\n⚠️ Emily 提醒：以上回复建议复核。"
            wi.add_warning("Guardian 标记")
        else:
            wi.result_text = "Emily 已处理完毕。（回复未通过审核，已使用兜底回复）"
            wi.add_warning("Guardian 拒绝，使用兜底回复")

        wi.llm_call_count += 1
        context.verified_reply = wi.result_text
        logger.debug("WI %s node4: reply_len=%d guardian=%s mock_prefix=%s",
                     wi.id, len(wi.result_text), guardian_mode, repr(mock_prefix))

    # ── Phase C: 鉴权引擎 ──

    async def authorize(self, user_id: str, route_decision) -> AuthResult:
        """Phase C: 真实鉴权 —— 基于 SOP allow_roles。

        当 EMILY_AUTH_MODE=real 时，替代 MockAuthEngine.authorize()。
        """
        auth_mode = self._resolve_mode("auth")
        if auth_mode != "real" or not self._sop_intent_registry:
            return AuthResult(decision=AuthDecision.ALLOW, matched_roles=["all"],
                            _source="mock_auth")

        sop_id = getattr(route_decision, "sop_id", None)
        if not sop_id:
            return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")

        spec = self._sop_intent_registry.get_spec(sop_id)
        if spec is None:
            return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")

        allowed = set(spec.allow_roles)
        if "all" in allowed:
            return AuthResult(decision=AuthDecision.ALLOW, matched_roles=["all"],
                            _source="real_auth")

        # 默认放行（完整用户角色检查 Phase C 后续迭代）
        return AuthResult(decision=AuthDecision.ALLOW, matched_roles=list(allowed),
                        _source="real_auth")

    # ── Phase C: 风险评估 ──

    def grade_risk(self, route_decision, operation_type: str = "") -> str:
        """Phase C: 真实风险评估 —— 基于意图类型和置信度。

        当 EMILY_RISK_MODE=real 时使用。
        """
        risk_mode = self._resolve_mode("risk")
        if risk_mode != "real":
            return "L2"

        intent_type = getattr(route_decision, "intent_type", "fallback")
        confidence = getattr(route_decision, "confidence", "none")
        is_compound = getattr(route_decision, "is_compound", False)

        if intent_type == "fast_reply":
            return "L1"
        if intent_type == "fallback" or confidence == "none":
            return "L3"
        if is_compound:
            return "L3"
        if operation_type == "delete":
            return "L3"
        if operation_type == "write":
            return "L2"
        if confidence == "low":
            return "L2"
        return "L1"

    # ── 节点 handler 映射（供 PipelineBUS.build_default 使用）──

    def node_handlers(self) -> dict:
        """返回 4 节点 handler 映射。"""
        return {
            "wi_node1": self.node1_intent,
            "wi_node2": self.node2_plan,
            "wi_node3": self.node3_execute,
            "wi_node4": self.node4_summary,
        }
