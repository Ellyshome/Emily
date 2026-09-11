"""会话主循环模块（M1）—— 唯一连续对话主体。

定位（计划 M1 / PRD US-01、D6、D7、R2、R4）：
  - Session 持有 `chat_with_tools` 循环：LLM → 工具执行 → tool_result 回灌 → 收敛；
  - 回复由循环**直接产出**，取消"渲染结构化结果 → LLM 二次合成"中间层；
  - 是否调用能力是循环内的隐式判断（能直答则直答），无回合开始的前置分类步骤（D6）；
  - 多能力协作时由 M4 做任务级粗排，循环**照单逐项执行**、成果逐步回灌、按结果重排（D7/R4）；
  - 挂起（缺参）→ M5 登记 + 对话中提问；确认/取消 → M6。

与旧链路的关系（PRD §4.4-4）：本模块为**并行模块**，`session_agent.py` / `session_pool.py`
/ `workitem/**` 零改动；由 EmilyCore 入口按开关分派。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from ..adapters.standard.reply import ReplyMessage
from ..services.session_archive_writer import CapabilityCallRecord

logger = logging.getLogger("emily.session.loop")

# 顺序/并列语义提示词：命中才尝试任务级粗排（避免每条业务消息都多花一次规划 LLM）
_COMPOUND_HINTS = (
    "然后", "再", "之后", "接着", "随后", "并且", "同时", "顺便", "以及", "并把", "并将",
    "分别", "依次", "先", "然后给", "再给",
)

_DEFAULT_MAX_ITERATIONS = 12


def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _strip_unfilled(template: str) -> str:
    return re.sub(r"\{[a-z_]+\}", "", template or "")


def _looks_compound(text: str) -> bool:
    """粗略判断是否需要任务级粗排（零 LLM 成本）。"""
    t = text or ""
    if len(t) < 8:
        return False
    return any(h in t for h in _COMPOUND_HINTS)


class SessionLoop:
    """会话主循环 —— 每会话一个实例。"""

    def __init__(
        self,
        conversation_id: str,
        context,
        llm_client=None,
        catalog=None,
        planner=None,
        suspends=None,
        dialog=None,
        capability_registry=None,
        capability_runner=None,
        resolvers=None,
        business_tools=None,
        archive_writer=None,
        outbound_bus=None,
        path_router=None,
        config=None,
    ) -> None:
        self.conversation_id = conversation_id
        self.context = context
        self._llm = llm_client
        self._catalog = catalog
        self._planner = planner
        self._suspends = suspends
        self._dialog = dialog
        self._capability_registry = capability_registry
        self._capability_runner = capability_runner
        self._resolvers = resolvers
        self._business_tools = business_tools
        self._archive_writer = archive_writer
        self._outbound_bus = outbound_bus
        self._path_router = path_router
        self._config = config

        self._archive_md_path = ""
        self._turn_counter = 0
        self._compacting = False
        self._last_actor: dict | None = None
        self._last_calls: list = []
        self._turn_capability_names: set = set()

        # 归档：写文件头（fail-open）
        if self._archive_writer is not None:
            try:
                self._archive_md_path = self._archive_writer.ensure_header(
                    conversation_id=conversation_id,
                    user_name=getattr(context, "user_name", "") or "anonymous",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    context=self._header_context(),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("SessionLoop archive header failed: %s", e)

    def _header_context(self) -> dict:
        c = self.context
        return {
            "user_id": getattr(c, "user_id", ""),
            "user_position": getattr(c, "user_position", ""),
            "company_name": getattr(c, "company_name", ""),
            "company_type": getattr(c, "company_type", ""),
            "level": getattr(c, "level", 1),
            "is_management_unit": getattr(c, "is_management_unit", False),
            "scopes": list(getattr(c, "scopes", []) or []),
            "project_ids": list(getattr(c, "project_ids", []) or []),
            "sop_allow": list(getattr(c, "sop_allow", []) or []),
            "project_name": getattr(c, "project_name", ""),
            "available_tools": list(getattr(c, "available_tools", []) or []),
            "available_skills": list(getattr(c, "available_skills", []) or []),
            "rag_available": getattr(c, "rag_available", False),
            "visible_files_count": getattr(c, "visible_files_count", 0),
            "prompt_name": "session_loop.md",
        }

    # ══════════════════════════════════════════════════════════════════════
    # 入口
    # ══════════════════════════════════════════════════════════════════════

    async def handle(self, message, db_message_id: str = "", current_user_id: str = "") -> ReplyMessage | None:
        """处理一条入站消息（与旧 SessionAgent.handle 同签名）。"""
        content = (message.content or "").strip()

        # ① 快速短路（零 LLM；AC-US-01.2）
        from .session_agent import SessionAgent
        fast = SessionAgent._try_fast_reply(content, getattr(message, "sender_name", "") or "")
        if fast is not None:
            return self._reply(message, fast)

        # ② 当前操作者快照（群聊多用户权限越界修复）
        self._last_actor = await self._fetch_actor(current_user_id)

        # ③ 归档：轮次开头
        self._turn_counter += 1
        self._append_archive_turn_start(message)

        try:
            final_reply = await self._handle_impl(message, db_message_id=db_message_id)
        except Exception as e:  # noqa: BLE001
            logger.error("SessionLoop[%s] handle crashed: %s", self.conversation_id, e, exc_info=True)
            final_reply = "抱歉，处理时出现了异常，请稍后重试或换个说法。"

        if final_reply is None:
            return None

        self._record_turn(message, final_reply)
        self._maybe_compact()
        self.append_capability_section(getattr(self, "_last_calls", []) or [])
        self._append_archive_turn_end(final_reply)
        return self._reply(message, final_reply)

    # ══════════════════════════════════════════════════════════════════════
    # 主流程
    # ══════════════════════════════════════════════════════════════════════

    async def _handle_impl(self, message, db_message_id: str = "") -> str | None:
        content = message.content or ""
        actor = self._last_actor or {}
        # 本轮能力调用清单（供归档内嵌；AC-US-08.1）
        self._last_calls: list = []

        # ── 挂起续接判定（US-06）──
        claimed = None
        if self._suspends is not None and self._suspends.has_pending:
            actor_uid = actor.get("user_id") or self.context.user_id
            if await self._suspends.is_continuation(content, actor_uid):
                pending = self._suspends.match(actor_uid)
                if pending is not None:
                    claimed = self._suspends.claim(pending.call_id, actor_uid)
            else:
                self._suspends.discard_all()
                logger.info("SessionLoop[%s] 挂起作废（用户转入新话题）", self.conversation_id)

        allowed_sops = self._path_router.allowed_sops() if self._path_router is not None else None
        entries = self._catalog.list_capabilities(
            actor, self.context, allowed_sops=allowed_sops) if self._catalog is not None else []
        capability_names = {e.name for e in entries if e.name}
        system_prompt = self._build_system_prompt(entries)

        # ── 续接：直接继续被挂起的能力（不重述原始需求；AC-US-06.1）──
        if claimed is not None:
            merged = (claimed.params or {}).get("request", "")
            merged = f"{merged}\n\n[用户补充] {content}" if merged else content
            result = await self._run_capability(claimed.capability, merged, message, db_message_id)
            self._last_calls.append(self._record_call(claimed.capability, merged, result))
            return self._settle_capability_result(claimed.capability, result, message, db_message_id)

        calls: list = self._last_calls
        self._turn_capability_names = capability_names
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self._llm_history())
        group_extra = await self._group_injections(message, db_message_id)
        messages.extend(group_extra)
        messages.append({"role": "user", "content": content})

        pending_injection = self._pending_event_injection()
        if pending_injection:
            messages.append(pending_injection)

        tool_specs = self._catalog.build_tool_specs(
            actor, self.context, allowed_sops=allowed_sops) if self._catalog is not None else []

        # ── 任务级粗排：仅对疑似复合请求（零 LLM 成本前置判定；US-05）──
        # 边界：粗排只编排 **SOP 能力**。计划步入参以统一键 `request` 传递，只有 SOP 能力的
        # 入参契约与之匹配；查询/写类工具 schema 各异，塞进粗排会丢参（实测产生"未命名事件"），
        # 故这些能力交由循环内 ReAct 按各自 schema 处理（D5：粗排只决定调哪些能力与顺序）。
        plan_names = {e.name for e in entries if e.kind == "sop"}
        if self._planner is not None and _looks_compound(content) and plan_names:
            plan = await self._planner.plan(content, plan_names)
            if plan is not None:
                return await self._run_plan(plan, messages, message, db_message_id, calls, tool_specs)

        # ── 主循环（ReAct）──
        reply = await self._run_loop(messages, tool_specs, message, db_message_id, calls, capability_names)

        if self._suspends is not None and self._suspends.has_pending:
            pending = self._suspends.match(actor.get("user_id") or self.context.user_id)
            if pending is not None:
                return pending.question  # 直接提问，不再经 LLM 改写
        return reply

    async def _run_loop(self, messages: list, tool_specs: list, message, db_message_id: str,
                        calls: list, capability_names: set) -> str:
        max_iter = int(getattr(self._config, "agent_loop_max_iterations", _DEFAULT_MAX_ITERATIONS)
                       or _DEFAULT_MAX_ITERATIONS)
        last_text = ""
        for iteration in range(max_iter):
            result = await self._llm_call(messages, tool_specs)
            if result is None:
                return last_text or "抱歉，我暂时无法完成这个请求，请稍后再试。"

            rtype = result.get("type", "")
            if rtype != "tool_call":
                text = (result.get("content") or "").strip()
                if text:
                    return self._enforce_capability_progress(text, calls)
                last_text = text
                # 空文本（reasoner 输出落在 reasoning_content）→ 追问一次
                messages.append({"role": "user", "content": "[系统] 请直接给用户回复。"})
                continue

            tool_name = result.get("tool_name", "")
            arguments = result.get("tool_arguments", {}) or {}
            tool_call_id = result.get("tool_call_id", "")

            assistant_msg = {
                "role": "assistant",
                "content": result.get("content") or "",
                "tool_calls": [{
                    "id": tool_call_id, "type": "function",
                    "function": {"name": tool_name,
                                 "arguments": json.dumps(arguments, ensure_ascii=False)},
                }],
            }
            if result.get("reasoning_content"):
                assistant_msg["reasoning_content"] = result["reasoning_content"]
            messages.append(assistant_msg)

            tool_payload = await self._execute_tool(
                tool_name, arguments, message, db_message_id, calls, capability_names)
            messages.append({
                "role": "tool", "tool_call_id": tool_call_id,
                "content": json.dumps(tool_payload, ensure_ascii=False, default=str),
            })

        # 达上限 → 可读收尾（AC-US-01.3）
        logger.warning("SessionLoop[%s] iteration cap %d reached", self.conversation_id, max_iter)
        return last_text or "这个请求步骤较多，我先就目前掌握的信息答复；如需继续，请补充一句让我接着做。"

    async def _run_plan(self, plan, messages: list, message, db_message_id: str,
                        calls: list, tool_specs: list) -> str:
        """照单逐项执行能力调用计划（执行权留在本循环；D7/R4）。"""
        from .capability_plan import PlanCursor

        cursor = PlanCursor(plan, max_depth=getattr(plan, "max_depth", 3))
        self._publish_progress(cursor.progress_text())

        while not cursor.is_complete:
            layer = cursor.next_runnable()
            if not layer:
                break
            outcomes = await asyncio.gather(*[
                self._run_plan_step(step, message, db_message_id)
                for step in layer
            ], return_exceptions=True)
            for step, outcome in zip(layer, outcomes):
                if isinstance(outcome, BaseException):
                    from .capability_runner import CapabilityResult
                    outcome = CapabilityResult.failed(f"调用异常：{outcome}")
                record = self._record_call(step.capability, (step.params or {}).get("request", ""), outcome)
                calls.append(record)
                if getattr(outcome, "needs_input", False):
                    cursor.mark_failed(step.step_id, outcome)
                elif getattr(outcome, "status", "failed") == "failed":
                    cursor.mark_failed(step.step_id, outcome)
                else:
                    cursor.mark_done(step.step_id, outcome)
            self._publish_progress(cursor.progress_text())

        # 计划成果回灌 → 由循环据实收口（AC-US-05.2：回复反映实际执行结果）
        messages.append({"role": "system", "content": self._render_plan_results(calls)})
        messages.append({"role": "user", "content": "[系统] 以上能力调用已完成，请据此直接回复用户，不要重复调用。"})
        # 收口循环仍用完整可见工具集（不能只放开计划内的能力，否则 confirm_pending 等控制工具不可用）
        all_names = {t.get("function", {}).get("name") for t in tool_specs if t.get("function")}
        all_names.discard(None)
        return await self._run_loop(messages, tool_specs, message, db_message_id, calls, all_names)

    # ══════════════════════════════════════════════════════════════════════
    # 工具执行
    # ══════════════════════════════════════════════════════════════════════

    async def _execute_tool(self, tool_name: str, arguments: dict, message, db_message_id: str,
                            calls: list, capability_names: set) -> dict:
        # fail-closed：不在当前可见能力集内 → 拒绝
        if not tool_name or tool_name not in capability_names:
            return {"success": False, "reply": "该操作无法执行，您可能没有相应权限。"}

        # ── 控制工具：确认 / 取消（M6）──
        if self._dialog is not None and tool_name in ("confirm_pending", "cancel_pending"):
            actor_uid = (self._last_actor or {}).get("user_id") or self.context.user_id
            event_id, _ = self._dialog.resolve_event(
                self.conversation_id, str(arguments.get("event_no") or ""))
            action = "confirm" if tool_name == "confirm_pending" else "cancel"
            text = await self._dialog.handle(action, event_id, actor_uid)
            calls.append(CapabilityCallRecord(
                capability=tool_name, params_digest=json.dumps(arguments, ensure_ascii=False),
                result_status="success", result_digest=text, triggered_by=actor_uid,
            ))
            return {"success": True, "reply": text}

        # ── SOP 能力（M3）──
        if self._capability_registry is not None and self._capability_registry.has(tool_name):
            request = str(arguments.get("request") or "")
            additional = str(arguments.get("additional_input") or "").strip()
            if additional:
                request = f"{request}\n\n[用户补充] {additional}"
            result = await self._run_capability(tool_name, request, message, db_message_id)
            calls.append(self._record_call(tool_name, request, result))
            if getattr(result, "needs_input", False):
                self._register_suspend(tool_name, request, getattr(result, "question", ""))
            return result.to_tool_dict()

        # ── resolver（名称→UUID 解析等）──
        resolver = self._resolvers.get(tool_name) if self._resolvers is not None else None
        if resolver is not None:
            try:
                rresult = await resolver.handle(arguments, self.context)
            except Exception as e:  # noqa: BLE001
                logger.error("resolver %s failed: %s", tool_name, e, exc_info=True)
                rresult = {"found": False, "error": f"resolver 异常: {e}"}
            return rresult if isinstance(rresult, dict) else {"result": rresult}

        # ── 业务工具（查询 / 执行手脚）──
        return await self._execute_business_tool(tool_name, arguments, db_message_id, calls)

    async def _execute_business_tool(self, tool_name: str, arguments: dict,
                                     db_message_id: str, calls: list) -> dict:
        from ..workitem.langgraph_engine.agent.fallback_policy import FallbackPolicy

        tool = self._business_tools.get(tool_name) if self._business_tools is not None else None
        if tool is None:
            return {"success": False, "reply": f"工具 '{tool_name}' 未注册"}

        actor = self._last_actor or {}
        tier = FallbackPolicy.gate(actor).value
        allowed = set(FallbackPolicy.resolve(tier, with_write=True) or [])
        if tool_name not in allowed:
            return {"success": False,
                    "reply": "该操作在当前档位不可用，请走对应标准流程或联系管理员。"}
        ok, gate_err = FallbackPolicy.assert_write_allowed(
            tool_name, tier, getattr(tool, "write_mode", "read"))
        if not ok:
            return {"success": False, "reply": gate_err}

        params = self._inject_runtime_params(arguments, db_message_id)
        try:
            import inspect
            sig = inspect.signature(tool.handler)
            kwargs = {"params": params}
            if "user_id" in sig.parameters:
                kwargs["user_id"] = actor.get("user_id") or self.context.user_id
            if "message_id" in sig.parameters:
                kwargs["message_id"] = db_message_id
            handler_result = await tool.handler(**kwargs)
        except Exception as e:  # noqa: BLE001
            logger.error("business tool %s failed: %s", tool_name, e, exc_info=True)
            handler_result = {"success": False, "reply": f"工具执行异常: {e}"}

        payload = handler_result if isinstance(handler_result, dict) else {}
        calls.append(CapabilityCallRecord(
            capability=tool_name,
            params_digest=json.dumps({k: v for k, v in arguments.items() if not k.startswith("_")},
                                     ensure_ascii=False, default=str),
            result_status="success" if payload.get("success", True) else "failed",
            result_digest=str(payload.get("reply", ""))[:300],
            triggered_by=(actor.get("user_id") or self.context.user_id),
        ))
        return payload

    def _inject_runtime_params(self, arguments: dict, db_message_id: str) -> dict:
        p = dict(arguments or {})
        p["_user_id"] = (self._last_actor or {}).get("user_id") or self.context.user_id
        p["_message_id"] = db_message_id
        p["_conversation_id"] = self.conversation_id
        return p

    # ══════════════════════════════════════════════════════════════════════
    # 能力调用（M3）+ 超时（AC-US-01.4）
    # ══════════════════════════════════════════════════════════════════════

    async def _run_plan_step(self, step, message, db_message_id: str):
        """执行计划中的单步 —— **按能力类型分派**（SOP 能力 / 查询 / 写 / 解析）。

        计划步的能力可以是任意可见能力（D5 粗排按能力名产出），因此不能一律走
        SOP 能力执行器；否则查询类步骤会被误判为"能力未注册"而失败。
        """
        from .capability_runner import CapabilityResult

        name = step.capability
        params = dict(step.params or {})
        if self._capability_registry is not None and self._capability_registry.has(name):
            return await self._run_capability(name, params.get("request", ""), message, db_message_id)

        # 非 SOP 能力：复用与主循环一致的工具执行路径（权限 fail-closed + 护栏）
        payload = await self._execute_tool(
            name, params, message, db_message_id, [], getattr(self, "_turn_capability_names", set()))
        payload = payload if isinstance(payload, dict) else {}
        ok = bool(payload.get("success"))
        issues = [str(i) for i in (payload.get("issues") or [])]
        if not ok and not issues:
            issues = [str(payload.get("reply") or "执行失败")]
        return CapabilityResult(
            status="success" if ok else "failed",
            summary=list(payload.get("summary") or []),
            data=dict(payload.get("data") or {}),
            business_object_no=str(payload.get("business_object_no") or ""),
            issues=issues,
            readable_text=str(payload.get("reply") or ""),
            needs_input=bool(payload.get("needs_input")),
            question=str(payload.get("question") or ""),
        )

    async def _run_capability(self, name: str, request: str, message, db_message_id: str):
        from .capability_runner import CapabilityResult

        cap = self._capability_registry.get(name) if self._capability_registry is not None else None
        if cap is None or self._capability_runner is None:
            return CapabilityResult.failed("能力未注册")
        timeout = int(getattr(self._config, "capability_call_timeout_seconds", 120) or 120)
        try:
            return await asyncio.wait_for(
                self._capability_runner.run(
                    cap.sop_id, request, name=cap.name, message=message,
                    db_message_id=db_message_id, actor_snapshot=self._last_actor,
                    session_context=self.context,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("SessionLoop[%s] capability %s timed out (%ds)",
                           self.conversation_id, name, timeout)
            return CapabilityResult.failed(f"该操作耗时超过 {timeout} 秒，已中止。")

    def _settle_capability_result(self, name: str, result, message, db_message_id: str) -> str:
        """续接执行后的收口（挂起则再提问，否则给可读成果）。"""
        if getattr(result, "needs_input", False):
            self._register_suspend(name, "", getattr(result, "question", ""))
            return getattr(result, "question", "") or "请补充信息"
        return getattr(result, "readable_text", "") or "处理完成。"

    def _register_suspend(self, capability: str, request: str, question: str) -> None:
        if self._suspends is None:
            return
        from .suspend_registry import PendingCall

        self._suspends.register(PendingCall(
            capability=capability,
            params={"request": request},
            question=question or "请补充信息",
            initiator_user_id=(self._last_actor or {}).get("user_id") or self.context.user_id,
        ))

    def _record_call(self, capability: str, request: str, result) -> CapabilityCallRecord:
        actor_uid = (self._last_actor or {}).get("user_id") or self.context.user_id
        return CapabilityCallRecord(
            capability=capability,
            params_digest=json.dumps({"request": request}, ensure_ascii=False)[:300],
            result_status=getattr(result, "status", "failed"),
            result_digest=(getattr(result, "readable_text", "") or "")[:300],
            triggered_by=actor_uid,
            elapsed_ms=int(getattr(result, "elapsed_ms", 0) or 0),
            needs_input=bool(getattr(result, "needs_input", False)),
            issues=list(getattr(result, "issues", []) or [])[:3],
        )

    # ══════════════════════════════════════════════════════════════════════
    # LLM / Prompt
    # ══════════════════════════════════════════════════════════════════════

    async def _llm_call(self, messages: list, tool_specs: list):
        if self._llm is None:
            return None
        model = (getattr(self._llm, "agent_loop_model", None)
                 or getattr(self._llm, "router_model", None) or self._llm.model)
        max_tokens = int(getattr(self._config, "llm_agent_loop_max_tokens", 8192) or 8192)
        cap = getattr(self.context, "cap_max_tokens", None)
        if callable(cap):
            try:
                max_tokens = cap(max_tokens, model, system_prompt="",
                                 tools=tool_specs,
                                 window_override=getattr(self._config, "llm_context_window_override", 0))
            except Exception as e:  # noqa: BLE001
                logger.debug("cap_max_tokens skipped: %s", e)
        try:
            result = await self._llm.chat_messages(
                messages, tools=tool_specs or None, model=model, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            logger.error("SessionLoop LLM call failed: %s", e, exc_info=True)
            return None
        rec = getattr(self.context, "record_usage", None)
        if callable(rec):
            try:
                rec((result or {}).get("usage"))
            except Exception as e:  # noqa: BLE001
                logger.debug("record_usage skipped: %s", e)
        return result

    def _build_system_prompt(self, entries: list) -> str:
        from ..infrastructure.llm.prompt_loader import load_prompt

        template = load_prompt("session_loop") or ""
        caps = "\n".join(
            f"- {e.name}（{e.kind}）: {e.description}" for e in entries if e.name
        ) or "（暂无可用能力）"
        prompt = template.replace("{capability_catalog}", caps)
        try:
            for key, value in (self.context.get_prompt_variables(self._last_actor) or {}).items():
                prompt = prompt.replace(key, str(value) if value else "（无）")
        except Exception as e:  # noqa: BLE001
            logger.debug("prompt variables skipped: %s", e)
        prompt = prompt.replace("{current_datetime}", _beijing_now_str())
        return _strip_unfilled(prompt)

    def _llm_history(self) -> list:
        getter = getattr(self.context, "get_llm_history", None)
        if callable(getter):
            try:
                return list(getter() or [])
            except Exception as e:  # noqa: BLE001
                logger.debug("get_llm_history skipped: %s", e)
        return list(getattr(self.context, "message_history", []) or [])

    # ══════════════════════════════════════════════════════════════════════
    # 上下文：操作者快照 / 群聊注入 / 待确认项
    # ══════════════════════════════════════════════════════════════════════

    async def _fetch_actor(self, current_user_id: str) -> dict:
        actor_uid = current_user_id or self.context.user_id
        try:
            from .session_data_fetcher import SessionDataFetcher
            core = getattr(self, "_core", None)
            snapshot = await asyncio.to_thread(
                SessionDataFetcher.fetch_actor_snapshot, actor_uid, core)
            snapshot = snapshot or {}
            snapshot["user_id"] = actor_uid
            return snapshot
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLoop fetch_actor_snapshot failed: %s", e)
            return {"user_id": actor_uid}

    async def _group_injections(self, message, db_message_id: str) -> list:
        """群聊回溯上下文 + 群级长期记忆注入（fail-open，与旧路径对齐）。"""
        extra: list = []
        if getattr(message, "conversation_type", "") != "group" or not getattr(message, "group_id", ""):
            return extra
        if db_message_id and self._llm is not None:
            try:
                from ..services.group_context_service import GroupContextService
                ctx_text = await GroupContextService(llm_client=self._llm).build_group_context(
                    group_id=message.group_id, current_message_id=db_message_id,
                    user_question=message.content or "")
                if ctx_text:
                    extra.append({"role": "system", "content": ctx_text})
            except Exception as e:  # noqa: BLE001
                logger.warning("group context build failed (non-blocking): %s", e)
        try:
            from ..services.group_memory_service import GroupMemoryService
            mem = GroupMemoryService().build_injection(message.group_id)
            if mem:
                extra.append({"role": "system", "content": mem})
        except Exception as e:  # noqa: BLE001
            logger.debug("group memory injection failed: %s", e)
        return extra

    def _pending_event_injection(self):
        if self._dialog is None:
            return None
        return self._dialog.prompt_injection(self._dialog.fetch_pending(self.conversation_id))

    # ══════════════════════════════════════════════════════════════════════
    # 归档 / 进度 / 回复
    # ══════════════════════════════════════════════════════════════════════

    def _render_plan_results(self, calls: list) -> str:
        if not calls:
            return "（无能力调用结果）"
        lines = ["## 能力调用结果"]
        for i, c in enumerate(calls, 1):
            lines.append(f"{i}. {c.capability} [{c.result_status}] {c.result_digest or '（无成果）'}")
        return "\n".join(lines)

    def _enforce_capability_progress(self, text: str, calls: list) -> str:
        """有失败的能力调用时，确保回复不静默丢失（AC-US-05.4）。"""
        failed = [c for c in calls if c.result_status == "failed"]
        if failed and "失败" not in text and "未" not in text:
            reasons = "；".join((c.result_digest or c.capability) for c in failed[:2])
            text = f"{text}\n（说明：部分操作未完成 —— {reasons}）"
        return text

    def _publish_progress(self, text: str) -> None:
        if not text or self._outbound_bus is None:
            return
        try:
            self._outbound_bus.publish("progress", {
                "content": text, "conversation_id": self.conversation_id,
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("publish progress failed: %s", e)

    def _append_archive_turn_start(self, message) -> None:
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            content = self._archive_writer.render_turn_start(
                turn_idx=self._turn_counter, user_message=(message.content or "")[:2000])
            self._archive_writer.append_section(self._archive_md_path, content)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive turn_start failed: %s", e)

    def _append_archive_turn_end(self, reply_text: str) -> None:
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            from ..services.session_archive_writer import SessionArchiveWriter
            reply_body, warnings = SessionArchiveWriter._split_reply_and_warnings((reply_text or "")[:2000])
            self._archive_writer.append_section(
                self._archive_md_path, self._archive_writer.render_turn_end(reply_body, warnings))
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive turn_end failed: %s", e)

    def append_capability_section(self, calls: list) -> None:
        """把本轮能力调用清单写入归档（AC-US-08.1）。"""
        if self._archive_writer is None or not self._archive_md_path or not calls:
            return
        try:
            section = self._archive_writer.render_capability_section(calls)
            if section:
                self._archive_writer.append_section(self._archive_md_path, section)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive capability section failed: %s", e)

    def _record_turn(self, message, reply_text: str) -> None:
        try:
            self.context.record_turn(
                user_content=(message.content or "")[:2000],
                assistant_content=(reply_text or "")[:2000],
                sender_name=getattr(message, "sender_name", "") or "",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("record_turn failed: %s", e)

    def _maybe_compact(self) -> None:
        if self._llm is None or self._compacting:
            return
        should = getattr(self.context, "should_compact", None)
        if not callable(should):
            return
        try:
            if not should(getattr(self._llm, "model", None), system_prompt="",
                          reserve_tokens=getattr(self._config, "llm_compact_reserve_tokens", 16384),
                          window_override=getattr(self._config, "llm_context_window_override", 0)):
                return
        except Exception as e:  # noqa: BLE001
            logger.debug("should_compact check failed: %s", e)
            return
        self._compacting = True

        async def _run():
            try:
                await self.context.compress_overflow(
                    self._llm,
                    keep_recent_tokens=getattr(self._config, "llm_compact_keep_recent_tokens", 20000),
                    reserve_tokens=getattr(self._config, "llm_compact_reserve_tokens", 16384))
            except Exception as e:  # noqa: BLE001
                logger.warning("compress_overflow failed: %s", e)
            finally:
                self._compacting = False

        asyncio.ensure_future(_run())

    def _reply(self, message, content: str) -> ReplyMessage:
        msg_id = message.message_id if getattr(message, "conversation_type", "") == "group" else None
        return ReplyMessage(
            conversation_id=message.conversation_id, content=content,
            reply_to_message_id=msg_id,
        )

    async def archive(self) -> None:
        """会话注销归档（复用 SessionContext.persist_and_consolidate）。"""
        try:
            await self.context.persist_and_consolidate(
                llm_client=self._llm, md_file_path=self._archive_md_path,
                archive_writer=self._archive_writer,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLoop archive warning: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# 会话池（并行模块，与旧 SessionPoolManager 同形）
# ══════════════════════════════════════════════════════════════════════════════

class SessionLoopPool:
    """新路径会话池：conversation_id → SessionLoop（TTL 复用 + 会话内串行）。"""

    class _Entry:
        __slots__ = ("loop", "last_active", "lock")

        def __init__(self, loop) -> None:
            import time
            self.loop = loop
            self.last_active = time.time()
            self.lock = asyncio.Lock()

    def __init__(self, config=None, core=None) -> None:
        self._core = core
        self._config = config
        self._sessions: dict = {}
        self._sweeper_task = None

    def _ttl(self) -> int:
        return int(getattr(self._config, "session_ttl_seconds", 600) or 600)

    def _max_concurrent(self) -> int:
        return int(getattr(self._config, "session_max_concurrent", 100) or 100)

    async def route(self, message, user_id: str = "", db_message_id: str = "") -> ReplyMessage | None:
        """路由一条入站消息（与旧 SessionPoolManager.route 同签名）。"""
        conv_id = message.conversation_id
        await self._ensure_sweeper()
        entry = self._sessions.get(conv_id)
        if entry is None:
            if len(self._sessions) >= self._max_concurrent():
                self.sweep_expired()
            loop = self._build_loop(message, user_id)
            if loop is None:
                return None
            entry = SessionLoopPool._Entry(loop)
            self._sessions[conv_id] = entry
            logger.info("SessionLoopPool created: conv=%s (pool size=%d)", conv_id, len(self._sessions))
        async with entry.lock:
            import time
            entry.last_active = time.time()
            return await entry.loop.handle(
                message, db_message_id=db_message_id, current_user_id=user_id)

    def _build_loop(self, message, user_id: str):
        core = self._core
        if core is None:
            logger.error("SessionLoopPool: core 未注入，无法构建会话主循环")
            return None
        try:
            from .session_context import SessionContext
            from .capability_catalog import build_catalog
            from .capability_plan import SessionPlanner
            from .capability_runner import build_runner
            from .suspend_registry import SuspendRegistry
            from .confirm_dialog import ConfirmDialog

            context = SessionContext.create(
                user_id=user_id, conversation_id=message.conversation_id,
                sender_name=getattr(message, "sender_name", "") or "", core=core,
            )
            llm = getattr(core, "_llm_client", None)
            path_router = getattr(core, "_session_path_router", None)
            loop = SessionLoop(
                conversation_id=message.conversation_id,
                context=context,
                llm_client=llm,
                catalog=build_catalog(core),
                planner=SessionPlanner(llm_client=llm, config=getattr(core, "config", None)),
                suspends=SuspendRegistry(llm_client=llm, config=getattr(core, "config", None)),
                dialog=ConfirmDialog(journal=getattr(core, "_event_journal", None)),
                capability_registry=getattr(core, "_capability_registry", None),
                capability_runner=build_runner(core),
                resolvers=getattr(core, "_resolvers", None),
                business_tools=getattr(core, "_business_flow_tools", None),
                archive_writer=getattr(core, "_session_archive_writer", None),
                outbound_bus=getattr(core, "outbound_bus", None),
                path_router=path_router,
                config=getattr(core, "config", None),
            )
            loop._core = core
            return loop
        except Exception as e:  # noqa: BLE001
            logger.error("SessionLoopPool build failed: %s", e, exc_info=True)
            return None

    # ── TTL 清理 ──

    async def _ensure_sweeper(self) -> None:
        if self._sweeper_task is not None:
            return
        interval = int(getattr(self._config, "sweep_interval_seconds", 300) or 300)
        self._sweeper_task = asyncio.create_task(self._sweep_loop(interval))

    async def _sweep_loop(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                self.sweep_expired()
            except Exception as e:  # noqa: BLE001
                logger.warning("SessionLoopPool sweeper error: %s", e)

    def sweep_expired(self) -> int:
        """按 TTL 归档并移除过期会话（memory-only 池，无 WorkItem 任务段判定）。"""
        import time
        now = time.time()
        ttl = self._ttl()
        expired = [cid for cid, e in self._sessions.items() if now - e.last_active > ttl]
        for cid in expired:
            entry = self._sessions.pop(cid, None)
            if entry is not None:
                try:
                    asyncio.ensure_future(entry.loop.archive())
                except Exception as e:  # noqa: BLE001
                    logger.warning("SessionLoopPool sweep archive failed for %s: %s", cid, e)
        if expired:
            logger.info("SessionLoopPool swept %d expired session(s)", len(expired))
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._sessions)
