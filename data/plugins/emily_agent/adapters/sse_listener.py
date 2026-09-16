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
import time
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .astrbot.outbound_sender import AstrBotOutboundSender
    from .standard.reply import ReplyMessage

logger = logging.getLogger("emily.plugin.sse")

#: conversation_id → IM event 映射的默认有效期（秒）。
#: 出站回复只在「刚收到过该会话入站消息」的窗口内才有投递意义；过期残留的 event
#: 会把后来的出站事件（如测试注入）误发到真实用户会话，故默认 5 分钟后判废。
DEFAULT_REGISTRY_TTL_SECONDS = 300.0


class SSEListener:
    """监听 Core SSE 出站事件流，分发到 AstrBotOutboundSender。"""

    def __init__(
        self,
        outbound: "AstrBotOutboundSender",
        event_registry: dict | None = None,
        api_token: str = "",
        registry_ttl_seconds: float = DEFAULT_REGISTRY_TTL_SECONDS,
    ):
        """
        Args:
            outbound: AstrBotOutboundSender 实例。
            event_registry: conversation_id → IM event 映射（由 main 维护），
                            异步出站回复据此定位原始 event 发送到 IM。
            api_token: 与 Core 的 EMILY_API_TOKEN 一致，经 X-Emily-Token 头随
                       SSE 请求发送（Core 设了 token 时为空会 401）。
            registry_ttl_seconds: 映射有效期（秒），超期条目视为残留并丢弃出站；
                        <=0 表示不判废（仅调试用）。
        """
        self.outbound = outbound
        self._event_registry = event_registry if event_registry is not None else {}
        self._api_token = api_token
        self._registry_ttl = float(registry_ttl_seconds or 0)
        self._running = False
        self._handlers = {
            "reply": self._handle_reply,
            "progress": self._handle_progress,
            "file_send": self._handle_file_send,
            "session_closed": self._handle_session_closed,
        }

    # ── 事件注册 / 判定 ──

    def register(self, conversation_id: str, event) -> None:
        """登记某会话最近一次入站 IM event（供异步出站回复定位）。"""
        if not conversation_id or event is None:
            return
        self._event_registry[conversation_id] = {"event": event, "ts": time.monotonic()}

    def _lookup_event(self, data: dict):
        """取未过期的入站 event；缺失或已过期返回 None。"""
        cid = data.get("conversation_id", "")
        entry = self._event_registry.get(cid)
        if entry is None:
            return None
        # 兼容直接存 event 的旧形态
        if not isinstance(entry, dict) or "event" not in entry:
            return entry
        age = time.monotonic() - float(entry.get("ts", 0) or 0)
        if self._registry_ttl > 0 and age > self._registry_ttl:
            self._event_registry.pop(cid, None)
            logger.warning(
                "SSE: IM event for conv=%s expired (%.0fs > %.0fs) — outbound dropped, not sent to IM",
                cid, age, self._registry_ttl,
            )
            return None
        return entry.get("event")

    def _is_test_session(self, data: dict) -> bool:
        """是否「测试会话」的出站（只走 SSE、不外发真实 IM）。

        由 Core 单点判定（会话 ID 命中测试前缀 → test_session=true）并随事件下发，
        插件不自行推断口径，避免两处规则不一致。字段缺失（旧版 Core）按「非测试会话」处理。
        """
        return data.get("test_session") is True

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
        """单次 SSE 连接，逐帧解析。

        注意：SSE 是长连接，不能沿用 aiohttp 默认的 total 超时（300s 会掐断流，
        导致重连空窗内的回复事件丢失）。这里 total=None（不设总超时），仅用
        sock_read 兜底检测对端静默断开（core 每 15s 有心跳，120s 足够保守）；
        sock_connect 必须设置：core 重启瞬间的重连可能卡在 TCP 建连上，
        没有 connect 超时会让重连循环永久挂起、出站彻底失联。
        """
        timeout = aiohttp.ClientTimeout(total=None, sock_read=120.0, sock_connect=10.0)
        headers = {}
        if self._api_token:
            headers["X-Emily-Token"] = self._api_token
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(sse_url) as resp:
                if resp.status != 200:
                    # 非 200（如 token 不匹配的 401）不是长连接：async for 会立即
                    # 结束并正常返回，上层不会退避 → 空转死循环打爆 core。抛异常
                    # 交给 listen() 的 except 分支做退避重连。
                    text = await resp.text()
                    raise RuntimeError(f"SSE http {resp.status}: {text[:200]}")
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
        if self._is_test_session(data):
            logger.warning(
                "SSE reply: conv=%s test_session — SSE only, not sent to IM", conv_id)
            return
        event = self._lookup_event(data)
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
        if self._is_test_session(data):
            logger.warning("SSE progress: conv=%s test_session — SSE only, not sent to IM", conv_id)
            return
        event = self._lookup_event(data)
        text = data.get("content", "")
        if event is not None and text:
            await self.outbound.send_progress(text, event)

    async def _handle_file_send(self, data: dict) -> None:
        """文件发送请求 → send_files。"""
        conv_id = data.get("conversation_id", "")
        if self._is_test_session(data):
            logger.warning("SSE file_send: conv=%s test_session — SSE only, not sent to IM", conv_id)
            return
        event = self._lookup_event(data)
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
