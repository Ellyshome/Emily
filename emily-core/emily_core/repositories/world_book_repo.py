"""ProjectWorldBookRepo —— 项目世界书 Repository 层。

参照模式：emily_core/repositories/node_repo.py（@staticmethod + get_session）。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.models import ProjectWorldBook
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.world_book_repo")


class ProjectWorldBookRepo:
    """项目世界书 Repository。"""

    @staticmethod
    def create(
        project_id: str,
        content_json: str = "{}",
        content_text: str = "",
        layer_versions: str = "{}",
        initialization_tier: int = 0,
        initialization_status: str = "{}",
        is_activated: bool = False,
        token_count: int = 0,
        generated_by: str = "manual",
    ) -> ProjectWorldBook:
        """创建项目世界书。"""
        with get_session() as session:
            wb = ProjectWorldBook(
                project_id=project_id,
                content_json=content_json,
                content_text=content_text,
                layer_versions=layer_versions,
                initialization_tier=initialization_tier,
                initialization_status=initialization_status,
                is_activated=is_activated,
                token_count=token_count,
                generated_by=generated_by,
            )
            session.add(wb)
            session.flush()
            logger.info("ProjectWorldBook created: project=%s tier=%d", project_id, initialization_tier)
            return wb

    @staticmethod
    def get_by_project(project_id: str) -> Optional[ProjectWorldBook]:
        """按 project_id 查询世界书（每项目唯一）。"""
        with get_session() as session:
            return (
                session.query(ProjectWorldBook)
                .filter(ProjectWorldBook.project_id == project_id)
                .first()
            )

    @staticmethod
    def get_by_id(wb_id: str) -> Optional[ProjectWorldBook]:
        """按主键查询。"""
        with get_session() as session:
            return session.query(ProjectWorldBook).filter(ProjectWorldBook.id == wb_id).first()

    @staticmethod
    def update_content(
        project_id: str,
        content_json: str = None,
        content_text: str = None,
        layer_versions: str = None,
        version: int = None,
        initialization_tier: int = None,
        initialization_status: str = None,
        is_activated: bool = None,
        token_count: int = None,
        generated_by: str = None,
    ) -> Optional[ProjectWorldBook]:
        """增量更新世界书字段。只更新非 None 的字段。"""
        from ..infrastructure.database.models import _utc_now

        with get_session() as session:
            wb = (
                session.query(ProjectWorldBook)
                .filter(ProjectWorldBook.project_id == project_id)
                .first()
            )
            if wb is None:
                return None

            if content_json is not None:
                wb.content_json = content_json
            if content_text is not None:
                wb.content_text = content_text
            if layer_versions is not None:
                wb.layer_versions = layer_versions
            if version is not None:
                wb.version = version
            if initialization_tier is not None:
                wb.initialization_tier = initialization_tier
            if initialization_status is not None:
                wb.initialization_status = initialization_status
            if is_activated is not None:
                wb.is_activated = is_activated
            if token_count is not None:
                wb.token_count = token_count
            if generated_by is not None:
                wb.generated_by = generated_by
            wb.updated_at = _utc_now()
            session.flush()
            logger.info("ProjectWorldBook updated: project=%s version=%d", project_id, wb.version)
            return wb

    @staticmethod
    def list_all() -> list[ProjectWorldBook]:
        """列出所有世界书。"""
        with get_session() as session:
            return session.query(ProjectWorldBook).all()

    @staticmethod
    def delete_by_project(project_id: str) -> bool:
        """删除指定项目的世界书。"""
        with get_session() as session:
            deleted = (
                session.query(ProjectWorldBook)
                .filter(ProjectWorldBook.project_id == project_id)
                .delete()
            )
            logger.info("ProjectWorldBook deleted: project=%s count=%d", project_id, deleted)
            return deleted > 0
