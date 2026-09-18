# emily-core/emily_core/workitem/langgraph_engine/agent/loop.py
"""工单侧 agent loop **适配层** —— 循环机制来自共享内核 `emily_core.kernel.react_kernel`。

M2 改造（US-03）前：本文件是工单侧自持的一份 ReAct 循环实现，与会话侧那份同构但各自维护。
M2 改造后：循环机制（提示装配 / 模型调用归一 / 消息回填 / 工具执行 / 迭代记账 / 文本纠错）
由 `react_kernel` 单份实现，本文件只保留**工单侧差异**：

  - agent_node：注入工单侧提示（SOP 全文 + work_spec）、历史、模型调用（reasoner 字段回传、
    动态压低输出上限、usage 记账）、上下文溢出压缩恢复、DSMT/文本纠错策略；
    并把内核裁决映射为工单侧终态（iteration cap / 模型异常 / 文本超限 → error_analysis）。
  - tool_node：注入工单侧控制工具（ask_user 挂起 / complete_work 收口）与执行通道
    （resolver、业务工具、权限 fail-closed、分级兜底门禁、StepResult 归档）。

路由（route_after_agent / route_after_tool）保持原语义不变。
"""
from __future__ import annotations

import json
import logging
import time as _time
from typing import Any

from ....kernel import react_kernel
from ....infrastructure.logging.llm_logger import LLMInteractionLogger
from ....infrastructure.llm.errors import ContextOverflowError
from ...pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
from .tool_adapter import _session_api_ids
from .fallback_policy import FallbackPolicy
from .prompt_builder import build_system_prompt

logger = logging.getLogger("emily.langgraph.loop")


def _get_ctx():
    from ..state import get_bus_context
    return get_bus_context()


def _ctx_from_runtime(runtime):
    """取工单上下文：**优先官方运行时上下文**（M4 / US-04），不可用时回退进程内通道。

    官方通道是上下文入口（`context=` 传入，图声明 `context_schema`）；
    进程内 ContextVar 仅作能力侧的兼容桥，不再作为入口。
    """
    bus = getattr(getattr(runtime, "context", None), "bus", None)
    if bus is not None:
        return bus
    return _get_ctx()


def _inject_runtime_params(tool_params: dict, ctx) -> dict:
    """注入运行时上下文到 tool_params。参照原 WorkItemAgent 运行时参数注入。"""
    p = dict(tool_params or {})
    p["_user_id"] = ctx.user_id or ""
    p["_message_id"] = ctx.db_message_id or ""
    p["_conversation_id"] = ctx.message.conversation_id if ctx.message else ""
    if ctx.message is not None:
        raw = getattr(ctx.message, "attachments", None) or []
        if raw:
            p["_attachments"] = raw
            first = raw[0] if isinstance(raw[0], dict) else {}
            p["_attachment_url"] = first.get("url", "")
            p["_attachment_type"] = first.get("type", 0)
    # 数据边界（项目范围 + 表级权限）：会话侧唯一出口，工具层（query_data 等）据此过滤
    session_ctx = ctx.get_session_context()
    if session_ctx is not None:
        p["_session_scope"] = session_ctx.build_tool_scope()
    return p


# ══════════════════════════════════════════════════════════════════════════════
# agent_node —— 模型步（适配层）
# ══════════════════════════════════════════════════════════════════════════════


async def agent_node(state: dict, *, llm_client, business_tools, resolvers, sop_text,
                     config, runtime=None) -> dict:
    """agent_node —— 调共享内核的模型步，按裁决返回工单侧状态。"""
    ctx = _ctx_from_runtime(runtime)
    wi = ctx.work_item
    session_ctx = ctx.get_session_context()
    keys = react_kernel.LoopKeys()

    def _prompt() -> str:
        return build_system_prompt(
            sop_text=sop_text,
            tool_specs=list(state.get("_tool_specs") or []),
            session_ctx=session_ctx,
            work_spec=getattr(wi, "work_spec", {}) or {},
            user_input=wi.user_input,
            additional_input=getattr(wi, "additional_input", "") or "",
        )

    def _history() -> list:
        if session_ctx is None:
            return []
        getter = getattr(session_ctx, "get_llm_history", None)
        if callable(getter):
            return list(getter() or [])
        return list(getattr(session_ctx, "message_history", []) or [])

    async def _llm_call(messages: list, tool_specs: list) -> dict:
        LLMInteractionLogger.set_context(
            pipeline_run_id=ctx.pipeline_run_id,
            conversation_id=ctx.message.conversation_id if ctx.message else "",
            user_id=ctx.user_id,
            call_category="agent_loop",
        )
        try:
            # 优先 agent_loop_model（v4-flash DSML 泄漏规避），回退 router_model → model
            model = (getattr(llm_client, "agent_loop_model", None)
                     or getattr(llm_client, "router_model", None)
                     or llm_client.model)
            configured_max = getattr(config, "llm_agent_loop_max_tokens", 8192)
            max_tokens = configured_max
            if session_ctx is not None and getattr(config, "llm_dynamic_output", True):
                try:
                    max_tokens = session_ctx.cap_max_tokens(
                        configured_max, model,
                        system_prompt=next((m.get("content", "") for m in messages
                                            if m.get("role") == "system"), ""),
                        tools=tool_specs,
                        window_override=getattr(config, "llm_context_window_override", 0),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("cap_max_tokens skipped: %s", e)
            result = await llm_client.chat_messages(messages, tools=tool_specs, model=model,
                                                    max_tokens=max_tokens)
            if session_ctx is not None:
                rec = getattr(session_ctx, "record_usage", None)
                if callable(rec):
                    rec(result.get("usage"))
            wi.llm_call_count += 1
            return result
        finally:
            LLMInteractionLogger.clear_context()

    async def _recover(exc: Exception) -> "list | None":
        """上下文溢出闭环：压缩后重建消息（Pi 的 overflow → compact → retry 语义）。"""
        if not isinstance(exc, ContextOverflowError) or session_ctx is None:
            return None
        logger.warning("agent_node context overflow, compacting then retrying once: %s", exc)
        await session_ctx.compress_overflow(
            llm_client,
            keep_recent_tokens=getattr(config, "llm_compact_keep_recent_tokens", 20000),
            reserve_tokens=getattr(config, "llm_compact_reserve_tokens", 16384),
        )
        rebuilt = [{"role": "system", "content": _prompt()}]
        rebuilt.extend(_history())
        rebuilt.append({"role": "user", "content": wi.user_input})
        return rebuilt

    def _nudge(result: dict, attempt: int) -> "react_kernel.NudgeOutcome | None":
        """文本纠错策略：诊断 → 纠正 → 重试；连续 3 次仍为文本则不再作为回复。"""
        content = str((result or {}).get("content") or "")
        if attempt >= 3:
            return react_kernel.NudgeOutcome(reject=True)
        if "<｜" in content or "DSML" in content or "<\u2016" in content:
            diagnosis = ("你返回了 DSML/XML 文本标签格式（如 <｜tool_calls>），"
                         "这不是有效的工具调用。请直接通过 function calling 接口调用工具，"
                         "不要在回复内容里写任何 XML/DSML 标签。")
        elif content.strip().startswith("{") or content.strip().startswith("["):
            diagnosis = ("你返回了 JSON 文本，但工具调用必须通过 function calling 接口输出，"
                         "不能在 content 里写 JSON。请直接调用对应工具。")
        else:
            diagnosis = ("你返回了纯文本回复，但当前阶段必须调用工具才能执行操作。"
                         f"可用工具：{', '.join(t['function']['name'] for t in (state.get('_tool_specs') or []))}。")
        if attempt == 1:
            correction = f"[系统纠正] {diagnosis}\n请立即通过 function calling 接口调用正确的工具。"
        else:
            correction = (
                f"[系统警告] {diagnosis}\n正确示例：调用 complete_work(status=\"success\", summary=[\"具体事实\"], data={{...}})\n"
                "或调用 ask_user(question=\"需要补充什么信息？\")\n"
                "请立即调用工具，不要返回文本。"
            )
        logger.warning("agent_node got type=text (attempt %d/3), retrying with correction", attempt)
        return react_kernel.NudgeOutcome(retry_text=correction)

    max_iter = int(getattr(config, "agent_loop_max_iterations", 12) or 12)
    ports = react_kernel.LoopPorts(
        llm_call=_llm_call,
        tool_specs=lambda: list(state.get("_tool_specs") or []),
        prompt=_prompt,
        history=_history,
        user_input=lambda: wi.user_input,
        max_iterations=lambda: max_iter,
        text_nudge=_nudge,
        context_recovery=_recover,
        classify_error=react_kernel.default_classify_error,
    )

    patch = await react_kernel.llm_step(state, ports=ports)
    outcome = react_kernel.decide_next(patch)
    # 中性文本不进工单状态（工单成果由 summarizing 统一产出）
    base = {k: v for k, v in patch.items() if k != keys.text}
    logger.info("agent_node outcome=%s iteration=%s", outcome, patch.get(keys.iteration))

    if outcome == react_kernel.OUTCOME_CAP:
        logger.warning("agent_node: iteration cap %d reached, escalate to error_analysis", max_iter)
        return {**base, "wi_state": "error_analysis",
                "error_analysis": {"should_abort": False, "should_escalate": True,
                                   "root_cause": f"agent loop 达到 iteration cap ({max_iter})"}}

    if outcome == react_kernel.OUTCOME_ERROR:
        err = dict(patch.get(keys.error) or {})
        logger.error("agent_node model call failed (fatal): %s", err.get("root_cause", ""))
        return {"wi_state": "error_analysis", "messages": [], "_pending_tool_call": None,
                "error_analysis": {"should_abort": True, "should_escalate": True,
                                   "root_cause": err.get("root_cause", "模型调用异常")}}

    if outcome == react_kernel.OUTCOME_REJECT:
        n = int(patch.get(keys.nudge) or 0)
        logger.error("agent_node: %d consecutive text responses, escalating to error_analysis", n)
        return {**base, "wi_state": "error_analysis",
                "error_analysis": {"should_abort": True, "should_escalate": False,
                                   "root_cause": f"LLM 连续 {n} 次返回文本而非工具调用",
                                   "error_type": "transient_failure"}}

    if outcome == react_kernel.OUTCOME_FINAL:
        # 工单侧的文本纠错策略不会放行"纯文本即回复"（第 3 次即 reject），此分支为防御性映射
        logger.warning("agent_node: unexpected final-text outcome, routing to summarizing")
        return {**base, "wi_state": "summarizing"}

    return {**base, "wi_state": "executing"}


# ══════════════════════════════════════════════════════════════════════════════
# tool_node —— 工具步（适配层）
# ══════════════════════════════════════════════════════════════════════════════


async def tool_node(state: dict, *, llm_client, business_tools, resolvers,
                    runtime=None) -> dict:
    """tool_node —— 控制工具（ask_user/complete_work）+ 执行通道，机制走共享内核。"""
    ctx = _ctx_from_runtime(runtime)
    wi = ctx.work_item
    keys = react_kernel.LoopKeys()

    # ── 控制工具：ask_user（挂起）──
    async def _ask_user(name: str, arguments: dict, _state: dict) -> dict:
        from langgraph.types import interrupt
        question = str((arguments or {}).get("question") or "请补充信息")
        # interrupt 挂起，用户续接时 Command(resume=...) 返回值作为 tool_result
        user_reply = interrupt(question)
        return {
            "outcome": react_kernel.OUTCOME_CONTINUE,
            "tool_message": f"用户回复：{user_reply}",
            "extra_messages": [{"role": "user", "content": str(user_reply)}],
            "patch": {"wi_state": "executing", "waiting_question": question},
        }

    # ── 控制工具：complete_work（收口）──
    async def _complete_work(name: str, arguments: dict, _state: dict) -> dict:
        from ...pipeline.interfaces.execution import StructuredResult
        args = arguments or {}
        sr = StructuredResult(
            status=args.get("status", "success"),
            intent=(getattr(wi, "output_spec", {}) or {}).get("intent", wi.sop_id or "fallback"),
            sop_id=wi.sop_id or "",
            risk_level=getattr(wi, "risk_level", "L2") or "L2",
            data=args.get("data", {}) or {},
            summary_facts=[str(s) for s in args.get("summary", []) or []],
            rag_sources=[],
            business_object_no=args.get("business_object_no", "") or "",
            issues=[str(i) for i in args.get("issues", []) or []],
            needs_confirm=bool(args.get("needs_confirm", False)),
            error_category="" if args.get("status", "success") != "failed" else "system",
            suggested_followup="",
        )
        wi.structured_result = sr
        ctx.set("work_completed", True)
        logger.info("tool_node complete_work: status=%s, object=%s", sr.status, sr.business_object_no)
        return {
            "outcome": react_kernel.OUTCOME_TERMINAL,
            "tool_message": "成果已接收，工作完成。",
            "patch": {"wi_state": "summarizing"},
        }

    # ── 执行通道：resolver / 业务工具（权限 fail-closed + 分级兜底门禁）──
    async def _execute(name: str, arguments: dict) -> dict:
        resolver = resolvers.get(name)
        if resolver is not None:
            session_ctx = ctx.get_session_context()
            try:
                rresult = await resolver.handle(arguments, session_ctx)
                logger.info("tool_node resolver %s result: %s", name,
                            json.dumps(rresult, ensure_ascii=False)[:200])
            except Exception as e:  # noqa: BLE001
                logger.error("resolver %s failed: %s", name, e, exc_info=True)
                rresult = {"found": False, "error": f"resolver 异常: {e}"}
            return rresult if isinstance(rresult, dict) else {"result": rresult}

        t_start = _time.monotonic()
        tool = business_tools.get(name) if name in business_tools else None
        if tool is None:
            err_msg = f"工具 '{name}' 未注册"
            _append_step_result(wi, name, arguments, {"success": False, "reply": err_msg},
                                t_start, success=False)
            return {"success": False, "reply": err_msg}

        # 权限检查（fail-closed，参照原 WorkItemAgent 权限过滤）
        session_api_ids = _session_api_ids(ctx)
        if not session_api_ids or name not in session_api_ids:
            err_msg = "该操作无法执行，您可能没有相应权限。"
            _append_step_result(wi, name, arguments, {"success": False, "reply": err_msg},
                                t_start, success=False)
            return {"success": False, "reply": err_msg}

        # M4: 分级兜底门禁（fail-closed）—— 意图识别失败时，写工具仅高级档追加/迁移放行，
        # 覆盖/删除类一律拒绝；读工具仍受档位白名单裁剪。
        if getattr(wi, "intent_type", "") == "fallback":
            tier = getattr(wi, "fallback_tier", "basic") or "basic"
            allowed_tools = FallbackPolicy.resolve(tier, with_write=True)
            if name not in allowed_tools:
                err_msg = "该操作在当前兜底档位不可用，请走对应标准流程或联系管理员。"
                _append_step_result(wi, name, arguments, {"success": False, "reply": err_msg},
                                    t_start, success=False)
                return {"success": False, "reply": err_msg}
            ok, gate_err = FallbackPolicy.assert_write_allowed(
                name, tier, getattr(tool, "write_mode", "read"))
            if not ok:
                _append_step_result(wi, name, arguments, {"success": False, "reply": gate_err},
                                    t_start, success=False)
                return {"success": False, "reply": gate_err}

        tool_params = _inject_runtime_params(arguments, ctx)
        try:
            import inspect
            sig = inspect.signature(tool.handler)
            handler_kwargs = {"params": tool_params}
            if "user_id" in sig.parameters:
                handler_kwargs["user_id"] = ctx.user_id
            if "message_id" in sig.parameters:
                handler_kwargs["message_id"] = ctx.db_message_id
            handler_result = await tool.handler(**handler_kwargs)
            logger.info("tool_node business %s result: %s", name,
                        json.dumps(handler_result, ensure_ascii=False, default=str)[:200])
        except Exception as e:  # noqa: BLE001
            logger.error("tool_node %s failed: %s", name, e, exc_info=True)
            handler_result = {"success": False, "reply": f"工具执行异常: {e}"}

        handler_dict = handler_result if isinstance(handler_result, dict) else {}
        _append_step_result(wi, name, tool_params, handler_dict, t_start,
                            success=handler_dict.get("success", True))
        return handler_dict

    ports = react_kernel.LoopPorts(
        control_tools={"ask_user": _ask_user, "complete_work": _complete_work},
        execute=_execute,
    )
    patch = await react_kernel.tool_step(state, ports=ports)
    patch.setdefault("wi_state", "executing")
    return patch


def _append_step_result(wi, tool_name, tool_params, handler_dict, t_start, success=True):
    """构建 StepResult 追加到 wi.step_results。参照原 WorkItemAgent 实现。

    保留 step_results 供 summarizing 节点提取 StructuredResult + ArchiveHook 归档兼容。
    """
    elapsed_ms = int((_time.monotonic() - t_start) * 1000)
    tool_call = ToolCallRecord(
        tool_name=tool_name,
        tool_input=tool_params,
        tool_output=handler_dict,
        success=success,
        elapsed_ms=elapsed_ms,
    )
    db_results = []
    object_id = handler_dict.get("object_id", "") or ""
    if object_id:
        db_results.append(DbResult(
            operation="insert",
            table=tool_name.replace("record_", "") + "s",
            affected_rows=1,
            result_data=handler_dict,
        ))
    sr = StepResult(
        step_id=f"iter-{len(wi.step_results) + 1}",
        success=success,
        output=str(handler_dict.get("reply", "")),
        tool_calls=[tool_call],
        db_results=db_results,
        business_data=handler_dict,
    )
    wi.add_step_result(sr)


def route_after_agent(state: dict) -> str:
    """agent_node 之后的条件边路由（语义不变）。

    - wi_state == 'summarizing' → summarizing（complete_work 完成）
    - wi_state == 'error_analysis' → error_analysis（text fallback 超限 / iteration cap）
    - 有 pending_tool_call → tool_node
    - wi_state == 'executing' → agent_node（text fallback 重试）
    - 兜底 → summarizing
    """
    wi_state = state.get("wi_state", "")
    if wi_state == "summarizing":
        return "summarizing"
    if wi_state == "error_analysis":
        return "error_analysis"
    if state.get("_pending_tool_call"):
        return "tool_node"
    # text fallback 重试：wi_state="executing" 但无 pending tool_call → 回 agent_node 继续
    if wi_state == "executing":
        return "agent_node"
    return "summarizing"


def route_after_tool(state: dict) -> str:
    """tool_node 之后路由：complete_work → summarizing，否则 → agent_node 继续循环。"""
    state["_pending_tool_call"] = None
    if state.get("wi_state") == "summarizing":
        return "summarizing"
    return "agent_node"
