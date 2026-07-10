"""WorldBookUpdateHandler — 每日 08:00 自动检测认知偏差并更新世界书。"""

from __future__ import annotations
import logging
from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.world_book_update")


class WorldBookUpdateHandler(SchedulerJobHandler):
    action_type = "world_book_update"
    description = "认知偏差检测 + 世界书增量更新"

    def __init__(self, world_book_service=None):
        self._service = world_book_service

    async def execute(self, params: dict) -> JobResult:
        try:
            if self._service is None:
                from ...services.world_book_service import ProjectWorldBookService
                self._service = ProjectWorldBookService()

            results = await self._service.update_all()
            updated = sum(1 for r in results if r.get("status") == "updated")
            no_drift = sum(1 for r in results if r.get("status") == "no_drift")
            errors = sum(1 for r in results if r.get("status") == "error")

            return JobResult(
                success=True,
                summary=f"世界书更新: {updated}个更新, {no_drift}个无偏差, {errors}个失败",
                data={"results": results},
            )
        except Exception as e:
            logger.error("WorldBookUpdateHandler failed: %s", e, exc_info=True)
            return JobResult(success=False, summary=str(e))
