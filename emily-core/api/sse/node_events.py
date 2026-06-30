"""SSE 节点事件推送端点 —— 需求文档 §8.5。

GET /api/v1/events/nodes?project_id={project_id}

SSE Event 消息格式：
event: node_event
data: {event_id, node_id, event_type, old_value, new_value, operator_id, created_at}

参照模式：api/sse/outbound.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("emily.sse.node_events")

router = APIRouter(prefix="/events", tags=["sse"])

# 延迟初始化
_bus = None


def set_node_event_bus(bus) -> None:
    """由 EmilyCore 注入 NodeEventBus。"""
    global _bus
    _bus = bus


async def _event_generator(request: Request, project_id: str = ""):
    """SSE 事件生成器。"""
    sub_id = str(uuid.uuid4())

    if _bus is None:
        # 总线未初始化，发送错误后退出
        yield f"event: error\ndata: {json.dumps({'message': 'NodeEventBus not initialized'})}\n\n"
        return

    from emily_core.node_event_bus import SubscriptionFilter

    filter_ = SubscriptionFilter(project_id=project_id) if project_id else None
    queue: asyncio.Queue = _bus.subscribe(sub_id, filter_)

    try:
        # 发送初始连接确认
        yield f"event: connected\ndata: {json.dumps({'sub_id': sub_id, 'project_id': project_id})}\n\n"

        while True:
            # 检查客户端是否断开
            if await request.is_disconnected():
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                yield f": heartbeat\n\n"
                continue

            yield (
                f"event: node_event\n"
                f"data: {json.dumps(event.to_outbound(), ensure_ascii=False)}\n\n"
            )
    finally:
        _bus.unsubscribe(sub_id)
        logger.debug("SSE node_events connection closed: %s", sub_id)


@router.get("/nodes")
async def node_events_stream(
    request: Request,
    project_id: str = Query(default="", description="按项目过滤（为空则接收所有项目事件）"),
):
    """SSE 节点事件流 —— 需求文档 §8.5。"""
    return StreamingResponse(
        _event_generator(request, project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
