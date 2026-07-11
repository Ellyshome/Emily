"""TaskApplication —— 任务创建编排。"""

import asyncio
import logging

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import TaskCommand
from ..services.task_service import TaskService

logger = logging.getLogger("emily.app.task")


def _log_business_event(**kwargs) -> None:
    """非阻断写入业务事件日志。在调用时立即捕获 Pipeline 上下文。"""
    try:
        from ..infrastructure.logging.business_event_logger import BusinessEventLogger
        # ensure_future 延迟执行，此时 Pipeline 上下文可能已清理，因此在此立即捕获
        ctx = BusinessEventLogger._current_context
        kwargs.setdefault("pipeline_run_id", ctx.get("pipeline_run_id", ""))
        kwargs.setdefault("conversation_id", ctx.get("conversation_id", ""))
        asyncio.ensure_future(BusinessEventLogger.log(**kwargs))
    except Exception:
        pass


class TaskApplication:
    def __init__(self, task_service: TaskService):
        self.task_service = task_service
        self._journal = None  # EventJournal（由 EmilyCore 注入）

    def set_journal(self, journal) -> None:
        """注入事件日志服务。"""
        self._journal = journal

    async def handle_task(
        self, route_result: RouteResult, user_id: str, message_id: str
    ) -> HandlerResult:
        try:
            data = route_result.data or {}
            cmd = TaskCommand(
                project_id=route_result.project_id,
                project_name=route_result.project_name,
                title=data.get("title", "未命名任务"),
                description=data.get("description", ""),
                assignee_text=data.get("assignee", ""),
                due_date=data.get("due_date"),
                due_text=data.get("due_text", ""),
                creator_id=user_id,
                source_message_id=message_id,
            )
            task = self.task_service.create_task(cmd)
            # 写入项目日志
            if self._journal is not None:
                from ._user_utils import resolve_user_name
                user_name = resolve_user_name(cmd.creator_id) or "用户"
                assignee = cmd.assignee_text or ""
                summary = f"创建任务：{task.title}（{task.task_no}）"
                if assignee:
                    summary += f"，负责人{assignee}"
                self._journal.append(name=user_name, summary=summary)
            # ── 进化日志：业务事件日志 ──
            from ._user_utils import resolve_user_name
            _uname = resolve_user_name(cmd.creator_id) or ""
            _log_business_event(
                event_category="task",
                event_action="created",
                target_type="task",
                target_id=task.id,
                target_no=getattr(task, "task_no", "") or "",
                summary=f"创建任务：{task.title[:100]}",
                user_id=user_id,
                user_name=_uname,
                project_id=route_result.project_id or "",
            )
            reply = f"✅ 已创建任务（{task.task_no}）\n──────────────\n标题：{task.title}"
            if task.owner_text:
                reply += f"\n负责人：{task.owner_text}"
            if task.due_date:
                reply += f"\n截止日期：{task.due_date}"
            return HandlerResult(
                success=True, object_type="task", object_id=task.id, reply=reply,
            )
        except Exception as e:
            logger.error("Task creation failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False, error_code="task_create_failed", reply=f"任务创建失败：{e}",
            )
