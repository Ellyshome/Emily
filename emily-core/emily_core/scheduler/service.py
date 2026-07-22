"""Scheduler Service —— 作业 CRUD + 执行记录。参照模式：plan_task_service.py。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .commands import (
    CreateJobCommand,
    ActivateJobCommand,
    TriggerJobCommand,
    SchedulerOperationResult,
)

logger = logging.getLogger("emily.scheduler.service")

BEIJING_TZ = timezone(datetime.now(timezone.utc).astimezone().utcoffset() or __import__('datetime').timedelta(hours=8))


class SchedulerService:
    """调度器核心业务 Service。"""

    def __init__(self, repo=None):
        from ..repositories.scheduler_repo import SchedulerRepo
        self._repo = repo or SchedulerRepo()

    # ── 作业 CRUD ──

    async def create_job(self, cmd: CreateJobCommand) -> SchedulerOperationResult:
        """创建调度作业（DRAFT）。"""
        job_no = self._repo.generate_job_no()
        job = await asyncio.to_thread(
            self._repo.create_job,
            job_no=job_no,
            name=cmd.name,
            action_type=cmd.action_type,
            handler_module=cmd.handler_module,
            job_type=cmd.job_type,
            cron_expression=cmd.cron_expression,
            interval_seconds=cmd.interval_seconds,
            deadline_rule=cmd.deadline_rule,
            action_params=cmd.action_params,
            creator_id=cmd.creator_id,
        )
        return SchedulerOperationResult(
            success=True, job_id=job.id, message=f"作业「{cmd.name}」创建成功（编号：{job_no}）",
        )

    async def activate_job(self, cmd: ActivateJobCommand) -> SchedulerOperationResult:
        """激活/停用作业。"""
        target = "ACTIVE" if cmd.activate else "INACTIVE"
        job = await asyncio.to_thread(self._repo.update_job_status, cmd.job_id, target)
        if job is None:
            return SchedulerOperationResult(success=False, job_id=cmd.job_id, message="作业不存在")
        return SchedulerOperationResult(
            success=True, job_id=cmd.job_id, message=f"作业已{'激活' if cmd.activate else '停用'}",
        )

    async def get_job(self, job_id: str):
        """查询作业详情。"""
        return await asyncio.to_thread(self._repo.get_job_by_id, job_id)

    async def list_jobs(self, status: str = "") -> list:
        """列出所有作业。"""
        return await asyncio.to_thread(self._repo.list_jobs, status)

    async def list_active_jobs(self) -> list:
        """获取所有 ACTIVE 作业（引擎用）。"""
        return await asyncio.to_thread(self._repo.list_active_jobs)

    async def has_executed_in_period(self, job_id: str, period_key: str) -> bool:
        """检查作业在指定周期是否已有执行记录（供引擎幂等去重）。"""
        return await asyncio.to_thread(
            self._repo.has_period_execution, job_id, period_key
        )

    # ── 执行记录 ──

    async def create_execution(self, job_id: str, period_key: str = "") -> SchedulerOperationResult:
        """创建执行记录。回传 execution_id 供引擎状态流转。"""
        execution_no = self._repo.generate_execution_no()
        execution = await asyncio.to_thread(
            self._repo.create_execution,
            execution_no=execution_no,
            job_id=job_id,
            period_key=period_key,
        )
        return SchedulerOperationResult(
            success=True,
            execution_no=execution_no,
            execution_id=getattr(execution, "id", ""),
        )

    async def update_execution_status(self, execution_id: str, status: str,
                                       error_message: str = "", result_summary: str = "") -> None:
        """更新执行状态。"""
        await asyncio.to_thread(
            self._repo.update_execution_status,
            execution_id, status, error_message, result_summary,
        )

    async def update_job_last_executed(self, job_id: str, last_executed_at: str,
                                        next_execution_at: str = "") -> None:
        """更新作业上次执行时间和下次执行时间（reschedule 用）。"""
        await asyncio.to_thread(
            self._repo.update_job_last_executed,
            job_id, last_executed_at, next_execution_at,
        )

    async def update_job_status(self, job_id: str, status: str) -> None:
        """更新作业状态（ONCE 执行后置 INACTIVE 等）。"""
        await asyncio.to_thread(self._repo.update_job_status, job_id, status)
