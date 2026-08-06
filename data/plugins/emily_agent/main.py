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
from astrbot.api.event import AstrMessageEvent, filter, MessageChain

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
        base_url = cfg.get("emily_core_url", cfg.get("emycore_url", "http://emily-core:18080"))
        api_token = cfg.get("emily_api_token", cfg.get("emycore_api_token", ""))

        self.inbound = AstrBotInboundAdapter()
        self.outbound = AstrBotOutboundSender()
        self.outbound.context = context  # 注入 context 供企微路径回退使用
        AstrBotOutboundSender._context = context  # 静态方法回退路径
        self.api = EmilyApiClient(base_url=base_url, api_token=api_token)
        # conversation_id → 最近 event，供异步 SSE 出站回复定位
        self._event_registry: dict = {}
        self.sse = SSEListener(self.outbound, event_registry=self._event_registry)
        self._sse_url = cfg.get("emycore_sse_url", "") or self.api.get_sse_url()

        self._seen: deque[str] = deque(maxlen=DEDUP_MAX)
        self._sse_task = None

    async def initialize(self) -> None:
        """启动 SSE 监听 + 健康检查 + 群列表同步。"""
        # 启动 SSE 监听（接收 Core 推送的出站消息）
        self._sse_task = asyncio.create_task(self.sse.listen(self._sse_url))
        # 健康检查
        health = await self.api.health_check()
        logger.info("Emily Core health: %s", health.get("status", "?"))
        # 同步群列表到 Core
        try:
            await self._sync_group_list()
        except Exception as e:
            logger.warning("group list sync failed (non-blocking): %s", e)

    async def _sync_group_list(self) -> None:
        """从 astrbot 获取 bot 加入的所有群，推给 core。"""
        groups = []
        platform_manager = getattr(self.context, "platform_manager", None)
        if platform_manager is None:
            return
        platform_insts = getattr(platform_manager, "platform_insts", None) or []
        for platform in platform_insts:
            bot = getattr(platform, "bot", None)
            if bot is None:
                continue
            try:
                group_list = await bot.call_action("get_group_list")
                for g in (group_list or []):
                    groups.append({
                        "group_id": str(g.get("group_id", "")),
                        "group_name": g.get("group_name", ""),
                        "member_count": g.get("member_count", 0),
                        "platform": getattr(platform, "platform_name", "unknown"),
                    })
            except Exception as e:
                logger.warning("get_group_list failed on platform %s: %s", platform, e)
        if groups:
            await self.api.sync_groups(groups)
            logger.info("synced %d groups to core", len(groups))

    async def terminate(self) -> None:
        """插件卸载：停止 SSE + 关闭 HTTP 连接。"""
        self.sse.stop()
        if self._sse_task is not None:
            self._sse_task.cancel()
        await self.api.close()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=maxsize)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """QQ 消息入口（跳过企微事件，交由 on_wecom_message 处理）。"""
        # 企微消息由专用处理器处理
        if getattr(event, "get_platform_name", None):
            platform = event.get_platform_name()
            if platform == "wecom":
                return

        # 1. 去重
        event_id = _event_fingerprint(event)
        if event_id in self._seen:
            return
        self._seen.append(event_id)

        # 2. AstrMessageEvent → StandardMessage（纯数据转换）
        msg = await self.inbound.to_standard_message(event)

        # 注册 event 供异步 SSE 出站回复定位
        if msg.conversation_id:
            self._event_registry[msg.conversation_id] = event

        # 3. HTTP 转发到 Core
        reply = await self.api.send_message(msg)

        # 4. 同步回复直接发送；异步回复（reply 为 None）将通过 SSE 通道送达
        if reply is not None:
            await self.outbound.send(reply, event)

    # ── 企业微信消息入口 ────────────────────────────────────────────

    @filter.platform_adapter_type(filter.PlatformAdapterType.WECOM)
    async def on_wecom_message(self, event: AstrMessageEvent) -> None:
        """企业微信消息入口。

        流程同 QQ 处理器，区别：
          - 入站适配用 convert_wecom()（静态方法，企微专用字段提取）
          - 出站可能有 event 也可能没有（企微 AstrBot 适配器差异）
          - 阻止 AstrBot 默认 LLM 处理链
        """
        event.call_llm = True

        # 1. 转换为 StandardMessage
        msg = self.inbound.convert_wecom(event)
        logger.debug(
            "收到企微消息: sender=%s conv=%s content=%.80s",
            msg.sender_id, msg.conversation_id, msg.content,
        )

        # 2. 注册 event 供异步 SSE 出站回复定位
        if msg.conversation_id:
            self._event_registry[msg.conversation_id] = event

        # 3. HTTP 转发到 Core
        reply = await self.api.send_message(msg)

        # 4. 同步回复直接发送；异步回复（reply 为 None）将通过 SSE 通道送达
        if reply is not None:
            await self.outbound.send(reply, event)
