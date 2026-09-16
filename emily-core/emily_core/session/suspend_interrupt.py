# emily-core/emily_core/session/suspend_interrupt.py
"""M5 中断挂起模块 —— 挂起与续接改由检查点承载（计划 M5 / PRD US-04、US-07）。

定位与依据：
  - 现状：挂起项存于会话内内存列表（`SessionLoop._register_suspend` → `SuspendRegistry`），
    进程重启即失效（上游需求 D3 已记账的书面缺口）。
  - 本次：挂起用框架原生 `interrupt()` 表达，状态随检查点落库，支持跨进程恢复与续接；
    恢复经 `Command(resume=...)` 回填用户补充信息。
  - 归属：挂起项归属发起者，非发起者的回复不消费该挂起（AC-US-04.2 语义）。
  - 无领域对象：中断负载只含标识与文本摘要，符合状态可序列化约束（D2、US-07）。

边界（M5 不负责）：
  - 不做挂起的业务判定（由能力返回 `needs_input` 决定）；
  - 不改动能力内部如何追问用户（文案仍由能力提供）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.types import Command, interrupt

logger = logging.getLogger("emily.session.suspend_interrupt")

NODE_SUSPEND = "suspend"

DEFAULT_RESUME_HINT = "请补充所需信息后回复，我接着处理。"

#: 非发起者回复挂起项时的回应（不消费挂起）
NOT_INITIATOR_REPLY = "这条待补充的信息是另一位同事发起的，请由他回复，我这边先不处理。"


def build_suspend_node(*, config: Any = None):
    """构建挂起节点：写入中断负载并暂停，等待用户补充信息后续接。"""

    async def _node(state: dict) -> dict:
        call = dict(state.get("_pending_tool_call") or {})
        actor = dict(state.get("actor_ref") or {})
        payload = {
            "question": str(state.get("_suspend_question") or "") or DEFAULT_RESUME_HINT,
            "capability": str(call.get("name") or ""),
            "initiator": str(actor.get("ref_id") or ""),
            "conversation_id": str(state.get("conversation_id") or ""),
            "asked_at_iteration": int(state.get("iteration_count", 0) or 0),
        }
        logger.info(
            "suspend: conversation=%s capability=%s initiator=%s",
            payload["conversation_id"], payload["capability"], payload["initiator"],
        )
        answer = interrupt(payload)          # ← 暂停点：状态随检查点落库
        if answer is None:
            answer = ""
        messages = list(state.get("messages") or [])
        messages.append({
            "role": "user",
            "content": f"[系统] 用户补充信息：{answer}",
        })
        logger.info("suspend resumed: conversation=%s", payload["conversation_id"])
        return {
            "messages": messages,
            "_pending_tool_call": {},
            "_should_suspend": False,
            "_suspend_question": "",
            "reply_text": "",
        }

    _node.__name__ = NODE_SUSPEND
    return _node


async def get_pending(graph, conversation_id: str) -> "dict | None":
    """读取该会话当前的中断负载；无挂起返回 None。"""
    if graph is None:
        return None
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(conversation_id)}})
    except Exception as e:  # noqa: BLE001
        logger.warning("aget_state failed: %s", e)
        return None
    for task in getattr(snapshot, "tasks", ()) or ():
        for item in (getattr(task, "interrupts", ()) or ()):
            value = getattr(item, "value", None)
            if isinstance(value, dict):
                return dict(value)
            return {"question": str(value or "")}
    return None


async def resume_graph(
    graph,
    conversation_id: str,
    answer: str,
    *,
    recursion_limit: int = 50,
    actor_ref: "dict | None" = None,
    context: Any = None,
) -> dict:
    """以用户补充信息续接挂起中的图（`Command(resume=...)`）。

    上下文入口为官方运行时上下文（M4）：`context=` 透传，未提供时不注入（兼容调用方）。
    """
    cfg = {"configurable": {"thread_id": str(conversation_id)},
           "recursion_limit": int(recursion_limit)}
    if context is not None:
        final = await graph.ainvoke(Command(resume=str(answer or "")), cfg, context=context)
    else:
        final = await graph.ainvoke(Command(resume=str(answer or "")), cfg)
    return final if isinstance(final, dict) else {}


def resolve_current_actor(pending: dict, actor_ref: "dict | None") -> bool:
    """归属判定：当前回复者是否为该挂起的发起者。

    无发起者记录时（历史挂起）按放行处理，避免把用户卡死。
    """
    initiator = str((pending or {}).get("initiator") or "")
    current = str((actor_ref or {}).get("ref_id") or "")
    if not initiator or not current:
        return True
    return initiator == current
