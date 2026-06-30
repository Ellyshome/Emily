"""PermissionApplication — 权限管理编排层（阶段二）。

遵循 plan_task_app.py 模式：
  - 接收 PermissionService 实例
  - 编排服务调用并格式化面向用户的文本回复
  - 返回 dict with {"success", "reply", ...} keys
  - try/except 永不抛
"""
from __future__ import annotations

import logging
from typing import Optional

from emily_core.services.permission_service import PermissionService

logger = logging.getLogger("emily.permission.app")


class PermissionApplication:
    """权限管理 Application 编排层。"""

    def __init__(self, service: PermissionService):
        self._service = service

    # ========================================================================
    #  权限校验（需求 §14.1）
    # ========================================================================

    async def check_permission(self, user_id: str, sop_id: str) -> dict:
        """检查用户是否有权访问指定 SOP。

        Returns:
            {"success": True, "allowed": bool, "reason": str, "suggested_approver": str}
        """
        try:
            result = await self._service.check(user_id, sop_id)
            result["success"] = True
            return result
        except Exception as e:
            logger.error("check_permission failed: %s", e)
            return {"success": False, "allowed": False, "reason": str(e), "reply": f"权限校验失败：{e}"}

    # ========================================================================
    #  授权管理（需求 §5）
    # ========================================================================

    async def grant_permission(self, *, grantee_id: str, grantor_id: str,
                               perm_code: str, grant_type: str = "TEMP",
                               operations: str = '["read"]',
                               expire_time: Optional[str] = None,
                               remark: str = "", client_ip: str = "") -> dict:
        """授权操作。

        Returns:
            {"success": bool, "grant_no": str, "reply": str}
        """
        try:
            return await self._service.grant(
                grantee_id=grantee_id,
                grantor_id=grantor_id,
                perm_code=perm_code,
                grant_type=grant_type,
                operations=operations,
                expire_time=expire_time,
                remark=remark,
                client_ip=client_ip,
            )
        except Exception as e:
            logger.error("grant_permission failed: %s", e)
            return {"success": False, "reply": f"授权操作失败：{e}"}

    async def revoke_permission(self, *, grant_no: str, revoke_reason: str = "",
                                operator_id: str = "") -> dict:
        """撤销授权。

        Returns:
            {"success": bool, "reply": str}
        """
        try:
            return await self._service.revoke(
                grant_no=grant_no,
                revoke_reason=revoke_reason,
                operator_id=operator_id,
            )
        except Exception as e:
            logger.error("revoke_permission failed: %s", e)
            return {"success": False, "reply": f"撤销操作失败：{e}"}

    # ========================================================================
    #  权限查询（需求 §14 GET）
    # ========================================================================

    async def query_user_permissions(self, user_id: str) -> dict:
        """查询用户权限清单。

        Returns:
            {"success": bool, "permissions": dict, "reply": str}
        """
        try:
            result = await self._service.query_user_permissions(user_id)
            if result.get("success"):
                perms = result["permissions"]
                # 格式化文本回复
                level_name = perms.get("level_name", "?")
                sop_list = perms.get("sop_allow", [])
                grants = perms.get("active_grants", [])

                reply_parts = [
                    f"📋 权限清单",
                    f"  权限层级: L{perms.get('permission_level', 1)} {level_name}",
                    f"  所属单位: {perms.get('company_name', '-')}",
                    f"  企业类型: {perms.get('company_type', '-')}",
                    f"  部门: {perms.get('department', '-')}",
                    f"  信息密级: {perms.get('info_level', '-')}",
                    f"  可用SOP ({len(sop_list)}): {', '.join(sop_list[:10])}{'...' if len(sop_list) > 10 else ''}",
                    f"  活跃授权 ({len(grants)}): " + (
                        ', '.join(g['grant_no'] for g in grants[:5])
                        if grants else '无'
                    ),
                ]
                result["reply"] = '\n'.join(reply_parts)
            return result
        except Exception as e:
            logger.error("query_user_permissions failed: %s", e)
            return {"success": False, "reply": f"查询权限失败：{e}"}

    # ========================================================================
    #  权限申请（需求 §9，轻量审批流）
    # ========================================================================

    async def request_permission(self, *, requester_id: str, perm_code: str,
                                 request_type: str = "TEMP_GRANT",
                                 reason: str = "", priority: str = "NORMAL") -> dict:
        """申请权限（创建 permission_request，阶段三完善审批流）。

        阶段二提供基础能力：创建申请记录 + 返回审批人建议。
        完整审批工作流（超时升级/转审）在阶段三实现。
        """
        import asyncio
        from emily_core.infrastructure.database.models import _utc_now
        from emily_core.repositories.permission_repo import PermissionRepository

        try:
            # 获取申请人信息
            repo = PermissionRepository()
            user = await asyncio.to_thread(repo.get_user, requester_id)
            if user is None:
                return {"success": False, "reply": f"用户 {requester_id} 不存在"}

            supervisor_id = user.supervisor_id or ""

            # 生成申请编号
            from datetime import datetime
            from emily_core.infrastructure.database.models import BEIJING_TZ
            today = datetime.now(BEIJING_TZ).strftime("%Y%m%d")

            # 写入 permission_requests 表
            from emily_core.infrastructure.database.session import get_session
            from emily_core.infrastructure.database.models import PermissionRequest

            def _create_request():
                with get_session() as session:
                    prefix = f"PRQ-{today}-"
                    count = session.query(PermissionRequest).filter(
                        PermissionRequest.request_no.like(prefix + "%")
                    ).count()
                    request_no = f"{prefix}{count + 1:04d}"

                    # 计算过期时间
                    from datetime import timedelta
                    if priority == "URGENT":
                        expire_delta = timedelta(hours=2)
                    else:
                        expire_delta = timedelta(hours=24)
                    expire_at = (datetime.now(BEIJING_TZ) + expire_delta).isoformat()

                    req = PermissionRequest(
                        request_no=request_no,
                        requester_id=requester_id,
                        perm_code=perm_code,
                        request_type=request_type,
                        reason=reason,
                        status="PENDING",
                        current_approver_id=supervisor_id,
                        approval_level=1,
                        priority=priority,
                        expire_at=expire_at,
                    )
                    session.add(req)
                    session.flush()
                    return request_no, supervisor_id

            request_no, approver = await asyncio.to_thread(_create_request)

            return {
                "success": True,
                "request_no": request_no,
                "approver_id": approver,
                "reply": f"权限申请已提交（编号 {request_no}），待 {approver or '管理员'} 审批",
            }
        except Exception as e:
            logger.error("request_permission failed: %s", e)
            return {"success": False, "reply": f"权限申请失败：{e}"}

    async def approve_request(self, *, request_no: str, approver_id: str,
                              approved: bool, remark: str = "") -> dict:
        """审批权限申请（阶段二基础版：直接通过/拒绝）。

        阶段三扩展：超时升级/转审/协同待办集成。
        """
        import asyncio
        from emily_core.infrastructure.database.session import get_session
        from emily_core.infrastructure.database.models import (
            PermissionRequest, PermissionGrant, _utc_now,
        )

        try:
            def _process_approval():
                with get_session() as session:
                    req = session.query(PermissionRequest).filter(
                        PermissionRequest.request_no == request_no,
                        PermissionRequest.is_deleted == False,
                    ).first()
                    if req is None:
                        return None, "申请记录不存在"
                    if req.status != "PENDING":
                        return None, f"申请状态为 {req.status}，无法审批"

                    # 验证审批人
                    if req.current_approver_id and req.current_approver_id != approver_id:
                        return None, "您不是该申请的当前审批人"

                    if approved:
                        req.status = "APPROVED"
                        req.approver_id = approver_id
                        req.approval_remark = remark
                        req.approved_at = _utc_now()

                        # 自动创建授权记录
                        grant_no_prefix = f"PGR-{_utc_now()[:10].replace('-', '')}-"
                        grant_count = session.query(PermissionGrant).filter(
                            PermissionGrant.grant_no.like(grant_no_prefix + "%")
                        ).count()
                        grant_no = f"{grant_no_prefix}{grant_count + 1:04d}"

                        grant = PermissionGrant(
                            grant_no=grant_no,
                            grantee_id=req.requester_id,
                            grantor_id=approver_id,
                            perm_code=req.perm_code,
                            grant_type="TEMP" if req.request_type == "TEMP_GRANT" else "PERMANENT",
                            operations='["read"]',
                            status="ACTIVE",
                            remark=f"审批通过 {request_no}: {remark}",
                        )
                        session.add(grant)
                    else:
                        req.status = "REJECTED"
                        req.approver_id = approver_id
                        req.approval_remark = remark
                        req.approved_at = _utc_now()

                    session.flush()
                    return req.request_no, "approved" if approved else "rejected"

            req_no, result = await asyncio.to_thread(_process_approval)
            if req_no is None:
                return {"success": False, "reply": result}

            action = "通过" if approved else "拒绝"
            return {
                "success": True,
                "request_no": req_no,
                "reply": f"权限申请 {req_no} 已{action}",
            }
        except Exception as e:
            logger.error("approve_request failed: %s", e)
            return {"success": False, "reply": f"审批操作失败：{e}"}
