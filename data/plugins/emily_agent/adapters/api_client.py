"""EmilyApiClient —— 插件到 Emily Core 的 HTTP 客户端（蓝图 §2.4）。

在 AstrBot 插件（薄通信层）内运行，负责将所有业务请求转发给独立 Core 容器。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from .standard.message import StandardMessage
    from .standard.reply import ReplyMessage

logger = logging.getLogger("emily.plugin.api_client")


class EmilyApiClient:
    """Emily Core 的 HTTP API 客户端。"""

    def __init__(
        self,
        base_url: str = "http://emily-core:18080",
        api_token: str = "",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_token:
                headers["X-Emily-Token"] = self.api_token
            self._session = aiohttp.ClientSession(timeout=self.timeout, headers=headers)
        return self._session

    async def close(self) -> None:
        """关闭底层连接。"""
        if self._session and not self._session.closed:
            await self._session.close()

    def get_sse_url(self) -> str:
        """出站 SSE 事件流 URL。"""
        return f"{self.base_url}/api/v1/events/outbound"

    async def send_message(self, msg: "StandardMessage") -> "ReplyMessage | None":
        """发送入站消息到 Core，同步等待回复（短路回复场景）。

        POST /api/v1/message/send
        Response: ReplyMessage (JSON) 或 204 No Content（异步处理中，走 SSE）。
        """
        from .standard.reply import ReplyMessage

        session = await self._ensure_session()
        url = f"{self.base_url}/api/v1/message/send"
        payload = self._message_to_dict(msg)
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 204:
                    return None
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("send_message %d: %s", resp.status, text[:200])
                    return None
                data = await resp.json()
                return ReplyMessage(
                    conversation_id=data.get("conversation_id", ""),
                    content=data.get("content", ""),
                    reply_to_message_id=data.get("reply_to_message_id"),
                )
        except Exception as e:
            logger.error("send_message failed: %s", e)
            return None

    async def terminate_session(self, conversation_id: str) -> bool:
        """强制终止指定 Session。

        POST /api/v1/session/terminate
        """
        session = await self._ensure_session()
        url = f"{self.base_url}/api/v1/session/terminate"
        try:
            async with session.post(url, json={"conversation_id": conversation_id}) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                return bool(data.get("terminated"))
        except Exception as e:
            logger.error("terminate_session failed: %s", e)
            return False

    async def health_check(self) -> dict:
        """检查 Core 健康状态。

        GET /api/v1/health
        """
        session = await self._ensure_session()
        url = f"{self.base_url}/api/v1/health"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"status": "error", "code": resp.status}
        except Exception as e:
            logger.error("health_check failed: %s", e)
            return {"status": "unreachable", "error": str(e)}

    @staticmethod
    def _message_to_dict(msg: "StandardMessage") -> dict:
        """StandardMessage → JSON 请求体（与 Core 端 MessageIn 对齐）。"""
        return {
            "message_id": msg.message_id,
            "platform": msg.platform,
            "conversation_type": msg.conversation_type,
            "conversation_id": msg.conversation_id,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "group_id": msg.group_id,
            "group_name": msg.group_name,
            "content": msg.content,
            "is_at_bot": msg.is_at_bot,
            "mentioned_user_ids": list(msg.mentioned_user_ids or []),
            "reply_to_message_id": msg.reply_to_message_id,
            "msg_type": getattr(msg, "msg_type", 1),
            "attachments": list(getattr(msg, "attachments", []) or []),
        }
