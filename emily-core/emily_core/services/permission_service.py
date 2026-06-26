"""PermissionService — 权限快照组装 + 校验/授权/查询（阶段一+二）。

build_permission_snapshot() 在 SessionFactory._build_context() 中被调用，
查询 User + CompanyInfo + 权限矩阵 + 授权记录，组装 PermissionSnapshot 注入 SessionContext。

阶段二新增：
  - check(): 三维鉴权（委托 PermissionAuthEngine）
  - grant() / revoke(): 授权管理（委托 PermissionGrantRepository + 审计日志）
  - query_user_permissions(): 查询用户权限清单
  - L1/L2 缓存集成（PermissionCache）

设计要点：
  - build_permission_snapshot() 保持 sync（_build_context 是 sync）
  - check/grant/revoke/query 为 async（Application 层调用，内部用 asyncio.to_thread 包裹 sync repo）
  - fail-open：查询失败降级为 L1 访客快照 + 告警（设计文档 §6.4）
  - sop_allow 细筛（公开 + 树形级别 + deny 绑定 + 企业类型/部门）
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from emily_core.infrastructure.database.models import (
    CompanyInfo,
    PermissionGroup,
    User,
    _utc_now,
)
from emily_core.permission.level import can_access, LEVEL_NAME
from emily_core.repositories.permission_grant_repo import PermissionGrantRepository
from emily_core.repositories.permission_repo import PermissionRepository
from emily_core.session.session_context import PermissionSnapshot

logger = logging.getLogger("emily.permission")


class PermissionService:
    """权限快照组装 + 校验/授权/查询服务。

    Args:
        repo: PermissionRepository（User/Company/SOP 矩阵查询）
        grant_repo: PermissionGrantRepository（授权记录查询）
        fail_open: 查询失败时降级为访客（True）或抛异常（False）
        cache: PermissionCache（阶段二 L1/L2 缓存，可选）
        auth_engine: PermissionAuthEngine（阶段二三维鉴权引擎，可选）
        audit_repo: PermissionAuditLogRepository（阶段二审计日志，可选）
    """

    def __init__(self,
                 repo: Optional[PermissionRepository] = None,
                 grant_repo: Optional[PermissionGrantRepository] = None,
                 fail_open: bool = True,
                 cache=None,
                 auth_engine=None,
                 audit_repo=None):
        self._repo = repo or PermissionRepository()
        self._grant_repo = grant_repo or PermissionGrantRepository()
        self._fail_open = fail_open
        self._cache = cache
        self._auth_engine = auth_engine
        self._audit_repo = audit_repo

    # ========================================================================
    #  快照组装（核心）
    # ========================================================================

    def build_permission_snapshot(self, user_id: str) -> PermissionSnapshot:
        """组装用户权限快照，注入 SessionContext。

        fail-open：任何异常降级为 L1 访客快照（设计文档 §6.4）。
        """
        try:
            return self._do_build_snapshot(user_id)
        except Exception as e:
            logger.warning("build_permission_snapshot failed user=%s: %s", user_id, e)
            if self._fail_open:
                return PermissionSnapshot(permission_level=1)  # L1 访客降级
            raise

    def _do_build_snapshot(self, user_id: str) -> PermissionSnapshot:
        user = self._repo.get_user(user_id)
        if user is None:
            logger.warning("user not found, fallback to L1: %s", user_id)
            return PermissionSnapshot(permission_level=1)

        company = self._repo.get_company(user.company) if user.company else None
        grants = self._grant_repo.get_active_grants(user_id)

        # SOP 白名单 + 拒绝列表（优先使用 L2 缓存）
        if self._cache is not None:
            sop_allow, denied_sop_ids = self._cache.get_user_whitelist(
                user_id, user.permission_level,
                company.type if company else "",
                self._primary_department(company),
            )
        else:
            sop_allow, denied_sop_ids = self._compute_sop_allow(user, company)

        # 授权码（临时/永久授权持有的权限编码，AUTO 不计入 granted_codes）
        granted_codes = [g.perm_code for g in grants if g.grant_type != "AUTO"]

        # denied_codes：从 SOP deny 绑定推导
        denied_codes = [f"SOP-INTERNAL-*-*-{sid}-*" for sid in denied_sop_ids]

        # 权限版本号（来自缓存或默认 0）
        perm_version = self._cache.get_version() if self._cache else 0

        return PermissionSnapshot(
            permission_level=user.permission_level,
            company_id=user.company or "",
            company_type=company.type if company else "",
            company_name=company.company_name if company else "",
            department=self._primary_department(company),
            project_ids=self._derive_project_ids(user, company),
            partner_ids=self._load_json_list(company.partners) if company else [],
            scopes=self._load_json_list(company.scope) if company else [],
            sop_allow=sop_allow,
            db_perms=self._derive_db_perms(user.permission_level),
            info_level=self._derive_info_level(user.permission_level),
            supervisor_id=user.supervisor_id or "",
            authorized_node_ids=self._derive_authorized_nodes(company),
            granted_codes=granted_codes,
            denied_codes=denied_codes,
            permissions_loaded_at=_utc_now(),
            permission_version=perm_version,
            extra_perms={"user_id": user_id},
        )

    # ========================================================================
    #  SOP 白名单计算
    # ========================================================================

    def _compute_sop_allow(self, user: User, company: Optional[CompanyInfo]):
        """计算用户可访问的 SOP 白名单 + 拒绝的 SOP ID 列表。

        阶段一粗筛规则：
          1. deny 绑定（用户匹配组）→ denied
          2. is_public=True → allow
          3. can_access(permission_level, min_permission_level) → allow
        企业类型/部门细筛在阶段二鉴权引擎 check_sop_access() 实现。
        """
        sop_flows = self._repo.list_active_sop_flows()
        bindings = self._repo.list_sop_bindings()
        groups = self._repo.list_permission_groups()

        user_company_type = company.type if company else ""
        user_department = self._primary_department(company)
        matched_group_ids = {
            g.id for g in groups
            if self._group_matches_user(g, user_company_type, user_department)
        }

        sop_allow: list[str] = []
        denied_sop_ids: list[str] = []
        for flow in sop_flows:
            flow_bindings = [b for b in bindings if b.sop_business_flow_id == flow.id]

            # 1. deny 绑定优先
            if any(b.binding_type == "deny" and b.permission_group_id in matched_group_ids
                   for b in flow_bindings):
                denied_sop_ids.append(flow.sop_id)
                continue

            # 2. 公开 SOP
            if flow.is_public:
                sop_allow.append(flow.sop_id)
                continue

            # 3. 树形继承级别检查
            if not can_access(user.permission_level, flow.min_permission_level):
                continue

            sop_allow.append(flow.sop_id)

        return sop_allow, denied_sop_ids

    @staticmethod
    def _group_matches_user(group: PermissionGroup, user_company_type: str, user_department: str) -> bool:
        """权限组是否匹配用户的企业类型 + 部门。"""
        if group.company_type and group.company_type != user_company_type:
            return False
        if group.department and group.department != user_department:
            return False
        return True

    # ========================================================================
    #  辅助推导
    # ========================================================================

    @staticmethod
    def _primary_department(company: Optional[CompanyInfo]) -> str:
        if not company or not company.department:
            return ""
        depts = PermissionService._load_json_list(company.department)
        return depts[0] if depts else ""

    @staticmethod
    def _derive_info_level(level: int) -> str:
        """permission_level → 可见最大密级（需求 §3.1）。"""
        if level >= 5:
            return "confidential"
        if level >= 2:
            return "internal"
        return "public"

    @staticmethod
    def _derive_db_perms(level: int) -> dict[str, str]:
        """级别 → 数据库表级权限（粗粒度，细粒度由行级安全拦截器处理）。"""
        perms: dict[str, str] = {}
        if level >= 1:
            perms["project"] = "read"
        if level >= 2:
            perms["event"] = "read_write"
            perms["task"] = "read_write"
            perms["meeting"] = "read_write"
        if level >= 5:
            perms["project"] = "read_write"
            perms["financial"] = "read"
        return perms

    @staticmethod
    def _derive_authorized_nodes(company: Optional[CompanyInfo]) -> list[str]:
        """从 company.function_scope 推导用户可访问的全景节点 ID（需求 §4.1）。"""
        if not company or not company.function_scope:
            return []
        try:
            fs = json.loads(company.function_scope)
        except (json.JSONDecodeError, TypeError):
            return []
        nodes: list[str] = []
        items = fs if isinstance(fs, list) else []
        for item in items:
            if isinstance(item, dict):
                nodes.extend(item.get("nodeIds", []) or [])
        return nodes

    @staticmethod
    def _derive_project_ids(user: User, company: Optional[CompanyInfo]) -> list[str]:
        """用户参与的项目 ID 列表（阶段二关联 projects 表后完善）。"""
        return []

    @staticmethod
    def _load_json_list(raw: str) -> list:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    # ========================================================================
    #  阶段二：三维鉴权 + 授权管理
    # ========================================================================

    async def check(self, user_id: str, sop_id: str) -> dict:
        """三维鉴权：检查用户是否有权访问指定 SOP（需求 §14.1）。

        Returns:
            {"allowed": bool, "reason": str, "suggested_approver": str}
        """
        import asyncio
        snapshot = await asyncio.to_thread(self.build_permission_snapshot, user_id)

        if self._auth_engine is not None:
            result = await self._auth_engine.check_sop_access(snapshot, sop_id)
            return {
                "allowed": result.allowed,
                "reason": result.reason,
                "suggested_approver": result.suggested_approver,
                "details": result.matched_details,
            }

        # 无引擎时走快照白名单
        allowed = sop_id in snapshot.sop_allow
        return {
            "allowed": allowed,
            "reason": "" if allowed else f"SOP {sop_id} 不在用户白名单中",
            "suggested_approver": snapshot.supervisor_id if not allowed else "",
        }

    async def grant(self, *, grantee_id: str, grantor_id: str, perm_code: str,
                    grant_type: str = "TEMP", operations: str = '["read"]',
                    expire_time: Optional[str] = None, remark: str = "",
                    client_ip: str = "") -> dict:
        """授权（需求 §5）—— 创建 PermissionGrant 记录 + 审计日志。

        Args:
            grantee_id: 被授权人
            grantor_id: 授权人
            perm_code: 权限编码
            grant_type: AUTO/TEMP/PERMANENT
            operations: 操作 JSON，如 '["read","write"]'
            expire_time: 过期时间（TEMP 必填）
            remark: 授权原因（PERMANENT 必填）

        Returns:
            {"success": bool, "grant_no": str, "reply": str}
        """
        import asyncio

        # 校验：PERMANENT 必须填写 remark
        if grant_type == "PERMANENT" and not remark:
            return {"success": False, "reply": "永久授权必须填写授权原因（remark）"}

        # 校验：TEMP 必须填写 expire_time
        if grant_type == "TEMP" and not expire_time:
            return {"success": False, "reply": "临时授权必须设置过期时间（expire_time）"}

        # 校验：授权人权限检查（授权人须有该资源权限或为 L5+）
        grantor = await asyncio.to_thread(self._repo.get_user, grantor_id)
        if grantor is None:
            return {"success": False, "reply": f"授权人 {grantor_id} 不存在"}

        from emily_core.permission.level import is_admin as _is_admin
        if not _is_admin(grantor.permission_level):
            # 非管理员需要自身持有该权限才能授权
            grantor_snapshot = await asyncio.to_thread(self.build_permission_snapshot, grantor_id)
            if not self._grantor_has_permission(grantor_snapshot, perm_code):
                return {"success": False, "reply": "授权人无此资源权限，无法授权"}

        # 创建授权记录
        try:
            grant_no = await asyncio.to_thread(
                self._grant_repo.generate_grant_no,
            )
            grant_record = await asyncio.to_thread(
                self._grant_repo.create,
                grant_no=grant_no,
                grantee_id=grantee_id,
                perm_code=perm_code,
                grant_type=grant_type,
                grantor_id=grantor_id,
                operations=operations,
                expire_time=expire_time,
                remark=remark,
                client_ip=client_ip,
            )

            # 审计日志
            if self._audit_repo is not None:
                await asyncio.to_thread(
                    self._audit_repo.log_grant,
                    grantor_id=grantor_id,
                    grantee_id=grantee_id,
                    perm_code=perm_code,
                    grant_type=grant_type,
                    remark=remark,
                )

            # 失效用户 L2 缓存
            if self._cache is not None:
                self._cache.invalidate_user(grantee_id)

            return {
                "success": True,
                "grant_no": grant_no,
                "reply": f"授权成功（{grant_type}，编号 {grant_no}）",
            }
        except Exception as e:
            logger.error("grant failed: %s", e)
            return {"success": False, "reply": f"授权失败：{e}"}

    async def revoke(self, *, grant_no: str, revoke_reason: str = "",
                     operator_id: str = "") -> dict:
        """撤销授权（需求 §5.2）。

        主动撤销/强制撤销（L5+）。

        Returns:
            {"success": bool, "reply": str}
        """
        import asyncio

        try:
            grant = await asyncio.to_thread(
                self._grant_repo.get_by_grant_no, grant_no,
            )
            if grant is None:
                return {"success": False, "reply": f"授权记录 {grant_no} 不存在"}
            if grant.status != "ACTIVE":
                return {"success": False, "reply": f"授权记录 {grant_no} 状态为 {grant.status}，无法撤销"}

            # 权限检查：授权人本人 或 L5+ 管理员
            operator = await asyncio.to_thread(self._repo.get_user, operator_id)
            if operator is not None:
                from emily_core.permission.level import is_admin as _is_admin
                is_force = _is_admin(operator.permission_level) and operator_id != grant.grantor_id
                if operator_id != grant.grantor_id and not is_force:
                    return {"success": False, "reply": "仅授权人或管理员可撤销授权"}
            else:
                is_force = False

            await asyncio.to_thread(
                self._grant_repo.revoke, grant_no, revoke_reason,
            )

            # 审计日志
            if self._audit_repo is not None:
                await asyncio.to_thread(
                    self._audit_repo.log_revoke,
                    grantor_id=operator_id,
                    grantee_id=grant.grantee_id,
                    perm_code=grant.perm_code,
                    remark=revoke_reason or ("强制撤销" if is_force else "主动撤销"),
                )

            # 失效用户 L2 缓存
            if self._cache is not None:
                self._cache.invalidate_user(grant.grantee_id)

            return {"success": True, "reply": f"授权 {grant_no} 已撤销"}
        except Exception as e:
            logger.error("revoke failed: %s", e)
            return {"success": False, "reply": f"撤销失败：{e}"}

    async def query_user_permissions(self, user_id: str) -> dict:
        """查询用户权限清单（需求 §14 GET /user/{userId}）。

        Returns:
            {"success": bool, "permissions": dict, "reply": str}
        """
        import asyncio

        try:
            snapshot = await asyncio.to_thread(self.build_permission_snapshot, user_id)
            grants = await asyncio.to_thread(self._grant_repo.get_active_grants, user_id)

            return {
                "success": True,
                "permissions": {
                    "user_id": user_id,
                    "permission_level": snapshot.permission_level,
                    "level_name": LEVEL_NAME.get(snapshot.permission_level, f"L{snapshot.permission_level}"),
                    "company_id": snapshot.company_id,
                    "company_type": snapshot.company_type,
                    "company_name": snapshot.company_name,
                    "department": snapshot.department,
                    "info_level": snapshot.info_level,
                    "sop_allow": snapshot.sop_allow,
                    "db_perms": snapshot.db_perms,
                    "authorized_node_ids": snapshot.authorized_node_ids,
                    "granted_codes": snapshot.granted_codes,
                    "denied_codes": snapshot.denied_codes,
                    "active_grants": [
                        {
                            "grant_no": g.grant_no,
                            "perm_code": g.perm_code,
                            "grant_type": g.grant_type,
                            "operations": g.operations,
                            "expire_time": g.expire_time,
                            "status": g.status,
                        }
                        for g in grants
                    ],
                    "permission_version": snapshot.permission_version,
                    "permissions_loaded_at": snapshot.permissions_loaded_at,
                },
            }
        except Exception as e:
            logger.error("query_user_permissions failed: %s", e)
            return {"success": False, "reply": f"查询权限失败：{e}"}

    @staticmethod
    def _grantor_has_permission(grantor_snapshot: PermissionSnapshot, perm_code: str) -> bool:
        """检查授权人是否持有指定权限编码（粗略检查）。"""
        from emily_core.permission.code_compiler import code_matches_any
        if perm_code in grantor_snapshot.granted_codes:
            return True
        if code_matches_any(perm_code, grantor_snapshot.granted_codes):
            return True
        return False
