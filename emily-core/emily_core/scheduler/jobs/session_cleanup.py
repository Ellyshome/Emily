"""超时 Session 清理 Handler —— 巡检超时 session，下发关闭指令。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.session_cleanup")


class SessionCleanupHandler(SchedulerJobHandler):
    """巡检超时 session，下发关闭指令。"""

    action_type = "cleanup_stale_sessions"
    description = "巡检超时 Session 并下发关闭指令"

    def __init__(self, session_pool=None, outbound_bus=None):
        self._session_pool = session_pool
        self._outbound_bus = outbound_bus

    async def execute(self, params: dict) -> JobResult:
        timeout_minutes = params.get("timeout_minutes", 30)
        count = 0

        if self._session_pool:
            # TODO: 接入 SessionPool 的超时关闭逻辑
            logger.info("Session cleanup: timeout=%dmin", timeout_minutes)
        else:
            logger.warning("SessionCleanup: SessionPool 未注入")

        return JobResult(success=True, summary=f"已关闭 {count} 个超时 session")
