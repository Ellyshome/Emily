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
from emily_core.permission.level import can_access, LEVEL_NAME, level_label
from emily_core.repositories.permission_grant_repo import PermissionGrantRepository
from emily_core.repositories.permission_repo import PermissionRepository

logger = logging.getLogger("emily.permission")


# ══════════════════════════════════════════════════════════════════════════════
# 模块级工具函数（function_scope 解析）
# ══════════════════════════════════════════════════════════════════════════════

def _extract_node_ids(fs) -> list[str]:
    """从 function_scope 解析出的 JSON 结构提取节点 ID。

    兼容三种格式：
      1. list: [{"nodeIds": ["design", "construction"]}]
      2. dict (值是 list): {"design": ["node-001", "node-002"]}
      3. dict (值是 str): {"design": "design", "construction": "construction"}
    """
    nodes: list[str] = []

    if isinstance(fs, list):
        for item in fs:
            if isinstance(item, dict):
                # 格式1: {"nodeIds": [...]}
                if "nodeIds" in item:
                    nodes.extend(item["nodeIds"] or [])
                else:
                    # 也可能是 [{"design": [...]}]
                    for v in item.values():
                        if isinstance(v, list):
                            nodes.extend(v)
                        elif isinstance(v, str):
                            nodes.append(v)

    elif isinstance(fs, dict):
        for v in fs.values():
            if isinstance(v, list):
                nodes.extend(v)
            elif isinstance(v, str):
                nodes.append(v)

    return nodes


# 中文业务范围 → 英文节点 ID 映射（兜底推导）
_SCOPE_NODE_MAP: dict[str, str] = {
    "室内精装": "design",
    "软装深化": "design",
    "BIM建模": "design",
    "幕墙精装": "design",
    "精装": "design",
    "设计": "design",
    "施工": "construction",
    "工程": "construction",
    "监理": "supervision",
    "景观": "landscape",
    "总包": "construction",
    "分包": "construction",
    "采购": "procurement",
    "供货": "procurement",
}


def _scope_to_node_ids(scopes: list[str]) -> list[str]:
    """从 company.scope 推导节点 ID（宽松兜底）。

    当 function_scope 为空时，从 scope 的中文关键词推导英文节点 ID。
    这确保了 scope 非空的公司不会返回零节点——至少有一个合理的节点范围。
    """
    node_set: set[str] = set()
    for scope_text in scopes:
        for cn_keyword, node_id in _SCOPE_NODE_MAP.items():
            if cn_keyword in scope_text:
                node_set.add(node_id)
    return sorted(node_set) if node_set else []


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
        if self._cache is not None:
            sop_allow, denied_sop_ids = self._cache.get_user_whitelist(
                user_id, user.level,
                company.type if company else "",
                self._all_departments(company),
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
            "department": self._all_departments(company),
            "project_ids": self._derive_project_ids(user, company),
            "partner_ids": self._load_json_list(company.partners) if company else [],
            "scopes": self._load_json_list(company.scope) if company else [],
            "sop_allow": sop_allow,
            "db_perms": self._derive_db_perms(user.level, company.type if company else ""),
            "info_level": self._derive_info_level(user.level),
            "supervisor_id": user.supervisor_id or "",
            "authorized_node_ids": self._derive_authorized_nodes(company),
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
            user_departments = self._all_departments(company)
            matched_group_ids = {
                g.id for g in groups
                if self._group_matches_user(g, user_company_type, user_departments)
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
                if not can_access(user.level, flow.min_level):
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
    def _group_matches_user(group: PermissionGroup, user_company_type: str,
                            user_departments: list[str]) -> bool:
        """权限组是否匹配用户的企业类型 + 部门（交集匹配）。

        多部门用户只要任一部门命中权限组的部门要求即算匹配，
        避免因只取首部门导致其他部门的 SOP 误拒。
        """
        if group.company_type and group.company_type != user_company_type:
            return False
        if group.department and group.department not in user_departments:
            return False
        return True

    # ========================================================================
    #  辅助推导
    # ========================================================================

    @staticmethod
    def _all_departments(company: Optional[CompanyInfo]) -> list[str]:
        """返回公司的全部部门列表（而非仅首个）。

        多部门用户（如精装设计单位含设计部/深化部/软装部/工程部）需完整部门列表，
        以便 SOP 鉴权做交集匹配。只取首个会导致其他部门的 SOP 被误拒。
        """
        if not company or not company.department:
            return []
        return PermissionService._load_json_list(company.department)

    # 向后兼容别名（返回首部门 str）
    @staticmethod
    def _primary_department(company: Optional[CompanyInfo]) -> str:
        depts = PermissionService._all_departments(company)
        return depts[0] if depts else ""

    @staticmethod
    def _derive_info_level(level: int) -> str:
        """level → 可见最大密级（需求 §3.1）。"""
        if level >= 5:
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
    def _derive_authorized_nodes(company: Optional[CompanyInfo]) -> list[str]:
        """从 company.function_scope 推导用户可访问的全景节点 ID（需求 §4.1）。

        兼容三种 JSON 格式：
          1. 列表格式（推荐）: [{"nodeIds": ["design", "construction"]}]
          2. 字典格式（常见）: {"design": ["node-001", "node-002"], "construction": ["node-003"]}
          3. 扁平字典: {"design": "design", "construction": "construction"}
        若 function_scope 为空或无法解析，从 company.scope 推导节点关键词作为兜底。
        """
        if not company:
            return []

        # ── 主路径：解析 function_scope ──
        if company.function_scope and company.function_scope not in ("{}", "[]", ""):
            try:
                fs = json.loads(company.function_scope)
                nodes = _extract_node_ids(fs)
                if nodes:
                    return nodes
            except (json.JSONDecodeError, TypeError):
                pass

        # ── 兜底：从 company.scope 推导节点关键词 ──
        # scope 如 ["室内精装", "软装深化", "BIM建模"] → 提取英文前缀作为节点 ID
        # 这是一个宽松兜底，确保 scope 非空时不会返回零节点
        scopes = PermissionService._load_json_list(company.scope) if company.scope else []
        if scopes:
            return _scope_to_node_ids(scopes)

        return []

    @staticmethod
    def _derive_project_ids(user: User, company: Optional[CompanyInfo]) -> list[str]:
        """用户参与的项目 ID 列表。

        当前阶段：从 user.project_id 取主项目。阶段二需扩展为
        从 project_members 关联表查询全部参与项目。
        """
        pid = getattr(user, "project_id", None)
        if pid:
            return [pid]
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
                    "department": perm_dict.get("department", []),
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
