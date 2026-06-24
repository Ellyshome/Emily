"""MeetingRepository —— 会议记录表 CRUD 抽象层。"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import Meeting

logger = logging.getLogger("emily.repo.meeting")


class MeetingRepository:
    """会议记录 CRUD 操作。"""

    @staticmethod
    def generate_meeting_no() -> str:
        """生成会议编号 MTG-YYYYMMDD-NNNN。"""
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"MTG-{today_str}-"
        with get_session() as session:
            last = (
                session.query(Meeting)
                .filter(Meeting.meeting_no.like(f"{prefix}%"))
                .order_by(Meeting.meeting_no.desc())
                .first()
            )
            if last is None:
                return f"{prefix}0001"
            seq_str = last.meeting_no[len(prefix):]
            try:
                seq = int(seq_str) + 1
            except ValueError:
                seq = 1
            return f"{prefix}{seq:04d}"

    @staticmethod
    def create(
        *,
        meeting_no: str,
        title: str = "",
        project_id: Optional[str] = None,
        summary: Optional[str] = None,
        attendees: Optional[list[str]] = None,
        source_message_id: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Meeting:
        with get_session() as session:
            meeting = Meeting(
                meeting_no=meeting_no,
                project_id=project_id,
                title=title,
                summary=summary,
                attendees=json.dumps(attendees or [], ensure_ascii=False),
                source_message_id=source_message_id,
                created_by=created_by,
            )
            session.add(meeting)
            session.flush()
            logger.info("Meeting created: no=%s, title=%s", meeting_no, title)
            return meeting

    @staticmethod
    def get_by_id(meeting_id: str) -> Optional[Meeting]:
        with get_session() as session:
            return session.query(Meeting).filter(Meeting.id == meeting_id).first()

    @staticmethod
    def get_by_meeting_no(meeting_no: str) -> Optional[Meeting]:
        with get_session() as session:
            return session.query(Meeting).filter(Meeting.meeting_no == meeting_no).first()

    # ── M5 查询 ──

    @staticmethod
    def query_meetings(
        *,
        project_id: str | None = None,
        time_range: str = "all",
        limit: int = 50,
    ) -> list[Meeting]:
        """按条件查询会议记录（按创建时间倒序）。"""
        from datetime import datetime, timezone
        from ..repositories.task_repo import _resolve_time_start

        with get_session() as session:
            q = session.query(Meeting)

            if project_id:
                q = q.filter(Meeting.project_id == project_id)

            if time_range != "all":
                now = datetime.now(timezone.utc)
                start = _resolve_time_start(time_range, now)
                if start:
                    q = q.filter(Meeting.created_at >= start.isoformat())

            q = q.order_by(Meeting.created_at.desc()).limit(limit)
            return q.all()
