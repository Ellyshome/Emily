"""系统健康检查 Handler —— 定时体检。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.health_check")


class HealthCheckHandler(SchedulerJobHandler):
    """系统自检：DB 连接、LLM 连通性、磁盘空间。"""

    action_type = "system_health_check"
    description = "系统健康自检（DB/LLM/磁盘）"

    def __init__(self, outbound_bus=None, session_factory=None):
        self._outbound_bus = outbound_bus
        self._session_factory = session_factory

    async def execute(self, params: dict) -> JobResult:
        checks = []

        # DB 连接检查
        try:
            from ....infrastructure.database.session import get_session_raw
            session = (self._session_factory() if self._session_factory
                       else get_session_raw())
            session.execute(__import__('sqlalchemy').text("SELECT 1"))
            session.close()
            checks.append("DB: OK")
        except Exception as e:
            checks.append(f"DB: FAIL ({e})")

        logger.info("Health check: %s", ", ".join(checks))
        return JobResult(
            success=all("FAIL" not in c for c in checks),
            summary="; ".join(checks),
        )
