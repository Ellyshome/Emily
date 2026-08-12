"""专家Agent Repository 层 —— ExpertRepository + ExpertApprovalRepository。

全 sync，async Service/节点通过 asyncio.to_thread() 包裹调用。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..infrastructure.database.models import Expert, ExpertApproval
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.expert_repo")


# ══════════════════════════════════════════════════════════════════════════════
# ExpertRepository
# ══════════════════════════════════════════════════════════════════════════════

class ExpertRepository:

    @staticmethod
    def create(*, expert_no: str, name: str, function_desc: str,
               manual_path: str, task_manual_path: str,
               review_schema: dict | None = None,
               sop_id: str = "", creator_id: str) -> Expert:
        """创建 PENDING 专家记录。"""
        import json
        with get_session() as session:
            expert = Expert(
                expert_no=expert_no,
                name=name,
                function_desc=function_desc,
                manual_path=manual_path,
                task_manual_path=task_manual_path,
                review_schema=json.dumps(review_schema or {}, ensure_ascii=False),
                sop_id=sop_id or "",
                status="PENDING",
                creator_id=creator_id,
            )
            session.add(expert)
            session.flush()
            logger.info("Expert created: %s (%s)", expert.expert_no, expert.name)
            return expert

    @staticmethod
    def get_by_id(expert_id: str) -> Expert | None:
        """按 UUID 查。"""
        with get_session() as session:
            return session.query(Expert).filter(Expert.id == expert_id).first()

    @staticmethod
    def get_by_expert_no(expert_no: str) -> Expert | None:
        """按业务编号查。"""
        with get_session() as session:
            return session.query(Expert).filter(Expert.expert_no == expert_no).first()

    @staticmethod
    def get_by_sop_id(sop_id: str) -> Expert | None:
        """按 SOP ID 查首个 ACTIVE 专家。"""
        with get_session() as session:
            return (
                session.query(Expert)
                .filter(Expert.sop_id == sop_id, Expert.status == "ACTIVE")
                .first()
            )

    @staticmethod
    def list_by_status(status: str) -> list[Expert]:
        """按状态列表。"""
        with get_session() as session:
            return (
                session.query(Expert)
                .filter(Expert.status == status)
                .order_by(Expert.created_at.desc())
                .all()
            )

    @staticmethod
    def list_active() -> list[Expert]:
        """全部 ACTIVE 专家。"""
        return ExpertRepository.list_by_status("ACTIVE")

    @staticmethod
    def update_status(expert_id: str, new_status: str,
                      approver_id: str = "") -> Expert | None:
        """状态机流转 + 写 approver_id / approved_at。"""
        with get_session() as session:
            expert = session.query(Expert).filter(Expert.id == expert_id).first()
            if not expert:
                logger.warning("Expert not found: %s", expert_id)
                return None
            expert.status = new_status
            if approver_id:
                expert.approver_id = approver_id
            if new_status in ("ACTIVE", "REJECTED"):
                expert.approved_at = datetime.now(timezone.utc).isoformat()
            session.flush()
            logger.info("Expert %s status → %s", expert.expert_no, new_status)
            return expert

    @staticmethod
    def generate_expert_no() -> str:
        """生成 EXP-001 递增编号。"""
        with get_session() as session:
            last = (
                session.query(Expert)
                .order_by(Expert.created_at.desc())
                .first()
            )
            if last and last.expert_no:
                try:
                    num = int(last.expert_no.replace("EXP-", "")) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            return f"EXP-{num:03d}"


# ══════════════════════════════════════════════════════════════════════════════
# ExpertApprovalRepository
# ══════════════════════════════════════════════════════════════════════════════

class ExpertApprovalRepository:

    @staticmethod
    def create(*, expert_id: str, action: str,
               operator_id: str, reason: str = "") -> ExpertApproval:
        """记录一次审批/启停操作。"""
        with get_session() as session:
            approval = ExpertApproval(
                expert_id=expert_id,
                action=action,
                operator_id=operator_id,
                reason=reason or "",
            )
            session.add(approval)
            session.flush()
            logger.info("ExpertApproval created: expert=%s action=%s", expert_id, action)
            return approval

    @staticmethod
    def list_by_expert(expert_id: str) -> list[ExpertApproval]:
        """查专家操作历史。"""
        with get_session() as session:
            return (
                session.query(ExpertApproval)
                .filter(ExpertApproval.expert_id == expert_id)
                .order_by(ExpertApproval.created_at.desc())
                .all()
            )
