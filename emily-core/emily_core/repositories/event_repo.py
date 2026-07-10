"""事件读写抽象层 —— 封装 SQL 细节。

Service 层只调 repo 方法，不碰 SQL。
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import Event, Project

logger = logging.getLogger("emily.repo.event")


class EventRepository:
    """事件 CRUD 操作。"""

    @staticmethod
    def generate_event_no() -> str:
        """生成人工可读编号 EVT-YYYYMMDD-NNNN。

        同一天内递增序号，基于 events 表已有记录计数。
        """
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"EVT-{today_str}-"
        with get_session() as session:
            # 查找当天已有最大编号
            last = (
                session.query(Event)
                .filter(Event.event_no.like(f"{prefix}%"))
                .order_by(Event.event_no.desc())
                .first()
            )
            if last is None:
                return f"{prefix}0001"
            # 提取序号部分并递增
            seq_str = last.event_no[len(prefix):]
            try:
                seq = int(seq_str) + 1
            except ValueError:
                seq = 1
            return f"{prefix}{seq:04d}"

    @staticmethod
    def create(
        *,
        event_no: str,
        event_type: str,
        title: str,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        message_id: Optional[str] = None,
        category: str = "待分类",
        description: Optional[str] = None,
        event_date: Optional[str] = None,
        payload: str = "{}",
        status: str = "pending",
        related_event_ids: list[str] | None = None,  # M8a
        conversation_id: Optional[str] = None,  # BUG-005: 会话 ID
    ) -> Event:
        """创建事件记录。

        Args:
            event_no: 人工可读编号
            event_type: 事件类型
            title: 事件标题
            project_id: 所属项目 UUID
            user_id: 报告人 UUID
            message_id: 来源消息 UUID
            category: 事件类别
            description: 完整描述
            event_date: 事件发生日期
            payload: LLM 提取的额外结构化数据（JSON 字符串）
            status: 初始状态（pending）
            conversation_id: 来源会话 ID（供确认流程直查）
        """
        with get_session() as session:
            import json as _json

            event = Event(
                event_no=event_no,
                event_type=event_type,
                category=category,
                project_id=project_id,
                user_id=user_id,
                message_id=message_id,
                title=title,
                description=description,
                event_date=event_date,
                payload=payload,
                status=status,
                related_event_ids=_json.dumps(related_event_ids, ensure_ascii=False)
                if related_event_ids
                else "[]",
                conversation_id=conversation_id,
            )
            session.add(event)
            session.flush()

            logger.info(
                "Event created: id=%s, no=%s, type=%s, status=%s",
                event.id, event_no, event_type, status,
            )
            return event

    @staticmethod
    def get_by_id(event_id: str) -> Optional[Event]:
        """按 ID 查找事件。"""
        with get_session() as session:
            return session.query(Event).filter(Event.id == event_id).first()

    @staticmethod
    def get_by_event_no(event_no: str) -> Optional[Event]:
        """按编号查找事件。"""
        with get_session() as session:
            return session.query(Event).filter(Event.event_no == event_no).first()

    @staticmethod
    def update_status(event_id: str, status: str) -> None:
        """更新事件状态。"""
        with get_session() as session:
            event = session.query(Event).filter(Event.id == event_id).first()
            if event:
                event.status = status
                if status == "confirmed":
                    event.confirmed_at = datetime.now(timezone.utc).isoformat()
                logger.info(
                    "Event status updated: id=%s, status=%s", event_id, status,
                )

    @staticmethod
    def update_remarks(event_id: str, remarks: str) -> None:
        """更新事件备注（用户确认时可补充）。"""
        with get_session() as session:
            event = session.query(Event).filter(Event.id == event_id).first()
            if event:
                event.remarks = remarks

    @staticmethod
    def find_pending_by_conversation(conversation_id: str) -> Optional[Event]:
        """查找会话中最近一条 pending 状态的事件。

        用于确认流程：用户在同一会话中回复"确认"/"取消"时，
        找到该会话最近 pending 的事件进行确认/取消。
        通过 message → conversation 反查。
        """
        with get_session() as session:
            return (
                session.query(Event)
                .join(Event.message_id)  # noqa: 不做 join，改用子查询
                .filter(Event.status == "pending")
                .order_by(Event.created_at.desc())
                .first()
            )

    @staticmethod
    def find_pending_by_conversation_id(conversation_id: str) -> Optional[Event]:
        """BUG-005: 通过 conversation_id 直查最近的 pending 事件。

        替代 find_pending_by_message_conversation()，不再依赖 messages 表中转。
        要求 Event 表有 conversation_id 字段（BUG-005 修复新增）。
        """
        with get_session() as session:
            return (
                session.query(Event)
                .filter(
                    Event.conversation_id == conversation_id,
                    Event.status == "pending",
                )
                .order_by(Event.created_at.desc())
                .first()
            )

    # ── M5 查询 ──

    @staticmethod
    def query_events(
        *,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        time_range: str = "all",
        status: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        """按条件查询事件（按创建时间倒序）。支持 project_ids 多项目范围过滤。"""
        from datetime import datetime, timezone, timedelta
        from ..infrastructure.database.models import Message

        with get_session() as session:
            q = session.query(Event)

            if project_ids:
                q = q.filter(Event.project_id.in_(project_ids))
            elif project_id:
                q = q.filter(Event.project_id == project_id)
            if status:
                q = q.filter(Event.status == status)

            # 时间范围过滤
            if time_range != "all":
                now = datetime.now(timezone.utc)
                if time_range == "today":
                    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                elif time_range == "this_week":
                    start = now - timedelta(days=now.weekday())
                    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                elif time_range == "this_month":
                    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                else:
                    start = None
                if start:
                    q = q.filter(Event.created_at >= start.isoformat())

            q = q.order_by(Event.created_at.desc()).limit(limit)
            return q.all()

    @staticmethod
    def count_by_status(project_id: str | None = None) -> dict[str, int]:
        """按状态统计事件数量。"""
        with get_session() as session:
            q = session.query(Event)
            if project_id:
                q = q.filter(Event.project_id == project_id)
            rows = q.all()
            counts = {}
            for row in rows:
                s = row.status or "unknown"
                counts[s] = counts.get(s, 0) + 1
            return counts

    # ── Project 查询 ──

    @staticmethod
    def find_project_by_name(name: str) -> Optional[Project]:
        """按名称查找项目（精确匹配）。"""
        with get_session() as session:
            return session.query(Project).filter(Project.name == name).first()

    @staticmethod
    def find_project_by_code(code: str) -> Optional[Project]:
        """按编码查找项目。"""
        with get_session() as session:
            return session.query(Project).filter(Project.code == code).first()

    @staticmethod
    def list_projects(status: str = "active") -> list[Project]:
        """列出所有项目（默认只返回 active）。"""
        with get_session() as session:
            return session.query(Project).filter(Project.status == status).all()
