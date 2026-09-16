"""事件业务服务 —— 事件录入的领域逻辑。

M3 职责：
- 创建 pending 事件（待用户确认）
- 确认 / 取消事件
- 生成事件简报
"""

import json
import logging
from typing import Optional

from ..repositories.event_repo import EventRepository
from ..adapters.standard.command import EventCommand
from ..infrastructure.database.models import Event, Project
from ..infrastructure.logging.audit import audited

logger = logging.getLogger("emily.service.event")


class EventService:
    """事件业务服务。"""

    def __init__(self):
        self.repo = EventRepository()

    @audited(
        category="event",
        action="created",
        target_type="event",
        actor_arg="cmd.creator_id",
        target_result_attr="id",
        summary_arg="cmd.title",
        summary="创建事件：{summary}",
    )
    def create_pending_event(self, cmd: EventCommand) -> Event:
        """创建 pending 状态的事件记录（待用户确认）。

        Args:
            cmd: 事件录入命令（包含 LLM 提取的参数）

        Returns:
            Event: 已持久化的事件记录（status=pending）
        """
        # 生成编号
        event_no = self.repo.generate_event_no()

        # 解析项目 ID
        project_id = cmd.project_id
        if not project_id and cmd.project_name:
            project = self.repo.find_project_by_name(cmd.project_name)
            if project:
                project_id = project.id

        # 构建额外数据
        payload = {
            "project_name": cmd.project_name,
            "source": "llm_extract",
        }

        event = self.repo.create(
            event_no=event_no,
            event_type=cmd.event_type,
            title=cmd.title,
            project_id=project_id,
            user_id=cmd.creator_id or None,
            message_id=cmd.source_message_id or None,
            category=cmd.category,
            description=cmd.description,
            event_date=cmd.event_date,
            payload=json.dumps(payload, ensure_ascii=False),
            status="pending",
            related_event_ids=cmd.related_event_ids,  # M8a
            conversation_id=cmd.conversation_id or None,  # BUG-005: 写入会话 ID
        )

        # 双写：同步累积到统一项目事件（project_events）
        try:
            from ..services.project_event_accumulator import ProjectEventAccumulator
            ProjectEventAccumulator.record_event(
                title=cmd.title,
                project_id=project_id,
                node_id=cmd.node_id or None,
                summary=cmd.description or "",
                status="pending",
                actor_id=cmd.creator_id or None,
                event_type=cmd.event_type or "general",
                category=cmd.category or "待分类",
                occurred_at=cmd.event_date,
                source_message_id=cmd.source_message_id or None,
                conversation_id=cmd.conversation_id or None,
            )
        except Exception as e:  # 双写失败不阻塞主流程
            logger.warning("ProjectEvent double-write failed: %s", e)

        logger.info(
            "Pending event created: no=%s, title=%s, project=%s",
            event_no, cmd.title, cmd.project_name,
        )
        return event

    def confirm_event(self, event_id: str, confirmed_by: Optional[str] = None) -> Optional[Event]:
        """确认事件（pending → confirmed）。

        Args:
            event_id: 事件 UUID
            confirmed_by: 认证人 UUID（溯源到人；谁确认的）

        Returns:
            Event: 更新后的事件，或 None（找不到/非 pending）
        """
        event = self.repo.get_by_id(event_id)
        if not event:
            logger.warning("Event not found: %s", event_id)
            return None
        if event.status != "pending":
            logger.warning("Event not pending: id=%s, status=%s", event_id, event.status)
            return None

        self.repo.update_status(event_id, "confirmed", confirmed_by=confirmed_by)
        logger.info("Event confirmed: id=%s", event_id)
        return self.repo.get_by_id(event_id)

    def cancel_event(self, event_id: str) -> Optional[Event]:
        """取消事件（pending → cancelled）。

        Args:
            event_id: 事件 UUID

        Returns:
            Event: 更新后的事件，或 None
        """
        event = self.repo.get_by_id(event_id)
        if not event:
            logger.warning("Event not found: %s", event_id)
            return None
        if event.status != "pending":
            logger.warning("Event not pending: id=%s, status=%s", event_id, event.status)
            return None

        self.repo.update_status(event_id, "cancelled")
        logger.info("Event cancelled: id=%s", event_id)
        return self.repo.get_by_id(event_id)

    def find_pending_by_conversation(self, conversation_id: str) -> Optional[Event]:
        """查找会话中最近的 pending 事件。

        BUG-005 修复：改用 conversation_id 直查，不再依赖 messages 表中转。
        """
        return self.repo.find_pending_by_conversation_id(conversation_id)

    @staticmethod
    def node_label(node_id: Optional[str]) -> str:
        """节点编号 → 可读标签「名称（编号）」。

        查不到节点名时退化为编号；未指定归属返回空串（此时由 Agent 在对话中
        按业务流程要求口头说明"暂存待归类"，不在此暴露内部归类概念）。
        """
        if not node_id:
            return ""
        name = ""
        try:
            from ..repositories.node_repo import ProjectNodeRepo
            node = ProjectNodeRepo.get_by_node_id(node_id)
            name = getattr(node, "node_name", "") if node else ""
        except Exception as e:  # 节点名仅用于展示，查询失败不阻断录入
            logger.debug("node_label lookup failed node=%s: %s", node_id, e)
        return f"{name}（{node_id}）" if name else node_id

    @staticmethod
    def format_confirmation_reply(
        event: Event, project_name: Optional[str] = None, node_id: Optional[str] = None,
    ) -> str:
        """生成事件确认简报。

        Args:
            event: 事件记录
            project_name: 项目名称（优先使用参数，其次从 payload 提取）
            node_id: 归属全景节点编号（非空时在简报中回显，便于用户确认归属）

        Returns:
            str: 格式化的确认简报文本
        """
        # 提取项目名
        if not project_name:
            try:
                payload = json.loads(event.payload) if event.payload else {}
                project_name = payload.get("project_name", "未指定")
            except (json.JSONDecodeError, TypeError):
                project_name = "未指定"

        # 事件日期
        event_date = event.event_date or "未指定"

        lines = [
            "📋 事件录入确认",
            "──────────────",
            f"简述：{event.title}",
            f"项目：{project_name}",
        ]
        label = EventService.node_label(node_id)
        if label:
            lines.append(f"归属：{label}")
        lines += [
            f"时间：{event_date}",
            f"编号：{event.event_no}",
            "──────────────",
            "回复\"确认\"录入，回复\"取消\"放弃",
        ]
        return "\n".join(lines)

    def get_by_id(self, event_id: str) -> Optional[Event]:
        """按 ID 获取事件（包装 repo 调用，保持分层原则）。

        Args:
            event_id: 事件 UUID

        Returns:
            Event: 事件记录，或 None
        """
        return self.repo.get_by_id(event_id)

    def get_project_list_text(self) -> str:
        """获取项目列表的文本描述，供工具结果上下文注入。"""
        projects = self.repo.list_projects(status="active")
        if not projects:
            return "暂无项目"
        lines = []
        for p in projects:
            code = p.code or ""
            lines.append(f"- {p.name}" + (f"（{code}）" if code else ""))
        return "\n".join(lines)
