# emily-core/emily_core/infrastructure/logging/business_event_logger.py
"""BusinessEventLogger —— 业务操作成功后写入 business_event_logs。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.evolution.business_event_logger")


class BusinessEventLogger:
    """业务事件日志采集器。

    在各 Application 层操作成功后调用 log()，非阻断写入 business_event_logs。
    同时保留 EventJournal 文件双写（人类可读）。
    """

    @staticmethod
    async def log(
        *,
        event_category: str,
        event_action: str,
        target_type: str = "",
        target_id: str = "",
        target_no: str = "",
        summary: str = "",
        detail_json: str = "",
        project_id: str = "",
        user_id: str = "",
        user_name: str = "",
        pipeline_run_id: str = "",
        conversation_id: str = "",
    ) -> None:
        """写入一条业务事件日志。"""
        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import BusinessEventLog

        await EvolutionLogWriter.write(
            BusinessEventLog,
            project_id=project_id,
            user_id=user_id,
            user_name=user_name,
            event_category=event_category,
            event_action=event_action,
            target_type=target_type,
            target_id=target_id,
            target_no=target_no,
            summary=summary,
            detail_json=detail_json,
            pipeline_run_id=pipeline_run_id,
        )
