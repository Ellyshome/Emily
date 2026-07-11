"""WorkItemAgent —— 全局单例，异步处理所有 WorkItem（蓝图 §5.3）。

核心设计：不是每个 WorkItem 创建独立 Agent，而是全局唯一 Agent 实例，
异步处理所有 WorkItem。新 WorkItem 进来时，KnowledgeInjector 增量注入
执行该 WorkItem 缺失的知识（SOP/工具/schema），最小化上下文污染。

节点 ↔ 大脑映射：
  wi_node1 [意图验证+注入]  ← KnowledgeInjector + RouteDecision 构建
  wi_node2 [计划+标准]      ← LLM Planning | MockPlanner
  wi_node3 [执行+验收]      ← RealExecutor | MockWorkAgent + RealGuardian（并进审核）
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
from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult, RagResult, RagChunk, GuardianStepVerdict
from .pipeline.interfaces.auth import AuthResult, AuthDecision
from .pipeline.mocks import MockPlanner, MockWorkAgent
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

        # Mock 大脑（保留作为 fallback）
        self._planner = MockPlanner()
        self._work_agent = MockWorkAgent()

        # Guardian: LLM 可用则自动启用，不可用则为 None（静默跳过）
        if self._llm:
            self._guardian = RealGuardian(llm_client=self._llm, config=config)
            logger.info("Guardian enabled: RealGuardian (lightweight LLM review)")
        else:
            self._guardian = None
            logger.info("LLM not available — Guardian disabled (silent skip)")

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

        return ExecutionPlan(
            risk_level="L2",
            steps=steps,
            acceptance_criteria=[],
            estimated_steps=len(steps),
            _source="skill_definition",
        )

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
        """Node 2 [计划+标准] —— Skill 定义优先，否则 LLM 规划 或 MockPlanner fallback。"""
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

        # ── 原有 LLM/Mock 规划路径 ──
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
        """LLM 动态规划 —— 从 SOP 全文、对话历史、用户输入生成 ExecutionPlan。

        使用 chat_messages() 传入完整 message_history，利用 KV cache 复用。
        """
        sop_text = ""
        if hasattr(self.injector, 'get_context_text'):
            sop_text = self.injector.get_context_text()

        # 构建可用工具列表（从 BusinessFlowToolRegistry 动态生成）
        tools_text = ""
        if self._business_flow_tools:
            tool_entries = []
            for name in sorted(self._business_flow_tools.list_names()):
                tool = self._business_flow_tools.get(name)
                if tool:
                    tool_entries.append(f"- {name}: {tool.description}")
            tools_text = "\n".join(tool_entries) if tool_entries else "（无可用工具）"

        planner_prompt = _load_planner_prompt()
        system_prompt = planner_prompt.format(
            sop_text=sop_text[:4000] if sop_text else f"SOP: {wi.sop_id or '未知'}（全文未加载）",
            user_input=wi.user_input,
            available_tools=tools_text,
        )

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

        try:
            result = await self._llm.chat_messages(full_messages, json_mode=True)
            data = result.get("data", {})
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

    # ── Node 3 真实执行引擎 ──

    async def node3_execute(self, context: BusContext) -> None:
        """Node 3 [执行+验收] —— Skill 路径优先，否则走原有 _real_execute。"""
        wi = context.work_item
        if wi.execution_plan is None:
            return

        # ── Skill 路径 ──
        if getattr(wi.execution_plan, "_source", "") == "skill_definition" and self._skill_executor:
            step_results = await self._execute_skill(wi, context)
        else:
            # ── 原有路径 ──
            executor_mode = self._resolve_mode("executor")

            if executor_mode == "real":
                step_results = await self._real_execute(wi.execution_plan, context)
            else:
                step_results = await self._work_agent.execute(wi.execution_plan, context)

        # Guardian 并进审核：每个 step 的 review 作为后台 Task，
        # 在全部步骤执行完后 gather() 汇合，不阻塞主链路
        guardian_tasks: list[asyncio.Task] = []
        if self._guardian:
            for sr in step_results:
                task = asyncio.create_task(
                    self._guardian.review_step(sr),
                    name=f"guardian_step_{sr.step_id}",
                )
                guardian_tasks.append(task)

        # 汇合点：等待所有 guardian task 完成（大部分早已自然完成）
        if guardian_tasks:
            try:
                notes = await asyncio.gather(*guardian_tasks, return_exceptions=True)
                for sr, note in zip(step_results, notes):
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

        for sr in step_results:
            wi.add_step_result(sr)

        wi.llm_call_count += len(step_results)
        if step_results:
            context.agent_result = step_results[-1]
            context.agent_reply = step_results[-1].output
        logger.debug(
            "WI %s node3: %d steps, executor=%s guardian=%s",
            wi.id, len(step_results), executor_mode,
            "enabled" if self._guardian else "disabled",
        )

    async def _real_execute(self, plan: ExecutionPlan, context: BusContext) -> list[StepResult]:
        """真实执行引擎 —— 按 PlanStep 调用工具 handler。

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
        """Node 4 [成果总结] —— LLM 回复合成 + Guardian 出站审核（追加标记）。

        优先使用 LLM 根据 workitem.md prompt + 步骤结果生成自然语言回复；
        LLM 不可用时回退到硬编码拼串。
        """
        wi = context.work_item
        summary = wi.to_summary()
        steps = summary.get("steps_executed", 0)
        tool_calls = summary.get("tool_calls", 0)

        # executor_mode=real 时无 Mock 前缀
        executor_mode = self._resolve_mode("executor")
        mock_prefix = "" if executor_mode == "real" else "[Mock 模式] "

        # ---- LLM 回复合成（传入对话历史 + SessionContext）----
        session_ctx = context.get_session_context() if context else None
        message_history = getattr(session_ctx, 'message_history', []) if session_ctx else []
        draft = await self._llm_synthesize_reply(wi, mock_prefix,
                                                   message_history=message_history if message_history else None,
                                                   session_ctx=session_ctx)

        # Guardian 出站审核 —— 只标记不拦截
        if self._guardian:
            try:
                note = await self._guardian.review_reply(draft, wi)
                if note and note.issues:
                    for issue in note.issues:
                        wi.add_warning(f"[reply] {issue}")
            except Exception as e:
                logger.debug("Guardian review_reply failed (silent skip): %s", e)

        # 将 warnings 追加到回复末尾（只标记，不替换）
        if wi.warnings:
            warning_text = (
                "\n\n⚠️ Emily 提醒（系统自动审核标记，供参考）：\n"
                + "\n".join(f"  • {w}" for w in wi.warnings[-5:])
            )
            wi.result_text = draft + warning_text
        else:
            wi.result_text = draft

        wi.llm_call_count += 1
        context.verified_reply = wi.result_text
        logger.debug(
            "WI %s node4: reply_len=%d guardian=%s warnings=%d",
            wi.id, len(wi.result_text),
            "enabled" if self._guardian else "disabled",
            len(wi.warnings),
        )

    async def _llm_synthesize_reply(self, wi, mock_prefix: str = "",
                                      message_history: list[dict] | None = None,
                                      session_ctx=None) -> str:
        """用 LLM 根据 workitem.md prompt + 步骤结果 + 对话历史合成自然语言回复。

        使用 chat_messages() 传入完整 message_history，利用 KV cache 复用。
        回退链：LLM chat_messages json → 硬编码拼串。

        Args:
            wi: WorkItem 实例
            mock_prefix: Mock 模式下的前缀
            message_history: 对话历史，由 node4_summary 从 BusContext 提取后传入
            session_ctx: SessionContext 实例，用于填充 prompt 中的用户/项目变量
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
                        if value:
                            system_prompt = system_prompt.replace(key, str(value))

                # 清除未替换的 {xxx} 占位符（防止残留模板语法泄露到 LLM）
                system_prompt = re.sub(r'\{[a-z_]+\}', '', system_prompt)

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
                    return mock_prefix + reply
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
                return mock_prefix + "根据知识库检索，找到以下相关信息：\n\n" + "\n".join(rag_texts[:5])
            return mock_prefix + f"已完成知识库查询，共找到 {rag_hits} 条相关信息。"
        elif tool_calls > 0:
            return (
                f"{mock_prefix}操作已完成！共执行 {steps} 个步骤，"
                f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
            )
        else:
            return mock_prefix + "Emily 已处理完毕。"

    @staticmethod
    def _build_tools_text() -> str:
        """构建可用工具列表文本（供 prompt 注入）。"""
        # 惰性导入避免循环依赖
        try:
            from ..tools.business_flow_tools import BusinessFlowToolRegistry
            registry = BusinessFlowToolRegistry.get_instance()
            if registry:
                entries = []
                for name in sorted(registry.list_names()):
                    tool = registry.get(name)
                    if tool:
                        entries.append(f"- {name}: {tool.description}")
                return "\n".join(entries) if entries else "（无可用工具）"
        except Exception:
            pass
        return "（无可用工具）"

    # ── 鉴权引擎 ──

    async def authorize(self, context: BusContext, route_decision) -> AuthResult:
        """三维鉴权 —— 基于 PermissionAuthEngine。

        [reserved] 此方法暂无调用者。鉴权当前由 AuthHook（hook.py）在 pipeline
        钩子中独立处理。本方法保留用于未来的 pipeline 内联鉴权集成（例如在
        node1_intent 或 node2_plan 中调用）。

        从 BusContext 获取 SessionContext，委托 PermissionAuthEngine 执行三维鉴权。
        mock 模式：始终 ALLOW。
        """
        auth_mode = self._resolve_mode("auth")
        if auth_mode != "real":
            return AuthResult(decision=AuthDecision.ALLOW, matched_roles=["all"],
                            _source="mock_auth")

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
        """真实风险评估 —— 基于意图类型和置信度。

        [reserved] 此方法暂无调用者。当前风险等级由 node2_plan 通过 LLM 规划器
        （或 MockPlanner）生成，写入 ExecutionPlan.risk_level。本方法保留用于未来
        的 node2_plan 内联调用（当 EMILY_RISK_MODE=real 时替代 LLM 输出中的
        risk_level 字段）。

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
