"""PermissionRepository — 权限快照加载所需数据查询。

遵循项目约定：纯 @staticmethod，可选 session 参数（sm_node_repo 完整版范式）。
"""

from typing import Optional

from sqlalchemy.orm import Session

from emily_core.infrastructure.database.models import (
    CompanyInfo,
    PermissionGroup,
    PermissionGrant,
    SOPBusinessFlow,
    SOPPermissionBinding,
    User,
)
from emily_core.infrastructure.database.session import get_session


class PermissionRepository:
    """权限快照数据访问 —— 查询 User/Company/Grants/SOP 权限矩阵。"""

    # ========================================================================
    #  User / Company
    # ========================================================================

    @staticmethod
    def get_user(user_id: str, *, session: Optional[Session] = None) -> Optional[User]:
        def _impl(sess: Session):
            return sess.query(User).filter(
                User.id == user_id,
                User.is_deleted == False,
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_company(company_id: str, *, session: Optional[Session] = None) -> Optional[CompanyInfo]:
        def _impl(sess: Session):
            if not company_id:
                return None
            return sess.query(CompanyInfo).filter(
                CompanyInfo.id == company_id,
                CompanyInfo.is_deleted == False,
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ========================================================================
    #  Grants（授权记录）
    # ========================================================================

    @staticmethod
    def get_active_grants(user_id: str, *, session: Optional[Session] = None) -> list[PermissionGrant]:
        """获取用户有效的授权记录（ACTIVE 状态，含 AUTO/TEMP/PERMANENT）。"""
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

    # ========================================================================
    #  SOP 权限矩阵（三表：permission_groups / sop_business_flows / sop_permission_bindings）
    # ========================================================================

    @staticmethod
    def list_active_sop_flows(*, session: Optional[Session] = None) -> list[SOPBusinessFlow]:
        """所有启用的 SOP 业务流。"""
        def _impl(sess: Session):
            return sess.query(SOPBusinessFlow).filter(
                SOPBusinessFlow.is_active == True,
                SOPBusinessFlow.is_deleted == False,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_sop_bindings(*, session: Optional[Session] = None) -> list[SOPPermissionBinding]:
        """所有 SOP-权限组绑定（含 allow/deny）。"""
        def _impl(sess: Session):
            return sess.query(SOPPermissionBinding).filter(
                SOPPermissionBinding.is_deleted == False,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_permission_groups(*, session: Optional[Session] = None) -> list[PermissionGroup]:
        """所有有效权限组。"""
        def _impl(sess: Session):
            return sess.query(PermissionGroup).filter(
                PermissionGroup.status == "active",
                PermissionGroup.is_deleted == False,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
