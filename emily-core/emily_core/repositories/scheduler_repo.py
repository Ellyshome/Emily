"""系统调度器 Repository 层 —— SchedulerJob + SchedulerExecution CRUD。

参照模式：plan_task_repo.py (PlanTaskTemplateRepo + PlanTaskInstanceRepo)。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from ..infrastructure.database.models import (
    SchedulerJob,
    SchedulerExecution,
)
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.scheduler_repo")

BEIJING_TZ = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════════════════
# SchedulerRepo
# ══════════════════════════════════════════════════════════════════════════════

class SchedulerRepo:
    """调度器仓储 —— 统一管理 SchedulerJob 和 SchedulerExecution。"""

    # ── 编号生成 ──

    @staticmethod
    def generate_job_no() -> str:
        """生成作业编号 JOB-YYYYMMDD-NNNN。"""
        date_part = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        with get_session() as session:
            latest = (
                session.query(SchedulerJob.job_no)
                .filter(SchedulerJob.job_no.like(f"JOB-{date_part}-%"))
                .order_by(SchedulerJob.job_no.desc())
                .first()
            )
            if latest:
                seq = int(latest[0].rsplit("-", 1)[-1]) + 1
            else:
                seq = 1
            return f"JOB-{date_part}-{seq:04d}"

    @staticmethod
    def generate_execution_no() -> str:
        """生成执行编号 SE-YYYYMMDD-NNNN。"""
        date_part = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        with get_session() as session:
            latest = (
                session.query(SchedulerExecution.execution_no)
                .filter(SchedulerExecution.execution_no.like(f"SE-{date_part}-%"))
                .order_by(SchedulerExecution.execution_no.desc())
                .first()
            )
            if latest:
                seq = int(latest[0].rsplit("-", 1)[-1]) + 1
            else:
                seq = 1
            return f"SE-{date_part}-{seq:04d}"

    # ── 作业 CRUD ──

    @staticmethod
    def create_job(**kwargs) -> SchedulerJob:
        """创建调度作业。"""
        with get_session() as session:
            job = SchedulerJob(**kwargs)
            session.add(job)
            session.flush()
            logger.info("SchedulerJob created: %s", job.job_no)
            return job

    @staticmethod
    def get_job_by_id(job_id: str) -> SchedulerJob | None:
        """按 ID 查询作业。"""
        with get_session() as session:
            return session.query(SchedulerJob).filter(SchedulerJob.id == job_id).first()

    @staticmethod
    def get_job_by_no(job_no: str) -> SchedulerJob | None:
        """按编号查询作业。"""
        with get_session() as session:
            return session.query(SchedulerJob).filter(SchedulerJob.job_no == job_no).first()

    @staticmethod
    def list_jobs(status: str = "") -> list[SchedulerJob]:
        """列出所有作业（可选按状态过滤）。"""
        with get_session() as session:
            q = session.query(SchedulerJob)
            if status:
                q = q.filter(SchedulerJob.status == status)
            return q.order_by(SchedulerJob.created_at.desc()).all()

    @staticmethod
    def list_active_jobs() -> list[SchedulerJob]:
        """获取所有 ACTIVE 作业（供引擎扫描）。"""
        with get_session() as session:
            return (
                session.query(SchedulerJob)
                .filter(SchedulerJob.status == "ACTIVE")
                .all()
            )

    @staticmethod
    def update_job_status(job_id: str, status: str) -> SchedulerJob | None:
        """更新作业状态。"""
        with get_session() as session:
            job = session.query(SchedulerJob).filter(SchedulerJob.id == job_id).first()
            if job is None:
                return None
            job.status = status
            job.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("SchedulerJob status: %s -> %s", job_id, status)
            return job

    @staticmethod
    def update_job_next_execution(job_id: str, next_execution_at: str) -> None:
        """更新作业下次执行时间。"""
        with get_session() as session:
            job = session.query(SchedulerJob).filter(SchedulerJob.id == job_id).first()
            if job is None:
                return
            job.next_execution_at = next_execution_at
            job.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()

    @staticmethod
    def update_job_last_executed(job_id: str, last_executed_at: str,
                                  next_execution_at: str = "") -> None:
        """更新作业上次执行时间和下次执行时间。"""
        with get_session() as session:
            job = session.query(SchedulerJob).filter(SchedulerJob.id == job_id).first()
            if job is None:
                return
            job.last_executed_at = last_executed_at
            if next_execution_at:
                job.next_execution_at = next_execution_at
            job.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()

    # ── 执行记录 CRUD ──

    @staticmethod
    def create_execution(**kwargs) -> SchedulerExecution:
        """创建执行记录。"""
        with get_session() as session:
            execution = SchedulerExecution(**kwargs)
            session.add(execution)
            session.flush()
            logger.info("SchedulerExecution created: %s", execution.execution_no)
            return execution

    @staticmethod
    def update_execution_status(execution_id: str, status: str,
                                 error_message: str = "",
                                 result_summary: str = "") -> SchedulerExecution | None:
        """更新执行状态。"""
        with get_session() as session:
            execution = (
                session.query(SchedulerExecution)
                .filter(SchedulerExecution.id == execution_id)
                .first()
            )
            if execution is None:
                return None
            execution.status = status
            if status == "RUNNING":
                execution.started_at = datetime.now(timezone.utc).isoformat()
            if status in ("SUCCESS", "FAILED"):
                execution.finished_at = datetime.now(timezone.utc).isoformat()
            if error_message:
                execution.error_message = error_message
            if result_summary:
                execution.result_summary = result_summary
            session.commit()
            logger.info("SchedulerExecution status: %s -> %s", execution_id, status)
            return execution

    @staticmethod
    def find_executions_by_job(job_id: str, limit: int = 50) -> list[SchedulerExecution]:
        """查询某作业的执行记录。"""
        with get_session() as session:
            return (
                session.query(SchedulerExecution)
                .filter(SchedulerExecution.job_id == job_id)
                .order_by(SchedulerExecution.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def has_period_execution(job_id: str, period_key: str) -> bool:
        """检查某周期是否已有 SUCCESS 执行记录（幂等：同周期成功后不重复触发）。

        reschedule 落地后 next_execution_at 会推进到下个周期，是主闸门；
        此处仅查 SUCCESS 作为兜底——允许同周期失败重试，PENDING/RUNNING
        由 tick 串行 + advisory lock 保证不并发。
        """
        if not period_key:
            return False
        with get_session() as session:
            return (
                session.query(SchedulerExecution)
                .filter(
                    SchedulerExecution.job_id == job_id,
                    SchedulerExecution.period_key == period_key,
                    SchedulerExecution.status == "SUCCESS",
                )
                .first()
                is not None
            )
