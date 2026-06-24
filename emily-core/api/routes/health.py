"""GET /api/v1/health —— 健康检查（蓝图 §2.6）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..server import get_core

router = APIRouter()


@router.get("/health")
async def health():
    """返回 Core 健康状态：{status, sessions, uptime, ...}。"""
    core = get_core()
    return core.health()
