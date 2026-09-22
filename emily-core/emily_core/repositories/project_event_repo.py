"""统一项目事件读写层 —— 封装 project_events 单表继承的 CRUD 与查询。

所有业务事件（会议/事件/任务/文件归档/流转单/节点成果/节点事件）
统一经由本仓库读写，形成项目维度的统一事件积累（时间线）。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    ProjectEvent,
    ProjectEventKind,
    UNASSIGNED_NODE_ID,
    EventRecord,
    TaskRecord,
    MeetingRecord,
    FileRecord,
    FlowOrderRecord,
    DeliverableRecord,
    NodeEventRecord,
)

logger = logging.getLogger("emily.repo.project_event")

# event_kind → 子类映射（单表继承 polymorphic_identity 需要正确的子类实例）
_KIND_CLASS = {
    ProjectEventKind.EVENT: EventRecord,
    ProjectEventKind.TASK: TaskRecord,
    ProjectEventKind.MEETING: MeetingRecord,
    ProjectEventKind.FILE: FileRecord,
    ProjectEventKind.BUSINESS_FLOW: FlowOrderRecord,
    ProjectEventKind.DELIVERABLE: DeliverableRecord,
    ProjectEventKind.NODE_EVENT: NodeEventRecord,
}


class ProjectEventRepository:
    """统一项目事件 CRUD 与查询。"""

    @staticmethod
    def generate_event_no() -> str:
        """生成统一编号 PE-YYYYMMDD-NNNN（同一天全局递增，跨类型唯一）。"""
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"PE-{today_str}-"
        with get_session() as session:
            last = (
                session.query(ProjectEvent)
                .filter(ProjectEvent.event_no.like(f"{prefix}%"))
                .order_by(ProjectEvent.event_no.desc())
                .first()
            )
            if last is None:
                return f"{prefix}0001"
            seq_str = last.event_no[len(prefix):]
            try:
                seq = int(seq_str) + 1
            except ValueError:
                seq = 1
            return f"{prefix}{seq:04d}"

    @staticmethod
    def create(
        *,
        event_kind: str,
        title: str,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        summary: str = "",
        status: str = "pending",
        actor_id: Optional[str] = None,
        event_type: str = "",
        occurred_at: Optional[str] = None,
        source_message_id: Optional[str] = None,
        payload: dict | None = None,
        **kind_fields,
    ) -> ProjectEvent:
        """创建统一项目事件（按 event_kind 落为对应子类）。

        Args:
            event_kind: ProjectEventKind 之一
            title: 事件标题
            project_id: 归属项目
            node_id: 归属全景节点（写入侧须解析为真实节点；未归类事件由
                ProjectEventAccumulator 兜底到本项目「未归类收容节点」）
            kind_fields: 子类专属字段（如 meeting_type / submission_status / flow_type）
        """
        cls = _KIND_CLASS.get(event_kind)
        if cls is None:
            raise ValueError(f"未知事件类型 event_kind={event_kind}")

        with get_session() as session:
            evt = cls(
                event_no=ProjectEventRepository.generate_event_no(),
                event_kind=event_kind,
                project_id=project_id,
                node_id=node_id or "",
                title=title,
                summary=summary,
                status=status,
                actor_id=actor_id,
                event_type=event_type,
                occurred_at=occurred_at,
                source_message_id=source_message_id,
                payload=json.dumps(payload, ensure_ascii=False) if payload else "{}",
            )
            for k, v in kind_fields.items():
                if hasattr(evt, k):
                    setattr(evt, k, v)
            session.add(evt)
            session.flush()
            logger.info(
                "ProjectEvent created: no=%s kind=%s status=%s",
                evt.event_no, event_kind, status,
            )
            return evt

    @staticmethod
    def get_by_id(event_id: str) -> Optional[ProjectEvent]:
        with get_session() as session:
            return session.query(ProjectEvent).filter(ProjectEvent.id == event_id).first()

    @staticmethod
    def get_by_event_no(event_no: str) -> Optional[ProjectEvent]:
        with get_session() as session:
            return session.query(ProjectEvent).filter(ProjectEvent.event_no == event_no).first()

    @staticmethod
    def query_timeline(
        *,
        project_id: Optional[str] = None,
        node_id: Optional[str] = None,
        kinds: Optional[list[str]] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[ProjectEvent]:
        """查询统一事件时间线（按发生时间倒序）。

        Args:
            project_id: 按项目过滤
            node_id: 按全景节点过滤
            kinds: 按事件类型过滤（ProjectEventKind 列表）
            status: 按状态过滤
        """
        with get_session() as session:
            q = session.query(ProjectEvent)
            if project_id:
                q = q.filter(ProjectEvent.project_id == project_id)
            if node_id:
                q = q.filter(ProjectEvent.node_id == node_id)
            if kinds:
                q = q.filter(ProjectEvent.event_kind.in_(kinds))
            if status:
                q = q.filter(ProjectEvent.status == status)
            q = q.order_by(ProjectEvent.created_at.desc()).limit(limit)
            return q.all()

    @staticmethod
    def update_status(event_id: str, status: str, confirmed_by: Optional[str] = None) -> None:
        """更新事件状态；确认时记录认证人与认证时间。"""
        with get_session() as session:
            evt = session.query(ProjectEvent).filter(ProjectEvent.id == event_id).first()
            if evt:
                evt.status = status
                if status == "confirmed":
                    evt.confirmed_at = datetime.now(timezone.utc).isoformat()
                    if confirmed_by:
                        evt.confirmed_by = confirmed_by

    @staticmethod
    def reassign_node(event_id: str, node_id: str, operator_id: str = "",
                      remark: str = "") -> dict:
        """事件归位：改挂到指定全景节点，并留下改挂痕迹（US-16.6）。

        留痕内容：改挂前归属 / 改挂后归属 / 操作人 / 操作时间 / 备注，
        追加进事件自身 payload 的 `reassign_history`（事件维度可查，与节点迁正留痕分列）。

        Returns:
            {"ok": bool, "before": str, "after": str, "reason": str}
        """
        with get_session() as session:
            evt = session.query(ProjectEvent).filter(ProjectEvent.id == event_id).first()
            if evt is None:
                return {"ok": False, "before": "", "after": "", "reason": "事件不存在"}
            before = evt.node_id or ""
            if before == node_id:
                return {"ok": False, "before": before, "after": node_id, "reason": "事件已归属该节点"}
            evt.node_id = node_id
            try:
                payload = json.loads(evt.payload or "{}")
                if not isinstance(payload, dict):
                    payload = {"_raw_payload": payload}
            except Exception:
                payload = {}
            history = payload.get("reassign_history")
            if not isinstance(history, list):
                history = []
            history.append({
                "from_node_id": before,
                "to_node_id": node_id,
                "operator_id": operator_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "remark": remark,
            })
            payload["reassign_history"] = history
            evt.payload = json.dumps(payload, ensure_ascii=False)
            logger.info("ProjectEvent reassigned: %s %s -> node %s (by %s)",
                        event_id, before, node_id, operator_id)
            return {"ok": True, "before": before, "after": node_id, "reason": ""}

    @staticmethod
    def count_by_kind(project_id: Optional[str] = None) -> dict[str, int]:
        """按事件类型统计数量。"""
        with get_session() as session:
            q = session.query(ProjectEvent)
            if project_id:
                q = q.filter(ProjectEvent.project_id == project_id)
            rows = q.all()
            counts: dict[str, int] = {}
            for row in rows:
                k = row.event_kind or "unknown"
                counts[k] = counts.get(k, 0) + 1
            return counts
