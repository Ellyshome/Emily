"""SessionPoolManager —— Session 池（蓝图 §3.4）。

Adapter 层的 Session 池维护。以 conversation_id 为 key 维护活跃 Session 的哈希表。

核心职责（蓝图 §3.4）：
  1. Session 池维护 —— conversation_id → SessionAgent
  2. 消息路由 —— lookup 命中则路由，未命中则 SessionFactory.create
  3. Session TTL 管理 —— 默认 10 分钟无新消息，后台扫描过期
  4. 并发控制 —— 同 conversation_id 串行（Session 内锁），不同 Session 并行

消息路由流程（蓝图 §3.4）：
    InboundMessage
        → SessionPool.lookup(conversation_id)
            ├── 命中 → 路由至 Session.handle_message()
            └── 未命中 → SessionFactory.create() → add → handle_message()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .session_config import SessionConfig
from .session_factory import SessionFactory

if TYPE_CHECKING:
    from ...adapters.standard.message import StandardMessage
    from ...adapters.standard.reply import ReplyMessage
    from ...session.session_agent import SessionAgent
    from ...workitem.pipeline.bus import PipelineBUS

logger = logging.getLogger("emily.session_pool")


class _Entry:
    """池内条目：SessionAgent + 最后活跃时间 + 串行锁。"""

    __slots__ = ("agent", "last_active", "lock")

    def __init__(self, agent):
        self.agent = agent
        self.last_active = time.time()
        self.lock = asyncio.Lock()


class SessionPoolManager:
    """Session 池管理器。"""

    def __init__(
        self,
        bus: "PipelineBUS",
        config: SessionConfig | None = None,
        factory: SessionFactory | None = None,
        core=None,
    ):
        self._config = config or SessionConfig()
        self._factory = factory or SessionFactory(bus, core=core)
        self._sessions: dict[str, _Entry] = {}
        self._start_time = time.time()

    # ── 路由入口 ──

    async def route(self, message: "StandardMessage", user_id: str = "") -> "ReplyMessage | None":
        """路由一条入站消息到对应 Session（命中复用，未命中创建）。

        同一 conversation_id 的消息串行处理（Session 内锁）；
        不同 Session 完全并行。

        Args:
            message: 标准化入站消息。
            user_id: 已绑定的用户 UUID（Adapter 层完成绑定后传入）。

        Returns:
            ReplyMessage | None: Session 处理后的回复。
        """
        conv_id = message.conversation_id
        entry = self._sessions.get(conv_id)

        if entry is None:
            # 未命中 → 创建（并发控制：超出上限先清理过期）
            if len(self._sessions) >= self._config.max_concurrent:
                self.sweep_expired()
            agent = self._factory.create(message, user_id=user_id)
            entry = _Entry(agent)
            self._sessions[conv_id] = entry
            logger.info("SessionPool created: conv=%s (pool size=%d)",
                        conv_id, len(self._sessions))
        else:
            logger.debug("SessionPool hit: conv=%s", conv_id)

        # Session 内串行
        async with entry.lock:
            entry.last_active = time.time()
            return await entry.agent.handle(message)

    # ── 池维护 ──

    def lookup(self, conversation_id: str) -> "SessionAgent | None":
        """查找活跃 Session（不创建）。"""
        entry = self._sessions.get(conversation_id)
        return entry.agent if entry else None

    async def terminate(self, conversation_id: str) -> bool:
        """强制终止指定 Session（蓝图 §2.6 /session/terminate）。

        执行注销归档后从池中移除。
        """
        entry = self._sessions.get(conversation_id)
        if entry is None:
            return False
        try:
            await entry.agent.archive()
        except Exception as e:
            logger.warning("SessionPool terminate archive failed: %s", e)
        self._sessions.pop(conversation_id, None)
        logger.info("SessionPool terminated: conv=%s", conversation_id)
        return True

    def sweep_expired(self) -> int:
        """扫描并移除过期 Session（TTL 到期，蓝图 §3.4）。

        Returns:
            int: 被移除的 Session 数量。
        """
        now = time.time()
        ttl = self._config.ttl_seconds
        expired = [
            cid for cid, e in self._sessions.items()
            if now - e.last_active > ttl
        ]
        for cid in expired:
            self._sessions.pop(cid, None)
        if expired:
            logger.info("SessionPool swept %d expired session(s)", len(expired))
        return len(expired)

    # ── 状态 ──

    @property
    def size(self) -> int:
        """当前活跃 Session 数。"""
        return len(self._sessions)

    @property
    def uptime_seconds(self) -> int:
        """池运行时长（秒）。"""
        return int(time.time() - self._start_time)
