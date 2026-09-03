"""EventApplication —— 事件录入编排。

职责：
- 接收 RouteResult，构建 EventCommand，调用 EventService 创建 pending 事件
- 生成确认简报回复
- 处理用户确认/取消操作
"""

import asyncio
import json
import logging
from typing import Optional

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import EventCommand
from ..services.event_service import EventService
from ..infrastructure.database.models import Event

logger = logging.getLogger("emily.app.event")


def _log_business_event(**kwargs) -> None:
    """非阻断写入业务事件日志。在调用时立即捕获 Pipeline 上下文。"""
    try:
        from ..infrastructure.logging.business_event_logger import BusinessEventLogger
        # ensure_future 延迟执行，此时 Pipeline 上下文可能已清理，因此在此立即捕获
        ctx = BusinessEventLogger._current_context
        kwargs.setdefault("pipeline_run_id", ctx.get("pipeline_run_id", ""))
        kwargs.setdefault("conversation_id", ctx.get("conversation_id", ""))
        asyncio.ensure_future(BusinessEventLogger.log(**kwargs))
    except Exception as e:
        logger.debug("_log_business_event failed: %s", e, exc_info=True)


class EventApplication:
    """事件录入应用服务。"""

    def __init__(self, event_service: EventService):
        self.event_service = event_service
        self._journal = None  # EventJournal（由 EmilyCore 注入）

    def set_journal(self, journal) -> None:
        """注入事件日志服务。"""
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

            # ── 进化日志：业务事件日志 ──
            _log_business_event(
                event_category="event",
                event_action="created",
                target_type="event",
                target_id=event.id,
                target_no=getattr(event, "event_no", "") or "",
                summary=f"创建事件：{event.title[:100]}",
                user_id=user_id,
                project_id=route_result.project_id or "",
            )

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
        confirmed_by: Optional[str] = None,
    ) -> HandlerResult:
        """处理用户对 pending 事件的确认/取消。

        Args:
            event_id: 事件 UUID
            action: "confirm" 或 "cancel"
            confirmed_by: 认证人 UUID（确认时写入 events.confirmed_by，溯源到人）

        Returns:
            HandlerResult: 处理结果
        """
        if action == "confirm":
            event = self.event_service.confirm_event(event_id, confirmed_by=confirmed_by)
            if event:
                # 写入项目日志
                if self._journal is not None:
                    from ._user_utils import resolve_user_name
                    user_name = resolve_user_name(event.user_id) if event.user_id else ""
                    self._journal.append(
                        name=user_name or "用户",
                        summary=f"确认录入事件：{event.title}（{event.event_no}）",
                    )
                # ── 进化日志：业务事件日志 ──
                _log_business_event(
                    event_category="event",
                    event_action="confirmed",
                    target_type="event",
                    target_id=event.id,
                    target_no=getattr(event, "event_no", "") or "",
                    summary=f"确认事件：{event.title[:100]}",
                    user_id=confirmed_by or event.user_id or "",  # BUG 修复：认证操作人优先，而非录入人
                    project_id=getattr(event, "project_id", "") or "",
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
            conversation_id=data.get("_conversation_id", ""),  # BUG-005: 透传会话 ID
        )
