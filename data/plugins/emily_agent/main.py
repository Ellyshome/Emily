"""emily_agent —— Emily 插件入口（薄通信层，蓝图 §2.3）。

容器化架构下，本插件退化为薄通信层，不含任何业务逻辑。职责仅四项：
  1. 消息去重（SHA256 指纹）
  2. AstrMessageEvent → StandardMessage（格式转换）
  3. HTTP 转发到独立 Emily Core 容器（EmilyApiClient）
  4. 接收 Core 的 SSE 出站推送 → AstrBotOutboundSender 发送到 IM（SSEListener）

全部业务逻辑（Session 管理、Agent 推理、Pipeline 执行、数据持久化）跑在
独立的 emily-core 容器中，两者通过内网 HTTP + SSE 解耦通信。
"""

import asyncio
import hashlib
import logging
from collections import deque
from sys import maxsize

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter

from .adapters.astrbot.inbound_adapter import AstrBotInboundAdapter
from .adapters.astrbot.outbound_sender import AstrBotOutboundSender
from .adapters.api_client import EmilyApiClient
from .adapters.sse_listener import SSEListener

logger = logging.getLogger("emily.plugin")

DEDUP_MAX = 200


def _event_fingerprint(event: AstrMessageEvent) -> str:
    raw = f"{event.session_id}|{event.message_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Main(star.Star):
    """Emily 插件入口 —— 薄通信层，不包含业务逻辑。"""

    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context, config=config)
        cfg = dict(config) if config else {}
        base_url = cfg.get("emycore_url", "http://emily-core:18080")
        api_token = cfg.get("emycore_api_token", "")

        self.inbound = AstrBotInboundAdapter()
        self.outbound = AstrBotOutboundSender()
        self.api = EmilyApiClient(base_url=base_url, api_token=api_token)
        # conversation_id → 最近 event，供异步 SSE 出站回复定位
        self._event_registry: dict = {}
        self.sse = SSEListener(self.outbound, event_registry=self._event_registry)
        self._sse_url = cfg.get("emycore_sse_url", "") or self.api.get_sse_url()

        self._seen: deque[str] = deque(maxlen=DEDUP_MAX)
        self._sse_task = None

    async def initialize(self) -> None:
        """启动 SSE 监听 + 健康检查。"""
        # 启动 SSE 监听（接收 Core 推送的出站消息）
        self._sse_task = asyncio.create_task(self.sse.listen(self._sse_url))
        # 健康检查
        health = await self.api.health_check()
        logger.info("Emily Core health: %s", health.get("status", "?"))

    async def terminate(self) -> None:
        """插件卸载：停止 SSE + 关闭 HTTP 连接。"""
        self.sse.stop()
        if self._sse_task is not None:
            self._sse_task.cancel()
        await self.api.close()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize)
    async def on_message(self, event: AstrMessageEvent) -> None:
        # 1. 去重
        event_id = _event_fingerprint(event)
        if event_id in self._seen:
            return
        self._seen.append(event_id)

        # 2. AstrMessageEvent → StandardMessage（纯数据转换）
        msg = self.inbound.to_standard_message(event)

        # 注册 event 供异步 SSE 出站回复定位
        if msg.conversation_id:
            self._event_registry[msg.conversation_id] = event

        # 3. HTTP 转发到 Core
        reply = await self.api.send_message(msg)

        # 4. 同步回复直接发送；异步回复（reply 为 None）将通过 SSE 通道送达
        if reply is not None:
            await self.outbound.send(reply, event)
