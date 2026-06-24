"""GET /api/v1/events/outbound —— SSE 出站事件流（蓝图 §2.6）。

Core 异步生成的所有出站消息（reply / progress / file_send / session_closed）
通过 EmilyCore.outbound_bus 发布；本端点订阅后以 SSE 格式推送给薄插件，
由插件的 SSEListener 接收 → AstrBotOutboundSender 发送到 IM。

SSE 帧格式（蓝图 §2.5）::

    event: reply
    data: {"conversation_id": "...", "content": "..."}

"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..server import get_core

logger = logging.getLogger("emily.api.sse")

router = APIRouter()


@router.get("/events/outbound")
async def outbound_events(request: Request):
    """SSE 端点：推送出站消息到薄插件。"""
    core = get_core()
    bus = core.outbound_bus
    queue = bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # 心跳保活（SSE 注释行）
                    yield ": keep-alive\n\n"
                    continue
                etype = event.get("type", "message")
                data = json.dumps(event.get("data", {}), ensure_ascii=False)
                yield f"event: {etype}\ndata: {data}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
