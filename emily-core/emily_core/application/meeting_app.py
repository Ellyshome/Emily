"""MeetingApplication —— 会议记录编排。

留痕（业务事件日志）已下沉到 MeetingService.create_meeting（service 动作层统一挂载），
本层只保留 EventJournal 的项目流水（面向项目成员的 md 台账，另案）。
"""

import logging

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import MeetingCommand
from ..services.meeting_service import MeetingService

logger = logging.getLogger("emily.app.meeting")


class MeetingApplication:
    def __init__(self, meeting_service: MeetingService):
        self.meeting_service = meeting_service
        self._journal = None  # EventJournal（由 EmilyCore 注入）

    def set_journal(self, journal) -> None:
        """注入事件日志服务。"""
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
            # 写入项目日志
            if self._journal is not None:
                from ._user_utils import resolve_user_name
                user_name = resolve_user_name(cmd.creator_id) or "用户"
                self._journal.append(
                    name=user_name,
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
