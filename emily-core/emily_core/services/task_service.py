"""TaskService —— 任务业务逻辑。"""

import logging
from typing import Optional

from ..repositories.task_repo import TaskRepository
from ..adapters.standard.command import TaskCommand
from ..infrastructure.database.models import Task

logger = logging.getLogger("emily.service.task")


class TaskService:
    def __init__(self):
        self.repo = TaskRepository()

    def create_task(self, cmd: TaskCommand) -> Task:
        task_no = self.repo.generate_task_no()
        task = self.repo.create(
            task_no=task_no,
            title=cmd.title,
            project_id=cmd.project_id or None,
            source_message_id=cmd.source_message_id or None,
            description=cmd.description or None,
            owner_text=cmd.assignee_text or None,
            due_date=cmd.due_date,
            due_text=cmd.due_text or None,
            created_by=cmd.creator_id or None,
            status="todo",
        )

        # 双写：同步累积到统一项目事件（project_events）
        try:
            from ..services.project_event_accumulator import ProjectEventAccumulator
            ProjectEventAccumulator.record_task(
                title=cmd.title,
                project_id=cmd.project_id or None,
                summary=cmd.description or "",
                status="todo",
                actor_id=cmd.creator_id or None,
                owner_text=cmd.assignee_text or None,
                due_date=cmd.due_date,
                due_text=cmd.due_text or None,
                source_message_id=cmd.source_message_id or None,
            )
        except Exception as e:
            logger.warning("ProjectEvent double-write failed: %s", e)

        logger.info("Task %s created: %s", task_no, cmd.title)
        return task
