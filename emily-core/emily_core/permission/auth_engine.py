"""PermissionAuthEngine —— 三维树形鉴权引擎（需求 §4 + §14）。

三维鉴权 = 主体属性(权限层级) × 资源属性(密级/企业类型/部门/节点) × 授权形式(临时/永久/deny)

优先级短路求值（需求 §1.4）：
  拒绝(DENY) > 单独文件授权 > 临时授权(TEMP) > 永久授权(PERMANENT) > 单位归属自动授权(AUTO)

check_sop_access() 流程：
  1. 查 DENY：sop_id 相关编码 in perms.denied_codes → DENY（优先级最高）
  2. 查单独授权：sop_id 相关编码 in perms.granted_codes → ALLOW
  3. 查 SOPBusinessFlow 表（min_level / security_level / required_node_ids）
     3.1 is_public=True → ALLOW
     3.2 树形继承：can_access(perms.level, sop_flow.min_level)
     3.3 密级校验：sop_flow.security_level 可见性 ⊆ perms.info_level
     3.4 企业类型匹配：require_company_match 且 perms.company_type 不在 allowed → DENY
     3.5 部门匹配：require_department_match 且 perms.department 不在 allowed → DENY
     3.6 节点范围：required_node_ids 与 perms.authorized_node_ids 有交集（或含 *）
  4. 全部通过 → ALLOW
  5. DENY 时写 permission_audit_log(operation_type=ACCESS_DENIED)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from .level import can_access, is_admin, LEVEL_NAME
from .code_compiler import compile_code, code_matches_any, can_view_security_level

if TYPE_CHECKING:
    from ..workitem.pipeline.context import BusContext

logger = logging.getLogger("emily.permission.auth_engine")


# ========================================================================
#  鉴权结果
# ========================================================================

@dataclass
class AccessCheckResult:
    """三维鉴权检查结果。

    Attributes:
        allowed: 是否允许
        reason: 拒绝原因（DENY 时填充）
        matched_details: 匹配到的详细信息（用于审计/调试）
        suggested_approver: 建议审批人 user_id（DENY 时填充，便于用户申请权限）
    """

    allowed: bool
    reason: str = ""
    matched_details: dict = field(default_factory=dict)
    suggested_approver: str = ""


# ========================================================================
#  SOP 编码工具
# ========================================================================

def _build_sop_code(sop_id: str, security_level: str = "INTERNAL",
                    project_id: str = "*", node_id: str = "*") -> str:
    """构建 SOP 资源的权限编码（用于 granted_codes / denied_codes 匹配）。

    格式: SOP-{security_level}-{project_id}-{node_id}-{sop_id}
    """
    return f"SOP-{security_level}-{project_id}-{node_id}-{sop_id}"


# ========================================================================
#  三维鉴权引擎
# ========================================================================

class PermissionAuthEngine:
    """三维树形鉴权引擎。

    按优先级短路求值：DENY > 单独授权 > TEMP > PERMANENT > AUTO。
    鉴权失败时自动写审计日志 + 返回建议审批人。
    """

    def __init__(self, cache=None, audit_repo=None):
        """
        Args:
            cache: PermissionCache 实例（L1 矩阵 + L2 用户白名单）
            audit_repo: PermissionAuditLogRepository（鉴权失败时写审计日志）
        """
        self._cache = cache
        self._audit_repo = audit_repo

    # ========================================================================
    #  核心鉴权
    # ========================================================================

    async def check_sop_access(
        self,
        perms: dict,
        sop_id: str,
        context: Optional["BusContext"] = None,
    ) -> AccessCheckResult:
        """三维鉴权：检查用户是否有权访问指定 SOP。

        按优先级短路求值，DENY 优先级最高。任何一步失败即返回 DENY。
        全部通过返回 ALLOW。
        """
        # ── 1. DENY 编码检查（优先级最高）──
        deny_result = self._check_deny_codes(perms, sop_id)
        if deny_result is not None:
            await self._log_access_denied(perms, sop_id, deny_result.reason)
            return deny_result

        # ── 2. 单独授权检查 ──
        grant_result = self._check_granted_codes(perms, sop_id)
        if grant_result is not None:
            return grant_result

        # ── 3. SOP 权限矩阵检查 ──
        matrix_result = await self._check_sop_matrix(perms, sop_id)
        if matrix_result is not None:
            if not matrix_result.allowed:
                await self._log_access_denied(perms, sop_id, matrix_result.reason)
            return matrix_result

        # ── 4. 默认拒绝（未命中任何 SOP 规则时拒绝，符合最小权限原则）──
        return AccessCheckResult(
            allowed=False,
            reason="SOP 未配置权限规则，默认拒绝（请联系管理员配置）",
            matched_details={"source": "default_deny"},
        )

    # ========================================================================
    #  Step 1: DENY 编码检查
    # ========================================================================

    def _check_deny_codes(self, perms: dict,
                          sop_id: str) -> Optional[AccessCheckResult]:
        """检查 denied_codes 是否包含此 SOP 的编码。

        denied_codes 优先级最高，任何级别用户命中即 DENY。
        使用 code_matches_any 进行通配符匹配。
        """
        denied_codes = perms.get("denied_codes", [])
        if not denied_codes:
            return None

        for security_level in ["PUBLIC", "INTERNAL", "PRIVATE", "CONFIDENTIAL"]:
            sop_code = _build_sop_code(sop_id, security_level)
            if code_matches_any(sop_code, denied_codes):
                return AccessCheckResult(
                    allowed=False,
                    reason=f"权限显式拒绝（SOP {sop_id} 在拒绝列表中）",
                    matched_details={"denied_sop_id": sop_id, "sop_code": sop_code},
                    suggested_approver=perms.get("supervisor_id", ""),
                )
        return None

    # ========================================================================
    #  Step 2: 单独授权检查
    # ========================================================================

    def _check_granted_codes(self, perms: dict,
                             sop_id: str) -> Optional[AccessCheckResult]:
        """检查 granted_codes 是否包含此 SOP 的编码。

        granted_codes 包含 TEMP/PERMANENT 授权，优先级高于角色继承。
        """
        granted_codes = perms.get("granted_codes", [])
        if not granted_codes:
            return None

        for security_level in ["PUBLIC", "INTERNAL", "PRIVATE", "CONFIDENTIAL"]:
            sop_code = _build_sop_code(sop_id, security_level)
            if code_matches_any(sop_code, granted_codes):
                return AccessCheckResult(
                    allowed=True,
                    matched_details={
                        "source": "granted_code",
                        "sop_code": sop_code,
                        "grant_type": "TEMP/PERMANENT",
                    },
                )
        return None

    # ========================================================================
    #  Step 3: SOP 权限矩阵检查（三维）
    # ========================================================================

    async def _check_sop_matrix(self, perms: dict,
                                sop_id: str) -> Optional[AccessCheckResult]:
        """三维矩阵检查：level × security_level × company_type × department × node_ids。

        从 L1 缓存获取 SOP 流定义，逐维度检查。
        """
        sop_flow = self._get_sop_flow(sop_id)
        if sop_flow is None:
            return None

        perm_level = perms.get("level", 1)
        info_level = perms.get("info_level", "public")
        company_type = perms.get("company_type", "")
        department = perms.get("department", [])
        authorized_node_ids = perms.get("authorized_node_ids", [])
        supervisor_id = perms.get("supervisor_id", "")

        # 3.1 公开 SOP
        if sop_flow.is_public:
            return AccessCheckResult(
                allowed=True,
                matched_details={"source": "public_sop"},
            )

        # 3.2 树形继承级别检查
        if not can_access(perm_level, sop_flow.min_level):
            user_level_name = LEVEL_NAME.get(perm_level, f"L{perm_level}")
            required_name = LEVEL_NAME.get(sop_flow.min_level, f"L{sop_flow.min_level}")
            return AccessCheckResult(
                allowed=False,
                reason=f"权限层级不足（当前 {user_level_name}，需 {required_name}）",
                matched_details={
                    "check": "level",
                    "user_level": perm_level,
                    "required_level": sop_flow.min_level,
                },
                suggested_approver=supervisor_id,
            )

        # 3.3 密级校验
        sop_security = sop_flow.security_level or "PUBLIC"
        if not can_view_security_level(perm_level, sop_security):
            return AccessCheckResult(
                allowed=False,
                reason=f"密级不足（SOP 密级 {sop_security}，用户可见 {info_level}）",
                matched_details={
                    "check": "security_level",
                    "sop_level": sop_security,
                    "user_max_level": info_level,
                },
                suggested_approver=supervisor_id,
            )

        # 3.4 企业类型匹配
        if sop_flow.require_company_match:
            allowed_types = json.loads(sop_flow.allowed_company_types) \
                if sop_flow.allowed_company_types else []
            if allowed_types and company_type not in allowed_types:
                return AccessCheckResult(
                    allowed=False,
                    reason=f"企业类型不匹配（SOP 要求 {allowed_types}，用户 {company_type}）",
                    matched_details={
                        "check": "company_type",
                        "allowed_types": allowed_types,
                        "user_type": company_type,
                    },
                    suggested_approver=supervisor_id,
                )

        # 3.5 部门匹配（交集匹配：用户任一部门命中 SOP 允许部门即可）
        if sop_flow.require_department_match:
            allowed_depts = json.loads(sop_flow.allowed_departments) \
                if sop_flow.allowed_departments else []
            user_departments = department if isinstance(department, list) else [department] if department else []
            if allowed_depts and not (set(user_departments) & set(allowed_depts)):
                return AccessCheckResult(
                    allowed=False,
                    reason=f"部门不匹配（SOP 要求 {allowed_depts}，用户 {user_departments}）",
                    matched_details={
                        "check": "department",
                        "allowed_depts": allowed_depts,
                        "user_dept": user_departments,
                    },
                    suggested_approver=supervisor_id,
                )

        # 3.6 节点范围
        required_nodes = json.loads(sop_flow.required_node_ids) \
            if sop_flow.required_node_ids else []
        if required_nodes:
            if not authorized_node_ids and "*" not in required_nodes:
                return AccessCheckResult(
                    allowed=False,
                    reason="无可访问的全景节点权限",
                    matched_details={
                        "check": "node_scope",
                        "required_nodes": required_nodes,
                        "user_nodes": authorized_node_ids,
                    },
                    suggested_approver=supervisor_id,
                )
            user_set = set(authorized_node_ids)
            req_set = set(required_nodes)
            if "*" not in user_set and not (user_set & req_set):
                return AccessCheckResult(
                    allowed=False,
                    reason="节点范围不匹配",
                    matched_details={
                        "check": "node_scope",
                        "required_nodes": required_nodes,
                        "user_nodes": authorized_node_ids,
                    },
                    suggested_approver=supervisor_id,
                )

        return AccessCheckResult(
            allowed=True,
            matched_details={"source": "matrix_check_passed"},
        )

    def _get_sop_flow(self, sop_id: str):
        """从 L1 缓存获取 SOPBusinessFlow 记录。"""
        if self._cache is None:
            return None
        matrix = self._cache.get_matrix()
        for flow in matrix.sop_flows:
            if flow.sop_id == sop_id:
                return flow
        return None

    # ========================================================================
    #  审计日志
    # ========================================================================

    async def _log_access_denied(self, perms: dict,
                                 sop_id: str, reason: str) -> None:
        """鉴权失败时写审计日志（需求 §8.1）。"""
        audit_user_id = perms.get("user_id", "")
        if self._audit_repo is None:
            logger.info("ACCESS_DENIED user=%s sop=%s reason=%s (no audit repo)",
                        audit_user_id or "?", sop_id, reason)
            return
        try:
            import asyncio
            await asyncio.to_thread(
                self._audit_repo.log_access_denied,
                grantee_id=audit_user_id,
                perm_code=f"SOP-INTERNAL-*-*-{sop_id}-*",
                reason=reason,
            )
        except Exception as e:
            logger.warning("Failed to write access_denied audit log: %s", e)

    # ========================================================================
    #  便捷方法
    # ========================================================================

    async def check_access(
        self,
        perms: dict,
        resource_type: str,
        resource_id: str,
        operation: str = "read",
    ) -> AccessCheckResult:
        """通用资源访问检查（非 SOP 类资源）。

        Args:
            perms: 权限 dict
            resource_type: DOC/DB/SOP/MSG/SYS
            resource_id: 资源标识
            operation: read/write/delete/execute
        """
        denied_codes = perms.get("denied_codes", [])
        granted_codes = perms.get("granted_codes", [])
        perm_level = perms.get("level", 1)
        supervisor_id = perms.get("supervisor_id", "")

        # DENY 编码优先
        if denied_codes:
            code = f"{resource_type}-*-*-*-{resource_id}"
            if code_matches_any(code, denied_codes):
                return AccessCheckResult(
                    allowed=False,
                    reason=f"权限显式拒绝（资源 {resource_id}）",
                    suggested_approver=supervisor_id,
                )

        # 单独授权
        if granted_codes:
            code = f"{resource_type}-*-*-*-{resource_id}"
            if code_matches_any(code, granted_codes):
                return AccessCheckResult(allowed=True)

        # 管理员放行
        if is_admin(perm_level):
            return AccessCheckResult(allowed=True)

        # 默认拒绝
        return AccessCheckResult(
            allowed=False,
            reason=f"无权访问资源 {resource_type}:{resource_id}",
            suggested_approver=supervisor_id,
        )
