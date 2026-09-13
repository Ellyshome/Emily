# emily-core/emily_core/session/graph_events.py
"""M7 统一事件来源 —— 进度与出站同源（计划 M7 / PRD US-05、US-10）。

定位：
  - 现状：进度由会话循环自行 `_publish_progress` 上报，节点内部另有事件总线，
    两处各自构造文案，用户可见信息与实际执行可能不一致。
  - 本次：图式路径的进度事件**由节点迁移派生**（框架的更新流为唯一来源），
    经同一出站通道发布；文案在下一层统一映射，不在节点内散落。

边界：本模块只做"事件派生与发布"，不改变出站协议（仍为 outbound_bus 的 progress/reply）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("emily.session.graph_events")

#: 节点 → 用户可见进度文案（未列出的节点不上报，避免噪声）
NODE_PROGRESS_TEXT = {
    "plan": "正在梳理执行步骤…",
    "execute": "正在调用能力处理…",
    "gate": "正在核对操作权限…",
    "suspend": "需要你补充一点信息…",
}

FIRST_PROGRESS = "收到，正在为你处理，请稍候…"


def build_event_port(*, publish: "Callable[[str, dict], Any] | None", conversation_id: str = ""):
    """构建事件端口：统一 progress/reply 的发布入口（复用既有出站通道）。"""

    def _emit(kind: str, payload: dict) -> None:
        if publish is None:
            return
        try:
            data = dict(payload or {})
            if conversation_id and "conversation_id" not in data:
                data["conversation_id"] = conversation_id
            publish(str(kind), data)
        except Exception as e:  # noqa: BLE001 — 事件失败不影响执行
            logger.debug("emit %s failed: %s", kind, e)

    return _emit


def progress_for(node: str) -> "str | None":
    """节点迁移 → 进度文案。"""
    return NODE_PROGRESS_TEXT.get(str(node or ""))


def iter_progress(stream_item: Any):
    """从框架更新流的一片中取出 (node, 文案)。"""
    if not isinstance(stream_item, dict):
        return []
    out = []
    for node in stream_item.keys():
        text = progress_for(node)
        if text:
            out.append((str(node), text))
    return out
