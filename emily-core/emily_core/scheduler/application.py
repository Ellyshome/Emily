"""Scheduler Application —— 作业管理编排。参照模式：plan_task_app.py。"""

from __future__ import annotations

import logging

from .commands import (
    CreateJobCommand,
    ActivateJobCommand,
    TriggerJobCommand,
    SchedulerOperationResult,
)
from .service import SchedulerService

logger = logging.getLogger("emily.scheduler.application")


class SchedulerApplication:
    """调度器应用层 —— 编排作业 CRUD、激活/停用、手动触发。"""

    def __init__(self, service: SchedulerService, engine=None):
        self._service = service
        self._engine = engine

    async def create_job(self, cmd: CreateJobCommand) -> dict:
        """创建调度作业。"""
        result = await self._service.create_job(cmd)
        return {"success": result.success, "job_id": result.job_id, "reply": result.message}

    async def activate_job(self, cmd: ActivateJobCommand) -> dict:
        """激活/停用作业。"""
        result = await self._service.activate_job(cmd)
        return {"success": result.success, "reply": result.message}

    async def trigger_job(self, cmd: TriggerJobCommand) -> dict:
        """手动触发一次作业执行。"""
        if self._engine is None:
            return {"success": False, "reply": "调度引擎未初始化"}
        try:
            await self._engine.trigger_job(cmd.job_id)
            return {"success": True, "reply": "已触发作业执行"}
        except Exception as e:
            return {"success": False, "reply": f"触发失败: {e}"}

    async def get_job_status(self, job_id: str) -> dict:
        """查询作业状态。"""
        job = await self._service.get_job(job_id)
        if job is None:
            return {"success": False, "reply": "作业不存在"}
        return {
            "success": True,
            "data": {
                "job_no": job.job_no,
                "name": job.name,
                "status": job.status,
                "action_type": job.action_type,
                "last_executed_at": job.last_executed_at,
                "next_execution_at": job.next_execution_at,
            },
        }

    async def list_jobs(self, status: str = "") -> dict:
        """列出所有作业。"""
        jobs = await self._service.list_jobs(status)
        return {
            "success": True,
            "data": [
                {
                    "job_no": j.job_no,
                    "name": j.name,
                    "status": j.status,
                    "action_type": j.action_type,
                    "job_type": j.job_type,
                    "enabled": j.status == "ACTIVE",
                }
                for j in jobs
            ],
        }
