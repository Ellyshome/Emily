"""SSEListener —— 监听 Emily Core 的 SSE 出站事件流（蓝图 §2.5）。

Core 异步生成的所有出站消息（Agent 回复、前导进度、文件发送请求、会话关闭通知）
均通过 SSE 推送给插件，由插件调用 AstrBotOutboundSender 发送到 IM。

事件类型：reply / progress / file_send / session_closed。

注：容器化出站推送需要一个"event 上下文"将回复发回正确的 IM 会话。本监听器
维护 conversation_id → AstrMessageEvent 的最近映射（由 main.on_message 注册），
使异步出站回复能定位到原始 event 进行发送。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .astrbot.outbound_sender import AstrBotOutboundSender
    from .standard.reply import ReplyMessage

logger = logging.getLogger("emily.plugin.sse")


class SSEListener:
    """监听 Core SSE 出站事件流，分发到 AstrBotOutboundSender。"""

    def __init__(self, outbound: "AstrBotOutboundSender", event_registry: dict | None = None):
        """
        Args:
            outbound: AstrBotOutboundSender 实例。
            event_registry: conversation_id → AstrMessageEvent 映射（由 main 维护），
                            异步出站回复据此定位原始 event 发送到 IM。
        """
        self.outbound = outbound
        self._event_registry = event_registry if event_registry is not None else {}
        self._running = False
        self._handlers = {
            "reply": self._handle_reply,
            "progress": self._handle_progress,
            "file_send": self._handle_file_send,
            "session_closed": self._handle_session_closed,
        }

    async def listen(self, sse_url: str, reconnect_delay: float = 3.0) -> None:
        """连接 SSE 端点，持续接收事件（断线自动重连）。"""
        self._running = True
        while self._running:
            try:
                await self._listen_once(sse_url)
            except Exception as e:
                logger.warning("SSE connection lost: %s — reconnecting in %.0fs", e, reconnect_delay)
                await asyncio.sleep(reconnect_delay)

    def stop(self) -> None:
        """停止监听。"""
        self._running = False

    async def _listen_once(self, sse_url: str) -> None:
        """单次 SSE 连接，逐帧解析。"""
        async with aiohttp.ClientSession() as session:
            async with session.get(sse_url) as resp:
                logger.info("SSE connected: %s (status=%d)", sse_url, resp.status)
                event_type = "message"
                data_buf: list[str] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
                    if line == "":
                        # 帧结束 → 分发
                        if data_buf:
                            await self._dispatch(event_type, "\n".join(data_buf))
                        event_type = "message"
                        data_buf = []
                        continue
                    if line.startswith(":"):
                        continue  # 心跳/注释
                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_buf.append(line[len("data:"):].strip())

    async def _dispatch(self, event_type: str, data_str: str) -> None:
        """分发单个 SSE 事件到对应 handler。"""
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            logger.warning("SSE bad data for %s: %s", event_type, data_str[:120])
            return
        handler = self._handlers.get(event_type)
        if handler:
            try:
                await handler(data)
            except Exception as e:
                logger.warning("SSE handler '%s' failed: %s", event_type, e)
        else:
            logger.debug("SSE unhandled event type: %s", event_type)

    # ── 事件 handlers ──

    async def _handle_reply(self, data: dict) -> None:
        """文本回复 → AstrBotOutboundSender.send。"""
        from .standard.reply import ReplyMessage

        conv_id = data.get("conversation_id", "")
        event = self._event_registry.get(conv_id)
        if event is None:
            logger.debug("SSE reply: no event for conv=%s (already replied sync?)", conv_id)
            return
        reply = ReplyMessage(
            conversation_id=conv_id,
            content=data.get("content", ""),
            reply_to_message_id=data.get("reply_to_message_id"),
        )
        await self.outbound.send(reply, event)

    async def _handle_progress(self, data: dict) -> None:
        """前导进度 → send_progress。"""
        conv_id = data.get("conversation_id", "")
        event = self._event_registry.get(conv_id)
        text = data.get("content", "")
        if event is not None and text:
            await self.outbound.send_progress(text, event)

    async def _handle_file_send(self, data: dict) -> None:
        """文件发送请求 → send_files。"""
        conv_id = data.get("conversation_id", "")
        event = self._event_registry.get(conv_id)
        if event is None:
            return
        file_paths = data.get("file_paths", [])
        caption = data.get("caption", "")
        if file_paths:
            await self.outbound.send_files(file_paths=file_paths, event=event, caption=caption)

    async def _handle_session_closed(self, data: dict) -> None:
        """会话关闭通知 → 清理 event 映射。"""
        conv_id = data.get("conversation_id", "")
        self._event_registry.pop(conv_id, None)
        logger.debug("SSE session_closed: conv=%s", conv_id)
