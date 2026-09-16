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

#: Core 未推送 Config 默认时的兜底测试前缀（空列表 = 不做前缀判定）
DEFAULT_TEST_PREFIXES = ("test_", "conv_")


def is_test_session(
    conversation_id: str = "",
    config=None,
    prefixes: list | None = None,
) -> bool:
    """判定该会话是否为「测试会话」（其出站只走 SSE、不外发真实 IM）。

    判定只看测试前缀：会话 ID 命中任一前缀 → 测试会话。前缀为空（未配置或控制台清空）
    → 一律不是测试会话，出站按真实投递处理（此时通道账号能对上真实用户就会真发）。

    口径来源：控制台左侧栏「测试前缀」（运行期覆盖）> Config.test_conv_prefixes
    > 模块默认（test_ / conv_）。

    Args:
        conversation_id: 会话 ID。
        config: Config 实例（缺省用已推送的默认口径）。
        prefixes: 显式前缀列表；传入则不读运行期配置（便于单测）。

    Returns:
        bool: True = 测试会话，出站仅 SSE。
    """
    if prefixes is None:
        try:
            from .services.test_settings import resolve_test_prefixes
            prefixes = resolve_test_prefixes(config)
        except Exception as e:  # noqa: BLE001 — 读取失败回退模块默认口径
            logger.debug("resolve test prefixes failed: %s", e)
            prefixes = list(DEFAULT_TEST_PREFIXES)

    cid = (conversation_id or "").strip()
    if not cid:
        return False
    return any(cid.startswith(str(p)) for p in (prefixes or []) if str(p))


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
