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

    # ── 只读观测（供 emy-console 消费；返回 dict，避免 detached-instance 访问）──

    @staticmethod
    def list_all(limit: int = 200) -> list[dict]:
        """列出全部归档记录（按归档时间倒序，只读）。"""
        with get_session() as session:
            rows = (
                session.query(SessionArchive)
                .order_by(SessionArchive.archived_at.desc())
                .limit(limit)
                .all()
            )
            return [_to_dict(r) for r in rows]

    @staticmethod
    def get_by_id(archive_id: str) -> Optional[dict]:
        """按归档记录主键 id 查单条（只读）。"""
        with get_session() as session:
            row = (
                session.query(SessionArchive)
                .filter(SessionArchive.id == archive_id)
                .first()
            )
            return _to_dict(row) if row else None

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


def _to_dict(row: SessionArchive) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id or "",
        "user_id": row.user_id or "",
        "user_name": row.user_name or "",
        "turn_count": row.turn_count or 0,
        "started_at": row.started_at or "",
        "archived_at": row.archived_at or "",
        "archive_reason": row.archive_reason or "",
        "md_file_path": row.md_file_path or "",
    }
