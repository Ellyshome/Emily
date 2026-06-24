"""POST /api/v1/session/terminate —— 强制终止 Session（蓝图 §2.6）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ..server import get_core

logger = logging.getLogger("emily.api.session")

router = APIRouter()


class TerminateIn(BaseModel):
    """终止请求体。"""
    conversation_id: str


@router.post("/session/terminate")
async def terminate_session(body: TerminateIn):
    """强制终止指定 Session（执行注销归档后从池中移除）。"""
    core = get_core()
    ok = await core.terminate_session(body.conversation_id)
    return {"terminated": ok, "conversation_id": body.conversation_id}
