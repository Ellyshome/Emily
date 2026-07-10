"""SystemDescriptionRepo —— 系统自我描述 Repository 层。

参照模式：emily_core/repositories/world_book_repo.py（@staticmethod + get_session）。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.models import SystemDescription, _utc_now
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.system_description_repo")


class SystemDescriptionRepo:
    """系统自我描述 Repository。全局唯一记录。"""

    @staticmethod
    def create(
        content_json: str = "{}",
        content_text: str = "",
        domain_versions: str = "{}",
        schema_hash: str = "",
        permission_hash: str = "",
        file_model_hash: str = "",
        token_count: int = 0,
        generated_by: str = "manual",
    ) -> SystemDescription:
        """创建系统自我描述记录。"""
        with get_session() as session:
            desc = SystemDescription(
                content_json=content_json,
                content_text=content_text,
                domain_versions=domain_versions,
                schema_hash=schema_hash,
                permission_hash=permission_hash,
                file_model_hash=file_model_hash,
                token_count=token_count,
                generated_by=generated_by,
            )
            session.add(desc)
            session.flush()
            logger.info("SystemDescription created: version=%d generated_by=%s", desc.version, generated_by)
            return desc

    @staticmethod
    def get_latest() -> Optional[SystemDescription]:
        """获取最新版本的系统描述（全局唯一）。"""
        with get_session() as session:
            return (
                session.query(SystemDescription)
                .order_by(SystemDescription.version.desc())
                .first()
            )

    @staticmethod
    def get_by_id(desc_id: str) -> Optional[SystemDescription]:
        """按主键查询。"""
        with get_session() as session:
            return session.query(SystemDescription).filter(SystemDescription.id == desc_id).first()

    @staticmethod
    def update_content(
        content_json: str = None,
        content_text: str = None,
        domain_versions: str = None,
        version: int = None,
        schema_hash: str = None,
        permission_hash: str = None,
        file_model_hash: str = None,
        token_count: int = None,
        generated_by: str = None,
    ) -> Optional[SystemDescription]:
        """更新系统描述。只更新非 None 的字段。递增版本号。"""
        with get_session() as session:
            desc = (
                session.query(SystemDescription)
                .order_by(SystemDescription.version.desc())
                .first()
            )
            if desc is None:
                return None

            if content_json is not None:
                desc.content_json = content_json
            if content_text is not None:
                desc.content_text = content_text
            if domain_versions is not None:
                desc.domain_versions = domain_versions
            if version is not None:
                desc.version = version
            if schema_hash is not None:
                desc.schema_hash = schema_hash
            if permission_hash is not None:
                desc.permission_hash = permission_hash
            if file_model_hash is not None:
                desc.file_model_hash = file_model_hash
            if token_count is not None:
                desc.token_count = token_count
            if generated_by is not None:
                desc.generated_by = generated_by
            desc.updated_at = _utc_now()
            session.flush()
            logger.info("SystemDescription updated: version=%d", desc.version)
            return desc

    @staticmethod
    def list_all() -> list[SystemDescription]:
        """列出所有系统描述记录。"""
        with get_session() as session:
            return session.query(SystemDescription).order_by(SystemDescription.version.desc()).all()
