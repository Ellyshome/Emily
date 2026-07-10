"""PatchValidationHandler — 每日 08:00 验证已应用补丁效果。"""

from __future__ import annotations
import logging
from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.patch_validator")


class PatchValidationHandler(SchedulerJobHandler):
    action_type = "validate_evolution_patches"
    description = "验证已应用 >= 7天的补丁效果"

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def execute(self, params: dict) -> JobResult:
        try:
            from emily_core.services.evolution.patch_validator import PatchValidator
            validator = PatchValidator()
            results = await validator.validate()
            confirmed = sum(1 for r in results if r.get("status") == "confirmed")
            rolled_back = sum(1 for r in results if r.get("status") == "rolled_back")
            return JobResult(
                success=True,
                summary=f"补丁验证完成: {len(results)} 个, CONFIRMED={confirmed}, ROLLED_BACK={rolled_back}",
            )
        except Exception as e:
            logger.error("PatchValidationHandler failed: %s", e, exc_info=True)
            return JobResult(success=False, summary=str(e))
