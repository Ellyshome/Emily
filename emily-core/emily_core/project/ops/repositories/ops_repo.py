"""OpsRepository — 运维表 CRUD。

遵循项目 sync Repository 模式：纯 @staticmethod，可选 session 参数。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from emily_core.infrastructure.database.session import get_session, get_session_raw
from emily_core.project.ops.models import (
    OpsTickLog,
    OpsProbeExecution,
    OpsFinding,
    OpsMailAudit,
    OpsStartupReport,
)

logger = logging.getLogger("emily.ops.repo")


class OpsRepository:
    """运维表 CRUD。

    所有方法遵循项目 sync Repository 模式：
      - @staticmethod（或普通方法）
      - 可选 session 参数
      - 调用方用 asyncio.to_thread() 包裹
    """

    # ── Tick 结果持久化 ──

    @staticmethod
    def save_tick_results(ctx, results: list[dict], *, session: Optional[Session] = None) -> None:
        """持久化一轮 Tick 的执行结果。

        写入 3 张表：ops_tick_log + ops_probe_execution + ops_finding。

        Args:
            ctx: TickContext 实例
            results: _run_probe_safe() 返回的 dict 列表
        """
        def _impl(sess: Session) -> None:
            now = datetime.now(timezone.utc)
            duration_ms = int((now - ctx.start_time).total_seconds() * 1000)

            # 统计
            success_count = sum(1 for r in results if r.get("status") == "SUCCESS")
            failed_count = sum(1 for r in results if r.get("status") == "FAILED")
            total_findings = sum(r.get("findings_count", 0) for r in results)

            # 1. ops_tick_log
            tick_log = OpsTickLog(
                tick_id=ctx.tick_id,
                tick_number=ctx.tick_number,
                start_time=ctx.start_time,
                end_time=now,
                duration_ms=duration_ms,
                probes_executed=len(results),
                success=success_count,
                failed=failed_count,
                total_findings=total_findings,
            )
            sess.add(tick_log)

            # 2. ops_probe_execution + 3. ops_finding
            for r in results:
                probe_name = r.get("probe", "unknown")
                status = r.get("status", "UNKNOWN")
                findings_count = r.get("findings_count", 0)

                exec_rec = OpsProbeExecution(
                    tick_id=ctx.tick_id,
                    probe_name=probe_name,
                    status=status,
                    findings_count=findings_count,
                    error_message=r.get("error", ""),
                )
                sess.add(exec_rec)

                # 写入每条 finding
                findings = r.get("findings", [])
                if findings:
                    for f in findings:
                        finding_rec = OpsFinding(
                            tick_id=ctx.tick_id,
                            probe_name=probe_name,
                            finding_type=f.finding_type if hasattr(f, 'finding_type') else f.get("finding_type", ""),
                            severity=f.severity if hasattr(f, 'severity') else f.get("severity", "INFO"),
                            target_id=f.target_id if hasattr(f, 'target_id') else f.get("target_id", ""),
                            message=f.message if hasattr(f, 'message') else f.get("message", ""),
                            metadata_json=f.metadata if hasattr(f, 'metadata') else f.get("metadata", {}),
                        )
                        sess.add(finding_rec)

            sess.flush()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ── 冷启动报告 ──

    @staticmethod
    def get_latest_startup_report(*, hours: int = 24, session: Optional[Session] = None) -> Optional[OpsStartupReport]:
        """获取最近 N 小时内的最新冷启动报告。

        Returns:
            OpsStartupReport 或 None（24h 内无报告 = 冷启动）
        """
        def _impl(sess: Session):
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            return sess.query(OpsStartupReport).filter(
                OpsStartupReport.created_at >= cutoff,
            ).order_by(OpsStartupReport.created_at.desc()).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def save_startup_report(report: dict, *, session: Optional[Session] = None) -> None:
        """持久化冷启动报告。"""
        def _impl(sess: Session):
            rec = OpsStartupReport(
                tick_id=report.get("tick_id", str(uuid4())),
                startup_time=report.get("startup_time", datetime.now(timezone.utc)),
                environment=report.get("environment", ""),
                instance_id=report.get("instance_id", ""),
                version=report.get("version", ""),
                db_status=report.get("db_status", True),
                llm_status=report.get("llm_status", ""),
                maxkb_status=report.get("maxkb_status", ""),
                email_status=report.get("email_status", ""),
                pipeline_status=report.get("pipeline_status", ""),
                projects_total=report.get("nodes_total", 0),
                nodes_completed=report.get("nodes_completed", 0),
                nodes_in_progress=report.get("nodes_in_progress", 0),
                nodes_blocked=report.get("nodes_blocked", 0),
                report_content=report.get("report_content", ""),
            )
            sess.add(rec)
            sess.flush()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def mark_report_sent(tick_id: str, *, session: Optional[Session] = None) -> bool:
        """标记冷启动报告已发送。"""
        def _impl(sess: Session):
            rec = sess.query(OpsStartupReport).filter(
                OpsStartupReport.tick_id == tick_id,
            ).first()
            if rec is None:
                return False
            rec.sent_to_mail = True
            rec.sent_at = datetime.now(timezone.utc)
            return True

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ── 邮箱审计 ──

    @staticmethod
    def mail_uid_exists(uid: str, *, session: Optional[Session] = None) -> bool:
        """检查 mail_uid 是否已存在（幂等去重）。"""
        def _impl(sess: Session):
            return sess.query(OpsMailAudit).filter(
                OpsMailAudit.mail_uid == uid,
            ).first() is not None

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def save_mail_audit(data: dict, *, session: Optional[Session] = None) -> Optional[OpsMailAudit]:
        """写入邮件审计记录。基于 mail_uid 幂等（已存在则跳过）。"""
        def _impl(sess: Session):
            uid = data.get("mail_uid", "")
            if not uid:
                return None
            # 幂等检查
            existing = sess.query(OpsMailAudit).filter(
                OpsMailAudit.mail_uid == uid,
            ).first()
            if existing:
                return existing
            rec = OpsMailAudit(
                tick_id=data.get("tick_id", str(uuid4())),
                mail_uid=uid,
                mail_from=data.get("mail_from", ""),
                mail_subject=data.get("mail_subject", ""),
                mail_date=data.get("mail_date"),
                command_text=data.get("command_text", ""),
                received_at=data.get("received_at", datetime.now(timezone.utc)),
            )
            sess.add(rec)
            sess.flush()
            return rec

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_pending_mail_commands(*, session: Optional[Session] = None) -> list[OpsMailAudit]:
        """获取未分派的邮件命令（dispatched=False）。"""
        def _impl(sess: Session):
            return sess.query(OpsMailAudit).filter(
                OpsMailAudit.dispatched == False,
            ).order_by(OpsMailAudit.received_at).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def mark_command_dispatched(command_id: str, *, session: Optional[Session] = None) -> bool:
        """标记邮件命令已分派。"""
        def _impl(sess: Session):
            rec = sess.query(OpsMailAudit).filter(
                OpsMailAudit.id == command_id,
            ).first()
            if rec is None:
                return False
            rec.dispatched = True
            rec.dispatched_at = datetime.now(timezone.utc)
            return True

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ── Findings 查询 ──

    @staticmethod
    def get_recent_findings(*, limit: int = 20, session: Optional[Session] = None) -> list[OpsFinding]:
        """获取最近 N 条探针发现结果（按创建时间倒序）。

        Args:
            limit: 返回条数上限，默认 20
            session: 可选外部事务 session

        Returns:
            OpsFinding 列表（可能为空列表）
        """
        def _impl(sess: Session) -> list[OpsFinding]:
            return sess.query(OpsFinding).order_by(
                OpsFinding.created_at.desc()
            ).limit(limit).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
