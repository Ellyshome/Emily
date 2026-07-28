"""WorkItemAgent —— 全局单例，异步处理所有 WorkItem（蓝图 §5.3）。

核心设计：不是每个 WorkItem 创建独立 Agent，而是全局唯一 Agent 实例，
异步处理所有 WorkItem。新 WorkItem 进来时，KnowledgeInjector 增量注入
执行该 WorkItem 缺失的知识（SOP/工具/schema），最小化上下文污染。

节点 ↔ 大脑映射：
  wi_node1 [意图验证+注入]  ← KnowledgeInjector + RouteDecision 构建
  wi_node2 [计划+标准]      ← Skill 定义优先，否则 LLM 规划（fallback steps 兜底）
  wi_node3 [执行+验收]      ← Skill 执行优先，否则 RealExecutor + RealGuardian（并进审核）
  wi_node4 [成果总结]        ← 组装 result_text + RealGuardian.review_reply()（追加标记）
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as _time

from .injector import KnowledgeInjector
from .pipeline.context import BusContext
from .pipeline.interfaces.routing import RouteDecision, SubTask
from .pipeline.interfaces.planning import ExecutionPlan, PlanStep
from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult, RagResult, RagChunk, GuardianStepVerdict, StructuredResult
from .pipeline.interfaces.auth import AuthResult, AuthDecision
from .pipeline.real_guardian import RealGuardian, GuardianNote
from ..session.session_context import format_message_history

logger = logging.getLogger("emily.workitem_agent")

# ══════════════════════════════════════════════════════════════════════════════
# LLM System Prompts（从 prompt 文件加载）
# ══════════════════════════════════════════════════════════════════════════════

def _load_planner_prompt() -> str:
    """加载执行规划 system prompt（带缓存），用于 node2 规划。"""
    from ..infrastructure.llm.prompt_loader import load_prompt
    return load_prompt("planner")


def _load_workitem_prompt() -> str:
    """加载 WorkItem 完整 system prompt（带缓存），用于 node4 回复合成。

    workitem.md 包含：执行角色 / 规划规则 / 回复合成规则 / step_results 模板
    与 planner.md 的区别：workitem.md 含回复合成指令，用于 node4；planner.md 仅 JSON 规划输出令，用于 node2。
    """
    from ..infrastructure.llm.prompt_loader import load_prompt
    return load_prompt("workitem")


def _fallback_steps() -> list[PlanStep]:
    """LLM 规划失败时的回退计划。"""
    return [
        PlanStep("step-01", "解析用户输入并确认意图"),
        PlanStep("step-02", "执行核心业务操作"),
        PlanStep("step-03", "确认结果并生成回复"),
    ]


def _build_params_summary(parameters: dict) -> str:
    """从 JSON Schema 中提取参数摘要，帮助 LLM 规划时了解合法参数值。

    只展示 required 标记和 enum 约束，避免过多细节干扰 LLM 规划。
    """
    if not parameters or not isinstance(parameters, dict):
        return ""
    props = parameters.get("properties", {})
    required_fields = parameters.get("required", [])
    if not props:
        return ""
    parts = []
    for name, schema in props.items():
        if not isinstance(schema, dict):
            continue
        enum_vals = schema.get("enum")
        is_required = name in required_fields
        if enum_vals:
            vals = "|".join(str(v) for v in enum_vals)
            marker = "*" if is_required else ""
            parts.append(f"{name}{marker}({vals})")
    if not parts:
        return ""
    return "\n    参数: " + ", ".join(parts)


class WorkItemAgent:
    """全局单例 WorkItem-Agent —— 提供公共 BUS 的 4 节点 handler。"""

    def __init__(
        self,
        injector: KnowledgeInjector | None = None,
        # 真实大脑依赖
        llm_client=None,
        config=None,
        # 执行和守护依赖
        business_flow_tools=None,
        rag_provider=None,
        # 三维鉴权引擎
        permission_engine=None,
        # Skill 模块
        skill_registry=None,
        skill_executor=None,
    ):
        self.injector = injector or KnowledgeInjector()
        self.config = config

        # LLM 依赖
        self._llm = llm_client

        # 执行依赖
        self._business_flow_tools = business_flow_tools

        # RAG 依赖
        self._rag_provider = rag_provider

        # 三维鉴权引擎
        self._permission_engine = permission_engine

        # Skill 模块
        self._skill_registry = skill_registry
        self._skill_executor = skill_executor

        # Guardian: LLM 可用则自动启用，不可用则为 None（静默跳过）
        if self._llm:
            self._guardian = RealGuardian(llm_client=self._llm, config=config)
            logger.info("Guardian enabled: RealGuardian (lightweight LLM review)")
        else:
            self._guardian = None
            logger.info("LLM not available — Guardian disabled (silent skip)")

    # ── 公共 Pipeline BUS 节点 handler ──

    @staticmethod
    def _skill_to_execution_plan(skill) -> ExecutionPlan:
        """将 SkillDefinition 转换为 ExecutionPlan。"""
        from ..workitem.pipeline.interfaces.planning import PlanStep, ExecutionPlan

        steps = [
            PlanStep(
                step_id=s.id,
                description=s.description,
                tool_name=s.tool_name,
                tool_params={},  # 由 SkillExecutor 在执行时从 ParamMapping 解析
                expected_output="",
                depends_on=[],
            )
            for s in skill.steps
        ]

        # M3: 按工具类型判定风险等级
        # L1(查询类): 只调 query_*/knowledge_search/chat_archive/query_files
        # L2(录入类): 调 record_*/create_*/update_*/submit_*/confirm_*/mount_*/activate_*
        # L3(高风险): 调 discard_*/return_*/delete_*
        tool_names = {s.tool_name for s in skill.steps if s.tool_name}
        risk_level = WorkItemAgent._grade_skill_risk(tool_names)

        return ExecutionPlan(
            risk_level=risk_level,
            steps=steps,
            acceptance_criteria=[],
            estimated_steps=len(steps),
            _source="skill_definition",
        )

    @staticmethod
    def _grade_skill_risk(tool_names: set[str]) -> str:
        """根据 Skill 涉及的工具集合判定风险等级。"""
        if not tool_names:
            return "L1"  # 纯逻辑步骤（如 SOP-005 step-02/03），视为查询类
        l1_tools = {"query_data", "query_files", "query_node", "query_my_nodes",
                    "knowledge_search", "chat_archive", "fetch_inbox"}
        l3_tools = {"discard_nodes", "return_node_deliverable"}
        # 任一工具是 L3 → 整体 L3
        if tool_names & l3_tools:
            return "L3"
        # 全部工具都是 L1 → L1
        if tool_names <= l1_tools:
            return "L1"
        # 含 record_*/create_*/update_*/submit_*/confirm_*/mount_*/activate_* → L2
        return "L2"

    async def _execute_skill(self, wi, context: BusContext) -> list[StepResult]:
        """通过 SkillExecutor 执行 Skill 定义。"""
        from ..skill.executor import SkillExecutionContext

        skill = self._skill_registry.get_by_sop_id(wi.sop_id)
        if skill is None:
            logger.error("Skill not found for sop_id=%s, falling back to _real_execute", wi.sop_id)
            return await self._real_execute(wi.execution_plan, context)

        # 从 BusContext 获取 session_context
        session_ctx = context.get_session_context() if context else None
        session_context_dict = {}
        session_available_tools: list[dict] = []
        if session_ctx:
            session_context_dict = {
                "user_id": getattr(session_ctx, "user_id", ""),
                "user_name": getattr(session_ctx, "user_name", ""),
                "project_ids": list(getattr(session_ctx, "project_ids", [])),
                "project_name": getattr(session_ctx, "project_name", ""),
                "db_perms": dict(getattr(session_ctx, "db_perms", {})),
                "info_level": getattr(session_ctx, "info_level", "public"),
                "company_type": getattr(session_ctx, "company_type", ""),
                "department": list(getattr(session_ctx, "department", [])),
            }
            session_available_tools = list(getattr(session_ctx, "available_tools", []))

        skill_ctx = SkillExecutionContext(
            skill=skill,
            user_input=wi.user_input,
            user_id=context.user_id if hasattr(context, "user_id") else "",
            message_id=context.db_message_id if hasattr(context, "db_message_id") else "",
            conversation_id=context.message.conversation_id if context.message else "",
            session_context=session_context_dict,
            step_results={},
            business_flow_tools=self._business_flow_tools,
            llm_client=self._llm,
            session_available_tools=session_available_tools,
            # M2: 从 WorkItem 读取路由派生的预填参数
            prefilled_params=dict(getattr(wi, "_prefilled_params", {}) or {}),
        )

        return await self._skill_executor.execute(skill_ctx)

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
        """Node 2 [计划+标准] —— Skill 定义优先，否则 LLM 规划，fallback steps 兜底。"""
        wi = context.work_item

        # ── Skill 路径优先 ──
        if self._skill_registry and self._skill_registry.has_skill(wi.sop_id or ""):
            skill = self._skill_registry.get_by_sop_id(wi.sop_id)
            plan = self._skill_to_execution_plan(skill)
            plan._source = "skill_definition"
            wi.execution_plan = plan
            wi.risk_level = plan.risk_level
            wi.acceptance_criteria = list(getattr(plan, "acceptance_criteria", []))
            wi.llm_call_count += 1
            logger.info("WI %s node2: using Skill definition (sop=%s)", wi.id, wi.sop_id)
            return

        # ── LLM 规划路径 ──
        if self._llm:
            plan = await self._llm_plan(wi, context)
        else:
            plan = ExecutionPlan(
                risk_level="L2",
                steps=_fallback_steps(),
                acceptance_criteria=[],
                estimated_steps=3,
                _source="fallback",
            )
            logger.info("WI %s node2: no LLM, using fallback steps", wi.id)

        wi.execution_plan = plan
        wi.risk_level = plan.risk_level
        wi.acceptance_criteria = list(getattr(plan, "acceptance_criteria", []))
        wi.llm_call_count += 1
        logger.debug("WI %s node2: risk=%s steps=%d _source=%s",
                     wi.id, plan.risk_level, getattr(plan, "estimated_steps", 0),
                     getattr(plan, "_source", "fallback"))

    async def _llm_plan(self, wi, context) -> ExecutionPlan:
        """LLM 动态规划 —— 从 SOP 全文、对话历史、用户输入生成 ExecutionPlan。

        使用 chat_messages() 传入完整 message_history，利用 KV cache 复用。
        """
        sop_text = ""
        if hasattr(self.injector, 'get_context_text'):
            sop_text = self.injector.get_context_text()

        # 构建可用工具列表 —— 按 session_api_ids 过滤（权限可见性）
        # 只列用户有权限的工具，避免未授权用户"观察到能力存在"
        # session_api_ids 来自 SessionContext.available_tools（tool_registry 表权限过滤结果）
        session_ctx = context.get_session_context() if context else None
        session_api_ids = set()
        if session_ctx:
            for t in getattr(session_ctx, "available_tools", []) or []:
                api_id = t.get("api_id") if isinstance(t, dict) else None
                if api_id:
                    session_api_ids.add(api_id)

        tools_text = ""
        tool_entries = []
        if self._business_flow_tools and session_api_ids:
            for name in sorted(self._business_flow_tools.list_names()):
                if name not in session_api_ids:
                    continue  # 用户无权限，不暴露给 LLM
                tool = self._business_flow_tools.get(name)
                if tool:
                    schema_summary = _build_params_summary(tool.parameters)
                    tool_entries.append(f"- {name}: {tool.description}{schema_summary}")
            tools_text = "\n".join(tool_entries) if tool_entries else "（无可用工具）"
        elif not session_api_ids:
            # fail-closed：session_api_ids 为空（tool_registry 表未填充）→ 不给 LLM 任何工具
            # 避免表空时 LLM 看到全量工具绕过权限
            tools_text = "（无可用工具——工具权限表未初始化，请联系管理员）"
            logger.warning("_llm_plan: session_api_ids 为空，tool_registry 表可能未填充，tools_text 已 fail-closed")
        else:
            tools_text = "（无可用工具）"

        planner_prompt = _load_planner_prompt()
        system_prompt = planner_prompt.format(
            sop_text=sop_text[:4000] if sop_text else f"SOP: {wi.sop_id or '未知'}（全文未加载）",
            user_input=wi.user_input,
            available_tools=tools_text,
        )

        # ── 归档：存储 Node2 Prompt 注入信息到 BusContext.baggage ──
        try:
            context.set("prompt_info_node2", {
                "template": "planner.md",
                "rendered_chars": len(system_prompt),
                "variables": {
                    "sop_text": f"{len(sop_text)}字" if len(sop_text) > 80 else sop_text[:80],
                    "user_input": (wi.user_input or "")[:300],
                    "available_tools": f"{len(tool_entries)}个",
                },
            })
        except Exception as e:
            logger.debug("node2 prompt_info storage failed: %s", e)

        # 从 SessionContext 获取消息历史
        session_ctx = context.get_session_context() if context else None
        message_history = getattr(session_ctx, 'message_history', []) if session_ctx else []

        # 组装多轮 messages: [system] + message_history + [plan_request]
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(message_history)
        full_messages.append({
            "role": "user",
            "content": f"Plan for: {wi.user_input[:200]}",
        })

        # ── 重规划反馈：若 error_analysis 产出了 replan_hint，注入到 prompt ──
        # 由 LangGraph node2 适配函数写入 context.baggage["replan_hint"]
        # 无 replan_hint 时（首次规划）此段不执行，行为不变
        replan_hint = context.get("replan_hint", "") if context else ""
        if replan_hint:
            full_messages.insert(-1, {
                "role": "system",
                "content": (
                    f"⚠️ 上次执行失败，错误分析建议：{replan_hint}\n"
                    f"请在重新规划时参考此建议调整工具选择或参数。"
                ),
            })
            logger.info("_llm_plan: replan_hint injected (hint=%s)", replan_hint[:80])

        try:
            result = await self._llm.chat_messages(full_messages, json_mode=True)
            data = result.get("data", {})
            logger.debug("LLM planner response: %s", data)
        except Exception as e:
            logger.error("LLM planner failed: %s, falling back to fallback steps", e)
            return ExecutionPlan(
                risk_level="L2",
                steps=_fallback_steps(),
                acceptance_criteria=[],
                estimated_steps=3,
                _source="fallback",
            )

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

    # ── Node 3 真实执行引擎 ──

    async def node3_execute(self, context: BusContext) -> None:
        """Node 3 [执行+验收] —— Skill 路径优先，否则走 RealExecutor。"""
        wi = context.work_item
        if wi.execution_plan is None:
            return

        # ── Skill 路径 ──
        if getattr(wi.execution_plan, "_source", "") == "skill_definition" and self._skill_executor:
            step_results = await self._execute_skill(wi, context)
        else:
            # ── RealExecutor 路径 ──
            step_results = await self._real_execute(wi.execution_plan, context)

        # Guardian 并进审核：每个 step 的 review 作为后台 Task，
        # 在全部步骤执行完后 gather() 汇合，不阻塞主链路。
        # 守门：只审计有实质数据（工具调用/RAG/DB 结果）的 step；
        # 纯指令/参数提取步骤无可审计数据，guardian 必然返回空，跳过省 token。
        guardian_tasks: list[asyncio.Task] = []
        audited_steps: list[Any] = []
        if self._guardian:
            for sr in step_results:
                has_data = (
                    getattr(sr, "tool_calls", None)
                    or getattr(sr, "rag_results", None)
                    or getattr(sr, "db_results", None)
                )
                if not has_data:
                    continue
                audited_steps.append(sr)
                task = asyncio.create_task(
                    self._guardian.review_step(sr),
                    name=f"guardian_step_{sr.step_id}",
                )
                guardian_tasks.append(task)

        # 汇合点：等待所有 guardian task 完成（大部分早已自然完成）
        if guardian_tasks:
            try:
                notes = await asyncio.gather(*guardian_tasks, return_exceptions=True)
                for sr, note in zip(audited_steps, notes):
                    if isinstance(note, GuardianNote) and note.issues:
                        for issue in note.issues:
                            wi.add_warning(f"[{note.source}] {issue}")
                        # 写入 StepResult 的 guardian 字段（已有结构）
                        sr.guardian = GuardianStepVerdict(
                            verdict="FLAG",
                            reason="; ".join(note.issues),
                        )
            except Exception as e:
                logger.warning("Guardian gather failed: %s", e)

        # ── 归档：存储 Node3 Guardian Prompt 注入信息到 baggage ──
        # Guardian prompt 在 RealGuardian 内构建，渲染后字符数未追踪（标 0）；
        # 关键变量值从 step_results 反推（output/tool_info/rag_info 字数）。
        try:
            guardian_prompt_info = []
            for sr in step_results:
                output = (getattr(sr, "output", "") or "")
                tool_info_str = ""
                for tc in getattr(sr, "tool_calls", []) or []:
                    tool_info_str += getattr(tc, "tool_name", "") or ""
                rag_info_str = ""
                for rr in getattr(sr, "rag_results", []) or []:
                    for chunk in getattr(rr, "chunks", []) or []:
                        rag_info_str += getattr(chunk, "content", "") or ""
                guardian_prompt_info.append({
                    "template": "guardian_step.md",
                    "rendered_chars": self._guardian.step_prompt_chars(sr) if self._guardian else 0,
                    "variables": {
                        "step_id": getattr(sr, "step_id", "?"),
                        "output": f"{len(output)}字",
                        "tool_info": f"{len(tool_info_str)}字",
                        "rag_info": f"{len(rag_info_str)}字",
                    },
                })
            context.set("prompt_info_node3", guardian_prompt_info)
        except Exception as e:
            logger.debug("node3 prompt_info storage failed: %s", e)

        for sr in step_results:
            wi.add_step_result(sr)

        wi.llm_call_count += len(step_results)
        if step_results:
            context.agent_result = step_results[-1]
            context.agent_reply = step_results[-1].output
        logger.debug(
            "WI %s node3: %d steps, guardian=%s",
            wi.id, len(step_results),
            "enabled" if self._guardian else "disabled",
        )

    async def _real_execute(self, plan: ExecutionPlan, context: BusContext) -> list[StepResult]:
        """真实执行引擎 —— 按 PlanStep 调用工具 handler。

        对有 tool_name 且在 BusinessFlowToolRegistry 中注册的步骤，
        调用 handler(tool_params) 直接执行；其他步骤返回纯文本结果。
        """
        # 构建 session_api_ids（权限可见性集合，来自 SessionContext.available_tools）
        session_ctx = context.get_session_context() if context else None
        session_api_ids = set()
        if session_ctx:
            for t in getattr(session_ctx, "available_tools", []) or []:
                api_id = t.get("api_id") if isinstance(t, dict) else None
                if api_id:
                    session_api_ids.add(api_id)

        if self._business_flow_tools is None:
            logger.error("RealExecutor: no BusinessFlowToolRegistry available, returning empty results")
            return []

        results: list[StepResult] = []

        for step in plan.steps:
            t_start = _time.monotonic()
            tool_name = step.tool_name
            tool_params = dict(getattr(step, 'tool_params', {}) or {})
            # 注入运行时上下文到 tool_params（所有 handler 可统一获取）
            tool_params["_user_id"] = context.user_id or ""
            tool_params["_message_id"] = context.db_message_id or ""
            tool_params["_conversation_id"] = (
                context.message.conversation_id if context.message else ""
            )
            # ── 文件上传链路: 注入原始消息附件 URL 到 tool_params ──
            if context.message is not None:
                raw_attachments = getattr(context.message, "attachments", None) or []
                if raw_attachments:
                    tool_params["_attachments"] = raw_attachments
                    # 第一个附件作为主文件信息注入
                    first = raw_attachments[0] if isinstance(raw_attachments[0], dict) else {}
                    tool_params["_attachment_url"] = first.get("url", "")
                    tool_params["_attachment_type"] = first.get("type", 0)
                    logger.debug(
                        "Injected attachments: %d item(s), primary_url=%s",
                        len(raw_attachments), first.get("url", "")[:80],
                    )

            try:
                if tool_name and tool_name in self._business_flow_tools:
                    # 权限检查：工具必须在 session_api_ids 里（fail-closed）
                    # 拦截时不暴露工具名，避免未授权用户得知能力存在
                    if not session_api_ids:
                        # fail-closed：session_api_ids 空（表未填充）→ 拒绝所有工具调用
                        logger.warning(
                            "Step %s: session_api_ids 为空，tool_registry 表可能未填充，工具调用 fail-closed",
                            step.step_id,
                        )
                        sr = StepResult(
                            step_id=step.step_id,
                            success=False,
                            output="该操作暂不可用，请联系管理员检查系统配置。",
                        )
                        results.append(sr)
                        break
                    if tool_name not in session_api_ids:
                        # 用户无权限调用此工具——不暴露工具名
                        logger.info(
                            "Step %s: 工具调用被权限拦截（不在 session_api_ids，不暴露工具名）",
                            step.step_id,
                        )
                        sr = StepResult(
                            step_id=step.step_id,
                            success=False,
                            output="该操作无法执行，您可能没有相应权限。",
                        )
                        results.append(sr)
                        break
                    # 框架直接调用 handler
                    tool = self._business_flow_tools.get(tool_name)
                    # 注入 user_id 和 message_id 到 handler 调用上下文
                    import inspect
                    sig = inspect.signature(tool.handler)
                    handler_kwargs = {"params": tool_params}
                    if "user_id" in sig.parameters:
                        handler_kwargs["user_id"] = context.user_id if hasattr(context, 'user_id') else ""
                    if "message_id" in sig.parameters:
                        handler_kwargs["message_id"] = context.db_message_id if hasattr(context, 'db_message_id') else ""
                    handler_result = await tool.handler(**handler_kwargs)
                    handler_dict = handler_result if isinstance(handler_result, dict) else {}

                    # 构建 ToolCallRecord
                    elapsed_ms = int((_time.monotonic() - t_start) * 1000)
                    tool_call = ToolCallRecord(
                        tool_name=tool_name,
                        tool_input=tool_params,
                        tool_output=handler_dict,
                        success=handler_dict.get("success", True),
                        elapsed_ms=elapsed_ms,
                    )

                    # 构建 DbResult（如果有 object_id 说明产生了数据库记录）
                    db_results = []
                    object_id = handler_dict.get("object_id", "") or ""
                    if object_id:
                        db_results.append(DbResult(
                            operation="insert",
                            table=tool_name.replace("record_", "") + "s",
                            affected_rows=1,
                            result_data=handler_dict,
                        ))

                    # 构建 RagResult（如果 handler 返回了 RAG 检索数据，如 knowledge_search）
                    rag_results = []
                    if handler_dict.get("rag_results_data"):
                        rrd = handler_dict["rag_results_data"]
                        rag_chunks = [
                            RagChunk(content=c["content"], score=c.get("score", 0.0), doc_name=c.get("doc_name", ""))
                            for c in rrd.get("chunks", [])
                        ]
                        rag_results.append(RagResult(
                            query=rrd.get("query", ""),
                            provider=rrd.get("provider", ""),
                            chunks=rag_chunks,
                            hit_count=rrd.get("hit_count", len(rag_chunks)),
                            elapsed_ms=rrd.get("elapsed_ms", 0),
                        ))

                    output = handler_dict.get("reply", step.description)
                    success = handler_dict.get("success", True)

                    sr = StepResult(
                        step_id=step.step_id,
                        success=success,
                        output=str(output),
                        tool_calls=[tool_call],
                        db_results=db_results,
                        rag_results=rag_results,
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
                logger.error(
                    "Step %s failed: %s (tool=%s params_keys=%s)",
                    step.step_id, e, tool_name or "(none)",
                    list(tool_params.keys()) if tool_params else [],
                    exc_info=True,
                )
                sr = StepResult(
                    step_id=step.step_id,
                    success=False,
                    output=f"步骤执行异常: {e}",
                )

            results.append(sr)

            if not sr.success:
                break  # 失败即停止

        return results

    # ── Node 4 成果总结 ──

    async def node4_summary(self, context: BusContext) -> None:
        """Node 4 [成果总结] —— M3: 规则提炼 structured_result，不做语言组织。

        语言组织由 Session 层 _synthesize_final_reply 完成（M4）。
        review_reply 审核迁移到 Session（M4），本节点不再调 Guardian.review_reply。
        """
        wi = context.work_item
        wi.structured_result = self._extract_structured_result(wi, context)
        # result_text 留空（Session 合成正常路径不用，兜底降级时填）
        wi.result_text = ""
        context.verified_reply = ""  # M4 后由 Session 填
        logger.debug(
            "WI %s node4: structured_result status=%s facts=%d",
            wi.id, wi.structured_result.status, len(wi.structured_result.summary_facts),
        )

    def _extract_structured_result(self, wi, context: BusContext) -> StructuredResult:
        """M3: 规则提炼——从 step_results + output_spec 提取 StructuredResult。零 LLM。"""
        spec = getattr(wi, "output_spec", {}) or {}
        step_results = getattr(wi, "step_results", []) or []

        # status：任一 step 失败 → partial/failed
        failed_steps = [sr for sr in step_results if not getattr(sr, "success", True)]
        if not step_results:
            status = "failed"
        elif failed_steps and len(failed_steps) == len(step_results):
            status = "failed"
        elif failed_steps:
            status = "partial"
        else:
            status = "success"

        # data：按 output_spec.data_fields 从 business_data 取
        data = {}
        for sr in step_results:
            bd = getattr(sr, "business_data", {}) or {}
            for field in spec.get("data_fields", []):
                if field in bd and field not in data:
                    data[field] = bd[field]

        # summary_facts：规则提炼（从 step_results.output 取关键句，截断）
        summary_facts = []
        for sr in step_results:
            output = (getattr(sr, "output", "") or "").strip()
            if output and len(summary_facts) < 8:
                summary_facts.append(output[:200])

        # rag_sources：从 rag_results 收集 doc_name
        rag_sources = []
        for sr in step_results:
            for rr in getattr(sr, "rag_results", []) or []:
                for chunk in getattr(rr, "chunks", []) or []:
                    doc = getattr(chunk, "doc_name", "") or ""
                    if doc and doc not in rag_sources:
                        rag_sources.append(doc)
        # RAG 内容截断后并入 summary_facts（供 Session 消化）
        for sr in step_results:
            for rr in getattr(sr, "rag_results", []) or []:
                for chunk in getattr(rr, "chunks", []) or []:
                    content = (getattr(chunk, "content", "") or "")[:500]
                    if content:
                        summary_facts.append(f"〔{getattr(chunk, 'doc_name', '?')}〕{content}")

        # business_object_no：录入类从 business_data 取
        business_object_no = ""
        for sr in step_results:
            bd = getattr(sr, "business_data", {}) or {}
            for key in ("event_no", "task_no", "meeting_no", "object_id"):
                val = bd.get(key, "")
                if val:
                    business_object_no = str(val)
                    break
            if business_object_no:
                break

        # issues：汇聚 Guardian issues（来自 node3 review_step）+ warnings
        issues = list(getattr(wi, "warnings", []) or [])
        for sr in step_results:
            guardian = getattr(sr, "guardian", None)
            if guardian and getattr(guardian, "reason", ""):
                issues.append(f"[{getattr(sr, 'step_id', '?')}] {guardian.reason}")

        # needs_confirm：从 step_results 推断（如 handler 返回 needs_confirm）
        needs_confirm = any(
            getattr(getattr(sr, "business_data", {}), "needs_confirm", False)
            for sr in step_results
        )

        # error_category：失败分类
        error_category = ""
        if status == "failed":
            for sr in failed_steps:
                output = (getattr(sr, "output", "") or "")
                if "权限" in output or "permission" in output:
                    error_category = "permission"; break
                if "参数" in output or "param" in output:
                    error_category = "param_error"; break
                if "不存在" in output or "未找到" in output:
                    error_category = "not_found"; break
            error_category = error_category or "system"

        # suggested_followup：规则填（可空）
        suggested_followup = ""
        if status == "success" and spec.get("intent", "").startswith("query"):
            suggested_followup = "需要看详情吗？"

        return StructuredResult(
            status=status,
            intent=spec.get("intent", wi.sop_id or "fallback"),
            sop_id=wi.sop_id or "",
            risk_level=getattr(wi, "risk_level", "L2") or "L2",
            data=data,
            summary_facts=summary_facts,
            rag_sources=rag_sources,
            business_object_no=business_object_no,
            issues=issues,
            needs_confirm=needs_confirm,
            error_category=error_category,
            suggested_followup=suggested_followup,
        )

    async def _llm_synthesize_reply(self, wi,
                                      message_history: list[dict] | None = None,
                                      session_ctx=None,
                                      context: BusContext | None = None) -> str:
        """M3 起：node4 不再调用本方法。保留作为 Session 合成 LLM 不可用时的兜底降级（M4）。

        LLM 合成已上移到 SessionAgent._synthesize_final_reply。
        回退链：LLM chat_messages json → 硬编码拼串。
        """
        # 组装步骤结果摘要
        steps_text = ""
        for sr in getattr(wi, "step_results", []) or []:
            status = "OK" if getattr(sr, "success", True) else "FAIL"
            output = (getattr(sr, "output", "") or "")[:300]
            steps_text += f"[{getattr(sr, 'step_id', '?')}] {status}: {output}\n"

        warnings_text = "\n".join(f"  • {w}" for w in (getattr(wi, "warnings", []) or []))
        if not warnings_text:
            warnings_text = "（无）"

        # 尝试 LLM 合成
        if self._llm:
            try:
                # ── 两阶段 prompt 变量替换（D5 设计）──
                # 阶段 1: WorkItem 级变量 — str.replace() 逐项替换
                # 阶段 2: Session 级变量 — 复用 SessionContext.get_prompt_variables()
                prompt_template = _load_workitem_prompt()

                # WorkItem 级变量
                wi_vars = {
                    "{available_tools}": self._build_tools_text(),
                    "{sop_text}": self.injector.get_context_text()[:3000] if self.injector else f"SOP: {wi.sop_id or '未知'}",
                    "{user_input}": (getattr(wi, "user_input", "") or "")[:1000],
                    "{step_results}": steps_text[:2000],
                    "{warnings}": warnings_text,
                }

                system_prompt = prompt_template
                for key, value in wi_vars.items():
                    system_prompt = system_prompt.replace(key, str(value))

                # Session 级变量 — 复用 get_prompt_variables()，无 session_ctx 时清空占位符
                if session_ctx is not None:
                    session_vars = session_ctx.get_prompt_variables()
                    for key, value in session_vars.items():
                        replacement = str(value) if value else "（无）"
                        system_prompt = system_prompt.replace(key, replacement)

                # 清除未替换的 {xxx} 占位符（防止残留模板语法泄露到 LLM）
                system_prompt = re.sub(r'\{[a-z_]+\}', '', system_prompt)

                # ── 归档：存储 Node4 Prompt 注入信息到 BusContext.baggage ──
                if context is not None:
                    try:
                        node4_vars = {
                            "available_tools": f"{len(wi_vars.get('{available_tools}', ''))}字",
                            "sop_text": f"{len(wi_vars.get('{sop_text}', ''))}字",
                            "user_input": (getattr(wi, "user_input", "") or "")[:300],
                            "step_results": f"{len(steps_text)}字",
                            "warnings": f"{len(warnings_text)}字" if getattr(wi, "warnings", None) else "（无）",
                        }
                        if session_ctx is not None:
                            node4_vars["session_vars"] = {
                                "user_name": (getattr(session_ctx, "user_name", "") or "")[:120],
                                "project_name": (getattr(session_ctx, "project_name", "") or "")[:120],
                                "level": f"L{getattr(session_ctx, 'level', 0)}",
                            }
                        context.set("prompt_info_node4", {
                            "template": "workitem.md",
                            "rendered_chars": len(system_prompt),
                            "variables": node4_vars,
                        })
                    except Exception as e:
                        logger.debug("node4 prompt_info storage failed: %s", e)

                full_messages = [{"role": "system", "content": system_prompt}]
                if message_history:
                    full_messages.extend(message_history)
                full_messages.append({
                    "role": "user",
                    "content": f"合成回复: {getattr(wi, 'user_input', '?')[:100]}",
                })

                result = await self._llm.chat_messages(full_messages, json_mode=True)
                data = result.get("data", {})
                reply = data.get("reply", "") if isinstance(data, dict) else ""
                if reply and len(reply) > 20:
                    logger.debug("node4: LLM synthesized reply (%d chars)", len(reply))
                    return reply
                # LLM 返回不可用（空/过短/缺 reply 键）——记录原因后退到硬编码兜底
                logger.warning(
                    "node4: LLM reply unusable (reply=%r len=%d keys=%s), falling back to hardcoded",
                    reply[:80], len(reply),
                    list(data.keys()) if isinstance(data, dict) else [],
                )
            except Exception as e:
                logger.warning("node4: LLM reply synthesis failed, falling back: %s", e)

        # 硬编码回退（保留旧逻辑）
        summary = wi.to_summary()
        rag_hits = summary.get("rag_hits", 0)
        tool_calls = summary.get("tool_calls", 0)
        steps = summary.get("steps_executed", 0)

        if rag_hits > 0:
            rag_texts = []
            for sr in wi.step_results:
                for rag_result in getattr(sr, "rag_results", []):
                    for chunk in getattr(rag_result, "chunks", []):
                        doc_name = getattr(chunk, "doc_name", "") or "未知来源"
                        rag_texts.append(f"根据《{doc_name}》：{chunk.content}")
            if rag_texts:
                return "根据知识库检索，找到以下相关信息：\n\n" + "\n".join(rag_texts[:5])
            return f"已完成知识库查询，共找到 {rag_hits} 条相关信息。"
        elif tool_calls > 0:
            # 优先使用最后一个成功步骤的 output（工具 handler 的 reply 字段，
            # 已在 _real_execute 中写入 sr.output），避免丢失工具实际返回内容
            last_output = ""
            for sr in getattr(wi, "step_results", []) or []:
                if getattr(sr, "success", True) and getattr(sr, "output", ""):
                    last_output = sr.output
            if last_output:
                return last_output
            return (
                f"操作已完成！共执行 {steps} 个步骤，"
                f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
            )
        else:
            return "Emily 已处理完毕。"

    def _build_tools_text(self) -> str:
        """构建可用工具列表文本（供 prompt 注入）。

        使用注入的 self._business_flow_tools 实例（而非 get_instance() 单例——
        BusinessFlowToolRegistry 非单例类，实例由 EmilyCore 构建时注入）。
        """
        try:
            registry = self._business_flow_tools
            if registry:
                entries = []
                for name in sorted(registry.list_names()):
                    tool = registry.get(name)
                    if tool:
                        entries.append(f"- {name}: {tool.description}")
                return "\n".join(entries) if entries else "（无可用工具）"
        except Exception as e:
            logger.warning("format tool list failed: %s", e, exc_info=True)
        return "（无可用工具）"

    # ── 鉴权引擎 ──

    async def authorize(self, context: BusContext, route_decision) -> AuthResult:
        """三维鉴权 —— 基于 PermissionAuthEngine。

        [reserved] 此方法暂无调用者。鉴权当前由 AuthHook（hook.py）在 pipeline
        钩子中独立处理。本方法保留用于未来的 pipeline 内联鉴权集成（例如在
        node1_intent 或 node2_plan 中调用）。

        从 BusContext 获取 SessionContext，委托 PermissionAuthEngine 执行三维鉴权。
        """

        sop_id = getattr(route_decision, "sop_id", None)
        if not sop_id:
            return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")  # 纯聊天无需鉴权

        # 获取 SessionContext（扁平化权限字段）
        session_ctx = context.get_session_context()
        if session_ctx is None:
            return AuthResult(decision=AuthDecision.DENY, reason="无会话上下文",
                            _source="real_auth")

        # 委托 PermissionAuthEngine 三维鉴权（传入 dict 格式）
        engine = getattr(self, "_permission_engine", None)
        if engine is None:
            # 无引擎时走 sop_allow 白名单
            if sop_id in session_ctx.sop_allow:
                return AuthResult(decision=AuthDecision.ALLOW,
                                matched_roles=[f"L{session_ctx.level}"],
                                _source="real_auth_sop_allow")
            return AuthResult(
                decision=AuthDecision.DENY,
                reason=f"SOP {sop_id} 不在用户白名单中",
                _source="real_auth_sop_allow",
            )

        # 构建权限 dict 给 AuthEngine
        perms_dict = {
            "level": session_ctx.level,
            "user_id": session_ctx.user_id,
            "sop_allow": list(session_ctx.sop_allow),
            "granted_codes": list(session_ctx.granted_codes),
            "denied_codes": list(session_ctx.denied_codes),
            "info_level": session_ctx.info_level,
            "company_type": session_ctx.company_type,
            "department": list(session_ctx.department),
            "authorized_node_ids": list(session_ctx.authorized_node_ids),
            "supervisor_id": session_ctx.supervisor_id,
        }
        result = await engine.check_sop_access(perms_dict, sop_id, context)
        if result.allowed:
            return AuthResult(decision=AuthDecision.ALLOW,
                            matched_roles=[f"L{session_ctx.level}"],
                            _source="real_auth_engine")
        return AuthResult(
            decision=AuthDecision.DENY,
            reason=result.reason,
            _source="real_auth_engine",
        )

    # ── 风险评估 ──

    def grade_risk(self, route_decision, operation_type: str = "") -> str:
        """风险评估 —— 基于意图类型和置信度。

        [reserved] 此方法暂无调用者。当前风险等级由 node2_plan 通过 LLM 规划器
        生成，写入 ExecutionPlan.risk_level。本方法保留用于未来的
        node2_plan 内联调用。

        降级逻辑：按 intent_type/confidence/operation_type 分级。
        """
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
