"""EventApplication —— 事件录入编排。

职责：
- 接收 RouteResult，构建 EventCommand，调用 EventService 创建 pending 事件
- 生成确认简报回复
- 处理用户确认/取消操作
"""

import json
import logging
from typing import Optional

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import EventCommand
from ..services.event_service import EventService
from ..infrastructure.database.models import Event

logger = logging.getLogger("emily.app.event")


class EventApplication:
    """事件录入应用服务。"""

    def __init__(self, event_service: EventService):
        self.event_service = event_service
        self._journal = None  # M8c: EventJournal（由 EmilyCore 注入）

    def set_journal(self, journal) -> None:
        """注入事件日志服务（M8c）。"""
        self._journal = journal

    async def handle_event(
        self,
        route_result: RouteResult,
        user_id: str,
        message_id: str,
    ) -> HandlerResult:
        """处理事件录入意图。

        流程：
        1. 从 RouteResult 提取参数，构建 EventCommand
        2. 调用 EventService 创建 pending 事件
        3. 生成确认简报回复

        Args:
            route_result: 路由结果
            user_id: 创建者系统用户 ID
            message_id: 来源消息 ID

        Returns:
            HandlerResult: 处理结果（含确认简报文本）
        """
        try:
            # 构建 EventCommand
            cmd = self._build_command(route_result, user_id, message_id)

            # 创建 pending 事件
            event = self.event_service.create_pending_event(cmd)

            # 生成确认简报
            project_name = route_result.project_name
            reply = EventService.format_confirmation_reply(event, project_name)

            return HandlerResult(
                success=True,
                object_type="event",
                object_id=event.id,
                reply=reply,
                pending_confirmation=True,
            )

        except Exception as e:
            logger.error("Event handling failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False,
                error_code="event_create_failed",
                reply=f"事件录入失败：{e}",
            )

    def handle_confirmation(
        self,
        event_id: str,
        action: str,
    ) -> HandlerResult:
        """处理用户对 pending 事件的确认/取消。

        Args:
            event_id: 事件 UUID
            action: "confirm" 或 "cancel"

        Returns:
            HandlerResult: 处理结果
        """
        if action == "confirm":
            event = self.event_service.confirm_event(event_id)
            if event:
                # M8c: 写入项目日志
                if self._journal is not None:
                    from ._user_utils import resolve_user_name
                    user_name = resolve_user_name(event.user_id) if event.user_id else ""
                    self._journal.append(
                        name=user_name or "用户",
                        summary=f"确认录入事件：{event.title}（{event.event_no}）",
                    )
                return HandlerResult(
                    success=True,
                    object_type="event",
                    object_id=event.id,
                    reply=f"✅ 已记录该事件（{event.event_no}）",
                )
            else:
                return HandlerResult(
                    success=False,
                    error_code="confirm_failed",
                    reply="确认失败，事件不存在或已处理",
                )

        elif action == "cancel":
            event = self.event_service.cancel_event(event_id)
            if event:
                return HandlerResult(
                    success=True,
                    object_type="event",
                    object_id=event.id,
                    reply="❌ 已取消录入",
                )
            else:
                return HandlerResult(
                    success=False,
                    error_code="cancel_failed",
                    reply="取消失败，事件不存在或已处理",
                )

        else:
            return HandlerResult(
                success=False,
                error_code="unknown_action",
                reply=f"未知操作：{action}",
            )

    @staticmethod
    def _build_command(
        route_result: RouteResult,
        user_id: str,
        message_id: str,
    ) -> EventCommand:
        """从 RouteResult 构建 EventCommand。

        Args:
            route_result: 路由结果（含 LLM 提取的 data 字段）
            user_id: 创建者系统用户 ID
            message_id: 来源消息 ID

        Returns:
            EventCommand: 事件录入命令
        """
        data = route_result.data or {}

        related = data.get("related_event_ids")
        if isinstance(related, str):
            import json as _json
            try:
                related = _json.loads(related)
            except (_json.JSONDecodeError, TypeError):
                related = [related] if related else []

        return EventCommand(
            project_id=route_result.project_id,
            project_name=route_result.project_name,
            title=data.get("title", "未命名事件"),
            event_type=data.get("event_type", "general"),
            category="待分类",
            description=data.get("description", ""),
            event_date=data.get("event_date"),
            creator_id=user_id,
            source_message_id=message_id,
            related_event_ids=related if isinstance(related, list) else None,
        )
