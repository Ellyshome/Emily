"""OutboundEventBus —— 出站事件总线（蓝图 §2.6）。

Core 异步生成的所有出站消息（Agent 回复、前导进度、文件发送请求、会话关闭通知）
通过本总线发布；api 层的 SSE 端点订阅后推送给薄插件，由插件发送到 IM。

事件类型（蓝图 §2.5 SSEListener）：reply / progress / file_send / session_closed。

基于 asyncio.Queue 的发布-订阅：每个 SSE 连接 subscribe 得到独立队列，
publish 向所有订阅者广播。无订阅者时事件被丢弃（薄插件未连接 = 无出站通道）。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("emily.outbound_bus")


class OutboundEventBus:
    """出站事件发布-订阅总线。"""

    def __init__(self, max_queue: int = 1000):
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        """订阅出站事件，返回独立队列（每个 SSE 连接一个）。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(queue)
        logger.debug("OutboundBus: subscriber added (total=%d)", len(self._subscribers))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """取消订阅（SSE 连接断开时）。"""
        self._subscribers.discard(queue)
        logger.debug("OutboundBus: subscriber removed (total=%d)", len(self._subscribers))

    def publish(self, event_type: str, data: dict) -> None:
        """向所有订阅者广播一个出站事件。

        Args:
            event_type: "reply" / "progress" / "file_send" / "session_closed"。
            data: 事件负载（JSON 可序列化）。
        """
        event = {"type": event_type, "data": data}
        dropped = 0
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            logger.warning("OutboundBus: %d subscriber queue(s) full, event dropped", dropped)
        if not self._subscribers:
            logger.debug("OutboundBus: no subscribers, event '%s' dropped", event_type)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量。"""
        return len(self._subscribers)
