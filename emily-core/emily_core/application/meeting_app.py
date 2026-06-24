"""MeetingApplication —— 会议记录编排。"""

import logging

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import MeetingCommand
from ..services.meeting_service import MeetingService

logger = logging.getLogger("emily.app.meeting")


class MeetingApplication:
    def __init__(self, meeting_service: MeetingService):
        self.meeting_service = meeting_service
        self._journal = None  # M8c

    def set_journal(self, journal) -> None:
        """注入事件日志服务（M8c）。"""
        self._journal = journal

    async def handle_meeting(
        self, route_result: RouteResult, user_id: str, message_id: str
    ) -> HandlerResult:
        try:
            data = route_result.data or {}
            cmd = MeetingCommand(
                project_id=route_result.project_id,
                project_name=route_result.project_name,
                title=data.get("title", "未命名会议"),
                summary=data.get("summary", ""),
                attendees=data.get("attendees") or [],
                creator_id=user_id,
                source_message_id=message_id,
            )
            meeting = self.meeting_service.create_meeting(cmd)
            # M8c: 写入项目日志
            if self._journal is not None:
                self._journal.append(
                    name=cmd.creator_id or "用户",
                    summary=f"录入会议纪要：{meeting.title}（{meeting.meeting_no}）",
                )
            reply = MeetingService.format_reply(meeting)
            return HandlerResult(
                success=True, object_type="meeting", object_id=meeting.id, reply=reply,
            )
        except Exception as e:
            logger.error("Meeting creation failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False, error_code="meeting_create_failed", reply=f"会议归档失败：{e}",
            )
