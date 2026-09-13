"""统一事件积累写入器 —— 各业务 Service 的唯一事件写入口。

将会议/事件/任务/文件归档/流转单/节点成果/节点事件统一累积到
project_events（单表继承），字段映射集中在此，保证口径一致。
"""

import json
import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    ProjectEvent,
    ProjectEventKind,
    ProjectNode,
)
from ..repositories.project_event_repo import ProjectEventRepository

logger = logging.getLogger("emily.service.project_event_accumulator")

# meeting.status(int) → 统一字符串状态
_MEETING_STATUS_MAP = {0: "draft", 1: "confirmed", 2: "archived"}
# business_flow_orders.status(int) → 统一字符串状态
_FLOW_STATUS_MAP = {0: "draft", 1: "processing", 2: "completed", 3: "rejected", 4: "cancelled"}


def _resolve_project_id(node_id: Optional[str]) -> Optional[str]:
    """由 node_id 反查 project_id（节点成果/节点事件仅有 node_id）。"""
    if not node_id:
        return None
    with get_session() as session:
        node = session.query(ProjectNode).filter(ProjectNode.node_id == node_id).first()
        return node.project_id if node else None


class ProjectEventAccumulator:
    """统一事件积累写入器。"""

    @staticmethod
    def accumulate(*, event_kind: str, title: str, **kwargs) -> ProjectEvent:
        """通用累积入口（薄封装 Repository.create）。"""
        return ProjectEventRepository.create(event_kind=event_kind, title=title, **kwargs)

    @staticmethod
    def record_event(
        *,
        title: str,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        summary: str = "",
        status: str = "pending",
        actor_id: Optional[str] = None,
        event_type: str = "general",
        category: str = "待分类",
        occurred_at: Optional[str] = None,
        source_message_id: Optional[str] = None,
        attachments: str = "[]",
        remarks: str = "",
        related_event_ids: str = "[]",
        conversation_id: Optional[str] = None,
    ) -> ProjectEvent:
        payload = {
            "attachments": attachments,
            "remarks": remarks,
            "related_event_ids": related_event_ids,
            "conversation_id": conversation_id or "",
        }
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.EVENT,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary=summary,
            status=status,
            actor_id=actor_id,
            event_type=event_type,
            occurred_at=occurred_at,
            source_message_id=source_message_id,
            payload=payload,
            category=category,
        )

    @staticmethod
    def record_task(
        *,
        title: str,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        summary: str = "",
        status: str = "todo",
        actor_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        owner_text: Optional[str] = None,
        due_date: Optional[str] = None,
        due_text: Optional[str] = None,
        source_message_id: Optional[str] = None,
    ) -> ProjectEvent:
        payload = {"owner_text": owner_text or "", "due_text": due_text or ""}
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.TASK,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary=summary,
            status=status,
            actor_id=actor_id,
            occurred_at=due_date,
            source_message_id=source_message_id,
            payload=payload,
            owner_id=owner_id,
            due_date=due_date,
        )

    @staticmethod
    def record_meeting(
        *,
        title: str,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        summary: str = "",
        status: int = 1,
        actor_id: Optional[str] = None,
        meeting_type: int = 0,
        meeting_date: Optional[str] = None,
        location: str = "",
        host_id: Optional[str] = None,
        conclusion: str = "",
        attendees: Optional[list] = None,
        attendee_names: Optional[list] = None,
        action_items: Optional[list] = None,
        related_file_ids: Optional[list] = None,
        source_message_id: Optional[str] = None,
    ) -> ProjectEvent:
        payload = {
            "attendees": json.dumps(attendees, ensure_ascii=False) if attendees else "[]",
            "attendee_names": json.dumps(attendee_names, ensure_ascii=False) if attendee_names else "[]",
            "action_items": json.dumps(action_items, ensure_ascii=False) if action_items else "[]",
            "related_file_ids": json.dumps(related_file_ids, ensure_ascii=False) if related_file_ids else "[]",
        }
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.MEETING,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary=summary,
            status=_MEETING_STATUS_MAP.get(status, "confirmed"),
            actor_id=actor_id,
            occurred_at=meeting_date,
            source_message_id=source_message_id,
            payload=payload,
            meeting_type=meeting_type,
            meeting_date=meeting_date,
            location=location,
            host_id=host_id,
            conclusion=conclusion,
        )

    @staticmethod
    def record_file(
        *,
        title: str,
        file_id: str,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        summary: str = "",
        actor_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
        source_message_id: Optional[str] = None,
        file_no: str = "",
        file_type: str = "",
        file_category: str = "OTHER",
        purpose: str = "RECORD",
        confidentiality: int = 1,
        version: str = "V1.0",
    ) -> ProjectEvent:
        payload = {
            "file_no": file_no,
            "file_type": file_type,
            "file_category": file_category,
            "purpose": purpose,
            "confidentiality": confidentiality,
            "version": version,
        }
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.FILE,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary=summary,
            status="archived",
            actor_id=actor_id,
            occurred_at=occurred_at,
            source_message_id=source_message_id,
            payload=payload,
            file_id=file_id,
        )

    @staticmethod
    def record_flow_order(
        *,
        title: str,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        status: int = 0,
        actor_id: Optional[str] = None,
        flow_type: int = 0,
        priority: int = 1,
        occurred_at: Optional[str] = None,
        metrics: str = "{}",
        flow_records: str = "[]",
        related_file_ids: str = "[]",
        related_meeting_ids: str = "[]",
        current_node: str = "",
        current_handler_id: str = "",
        actual_finish_time: Optional[str] = None,
    ) -> ProjectEvent:
        payload = {
            "metrics": metrics,
            "flow_records": flow_records,
            "related_file_ids": related_file_ids,
            "related_meeting_ids": related_meeting_ids,
            "current_node": current_node,
            "current_handler_id": current_handler_id,
            "actual_finish_time": actual_finish_time or "",
        }
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.BUSINESS_FLOW,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary="",
            status=_FLOW_STATUS_MAP.get(status, "draft"),
            actor_id=actor_id,
            occurred_at=occurred_at,
            payload=payload,
            flow_type=flow_type,
            priority=priority,
        )

    @staticmethod
    def record_deliverable(
        *,
        title: str,
        node_id: Optional[str] = None,
        deliverable_id: Optional[str] = None,
        project_id: Optional[str] = None,
        status: str = "PENDING",
        actor_id: Optional[str] = None,
        confirmed_by: Optional[str] = None,
        confirmed_at: Optional[str] = None,
        occurred_at: Optional[str] = None,
        target_amount: str = "0.00",
        current_amount: str = "0.00",
        unit: str = "",
        is_required: bool = True,
        file_id: str = "",
        return_reason: str = "",
        attachment_file_id: str = "",
        completed_at: Optional[str] = None,
    ) -> ProjectEvent:
        if project_id is None:
            project_id = _resolve_project_id(node_id)
        payload = {
            "is_required": is_required,
            "file_id": file_id,
            "return_reason": return_reason,
            "attachment_file_id": attachment_file_id,
            "completed_at": completed_at or "",
        }
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.DELIVERABLE,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary="",
            status=status,
            actor_id=actor_id,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
            occurred_at=occurred_at,
            payload=payload,
            deliverable_id=deliverable_id,
            submission_status=status,
            target_amount=target_amount,
            current_amount=current_amount,
            unit=unit,
        )

    @staticmethod
    def record_node_event(
        *,
        title: str,
        node_id: Optional[str] = None,
        event_type: str = "",
        project_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
        old_value: str = "",
        new_value: str = "",
        remark: str = "",
    ) -> ProjectEvent:
        if project_id is None:
            project_id = _resolve_project_id(node_id)
        payload = {"remark": remark}
        return ProjectEventRepository.create(
            event_kind=ProjectEventKind.NODE_EVENT,
            title=title,
            project_id=project_id,
            node_id=node_id,
            summary="",
            status="logged",
            actor_id=actor_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
            old_value=old_value,
            new_value=new_value,
        )
