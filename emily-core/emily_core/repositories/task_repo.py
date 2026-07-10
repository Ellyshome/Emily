"""TaskRepository —— 任务表 CRUD 抽象层。"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import Task

logger = logging.getLogger("emily.repo.task")


class TaskRepository:
    """任务 CRUD 操作。"""

    @staticmethod
    def generate_task_no() -> str:
        """生成任务编号 TSK-YYYYMMDD-NNNN。"""
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"TSK-{today_str}-"
        with get_session() as session:
            last = (
                session.query(Task)
                .filter(Task.task_no.like(f"{prefix}%"))
                .order_by(Task.task_no.desc())
                .first()
            )
            if last is None:
                return f"{prefix}0001"
            seq_str = last.task_no[len(prefix):]
            try:
                seq = int(seq_str) + 1
            except ValueError:
                seq = 1
            return f"{prefix}{seq:04d}"

    @staticmethod
    def create(
        *,
        task_no: str,
        title: str,
        project_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        description: Optional[str] = None,
        owner_id: Optional[str] = None,
        owner_text: Optional[str] = None,
        status: str = "todo",
        due_date: Optional[str] = None,
        due_text: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Task:
        with get_session() as session:
            task = Task(
                task_no=task_no,
                project_id=project_id,
                source_message_id=source_message_id,
                title=title,
                description=description,
                owner_id=owner_id,
                owner_text=owner_text,
                status=status,
                due_date=due_date,
                due_text=due_text,
                created_by=created_by,
            )
            session.add(task)
            session.flush()
            logger.info("Task created: no=%s, title=%s", task_no, title)
            return task

    @staticmethod
    def get_by_id(task_id: str) -> Optional[Task]:
        with get_session() as session:
            return session.query(Task).filter(Task.id == task_id).first()

    @staticmethod
    def get_by_task_no(task_no: str) -> Optional[Task]:
        with get_session() as session:
            return session.query(Task).filter(Task.task_no == task_no).first()

    # ── M5 查询 ──

    @staticmethod
    def query_tasks(
        *,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        time_range: str = "all",
        status: str | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """按条件查询任务（按创建时间倒序）。支持 project_ids 多项目范围过滤。"""
        with get_session() as session:
            q = session.query(Task)

            if project_ids:
                q = q.filter(Task.project_id.in_(project_ids))
            elif project_id:
                q = q.filter(Task.project_id == project_id)
            if status:
                q = q.filter(Task.status == status)
            if assignee:
                q = q.filter(Task.owner_text.like(f"%{assignee}%"))

            if time_range != "all":
                now = datetime.now(timezone.utc)
                start = _resolve_time_start(time_range, now)
                if start:
                    q = q.filter(Task.created_at >= start.isoformat())

            q = q.order_by(Task.created_at.desc()).limit(limit)
            return q.all()

    @staticmethod
    def count_by_status(project_id: str | None = None) -> dict[str, int]:
        """按状态统计任务数量。"""
        with get_session() as session:
            q = session.query(Task)
            if project_id:
                q = q.filter(Task.project_id == project_id)
            rows = q.all()
            counts = {}
            for row in rows:
                s = row.status or "unknown"
                counts[s] = counts.get(s, 0) + 1
            return counts


def _resolve_time_start(time_range: str, now) -> Optional[datetime]:
    """将时间范围字符串解析为起始时间（供各 repo 复用）。"""
    if time_range == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "this_week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "this_month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None
