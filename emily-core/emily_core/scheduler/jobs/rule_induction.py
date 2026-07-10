"""RuleInductionHandler — 每周日 22:30 自动归纳进化规则。"""

from __future__ import annotations
import logging
from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.rule_induction")


class RuleInductionHandler(SchedulerJobHandler):
    action_type = "induct_evolution_rules"
    description = "从近N天洞察中归纳进化规则（默认7天）"

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def execute(self, params: dict) -> JobResult:
        from datetime import datetime, timezone, timedelta
        beijing_tz = timezone(timedelta(hours=8))
        end_date = params.get("end_date", datetime.now(beijing_tz).strftime("%Y-%m-%d"))
        days = params.get("days", 7)

        try:
            from emily_core.services.evolution.rule_inductor import RuleInductor
            inductor = RuleInductor(llm_client=self._llm)
            rules = await inductor.induct(end_date, days=days)
            return JobResult(success=True, summary=f"规则归纳完成: {len(rules)} 条")
        except Exception as e:
            logger.error("RuleInductionHandler failed: %s", e, exc_info=True)
            return JobResult(success=False, summary=str(e))
