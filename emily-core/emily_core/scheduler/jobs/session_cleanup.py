"""超时 Session 巡检 Handler —— 只读巡检活跃 session，上报状态（清理由 SessionPool sweeper 负责）。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.session_cleanup")


class SessionCleanupHandler(SchedulerJobHandler):
    """只读巡检活跃 Session 池（不触发清理，避免与 SessionPool 内部 sweeper 双重并发）。"""

    action_type = "cleanup_stale_sessions"
    description = "巡检超时 Session 并下发关闭指令"

    def __init__(self, session_pool=None, outbound_bus=None):
        self._session_pool = session_pool
        self._outbound_bus = outbound_bus

    async def execute(self, params: dict) -> JobResult:
        timeout_minutes = params.get("timeout_minutes", 30)

        # Session TTL 清理由 SessionPoolManager 内部 sweeper（sweep_expired）自动执行，
        # 本 handler 仅做只读巡检上报，不触发清理，避免与 sweeper 双重并发。
        if self._session_pool:
            try:
                status = self._session_pool.get_status()
                total = status.get("total", 0)
                if total:
                    idle_max = max(
                        (s.get("idle_seconds", 0) for s in status.get("sessions", [])),
                        default=0,
                    )
                    return JobResult(
                        success=True,
                        summary=(f"巡检：活跃 {total} 个 session，最长空闲 {idle_max}s"
                                 f"（超时阈值 {timeout_minutes}min，由 SessionPool sweeper 自动清理）"),
                    )
                return JobResult(success=True, summary="巡检：无活跃 session")
            except Exception as e:
                logger.warning("SessionCleanup 巡检失败: %s", e)
                return JobResult(success=False, summary=f"巡检失败: {e}")
        logger.warning("SessionCleanup: SessionPool 未注入")
        return JobResult(success=False, summary="SessionPool 未注入，无法巡检")
