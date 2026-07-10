# emily-core/emily_core/infrastructure/logging/session_lifecycle_logger.py
"""SessionLifecycleLogger —— Session 生命周期事件写入 session_lifecycle_logs。"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.evolution.session_lifecycle_logger")


class SessionLifecycleLogger:
    """Session 生命周期日志采集器。"""

    @staticmethod
    async def log(
        *,
        conversation_id: str,
        user_id: str = "",
        event_type: str = "",       # created/refreshed/compressed/archived/terminated
        detail_json: str = "",
        message_count: int = 0,
        duration_ms: int = 0,
    ) -> None:
        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import SessionLifecycleLog

        await EvolutionLogWriter.write(
            SessionLifecycleLog,
            conversation_id=conversation_id,
            user_id=user_id,
            event_type=event_type,
            detail_json=detail_json,
            message_count=message_count,
            duration_ms=duration_ms,
        )
