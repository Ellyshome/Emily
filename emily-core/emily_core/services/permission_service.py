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
  - sop_allow 细筛（公开 + 树形级别 + deny 绑定 + 企业类型；部门维度已移除）
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
from emily_core.permission.level import LEVEL_NAME, level_label
from emily_core.repositories.permission_grant_repo import PermissionGrantRepository
from emily_core.repositories.permission_repo import PermissionRepository

logger = logging.getLogger("emily.permission")


# ══════════════════════════════════════════════════════════════════════════════
# 二维权限矩阵：level × company_type → db_perms
# ══════════════════════════════════════════════════════════════════════════════
#
# 设计原则：
#   - 级别（L1-L6）定"你是谁"（组织位置）
#   - 公司类型定"你能做什么"（职能角色）
#   - 矩阵 = f(level, company_type)
#
# 规则说明：
#   1. L1 访客：无论公司类型，只有 project:read
#   2. L2/L3 参建线：event/meeting 所有参建方均可读写
#      但 task 写权限只给施工相关方（施工单位/总包），其余只读
#   3. L4 建设主管：同 L2/L3 但加 project:read_write（建设方管理项目）
#   4. L5+ 管理员：全表读写
#
# ┌─────────────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
# │ company_type    │ project  │ event    │ task     │ meeting  │ financial│
# ├─────────────────┼──────────┼──────────┼──────────┼──────────┼──────────┤
# │ L2/L3 施工/总包 │ read     │ rw       │ rw       │ rw       │ —        │
# │ L2/L3 设计      │ read     │ rw       │ read     │ rw       │ —        │
# │ L2/L3 监理      │ read     │ rw       │ read     │ rw       │ —        │
# │ L2/L3 供应商    │ read     │ rw       │ read     │ rw       │ —        │
# │ L2/L3 其他      │ read     │ rw       │ read     │ rw       │ —        │
# │ L4 建设单位     │ rw       │ rw       │ rw       │ rw       │ —        │
# │ L5+ 管理员      │ rw       │ rw       │ rw       │ rw       │ read     │
# └─────────────────┴──────────┴──────────┴──────────┴──────────┴──────────┘
#
# 施工相关方定义：可以创建和修改任务的公司类型
_CONSTRUCTION_TYPES: frozenset[str] = frozenset({
    "施工单位", "总包", "总承包", "施工总包",
})

# L2/L3 参建线：按公司类型差异化（task 表区分读写）
_PARTICIPANT_DB_PERMS: dict[str, dict[str, str]] = {
    # 施工相关方：task 可读写
    "construction": {
        "project": "read",
        "event": "read_write",
        "task": "read_write",
        "meeting": "read_write",
    },
    # 非施工方（设计/监理/供应商等）：task 只读
    "non_construction": {
        "project": "read",
        "event": "read_write",
        "task": "read",
        "meeting": "read_write",
    },
}


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
                 audit_repo=None,
                 skill_registry=None):
        self._repo = repo or PermissionRepository()
        self._grant_repo = grant_repo or PermissionGrantRepository()
        self._fail_open = fail_open
        self._cache = cache
        self._auth_engine = auth_engine
        self._audit_repo = audit_repo
        self._skill_registry = skill_registry

    # ========================================================================
    #  快照组装（核心）
    # ========================================================================

    def build_permission_dict(self, user_id: str) -> dict:
        """组装用户权限快照（返回 dict），注入 SessionContext。

        fail-open：任何异常降级为 L1 访客快照（设计文档 §6.4）。
        """
        try:
            return self._do_build_snapshot(user_id)
        except Exception as e:
            logger.warning("build_permission_dict failed user=%s: %s", user_id, e)
            if self._fail_open:
                return {"level": 1}  # L1 访客降级
            raise

    # 向后兼容别名
    build_permission_snapshot = build_permission_dict

    def _do_build_snapshot(self, user_id: str) -> dict:
        user = self._repo.get_user(user_id)
        if user is None:
            logger.warning("user not found, fallback to L1: %s", user_id)
            return {"level": 1}

        company = self._repo.get_company(user.company) if user.company else None
        grants = self._grant_repo.get_active_grants(user_id)

        # SOP 白名单 + 拒绝列表（优先使用 L2 缓存）
        # 权限归口仅由「等级 + 单位性质」决定，部门维度已移除（PRD US-01/US-02）
        if self._cache is not None:
            sop_allow, denied_sop_ids = self._cache.get_user_whitelist(
                user_id, user.level,
                company.type if company else "",
            )
        else:
            sop_allow, denied_sop_ids = self._compute_sop_allow(user, company)

        # 授权码（临时/永久授权持有的权限编码，AUTO 不计入 granted_codes）
        granted_codes = [g.perm_code for g in grants if g.grant_type != "AUTO"]

        # denied_codes：从 SOP deny 绑定推导（5 段编码格式）
        denied_codes = [f"SOP-INTERNAL-*-*-{sid}" for sid in denied_sop_ids]

        # 权限版本号（来自缓存或默认 0）
        perm_version = self._cache.get_version() if self._cache else 0

        return {
            "level": user.level,
            "user_id": user_id,
            "company_id": user.company or "",
            "company_type": company.type if company else "",
            "company_name": company.company_name if company else "",
            "project_ids": self._derive_project_ids(user, company),
            "partner_ids": self._load_json_list(company.partners) if company else [],
            "scopes": self._load_json_list(company.scope) if company else [],
            "sop_allow": sop_allow,
            "db_perms": self._derive_db_perms(user.level, company.type if company else ""),
            "is_management_unit": bool(company and getattr(company, 'is_admin', False)),
            "info_level": self._derive_info_level(user.level),
            "supervisor_id": user.supervisor_id or "",
            "authorized_node_ids": self._derive_authorized_nodes(user, company),
            "granted_codes": granted_codes,
            "denied_codes": denied_codes,
            "permissions_loaded_at": _utc_now(),
            "permission_version": perm_version,
        }

    # ========================================================================
    #  SOP 白名单计算
    # ========================================================================

    def _compute_sop_allow(self, user: User, company: Optional[CompanyInfo]):
        """计算用户可访问的 SOP 白名单 + 拒绝的 SOP ID 列表。

        策略（agent-sop-skill 架构）：
          1. 优先查 sop_business_flows DB 表（细粒度权限矩阵）
          2. 若 DB 表无记录，fallback 到 SkillRegistry（磁盘 .skill.yaml）
             ——只要 Skill 存在就视为可用，细筛交由 AuthEngine 执行时做
          3. SkillRegistry 也无记录时返回空列表（退化模式）
        """
        sop_flows = self._repo.list_active_sop_flows()

        if sop_flows:
            # DB 有记录：走传统权限矩阵细筛
            bindings = self._repo.list_sop_bindings()
            groups = self._repo.list_permission_groups()

            user_company_type = company.type if company else ""
            matched_group_ids = {
                g.id for g in groups
                if self._group_matches_user(g, user_company_type)
            }

            sop_allow: list[str] = []
            denied_sop_ids: list[str] = []
            logger.info("_compute_sop_allow: level=%s flows=%d", user.level, len(sop_flows))
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

                # 3. 级别检查（线性：不低于 min_level 即可用）
                #    与 PermissionCache._compute_user_whitelist 保持同一口径 —— 树形继承下
                #    建设线（L4/L5/L6）不继承参建线（L2），用 can_access 会让管理员反而
                #    失去参建类 SOP。
                if flow.min_level is not None and int(user.level or 1) < int(flow.min_level):
                    continue

                sop_allow.append(flow.sop_id)

            return sop_allow, denied_sop_ids

        # DB 表无记录：fallback 到 SkillRegistry
        if self._skill_registry is not None:
            try:
                skill_ids = self._skill_registry.list_sop_ids()
                if skill_ids:
                    logger.info(
                        "sop_business_flows empty, using SkillRegistry fallback: %d skills for user=%s",
                        len(skill_ids), user.id,
                    )
                    return skill_ids, []
            except Exception as e:
                logger.warning("SkillRegistry fallback failed: %s", e)

        return [], []

    @staticmethod
    def _group_matches_user(group: PermissionGroup, user_company_type: str) -> bool:
        """权限组是否匹配用户的企业类型。

        部门维度已移除（PRD R1：权限只由「单位性质 + 等级」决定），
        group.department 不再参与匹配。
        """
        if group.company_type and group.company_type != user_company_type:
            return False
        return True

    # ========================================================================
    #  辅助推导
    # ========================================================================

    @staticmethod
    def _derive_info_level(level: int) -> str:
        """level → 可见最大密级（需求 §3.1，密级已简化为 3 级）。

        机密(confidential) 为白名单制：仅系统管理员(L6) 原生可见；
        上传人自有与显式授权由 VisibleFileSetResolver 单独放行，不在此推导。
        """
        if level >= 6:
            return "confidential"
        if level >= 2:
            return "internal"
        return "public"

    @staticmethod
    def _derive_db_perms(level: int, company_type: str = "") -> dict[str, str]:
        """级别 × 公司类型 → 数据库表级权限（二维矩阵）。

        粗粒度表级权限，细粒度由行级安全拦截器处理。

        Args:
            level: 权限层级 1-6
            company_type: 企业类型（设计单位/施工单位/监理等）

        映射表定义在模块级 _PARTICIPANT_DB_PERMS / _CONSTRUCTION_TYPES，
        详见该处注释的完整矩阵图。
        """
        # L1 访客：只有项目只读
        if level < 2:
            return {"project": "read"} if level >= 1 else {}

        # L5+ 管理员：全表读写
        if level >= 5:
            perms = {
                "project": "read_write",
                "event": "read_write",
                "task": "read_write",
                "meeting": "read_write",
            }
            if level >= 5:
                perms["financial"] = "read"
            return perms

        # L4 建设主管：同 L2/L3 施工方权限 + project 读写
        if level == 4:
            return {
                "project": "read_write",
                "event": "read_write",
                "task": "read_write",
                "meeting": "read_write",
            }

        # L2/L3 参建线：按公司类型差异化
        key = "construction" if company_type in _CONSTRUCTION_TYPES else "non_construction"
        return dict(_PARTICIPANT_DB_PERMS[key])

    @staticmethod
    def _derive_authorized_nodes(user: User, company: Optional[CompanyInfo]) -> list[str]:
        """用户可见的全景节点集合（唯一实现见 ParticipationRepo）。

        口径：
          - 管理单位（company_info.is_admin=True）→ 本项目全部节点
          - 其他单位 → 企业参与节点
          - 无企业 / 无参与 → 空集（fail-closed）

        **容器节点可见范围（US-15.5，唯一插入点）**：收容容器不属于任何参建单位，
        故一律从「企业参与」结果中剔除，改由容器规则单独判定——
          · L4+ → 其所属项目下**全部容器节点**
          · 创建者本人 → 其本人创建的容器节点（即使低于 L4）
        不改 `fetch_world_book._visible` 的 fail-closed 语义，只扩充集合。
        """
        if not company:
            return []
        from ..repositories.participation_repo import ParticipationRepo

        project_ids = ParticipationRepo.project_ids_of_company(company.id)
        if getattr(company, "is_admin", False):
            base = ParticipationRepo.all_node_ids_of_projects(project_ids)
        else:
            base = ParticipationRepo.node_ids_of_company(company.id)

        try:
            from ..repositories.node_repo import ProjectNodeRepo
            from .node_state_machine import CONTAINER_NODE_ROLES

            role_list = tuple(CONTAINER_NODE_ROLES)
            all_containers = set(ProjectNodeRepo.find_container_ids(
                role_list, project_ids=project_ids))
            level = int(getattr(user, "level", 0) or 0)
            visible_containers = ProjectNodeRepo.find_container_ids(
                role_list,
                project_ids=project_ids if level >= 4 else [],
                creator_id=str(getattr(user, "id", "") or ""),
            )
            return [n for n in base if n not in all_containers] + visible_containers
        except Exception as e:
            logger.warning("容器可见范围扩展失败，按企业参与口径返回: %s", e)
            return base

    @staticmethod
    def _derive_project_ids(user: User, company: Optional[CompanyInfo]) -> list[str]:
        """用户参与的项目 ID 列表。

        口径：人员不带项目归属字段，参与项目由
        「企业 → 其在节点上的参与记录 → 节点所属项目」推导。
        唯一实现见 ParticipationRepo。
        """
        if not company:
            return []
        from ..repositories.participation_repo import ParticipationRepo
        return ParticipationRepo.project_ids_of_company(company.id)

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
        perm_dict = await asyncio.to_thread(self.build_permission_dict, user_id)

        if self._auth_engine is not None:
            result = await self._auth_engine.check_sop_access(perm_dict, sop_id)
            return {
                "allowed": result.allowed,
                "reason": result.reason,
                "suggested_approver": result.suggested_approver,
                "details": result.matched_details,
            }

        # 无引擎时走快照白名单
        allowed = sop_id in perm_dict.get("sop_allow", [])
        return {
            "allowed": allowed,
            "reason": "" if allowed else f"SOP {sop_id} 不在用户白名单中",
            "suggested_approver": perm_dict.get("supervisor_id", "") if not allowed else "",
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
        if not _is_admin(grantor.level):
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
                is_force = _is_admin(operator.level) and operator_id != grant.grantor_id
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
            perm_dict = await asyncio.to_thread(self.build_permission_dict, user_id)
            grants = await asyncio.to_thread(self._grant_repo.get_active_grants, user_id)

            return {
                "success": True,
                "permissions": {
                    "user_id": user_id,
                    "level": perm_dict["level"],
                    "level_name": level_label(perm_dict["level"]),
                    "company_id": perm_dict.get("company_id", ""),
                    "company_type": perm_dict.get("company_type", ""),
                    "company_name": perm_dict.get("company_name", ""),
                    "is_management_unit": perm_dict.get("is_management_unit", False),
                    "info_level": perm_dict.get("info_level", "public"),
                    "sop_allow": perm_dict.get("sop_allow", []),
                    "db_perms": perm_dict.get("db_perms", {}),
                    "authorized_node_ids": perm_dict.get("authorized_node_ids", []),
                    "granted_codes": perm_dict.get("granted_codes", []),
                    "denied_codes": perm_dict.get("denied_codes", []),
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
                    "permission_version": perm_dict.get("permission_version", 0),
                    "permissions_loaded_at": perm_dict.get("permissions_loaded_at", ""),
                },
            }
        except Exception as e:
            logger.error("query_user_permissions failed: %s", e)
            return {"success": False, "reply": f"查询权限失败：{e}"}

    @staticmethod
    def _grantor_has_permission(grantor_snapshot: dict, perm_code: str) -> bool:
        """检查授权人是否持有指定权限编码（粗略检查）。"""
        from emily_core.permission.code_compiler import code_matches_any
        granted = grantor_snapshot.get("granted_codes", [])
        if perm_code in granted:
            return True
        if code_matches_any(perm_code, granted):
            return True
        return False
