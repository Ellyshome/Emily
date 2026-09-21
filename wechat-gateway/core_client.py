"""wechat-gateway —— Emily Core 的 HTTP/SSE 客户端。

薄网关职责（方案 A MVP，与 AstrBot 薄插件同思路）：
  - /chat 请求组 StandardMessage 后 POST emily-core /api/v1/message/send；
  - 回复不依赖该 POST 的响应体（200/204/超时都可能），统一从
    GET /api/v1/events/outbound SSE 流捞取，按 conversation_id 关联回 /chat。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp

logger = logging.getLogger("wxmp.core_client")

SSEEventHandler = Callable[[str, dict], Awaitable[None]]


def load_env_token() -> str:
    """从仓库根 .env 读 EMILY_API_TOKEN（未配置返回空串）。"""
    try:
        from dotenv import dotenv_values

        root = Path(__file__).resolve().parent.parent
        return dotenv_values(root / ".env").get("EMILY_API_TOKEN", "") or ""
    except Exception:
        return ""


class CoreApiClient:
    """emily-core HTTP + SSE 客户端。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18080",
        api_token: str = "",
        request_timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.request_timeout = request_timeout

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["X-Emily-Token"] = self.api_token
        return headers

    def auth_headers(self) -> dict:
        """Core 鉴权头（供网关自行发起流式请求复用，不下发到端上）。"""
        return self._headers()

    def file_download_url(self, file_no: str) -> str:
        """Core 侧按文件编号下载归档文件的端点地址。"""
        return f"{self.base_url}/api/v1/files/{file_no}/download"

    async def send_message(self, payload: dict) -> tuple[int, dict]:
        """转发入站消息到 core。

        Returns:
            (status, body)：status=200/204 表示 core 已接收；
            status=-1 表示等待超时（core 可能仍在处理，走 SSE）；
            status=0 表示连接失败（core 不可达）。
        """
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
            try:
                async with session.post(
                    f"{self.base_url}/api/v1/message/send", json=payload
                ) as resp:
                    text = await resp.text()
                    body: dict = {}
                    if text:
                        try:
                            body = json.loads(text)
                        except json.JSONDecodeError:
                            logger.warning("core non-json response %s: %.120s", resp.status, text)
                    logger.info("core /message/send -> %s (回复统一走 SSE)", resp.status)
                    return resp.status, body
            except asyncio.TimeoutError:
                logger.info(
                    "core /message/send 等待 %.0fs 超时——core 可能在处理中，等 SSE",
                    self.request_timeout,
                )
                return -1, {}
            except aiohttp.ClientError as e:
                logger.warning("core /message/send 连接失败: %s", e)
                return 0, {}

    async def subscribe_sse(
        self, on_event: SSEEventHandler, reconnect_delay: float = 3.0
    ) -> None:
        """常驻订阅 SSE 出站流，断线自动重连（不退出）。"""
        while True:
            try:
                await self._listen_once(on_event)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("SSE 连接丢失: %s — %.0fs 后重连", e, reconnect_delay)
                await asyncio.sleep(reconnect_delay)

    async def _listen_once(self, on_event: SSEEventHandler) -> None:
        """单次 SSE 连接，逐帧解析并回调。

        注意：SSE 是长连接，不能沿用 aiohttp 默认的 total 超时（300s 会掐断流，
        导致重连空窗内的回复事件丢失）。这里 total=None（不设总超时），仅用
        sock_read 兜底检测对端静默断开（core 每 15s 有心跳，120s 足够保守）。
        """
        timeout = aiohttp.ClientTimeout(total=None, sock_read=120.0)
        async with aiohttp.ClientSession(
            timeout=timeout, headers=self._headers()
        ) as session:
            async with session.get(f"{self.base_url}/api/v1/events/outbound") as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning("SSE http %s: %.200s", resp.status, text[:200])
                    return  # 交给上层重连
                logger.info("SSE connected: %s (status=%d)", self.base_url, resp.status)
                event_type = "message"
                data_buf: list[str] = []
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
                    if line == "":
                        if data_buf:
                            await self._dispatch(event_type, "\n".join(data_buf), on_event)
                        event_type = "message"
                        data_buf = []
                        continue
                    if line.startswith(":"):
                        continue  # 心跳
                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_buf.append(line[len("data:"):].strip())

    @staticmethod
    async def _dispatch(
        event_type: str, data_str: str, on_event: SSEEventHandler
    ) -> None:
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            logger.warning("SSE bad data for %s: %.120s", event_type, data_str)
            return
        try:
            await on_event(event_type, data)
        except Exception as e:
            logger.warning("SSE handler '%s' failed: %s", event_type, e)
