"""Permission API routes — 权限校验/授权/查询/申请/审批（需求 §14）。

端点：
    POST /check              — 权限校验
    POST /grant              — 授权
    POST /revoke             — 撤销授权
    GET  /user/{userId}      — 查询用户权限
    POST /request            — 申请权限
    POST /approve            — 审批权限申请
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from emily_core.application.permission_app import PermissionApplication

logger = logging.getLogger("emily.api.permission")

router = APIRouter(prefix="/permission", tags=["permission"])

_app: PermissionApplication | None = None


def set_permission_app(app: PermissionApplication) -> None:
    global _app
    _app = app


def _get_app() -> PermissionApplication:
    if _app is None:
        raise HTTPException(status_code=503, detail="Permission module not initialized")
    return _app


# ========================================================================
#  Pydantic models
# ========================================================================

class CheckRequest(BaseModel):
    user_id: str = Field(..., description="用户 ID")
    sop_id: str = Field(..., description="SOP 编号")


class GrantRequest(BaseModel):
    grantee_id: str = Field(..., description="被授权人 ID")
    grantor_id: str = Field(..., description="授权人 ID")
    perm_code: str = Field(..., description="权限编码")
    grant_type: str = Field(default="TEMP", description="授权类型: AUTO/TEMP/PERMANENT")
    operations: str = Field(default='["read"]', description="操作 JSON")
    expire_time: str | None = Field(default=None, description="过期时间（TEMP 必填）")
    remark: str = Field(default="", description="授权原因（PERMANENT 必填）")
    client_ip: str = Field(default="", description="授权人 IP")


class RevokeRequest(BaseModel):
    grant_no: str = Field(..., description="授权编号")
    revoke_reason: str = Field(default="", description="撤销原因")
    operator_id: str = Field(default="", description="操作人 ID")


class PermissionRequest(BaseModel):
    requester_id: str = Field(..., description="申请人 ID")
    perm_code: str = Field(..., description="申请的权限编码")
    request_type: str = Field(default="TEMP_GRANT", description="申请类型: TEMP_GRANT/UNIT_BIND/LEVEL_UP/ANOMALY_DATA")
    reason: str = Field(default="", description="申请理由")
    priority: str = Field(default="NORMAL", description="优先级: NORMAL/HIGH/URGENT")


class ApproveRequest(BaseModel):
    request_no: str = Field(..., description="申请编号")
    approver_id: str = Field(..., description="审批人 ID")
    approved: bool = Field(..., description="是否通过")
    remark: str = Field(default="", description="审批意见")


# ========================================================================
#  Endpoints
# ========================================================================

@router.post("/check")
async def check_permission(body: CheckRequest):
    """权限校验（需求 §14.1）。

    返回 {allowed, reason, suggestedApprover}。
    """
    app = _get_app()
    result = await app.check_permission(body.user_id, body.sop_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result


@router.post("/grant")
async def grant_permission(body: GrantRequest):
    """授权（AUTO/TEMP/PERMANENT，需求 §5）。"""
    app = _get_app()
    result = await app.grant_permission(
        grantee_id=body.grantee_id,
        grantor_id=body.grantor_id,
        perm_code=body.perm_code,
        grant_type=body.grant_type,
        operations=body.operations,
        expire_time=body.expire_time,
        remark=body.remark,
        client_ip=body.client_ip,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result


@router.post("/revoke")
async def revoke_permission(body: RevokeRequest):
    """撤销授权（需求 §5.2）。"""
    app = _get_app()
    result = await app.revoke_permission(
        grant_no=body.grant_no,
        revoke_reason=body.revoke_reason,
        operator_id=body.operator_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result


@router.get("/user/{user_id}")
async def query_user_permissions(user_id: str):
    """查询用户权限列表（需求 §14 GET）。"""
    app = _get_app()
    result = await app.query_user_permissions(user_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("reply", ""))
    return result


@router.post("/request")
async def request_permission(body: PermissionRequest):
    """申请权限（创建 permission_request，需求 §9）。"""
    app = _get_app()
    result = await app.request_permission(
        requester_id=body.requester_id,
        perm_code=body.perm_code,
        request_type=body.request_type,
        reason=body.reason,
        priority=body.priority,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result


@router.post("/approve")
async def approve_request(body: ApproveRequest):
    """审批权限申请（需求 §9）。"""
    app = _get_app()
    result = await app.approve_request(
        request_no=body.request_no,
        approver_id=body.approver_id,
        approved=body.approved,
        remark=body.remark,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result
