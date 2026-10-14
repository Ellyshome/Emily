"""平台动作回调端点 —— 薄插件执行完 OneBot 动作后把结果送回内核。

与 `groups.py` / `message.py` 同属"插件 ↔ 内核"的内部端点：响应为裸 dict，
不套 `_ok/_err` 控制台封装；受 AuthMiddleware 的 X-Emily-Token 约束（插件已携带）。

配对的请求方向见 `emily_core/services/platform_action_service.py`
（内核经 SSE 出站总线下发 `platform_action` 事件）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("emily.api.platform")

router = APIRouter()


class PlatformActionResultIn(BaseModel):
    """插件回调负载：一次平台动作的执行结果。"""

    request_id: str
    ok: bool = False
    data: object = None
    error: str = ""


@router.post("/platform/action-result")
async def platform_action_result(payload: PlatformActionResultIn):
    """接收插件回传的平台动作结果，兑现内核侧在途请求。"""
    from api.server import get_core

    core = get_core()
    svc = getattr(core, "_platform_action_service", None)
    if svc is None:
        return {"accepted": False, "reason": "平台动作服务未启用"}

    accepted = svc.resolve(payload.request_id, payload.ok, payload.data, payload.error)
    if not accepted:
        # 未知 request_id 属正常竞争（内核已超时放弃）或伪造；如实回报但不视为错误
        logger.warning("platform action result rejected: request_id=%s", payload.request_id)
    return {"accepted": bool(accepted)}
