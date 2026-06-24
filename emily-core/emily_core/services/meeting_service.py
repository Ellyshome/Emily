"""MeetingService —— 会议记录业务逻辑。"""

import json
import logging
from typing import Optional

from ..repositories.meeting_repo import MeetingRepository
from ..adapters.standard.command import MeetingCommand
from ..infrastructure.database.models import Meeting

logger = logging.getLogger("emily.service.meeting")


class MeetingService:
    def __init__(self):
        self.repo = MeetingRepository()

    def create_meeting(self, cmd: MeetingCommand) -> Meeting:
        meeting_no = self.repo.generate_meeting_no()
        meeting = self.repo.create(
            meeting_no=meeting_no,
            title=cmd.title,
            project_id=cmd.project_id or None,
            summary=cmd.summary or None,
            attendees=cmd.attendees or [],
            source_message_id=cmd.source_message_id or None,
            created_by=cmd.creator_id or None,
        )
        logger.info("Meeting %s archived: %s", meeting_no, cmd.title)
        return meeting

    @staticmethod
    def format_reply(meeting: Meeting) -> str:
        try:
            att = json.loads(meeting.attendees) if meeting.attendees else []
        except (json.JSONDecodeError, TypeError):
            att = []
        attendees_str = "、".join(att) if att else "未指定"
        return (
            f"📋 会议纪要已归档\n"
            f"──────────────\n"
            f"编号：{meeting.meeting_no}\n"
            f"标题：{meeting.title}\n"
            f"参会人：{attendees_str}\n"
            f"──────────────"
        )
