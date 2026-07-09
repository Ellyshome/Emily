"""PermissionCache —— 权限矩阵两级缓存（设计文档 §六-B）。

层级：
  L1 矩阵: PermissionGroup × SOPBusinessFlow × SOPPermissionBinding 全量结果集
            TTL = permission_cache_ttl_seconds（默认 5 分钟）
  L2 用户白名单: 单用户 sop_allow（基于 L1 + 用户属性计算）
            TTL = Session 生命周期

失效触发：
  - 管理员修改权限组/SOP 绑定 → cache.invalidate()
  - TTL 到期自动重载
  - 加载失败降级直查 DB
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    PermissionGroup,
    SOPBusinessFlow,
    SOPPermissionBinding,
)

logger = logging.getLogger("emily.permission.cache")


@dataclass
class PermissionMatrix:
    """L1 缓存：权限矩阵全量快照。

    包含所有活跃的权限组、SOP 业务流、绑定关系。
    在 TTL 内被所有用户共享，避免逐用户查 DB。
    """

    groups: list = field(default_factory=list)           # list[PermissionGroup]
    sop_flows: list = field(default_factory=list)        # list[SOPBusinessFlow]
    bindings: list = field(default_factory=list)         # list[SOPPermissionBinding]
    loaded_at: float = 0.0                               # time.monotonic()
    version: int = 0                                      # 递增版本号


class PermissionCache:
    """权限矩阵两级缓存。

    线程安全：L1 缓存用 RLock 保护，L2 用独立 dict + RLock。
    """

    def __init__(self, ttl_seconds: int = 300):
        """
        Args:
            ttl_seconds: L1 矩阵缓存 TTL（秒），默认 300（5 分钟）。
        """
        self._ttl = ttl_seconds
        self._matrix: Optional[PermissionMatrix] = None
        self._matrix_lock = threading.RLock()

        # L2 用户白名单缓存：user_id → (sop_allow, denied_sop_ids, version, loaded_at)
        self._user_cache: dict[str, tuple[list[str], list[str], int, float]] = {}
        self._user_lock = threading.RLock()

    # ========================================================================
    #  L1 矩阵缓存
    # ========================================================================

    def get_matrix(self) -> PermissionMatrix:
        """获取权限矩阵（L1 缓存），TTL 过期或首次调用时从 DB 加载。

        加载失败降级：返回空矩阵 + WARNING 日志（fail-open）。
        """
        with self._matrix_lock:
            if self._matrix is not None:
                age = time.monotonic() - self._matrix.loaded_at
                if age < self._ttl:
                    return self._matrix
            # TTL 过期或首次 —— 重新加载
            matrix = self._load_matrix_from_db()
            if matrix is not None:
                self._matrix = matrix
                return self._matrix
            # 加载失败：返回旧矩阵（即使过期）或空矩阵
            if self._matrix is not None:
                logger.warning("Permission matrix reload failed, using stale cache")
                return self._matrix
            logger.warning("Permission matrix not available, returning empty matrix")
            return PermissionMatrix()

    def _load_matrix_from_db(self) -> Optional[PermissionMatrix]:
        """从 DB 加载权限矩阵（全量三表）。"""
        try:
            with get_session() as session:
                groups = (
                    session.query(PermissionGroup)
                    .filter(PermissionGroup.status == "active",
                            PermissionGroup.is_deleted == False)
                    .all()
                )
                sop_flows = (
                    session.query(SOPBusinessFlow)
                    .filter(SOPBusinessFlow.is_active == True,
                            SOPBusinessFlow.is_deleted == False)
                    .all()
                )
                bindings = (
                    session.query(SOPPermissionBinding)
                    .filter(SOPPermissionBinding.is_deleted == False)
                    .all()
                )

            # 计算新版本号
            old_version = self._matrix.version if self._matrix else 0
            new_version = old_version + 1

            matrix = PermissionMatrix(
                groups=list(groups),
                sop_flows=list(sop_flows),
                bindings=list(bindings),
                loaded_at=time.monotonic(),
                version=new_version,
            )
            logger.info(
                "Permission matrix loaded: %d groups, %d flows, %d bindings, version=%d",
                len(groups), len(sop_flows), len(bindings), new_version,
            )
            return matrix
        except Exception as e:
            logger.error("Failed to load permission matrix from DB: %s", e)
            return None

    def invalidate(self) -> None:
        """强制失效 L1 + L2 缓存（管理员修改权限组/SOP 绑定时调用）。"""
        with self._matrix_lock:
            self._matrix = None
        with self._user_lock:
            self._user_cache.clear()
        logger.info("Permission cache invalidated (L1 + L2)")

    def get_version(self) -> int:
        """获取当前矩阵版本号（用于变更检测）。"""
        with self._matrix_lock:
            if self._matrix is None:
                return 0
            return self._matrix.version

    # ========================================================================
    #  L2 用户白名单缓存
    # ========================================================================

    def get_user_whitelist(
        self, user_id: str, user_level: int,
        company_type: str, department: str,
    ) -> tuple[list[str], list[str]]:
        """获取用户 SOP 白名单 + 拒绝列表（L2 缓存）。

        基于当前 L1 矩阵 + 用户属性计算。矩阵版本变化时自动重算。

        Returns:
            (sop_allow, denied_sop_ids)
        """
        matrix = self.get_matrix()
        current_version = matrix.version

        with self._user_lock:
            cached = self._user_cache.get(user_id)
            if cached is not None:
                cached_allow, cached_deny, cached_version, _loaded_at = cached
                if cached_version == current_version:
                    return cached_allow, cached_deny

        # L2 未命中或版本不一致 —— 重新计算
        sop_allow, denied_sop_ids = self._compute_user_whitelist(
            matrix, user_level, company_type, department,
        )

        with self._user_lock:
            self._user_cache[user_id] = (
                sop_allow, denied_sop_ids, current_version, time.monotonic(),
            )

        return sop_allow, denied_sop_ids

    @staticmethod
    def _compute_user_whitelist(
        matrix: PermissionMatrix,
        user_level: int,
        company_type: str,
        department: str | list[str],
    ) -> tuple[list[str], list[str]]:
        """基于矩阵 + 用户属性计算白名单（阶段二含企业类型/部门细筛）。

        规则：
          1. deny 绑定（用户匹配组）→ denied
          2. is_public=True → allow
          3. can_access(level, min_level) → level check
          4. 企业类型/部门细筛（阶段二新增）
        """
        from .level import can_access

        # 匹配用户的权限组
        matched_group_ids: set[str] = set()
        for g in matrix.groups:
            if g.company_type and g.company_type != company_type:
                continue
            # 多部门交集匹配：用户任一部门命中权限组的部门要求即可
            user_depts = department if isinstance(department, list) else [department] if department else []
            if g.department and g.department not in user_depts:
                continue
            matched_group_ids.add(g.id)

        sop_allow: list[str] = []
        denied_sop_ids: list[str] = []

        for flow in matrix.sop_flows:
            flow_bindings = [b for b in matrix.bindings
                             if b.sop_business_flow_id == flow.id]

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
            if not can_access(user_level, flow.min_level):
                continue

            # 4. 企业类型匹配（阶段二新增）
            if flow.require_company_match:
                import json
                allowed_types = json.loads(flow.allowed_company_types) if flow.allowed_company_types else []
                if allowed_types and company_type not in allowed_types:
                    continue

            # 5. 部门匹配（阶段二新增）
            if flow.require_department_match:
                import json
                allowed_depts = json.loads(flow.allowed_departments) if flow.allowed_departments else []
                if allowed_depts and department not in allowed_depts:
                    continue

            sop_allow.append(flow.sop_id)

        return sop_allow, denied_sop_ids

    def invalidate_user(self, user_id: str) -> None:
        """失效单用户 L2 缓存（权限变更时调用）。"""
        with self._user_lock:
            self._user_cache.pop(user_id, None)

    # ========================================================================
    #  统计
    # ========================================================================

    def stats(self) -> dict:
        """缓存统计信息（监控用）。"""
        with self._matrix_lock:
            matrix_info = {
                "loaded": self._matrix is not None,
                "version": self._matrix.version if self._matrix else 0,
                "age_seconds": (
                    round(time.monotonic() - self._matrix.loaded_at, 1)
                    if self._matrix else 0
                ),
                "ttl_seconds": self._ttl,
            }
        with self._user_lock:
            user_info = {
                "cached_users": len(self._user_cache),
            }
        return {"l1_matrix": matrix_info, "l2_users": user_info}
