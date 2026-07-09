"""MeetingApplication —— 会议记录编排。"""

import asyncio
import logging

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import MeetingCommand
from ..services.meeting_service import MeetingService

logger = logging.getLogger("emily.app.meeting")


async def _log_business_event(**kwargs) -> None:
    """非阻断写入业务事件日志。"""
    try:
        from ..infrastructure.logging.business_event_logger import BusinessEventLogger
        await BusinessEventLogger.log(**kwargs)
    except Exception:
        pass


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
                from ._user_utils import resolve_user_name
                user_name = resolve_user_name(cmd.creator_id) or "用户"
                self._journal.append(
                    name=user_name,
                    summary=f"录入会议纪要：{meeting.title}（{meeting.meeting_no}）",
                )
            # ── 进化日志：业务事件日志 ──
            from ._user_utils import resolve_user_name
            _uname = resolve_user_name(cmd.creator_id) or ""
            asyncio.ensure_future(_log_business_event(
                event_category="meeting",
                event_action="created",
                target_type="meeting",
                target_id=meeting.id,
                target_no=getattr(meeting, "meeting_no", "") or "",
                summary=f"录入会议：{meeting.title[:100]}",
                user_id=user_id,
                user_name=_uname,
                project_id=route_result.project_id or "",
            ))
            reply = MeetingService.format_reply(meeting)
            return HandlerResult(
                success=True, object_type="meeting", object_id=meeting.id, reply=reply,
            )
        except Exception as e:
            logger.error("Meeting creation failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False, error_code="meeting_create_failed", reply=f"会议归档失败：{e}",
            )
