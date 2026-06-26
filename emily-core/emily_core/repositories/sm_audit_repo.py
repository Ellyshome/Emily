"""SMAuditRepository — write and query sm_status_history and sm_audit_logs."""

import json
from typing import Optional

from sqlalchemy.orm import Session

from emily_core.infrastructure.database.models import SMAuditLog, SMStatusHistory
from emily_core.infrastructure.database.session import get_session


class SMAuditRepository:

    # ========================================================================
    #  Status History (state change audit)
    # ========================================================================

    @staticmethod
    def write_status_history(node_id: str, from_status: str, to_status: str, *,
                             operator_id: str = "",
                             reason: str = "",
                             snapshot: str = "{}",
                             session: Optional[Session] = None,
                             ) -> SMStatusHistory:
        def _impl(sess: Session):
            entry = SMStatusHistory(
                node_id=node_id,
                from_status=from_status,
                to_status=to_status,
                operator_id=operator_id,
                reason=reason,
                snapshot=json.dumps(snapshot, ensure_ascii=False) if isinstance(snapshot, dict) else snapshot,
            )
            sess.add(entry)
            sess.flush()
            return entry

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_node_history(node_id: str, *, limit: int = 50,
                         session: Optional[Session] = None) -> list[SMStatusHistory]:
        def _impl(sess: Session):
            return sess.query(SMStatusHistory).filter(
                SMStatusHistory.node_id == node_id,
            ).order_by(SMStatusHistory.created_at.desc()).limit(limit).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ========================================================================
    #  Audit Logs (general operational audit)
    # ========================================================================

    @staticmethod
    def write_audit_log(timestamp: str, operator: str, target_type: str, target_id: str,
                        action: str, *,
                        old_value: str = "",
                        new_value: str = "",
                        reason: str = "",
                        risk_marked: bool = False,
                        client_ip: str = "",
                        session: Optional[Session] = None,
                        ) -> SMAuditLog:
        def _impl(sess: Session):
            log = SMAuditLog(
                timestamp=timestamp,
                operator=operator,
                target_type=target_type,
                target_id=target_id,
                action=action,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
                risk_marked=risk_marked,
                client_ip=client_ip,
            )
            sess.add(log)
            sess.flush()
            return log

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def query_logs(*, target_type: Optional[str] = None, target_id: Optional[str] = None,
                   limit: int = 100, offset: int = 0,
                   session: Optional[Session] = None) -> list[SMAuditLog]:
        def _impl(sess: Session):
            q = sess.query(SMAuditLog)
            if target_type:
                q = q.filter(SMAuditLog.target_type == target_type)
            if target_id:
                q = q.filter(SMAuditLog.target_id == target_id)
            return q.order_by(SMAuditLog.created_at.desc()).limit(limit).offset(offset).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
