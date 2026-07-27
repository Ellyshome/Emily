"""NodeEventBus —— 全景节点图事件总线（需求文档 §7）。

架构：
  NodeEventBus（节点内部总线，细粒度过滤）
      ↓ 事件封装
  OutboundEventBus（全局出站总线，复用现有）
      ↓ 多通道推送
  SSE前端 / IM群 / Webhook第三方

基于 asyncio.Queue 发布-订阅。支持按节点ID/事件类型/项目/阶段过滤订阅。

参照模式：outbound_bus.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger("emily.node_event_bus")


# ══════════════════════════════════════════════════════════════════════════════
# 事件数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NodeEvent:
    """节点事件。"""
    event_id: str
    node_id: str
    event_type: str
    project_id: str = ""
    old_value: str = ""
    new_value: str = ""
    operator_id: str = ""
    remark: str = ""
    created_at: str = ""

    def to_outbound(self) -> dict:
        """转换为 OutboundEventBus 兼容格式。"""
        return {
            "event_id": self.event_id,
            "node_id": self.node_id,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "operator_id": self.operator_id,
            "created_at": self.created_at,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 订阅过滤器
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SubscriptionFilter:
    """细粒度订阅过滤（所有字段为可选，NULL=不过滤）。"""
    node_id: str | None = None          # 特定节点（含子节点事件）
    event_types: list[str] | None = None  # 只订阅这些事件类型
    project_id: str | None = None       # 只订阅某项目
    owner_dept_id: str | None = None    # 只订阅某主责条线


# ══════════════════════════════════════════════════════════════════════════════
# NodeEventBus
# ══════════════════════════════════════════════════════════════════════════════

class NodeEventBus:
    """节点事件发布-订阅总线。

    支持两种订阅模式：
    1. 无过滤订阅：收到所有节点事件（用于 SSE 全量推送）
    2. 带过滤订阅：仅收到匹配条件的事件（用于前端按需订阅）
    """

    def __init__(self, max_queue: int = 1000):
        self._subscribers: dict[str, tuple[asyncio.Queue, SubscriptionFilter | None]] = {}
        self._max_queue = max_queue
        self._outbound_bus = None  # 由 EmilyCore 注入

    def set_outbound_bus(self, bus) -> None:
        """注入全局出站总线（用于多通道推送）。"""
        self._outbound_bus = bus

    def subscribe(self, sub_id: str, filter_: SubscriptionFilter | None = None) -> asyncio.Queue:
        """订阅节点事件，返回独立队列。

        Args:
            sub_id: 订阅者唯一ID
            filter_: 可选过滤条件

        Returns:
            独立 asyncio.Queue
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers[sub_id] = (queue, filter_)
        logger.debug("NodeEventBus: subscriber '%s' added (total=%d)", sub_id, len(self._subscribers))
        return queue

    def unsubscribe(self, sub_id: str) -> None:
        """取消订阅。"""
        self._subscribers.pop(sub_id, None)
        logger.debug("NodeEventBus: subscriber '%s' removed (total=%d)", len(self._subscribers))

    async def publish(self, event: NodeEvent) -> None:
        """发布节点事件到所有匹配的订阅者。

        同时：
        - 发布到 OutboundEventBus（如果已注入）用于 SSE/IM 推送
        - 过滤不匹配的订阅者
        """
        dropped = 0
        for sub_id, (q, filter_) in list(self._subscribers.items()):
            if filter_ and not self._matches_filter(event, filter_):
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1
                logger.warning("NodeEventBus: subscriber '%s' queue full, event dropped", sub_id)

        if dropped:
            logger.warning("NodeEventBus: %d subscriber(s) dropped event", dropped)

        # 对接 OutboundEventBus
        if self._outbound_bus is not None:
            self._outbound_bus.publish("node_event", event.to_outbound())

    @staticmethod
    def _matches_filter(event: NodeEvent, filter_: SubscriptionFilter) -> bool:
        """检查事件是否匹配订阅过滤条件（AND 逻辑）。"""
        if filter_.node_id is not None and event.node_id != filter_.node_id:
            return False
        if filter_.event_types is not None and event.event_type not in filter_.event_types:
            return False
        if filter_.project_id is not None and event.project_id != filter_.project_id:
            return False
        return True

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
