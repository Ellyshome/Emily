"""Agent 追踪查询 API —— 全链路回溯。

端点：
    GET /api/v1/trace/{message_id}  — 获取一条消息的完整 Agent 执行追踪
                                       （推理 + LLM 调用 + 工具调用）

参照模式：api/routes/monitor.py（lazy _get_service + set_xxx_service 注入）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("emily.api.trace")

router = APIRouter(prefix="/trace", tags=["trace"])

# 延迟初始化（与 monitor 路由同模式）
_service = None


def set_trace_service(service) -> None:
    """由 EmilyCore 注入 AgentTraceService 实例。"""
    global _service
    _service = service


def _get_service():
    """惰性获取 AgentTraceService。"""
    global _service
    if _service is not None:
        return _service
    try:
        from api.server import get_core
        core = get_core()
        core._ensure_initialized()
        _service = core._agent_trace_service
    except Exception:
        pass
    if _service is None:
        raise HTTPException(status_code=503, detail="Trace module not initialized")
    return _service


@router.get("/{message_id}")
async def get_agent_trace(message_id: str):
    """获取一条消息的完整 Agent 执行追踪。

    Returns:
        AgentTraceService.get_complete_agent_trace 的返回值
        （found=False 或含三层链路的 dict）。
    """
    svc = _get_service()
    # get_complete_agent_trace 内部走 sync Repository → 用 to_thread 包裹
    result = await asyncio.to_thread(svc.get_complete_agent_trace, message_id)
    return result
