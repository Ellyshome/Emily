"""Session 归档读写抽象层 —— BUG-004: Session 注销时持久化。"""

import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import SessionArchive

logger = logging.getLogger("emily.repo.session_archive")


class SessionArchiveRepo:
    """Session 归档表 CRUD 操作。"""

    @staticmethod
    def create(
        *,
        conversation_id: str,
        user_id: Optional[str] = None,
        user_name: str = "",
        turn_count: int = 0,
        md_file_path: str = "",
        started_at: Optional[str] = None,
        archive_reason: str = "expired",
    ) -> SessionArchive:
        """创建归档记录。"""
        with get_session() as session:
            archive = SessionArchive(
                conversation_id=conversation_id,
                user_id=user_id,
                user_name=user_name,
                turn_count=turn_count,
                md_file_path=md_file_path,
                started_at=started_at,
                archive_reason=archive_reason,
            )
            session.add(archive)
            session.flush()

            logger.info(
                "SessionArchive created: conv=%s user=%s turns=%d reason=%s",
                conversation_id, user_id or "?", turn_count, archive_reason,
            )
            return archive

    @staticmethod
    def get_by_conversation_id(conversation_id: str) -> Optional[SessionArchive]:
        """按 conversation_id 查找最新归档。"""
        with get_session() as session:
            return (
                session.query(SessionArchive)
                .filter(SessionArchive.conversation_id == conversation_id)
                .order_by(SessionArchive.archived_at.desc())
                .first()
            )

    @staticmethod
    def list_by_user(user_id: str, limit: int = 20) -> list[SessionArchive]:
        """按用户查询归档历史。"""
        with get_session() as session:
            return (
                session.query(SessionArchive)
                .filter(SessionArchive.user_id == user_id)
                .order_by(SessionArchive.archived_at.desc())
                .limit(limit)
                .all()
            )
