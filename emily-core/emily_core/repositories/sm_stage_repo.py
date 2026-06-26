"""SMStageRepository — CRUD for sm_stages table."""

import json
from typing import Optional

from sqlalchemy.orm import Session

from emily_core.infrastructure.database.models import SMStage
from emily_core.infrastructure.database.session import get_session


class SMStageRepository:

    @staticmethod
    def create(stage_id: int, stage_name: str, *,
               boundary_start: str = "",
               boundary_end: str = "",
               milestone: bool = False,
               session: Optional[Session] = None,
               ) -> SMStage:
        def _impl(sess: Session):
            existing = sess.query(SMStage).filter(SMStage.stage_id == stage_id).first()
            if existing:
                return existing
            stage = SMStage(
                stage_id=stage_id,
                stage_name=stage_name,
                boundary_start=boundary_start,
                boundary_end=boundary_end,
                milestone=milestone,
            )
            sess.add(stage)
            sess.flush()
            return stage

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_by_id(stage_id: int, *, session: Optional[Session] = None) -> Optional[SMStage]:
        def _impl(sess: Session):
            return sess.query(SMStage).filter(SMStage.stage_id == stage_id).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_all(*, session: Optional[Session] = None) -> list[SMStage]:
        def _impl(sess: Session):
            return sess.query(SMStage).order_by(SMStage.stage_id).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def update_progress(stage_id: int, total_nodes: int, completed_nodes: int, *,
                        session: Optional[Session] = None) -> Optional[SMStage]:
        def _impl(sess: Session):
            stage = sess.query(SMStage).filter(SMStage.stage_id == stage_id).first()
            if stage is None:
                return None
            stage.total_nodes = total_nodes
            stage.completed_nodes = completed_nodes
            stage.progress = int(completed_nodes / total_nodes * 100) if total_nodes > 0 else 0
            if stage.progress == 100:
                stage.status = "已完成"
            elif stage.progress > 0:
                stage.status = "进行中"
            sess.flush()
            return stage

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def update_critical_path(stage_id: int, critical_path: str, *,
                             session: Optional[Session] = None) -> Optional[SMStage]:
        def _impl(sess: Session):
            stage = sess.query(SMStage).filter(SMStage.stage_id == stage_id).first()
            if stage is None:
                return None
            stage.critical_path = critical_path
            sess.flush()
            return stage

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
