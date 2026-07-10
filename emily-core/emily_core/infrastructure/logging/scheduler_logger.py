# emily-core/emily_core/infrastructure/logging/scheduler_logger.py
"""SchedulerJobLogger —— 调度器作业执行后写入 scheduler_job_logs。"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("emily.evolution.scheduler_logger")


class SchedulerJobLogger:
    """调度器作业日志采集器。"""

    @staticmethod
    def log_sync(
        *,
        job_id: str,
        action_type: str,
        params_json: str = "",
        success: bool = True,
        summary: str = "",
        elapsed_ms: int = 0,
        error_detail: str = "",
        started_at: str = "",
        completed_at: str = "",
    ) -> None:
        """同步写入（在 SchedulerEngine 的同步上下文中使用）。"""
        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import SchedulerJobLog

        EvolutionLogWriter.write_sync(
            SchedulerJobLog,
            job_id=job_id,
            action_type=action_type,
            params_json=params_json,
            success=success,
            summary=summary,
            elapsed_ms=elapsed_ms,
            error_detail=error_detail,
            started_at=started_at,
            completed_at=completed_at,
        )
