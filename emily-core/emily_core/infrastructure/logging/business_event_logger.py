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

    上下文机制：
        PipelineBUS.run() 执行前调用 set_context()，执行结束后 clear_context()。
        log() 在调用方未传 pipeline_run_id / conversation_id 时自动从上下文填充。
    """

    _current_context: dict[str, str] = {}

    @classmethod
    def set_context(cls, pipeline_run_id: str = "", conversation_id: str = "") -> None:
        """设置当前 Pipeline 请求上下文（由 PipelineBUS.run() 调用）。"""
        cls._current_context = {
            "pipeline_run_id": pipeline_run_id,
            "conversation_id": conversation_id,
        }

    @classmethod
    def clear_context(cls) -> None:
        """清除当前请求上下文。"""
        cls._current_context = {}

    @classmethod
    async def log(
        cls,
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
        # 自动从 Pipeline 上下文填充（调用方未传时生效）
        ctx = cls._current_context
        if not pipeline_run_id:
            pipeline_run_id = ctx.get("pipeline_run_id", "")
        if not conversation_id:
            conversation_id = ctx.get("conversation_id", "")

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
