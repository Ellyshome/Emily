"""SystemDescriptionUpdateHandler — 每周自动检测系统描述偏差并更新。

与世界书日级更新不同，系统描述变动频率极低（仅部署/迁移/配置变更时），使用周级调度。

参照模式：emily_core/scheduler/jobs/world_book_update.py
"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.system_description_update")


class SystemDescriptionUpdateHandler(SchedulerJobHandler):
    action_type = "system_description_update"
    description = "系统描述偏差检测 + 自动更新"

    def __init__(self, system_description_service=None):
        self._service = system_description_service

    async def execute(self, params: dict) -> JobResult:
        try:
            if self._service is None:
                from ...services.system_description_service import SystemDescriptionService
                self._service = SystemDescriptionService()

            result = await self._service.check_and_update()

            status = result.get("status", "unknown")
            if status == "no_drift":
                summary = "系统描述无需更新：与当前代码结构一致"
            elif status in ("built", "updated"):
                stale = result.get("updated_domains", [])
                summary = f"系统描述已更新：偏差域 {stale}"
            elif status == "preview":
                summary = "系统描述预览模式"
            else:
                summary = f"系统描述更新状态: {status}"

            return JobResult(
                success=True,
                summary=summary,
                data=result,
            )
        except Exception as e:
            logger.error("SystemDescriptionUpdateHandler failed: %s", e, exc_info=True)
            return JobResult(success=False, summary=str(e))
