# emily-core/emily_core/repositories/project_repo.py
"""ProjectRepository —— 项目查询（resolver 用）。

参照 event_repo.py:234 的 @staticmethod + get_session() 模式。
"""
from __future__ import annotations

import logging
from typing import Optional

from ..infrastructure.database.models import Project
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.repo.project")


class ProjectRepository:
    """项目查询仓储。"""

    @staticmethod
    def find_by_name_fuzzy(name: str, limit: int = 10) -> list[Project]:
        """按名称模糊匹配项目（ilike），返回候选列表。

        超范围读：此处不做 session 约束过滤，由 Resolver 第二层做输出过滤。
        仅返回未删除项目。
        """
        if not name or not name.strip():
            return []
        with get_session() as session:
            return (
                session.query(Project)
                .filter(Project.is_deleted == False)  # noqa: E712
                .filter(Project.name.ilike(f"%{name.strip()}%"))
                .limit(limit)
                .all()
            )

    @staticmethod
    def get_by_id(project_id: str) -> Optional[Project]:
        """按 UUID 查项目。"""
        if not project_id:
            return None
        with get_session() as session:
            return session.query(Project).filter(Project.id == project_id).first()
