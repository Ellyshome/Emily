"""CompanyRepository — 企业清单 / 查重 / 新增 / 软删 / 在职人数统计。

参照模式：emily_core/repositories/user_repo.py（@staticmethod + get_session）。
只做数据访问，不做授权判定（授权在 service 层）。
"""

from __future__ import annotations

import logging

from sqlalchemy import func

from ..infrastructure.database.models import CompanyInfo, User
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.repo.company")


class CompanyRepository:
    """企业主数据访问。"""

    @staticmethod
    def list_companies(include_deleted: bool = False) -> list[dict]:
        """企业清单 + 在职归属人数。

        Returns:
            dict 列表，字段：company_id / company_name / status / is_deleted /
            member_count（在职归属人数）。
        """
        with get_session() as session:
            q = session.query(CompanyInfo)
            if not include_deleted:
                q = q.filter(CompanyInfo.is_deleted.isnot(True))
            companies = q.order_by(CompanyInfo.company_name).all()

            member_counts = dict(
                session.query(User.company, func.count(User.id))
                .filter(
                    User.is_deleted.isnot(True),
                    User.status == "active",
                    User.company.isnot(None),
                )
                .group_by(User.company)
                .all()
            )
        return [
            {
                "company_id": c.id,
                "company_name": c.company_name or "",
                "status": c.status or "",
                "is_deleted": bool(c.is_deleted),
                "member_count": int(member_counts.get(c.id, 0)),
            }
            for c in companies
        ]

    @staticmethod
    def get_by_id(company_id: str) -> CompanyInfo | None:
        """按 ID 查有效企业（归属/删除校验用）。"""
        if not company_id:
            return None
        with get_session() as session:
            return (
                session.query(CompanyInfo)
                .filter(
                    CompanyInfo.id == company_id,
                    CompanyInfo.is_deleted.isnot(True),
                )
                .first()
            )

    @staticmethod
    def get_by_name(name: str) -> CompanyInfo | None:
        """按名称查有效企业（同名查重用）。"""
        name = (name or "").strip()
        if not name:
            return None
        with get_session() as session:
            return (
                session.query(CompanyInfo)
                .filter(
                    CompanyInfo.company_name == name,
                    CompanyInfo.is_deleted.isnot(True),
                )
                .first()
            )

    @staticmethod
    def create(
        name: str,
        unified_code: str,
        project_leader_id: str,
        creator_id: str,
    ) -> CompanyInfo:
        """新增企业（最小字段；id/created_at 走 ORM 默认值）。"""
        with get_session() as session:
            company = CompanyInfo(
                company_name=name,
                unified_code=unified_code,
                project_leader_id=project_leader_id,
                creator_id=creator_id,
            )
            session.add(company)
            session.flush()
            return company

    @staticmethod
    def soft_delete(company_id: str) -> bool:
        """逻辑删除企业（is_deleted=True）。"""
        with get_session() as session:
            company = (
                session.query(CompanyInfo)
                .filter(CompanyInfo.id == company_id)
                .first()
            )
            if company is None:
                return False
            company.is_deleted = True
            return True

    @staticmethod
    def count_active_members(company_id: str) -> int:
        """统计某企业在职归属人数（删除前置判定用）。"""
        if not company_id:
            return 0
        with get_session() as session:
            return (
                session.query(User.id)
                .filter(
                    User.company == company_id,
                    User.is_deleted.isnot(True),
                    User.status == "active",
                )
                .count()
            )
