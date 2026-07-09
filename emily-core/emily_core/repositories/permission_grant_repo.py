"""PermissionGrantRepository — 授权记录 CRUD（需求 §5）。

遵循项目约定：纯 @staticmethod，可选 session 参数。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from emily_core.infrastructure.database.models import (
    BEIJING_TZ,
    PermissionGrant,
    _utc_now,
)
from emily_core.infrastructure.database.session import get_session


class PermissionGrantRepository:
    """授权记录数据访问 —— AUTO/TEMP/PERMANENT 三种形式的创建/查询/撤销/过期。"""

    @staticmethod
    def generate_grant_no(*, session: Optional[Session] = None) -> str:
        """生成授权编号 PGR-YYYYMMDD-NNNN（按当日序号递增）。"""
        def _impl(sess: Session) -> str:
            today = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
            prefix = f"PGR-{today}-"
            count = sess.query(PermissionGrant).filter(
                PermissionGrant.grant_no.like(prefix + "%")
            ).count()
            return f"{prefix}{count + 1:04d}"

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def create(grant_no: str, grantee_id: str, perm_code: str, grant_type: str, *,
               grantor_id: str = "", operations: str = '["read"]',
               expire_time: Optional[str] = None, remark: str = "", client_ip: str = "",
               session: Optional[Session] = None) -> PermissionGrant:
        def _impl(sess: Session) -> PermissionGrant:
            grant = PermissionGrant(
                grant_no=grant_no,
                grantee_id=grantee_id,
                grantor_id=grantor_id,
                perm_code=perm_code,
                grant_type=grant_type,
                operations=operations,
                expire_time=expire_time,
                status="ACTIVE",
                remark=remark,
                client_ip=client_ip,
            )
            sess.add(grant)
            sess.flush()
            return grant

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_by_grant_no(grant_no: str, *, session: Optional[Session] = None) -> Optional[PermissionGrant]:
        def _impl(sess: Session):
            return sess.query(PermissionGrant).filter(
                PermissionGrant.grant_no == grant_no,
                PermissionGrant.is_deleted == False,
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_active_grants(user_id: str, *, session: Optional[Session] = None) -> list[PermissionGrant]:
        """获取用户有效的授权记录（ACTIVE 状态）。"""
        def _impl(sess: Session):
            return sess.query(PermissionGrant).filter(
                PermissionGrant.grantee_id == user_id,
                PermissionGrant.status == "ACTIVE",
                PermissionGrant.is_deleted == False,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def revoke(grant_no: str, revoke_reason: str = "", *,
               session: Optional[Session] = None) -> Optional[PermissionGrant]:
        """撤销授权（置 REVOKED + 记录撤销时间/原因）。"""
        def _impl(sess: Session):
            grant = sess.query(PermissionGrant).filter(
                PermissionGrant.grant_no == grant_no,
                PermissionGrant.is_deleted == False,
            ).first()
            if grant is None:
                return None
            grant.status = "REVOKED"
            grant.revoke_reason = revoke_reason
            grant.revoke_time = _utc_now()
            sess.flush()
            return grant

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def expire_overdue(*, session: Optional[Session] = None) -> int:
        """将过期的 ACTIVE 临时授权标记为 EXPIRED，返回处理条数。

        后台调度器定期调用（需求 §5.2 自动撤销）。
        """
        def _impl(sess: Session) -> int:
            now = _utc_now()
            rows = sess.query(PermissionGrant).filter(
                PermissionGrant.status == "ACTIVE",
                PermissionGrant.expire_time.isnot(None),
                PermissionGrant.expire_time < now,
                PermissionGrant.is_deleted == False,
            ).all()
            for g in rows:
                g.status = "EXPIRED"
            sess.flush()
            return len(rows)

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def cascade_revoke_above_level(user_id: str, new_level: int, *,
                                   session: Optional[Session] = None) -> int:
        """级联撤销：用户 level 降级后，撤销超出新级别的 TEMP/PERMANENT 授权。

        需求 §5.2 级联撤销。AUTO 授权随单位归属变更处理，此处仅处理 TEMP/PERMANENT。
        返回撤销条数。
        """
        def _impl(sess: Session) -> int:
            # 通过权限码的 min_level 判断是否超出新级别
            # 简化：撤销所有 TEMP/PERMANENT 的 ACTIVE 授权中，关联 SOP 要求级别 > new_level 的
            # 阶段三鉴权引擎落地后可精确判断；阶段一先提供基础能力
            rows = sess.query(PermissionGrant).filter(
                PermissionGrant.grantee_id == user_id,
                PermissionGrant.status == "ACTIVE",
                PermissionGrant.grant_type.in_(["TEMP", "PERMANENT"]),
                PermissionGrant.is_deleted == False,
            ).all()
            count = 0
            for g in rows:
                # 保守策略：降级到 L1/L2 时撤销所有 TEMP/PERMANENT
                # 精确级别判断在阶段三鉴权引擎集成后完善
                if new_level <= 2:
                    g.status = "REVOKED"
                    g.revoke_reason = f"级联撤销：用户权限降级至 L{new_level}"
                    g.revoke_time = _utc_now()
                    count += 1
            sess.flush()
            return count

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
