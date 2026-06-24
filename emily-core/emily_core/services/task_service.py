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
        logger.info("Task %s created: %s", task_no, cmd.title)
        return task
