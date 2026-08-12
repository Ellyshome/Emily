# emily-core/emily_core/workitem/langgraph_engine/agent/loop.py
"""Agent loop —— agent_node ↔ tool_node ReAct 循环。

agent_node: 调 chat_with_tools(messages, tools)，按返回 type 路由
tool_node:  执行 tool_call handler，追加 StepResult + tool_result message
route_after_agent: tool_call→tool_node / text→done / interrupt→waiting / cap→error_analysis

状态即对话历史：messages list 累积 system+user+assistant(tool_call)+tool_result。
"""
from __future__ import annotations

import json
import logging
import time as _time
from typing import Any

from ....infrastructure.logging.llm_logger import LLMInteractionLogger
from ...pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
from .tool_adapter import _session_api_ids
from .prompt_builder import build_system_prompt

logger = logging.getLogger("emily.langgraph.loop")


def _get_ctx():
    from ..state import get_bus_context
    return get_bus_context()


def _inject_runtime_params(tool_params: dict, ctx) -> dict:
    """注入运行时上下文到 tool_params。参照 workitem_agent.py:567。"""
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
    return p


async def agent_node(state: dict, *, llm_client, business_tools, resolvers, sop_text, config) -> dict:
    """agent_node —— 调 chat_with_tools，返回增量 messages。

    首次进入时构建 system prompt 并初始化 messages。
    """
    ctx = _get_ctx()
    wi = ctx.work_item
    messages = list(state.get("messages", []))

    # tool_specs 由 created 节点固化到 state，全 loop 只读取用、不重建
    tool_specs = state.get("_tool_specs") or []

    # ── 首次进入：构建 system prompt + 初始 messages ──
    if not messages:
        session_ctx = ctx.get_session_context()
        system_prompt = build_system_prompt(
            sop_text=sop_text,
            tool_specs=tool_specs,
            session_ctx=session_ctx,
            work_spec=getattr(wi, "work_spec", {}) or {},
            user_input=wi.user_input,
            additional_input=getattr(wi, "additional_input", "") or "",
        )
        messages = [{"role": "system", "content": system_prompt}]
        # 追加 session 消息历史（多轮上下文）
        if session_ctx is not None:
            messages.extend(getattr(session_ctx, "message_history", []) or [])
        messages.append({"role": "user", "content": wi.user_input})

    # ── iteration cap 检查 ──
    iteration_count = state.get("iteration_count", 0)
    max_iter = getattr(config, "agent_loop_max_iterations", 12)
    if iteration_count >= max_iter:
        logger.warning("agent_node: iteration_count=%d >= cap=%d, escalate to error_analysis",
                       iteration_count, max_iter)
        return {"wi_state": "error_analysis",
                "error_analysis": {"should_abort": False, "should_escalate": True,
                                   "root_cause": f"agent loop 达到 iteration cap ({max_iter})"},
                "iteration_count": iteration_count}

    # ── 调 LLM ──
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
        result = await llm_client.chat_messages(messages, tools=tool_specs, model=model,
                                                 max_tokens=getattr(config, "llm_agent_loop_max_tokens", 8192))
    except Exception as e:
        logger.error("agent_node LLM failed: %s", e, exc_info=True)
        # 防止死循环：连续 3 次 LLM 失败则强制 abort
        fail_count = state.get("_llm_fail_count", 0) + 1
        should_abort = fail_count >= 3
        if should_abort:
            logger.critical("agent_node: %d consecutive LLM failures, forcing abort", fail_count)
        state["error_analysis"] = {"should_abort": should_abort, "should_escalate": True,
                                   "root_cause": f"LLM 调用异常(第{fail_count}次): {e}"}
        return {"wi_state": "error_analysis", "_llm_fail_count": fail_count,
                "error_analysis": state["error_analysis"],
                "messages": [],  # 重置 messages 防止 stale tool_calls
                "_pending_tool_call": None}
    finally:
        LLMInteractionLogger.clear_context()

    rtype = result.get("type", "")
    logger.info("agent_node LLM result: type=%s tool=%s content_preview=%s",
                rtype, result.get("tool_name",""),
                (result.get("content","") or "")[:80])

    if rtype == "tool_call":
        # 追加 assistant tool_call message（OpenAI 格式）
        # DeepSeek reasoner 模型要求把 reasoning_content 作为独立字段回传，
        # 不能合并进 content（合并会导致下一轮 400：reasoning_content must be passed back）。
        # content 用 LLM 实际返回的 content（reasoner 模式下通常为空）。
        assistant_msg: dict = {
            "role": "assistant",
            "content": result.get("content") or "",
            "tool_calls": [{
                "id": result.get("tool_call_id", ""),
                "type": "function",
                "function": {
                    "name": result.get("tool_name", ""),
                    "arguments": json.dumps(result.get("tool_arguments", {}),
                                            ensure_ascii=False),
                },
            }],
        }
        reasoning_content = result.get("reasoning_content") or ""
        if reasoning_content:
            assistant_msg["reasoning_content"] = reasoning_content
        messages.append(assistant_msg)
        # 暂存当前 tool_call 供 tool_node 取
        state["_pending_tool_call"] = {
            "id": result.get("tool_call_id", ""),
            "name": result.get("tool_name", ""),
            "arguments": result.get("tool_arguments", {}),
        }
        wi.llm_call_count += 1
        state["_text_fallback_count"] = 0  # 重置 text fallback 计数
        return {"messages": messages, "wi_state": "executing",
                "_pending_tool_call": state.get("_pending_tool_call"),
                "iteration_count": iteration_count + 1}

    # type == "text" → LLM 未遵守 prompt（应调 complete_work/ask_user/tool）
    content = result.get("content", "")
    # reasoner 模型 text 分支同样需要回传 reasoning_content（独立字段，不合并进 content）
    text_msg: dict = {"role": "assistant", "content": content}
    reasoning_content = result.get("reasoning_content") or ""
    if reasoning_content:
        text_msg["reasoning_content"] = reasoning_content
    messages.append(text_msg)
    wi.llm_call_count += 1

    text_fallback_count = state.get("_text_fallback_count", 0) + 1
    max_text_fallback = 3

    if text_fallback_count < max_text_fallback:
        # 诊断文本特征，给出具体纠错方向
        if "<｜" in content or "DSML" in content or "<\u2016" in content:
            diagnosis = ("你返回了 DSML/XML 文本标签格式（如 <｜tool_calls>），"
                         "这不是有效的工具调用。请直接通过 function calling 接口调用工具，"
                         "不要在回复内容里写任何 XML/DSML 标签。")
        elif content.strip().startswith("{") or content.strip().startswith("["):
            diagnosis = ("你返回了 JSON 文本，但工具调用必须通过 function calling 接口输出，"
                         "不能在 content 里写 JSON。请直接调用对应工具。")
        else:
            diagnosis = ("你返回了纯文本回复，但当前阶段必须调用工具才能执行操作。"
                         f"可用工具：{', '.join(t['function']['name'] for t in tool_specs)}。")
        if text_fallback_count == 1:
            correction = (
                f"[系统纠正] {diagnosis}\n请立即通过 function calling 接口调用正确的工具。"
            )
        else:
            correction = (
                f"[系统警告] {diagnosis}\n正确示例：调用 complete_work(status=\"success\", summary=[\"具体事实\"], data={{...}})\n"
                "或调用 ask_user(question=\"需要补充什么信息？\")\n"
                "请立即调用工具，不要返回文本。"
            )
        messages.append({"role": "user", "content": correction})
        logger.warning("agent_node got type=text (attempt %d/%d), retrying with correction",
                       text_fallback_count, max_text_fallback)
        return {"messages": messages, "wi_state": "executing",
                "_text_fallback_count": text_fallback_count,
                "iteration_count": iteration_count + 1}

    # Tier C: 超过重试次数，升级 error_analysis（直接 abort 防止死循环）
    logger.error("agent_node: %d consecutive text responses, escalating to error_analysis",
                 text_fallback_count)
    return {"wi_state": "error_analysis",
            "error_analysis": {
                "should_abort": True,
                "should_escalate": False,
                "root_cause": f"LLM 连续 {text_fallback_count} 次返回文本而非工具调用",
                "error_type": "transient_failure",
            },
            "_text_fallback_count": text_fallback_count,
            "iteration_count": iteration_count + 1}


async def tool_node(state: dict, *, llm_client, business_tools, resolvers) -> dict:
    """tool_node —— 执行 pending tool_call，追加 tool_result message + StepResult。

    支持 ask_user 工具 → 触发 interrupt（WAITING_FOR_INPUT）。
    """
    ctx = _get_ctx()
    wi = ctx.work_item
    tc = state.get("_pending_tool_call") or {}
    tool_name = tc.get("name", "")
    arguments = tc.get("arguments", {}) or {}
    tool_call_id = tc.get("id", "")

    messages = list(state.get("messages", []))

    # ── ask_user 工具 → interrupt ──
    if tool_name == "ask_user":
        from langgraph.types import interrupt
        question = arguments.get("question", "请补充信息")
        state["waiting_question"] = question
        # interrupt 挂起，用户续接时 Command(resume=...) 返回值作为 tool_result
        user_reply = interrupt(question)
        # resume 后 user_reply 是用户补充输入
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"用户回复：{user_reply}",
        })
        # 把用户回复追加为 user message，供 LLM 下一轮消费
        messages.append({"role": "user", "content": str(user_reply)})
        return {"messages": messages, "wi_state": "executing", "_pending_tool_call": None}

    # ── complete_work 控制工具 → 构造 StructuredResult，路由 summarizing ──
    if tool_name == "complete_work":
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
        messages.append({"role": "tool", "tool_call_id": tool_call_id,
                         "content": "成果已接收，工作完成。"})
        logger.info("tool_node complete_work: status=%s, object=%s",
                    sr.status, sr.business_object_no)
        return {"messages": messages, "wi_state": "summarizing", "_pending_tool_call": None}

    # ── resolver 工具 ──
    resolver = resolvers.get(tool_name)
    if resolver is not None:
        session_ctx = ctx.get_session_context()
        try:
            rresult = await resolver.handle(arguments, session_ctx)
            logger.info("tool_node resolver %s result: %s", tool_name,
                        json.dumps(rresult, ensure_ascii=False)[:200])
        except Exception as e:
            logger.error("resolver %s failed: %s", tool_name, e, exc_info=True)
            rresult = {"found": False, "error": f"resolver 异常: {e}"}
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(rresult, ensure_ascii=False),
        })
        return {"messages": messages, "wi_state": "executing", "_pending_tool_call": None}

    # ── 业务工具 ──
    t_start = _time.monotonic()
    tool = business_tools.get(tool_name) if tool_name in business_tools else None
    if tool is None:
        err_msg = f"工具 '{tool_name}' 未注册"
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": err_msg})
        _append_step_result(wi, tool_name, arguments, {"success": False, "reply": err_msg},
                            t_start, success=False)
        return {"messages": messages, "wi_state": "executing", "_pending_tool_call": None}

    # 权限检查（fail-closed，参照 workitem_agent.py:590）
    session_api_ids = _session_api_ids(ctx)
    if not session_api_ids or tool_name not in session_api_ids:
        err_msg = "该操作无法执行，您可能没有相应权限。"
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": err_msg})
        _append_step_result(wi, tool_name, arguments, {"success": False, "reply": err_msg},
                            t_start, success=False)
        return {"messages": messages, "wi_state": "executing", "_pending_tool_call": None}

    # 注入运行时上下文
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
        logger.info("tool_node business %s result: %s", tool_name,
                    json.dumps(handler_result, ensure_ascii=False, default=str)[:200])
    except Exception as e:
        logger.error("tool_node %s failed: %s", tool_name, e, exc_info=True)
        handler_result = {"success": False, "reply": f"工具执行异常: {e}"}

    handler_dict = handler_result if isinstance(handler_result, dict) else {}
    _append_step_result(wi, tool_name, tool_params, handler_dict, t_start,
                        success=handler_dict.get("success", True))

    messages.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(handler_dict, ensure_ascii=False, default=str),
    })
    return {"messages": messages, "wi_state": "executing", "_pending_tool_call": None}


def _append_step_result(wi, tool_name, tool_params, handler_dict, t_start, success=True):
    """构建 StepResult 追加到 wi.step_results。参照 workitem_agent.py:629。

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
    """agent_node 之后的条件边路由。

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
