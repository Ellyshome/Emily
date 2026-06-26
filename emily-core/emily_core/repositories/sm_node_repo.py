"""SMNodeRepository — CRUD for sm_nodes and sm_node_dependencies tables.

Follows the project convention: pure @staticmethod, optional session parameter.
"""

import json
from contextlib import contextmanager
from typing import Optional

from sqlalchemy.orm import Session

from emily_core.infrastructure.database.models import SMNode, SMNodeDeliverable, SMNodeDependency
from emily_core.infrastructure.database.session import get_session


class SMNodeRepository:

    # ========================================================================
    #  Nodes
    # ========================================================================

    @staticmethod
    def create(node_id: str, node_name: str, stage_id: int, *,
               parent_section: str = "",
               node_type: str = "standard",
               owner: str = "",
               approver: str = "",
               viewers: str = "[]",
               is_milestone: bool = False,
               recurrence_type: str = "SINGLE",
               sort_order: int = 0,
               session: Optional[Session] = None,
               ) -> SMNode:
        def _impl(sess: Session) -> SMNode:
            node = SMNode(
                node_id=node_id,
                node_name=node_name,
                stage_id=stage_id,
                parent_section=parent_section,
                node_type=node_type,
                owner=owner,
                approver=approver,
                viewers=viewers,
                is_milestone=is_milestone,
                recurrence_type=recurrence_type,
                sort_order=sort_order,
            )
            sess.add(node)
            sess.flush()
            return node

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_by_node_id(node_id: str, session: Optional[Session] = None) -> Optional[SMNode]:
        def _impl(sess: Session):
            return sess.query(SMNode).filter(
                SMNode.node_id == node_id,
                SMNode.is_deleted == False,
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_all(*, session: Optional[Session] = None) -> list[SMNode]:
        def _impl(sess: Session):
            return sess.query(SMNode).filter(
                SMNode.is_deleted == False,
            ).order_by(SMNode.sort_order, SMNode.node_id).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_by_stage(stage_id: int, *, session: Optional[Session] = None) -> list[SMNode]:
        def _impl(sess: Session):
            return sess.query(SMNode).filter(
                SMNode.stage_id == stage_id,
                SMNode.is_deleted == False,
            ).order_by(SMNode.sort_order, SMNode.node_id).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_by_status(status: str, *, session: Optional[Session] = None) -> list[SMNode]:
        def _impl(sess: Session):
            return sess.query(SMNode).filter(
                SMNode.status == status,
                SMNode.is_deleted == False,
            ).order_by(SMNode.node_id).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_stale(*, statuses: list[str], older_than_iso: str,
                   session: Optional[Session] = None) -> list[SMNode]:
        """Find nodes stuck in given statuses whose updated_at is older than threshold.

        Used by ProjectAgent for stale node detection — e.g. IN_PROGRESS nodes
        unchanged for 14+ days should trigger an alert.
        """
        def _impl(sess: Session):
            return sess.query(SMNode).filter(
                SMNode.status.in_(statuses),
                SMNode.updated_at < older_than_iso,
                SMNode.is_deleted == False,
            ).order_by(SMNode.stage_id, SMNode.node_id).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_milestones_near_deadline(*, now_iso: str, warn_before_days: int,
                                      session: Optional[Session] = None) -> list[SMNode]:
        """Find milestone nodes whose planned_end_date is within warn_before_days of now.

        Used by ProjectAgent for milestone deadline warnings.
        """
        from datetime import datetime, timedelta, timezone
        try:
            now_dt = datetime.fromisoformat(now_iso)
        except (ValueError, TypeError):
            now_dt = datetime.now(timezone.utc)
        cutoff_dt = now_dt + timedelta(days=warn_before_days)

        def _impl(sess: Session):
            return sess.query(SMNode).filter(
                SMNode.is_milestone == True,
                SMNode.status.in_(["NOT_STARTED", "IN_PROGRESS", "BLOCKED", "DELAYED"]),
                SMNode.planned_end_date != "",
                SMNode.planned_end_date <= cutoff_dt.isoformat(),
                SMNode.is_deleted == False,
            ).order_by(SMNode.planned_end_date, SMNode.node_id).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def count(*, session: Optional[Session] = None) -> int:
        def _impl(sess: Session):
            return sess.query(SMNode).filter(SMNode.is_deleted == False).count()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def update_status(node_id: str, new_status: str, *,
                      actual_start_date: Optional[str] = None,
                      actual_end_date: Optional[str] = None,
                      block_reason: Optional[str] = None,
                      delay_reason: Optional[str] = None,
                      risk_level: Optional[str] = None,
                      session: Optional[Session] = None,
                      ) -> Optional[SMNode]:
        def _impl(sess: Session):
            node = sess.query(SMNode).filter(
                SMNode.node_id == node_id,
                SMNode.is_deleted == False,
            ).first()
            if node is None:
                return None

            node.status = new_status
            if actual_start_date is not None:
                node.actual_start_date = actual_start_date
            if actual_end_date is not None:
                node.actual_end_date = actual_end_date
            if block_reason is not None:
                node.block_reason = block_reason
            if delay_reason is not None:
                node.delay_reason = delay_reason
            if risk_level is not None:
                node.risk_level = risk_level
            sess.flush()
            return node

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def update_precondition_score(node_id: str, score: int, *,
                                  session: Optional[Session] = None) -> bool:
        def _impl(sess: Session):
            node = sess.query(SMNode).filter(
                SMNode.node_id == node_id,
                SMNode.is_deleted == False,
            ).first()
            if node is None:
                return False
            node.precondition_score = score
            return True

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ========================================================================
    #  Dependencies
    # ========================================================================

    @staticmethod
    def create_dependency(from_node_id: str, to_node_id: str, *,
                          weight: float = 1.0,
                          required: bool = True,
                          session: Optional[Session] = None,
                          ) -> SMNodeDependency:
        def _impl(sess: Session):
            # Idempotent — skip if already exists
            existing = sess.query(SMNodeDependency).filter(
                SMNodeDependency.from_node_id == from_node_id,
                SMNodeDependency.to_node_id == to_node_id,
            ).first()
            if existing:
                return existing
            dep = SMNodeDependency(
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                weight=str(weight),
                required=required,
            )
            sess.add(dep)
            sess.flush()
            return dep

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_dependencies(from_node_id: str, *,
                         session: Optional[Session] = None) -> list[SMNodeDependency]:
        """Get all dependencies WHERE from_node_id is the downstream node (this node depends on to_node_id)."""
        def _impl(sess: Session):
            return sess.query(SMNodeDependency).filter(
                SMNodeDependency.from_node_id == from_node_id,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_downstream_nodes(to_node_id: str, *,
                             session: Optional[Session] = None) -> list[SMNodeDependency]:
        """Get all nodes that depend ON to_node_id (downstream = nodes WHERE from_node_id references this)."""
        def _impl(sess: Session):
            return sess.query(SMNodeDependency).filter(
                SMNodeDependency.to_node_id == to_node_id,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ========================================================================
    #  Deliverables
    # ========================================================================

    @staticmethod
    def create_deliverable(node_id: str, deliverable_name: str, *,
                           required: bool = True,
                           session: Optional[Session] = None,
                           ) -> SMNodeDeliverable:
        def _impl(sess: Session):
            existing = sess.query(SMNodeDeliverable).filter(
                SMNodeDeliverable.node_id == node_id,
                SMNodeDeliverable.deliverable_name == deliverable_name,
            ).first()
            if existing:
                return existing
            dlv = SMNodeDeliverable(
                node_id=node_id,
                deliverable_name=deliverable_name,
                required=required,
            )
            sess.add(dlv)
            sess.flush()
            return dlv

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_deliverables(node_id: str, *,
                         session: Optional[Session] = None) -> list[SMNodeDeliverable]:
        def _impl(sess: Session):
            return sess.query(SMNodeDeliverable).filter(
                SMNodeDeliverable.node_id == node_id,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
