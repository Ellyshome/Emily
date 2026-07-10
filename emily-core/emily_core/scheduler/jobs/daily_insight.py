"""DailyInsightHandler — 每日 22:00 自动生成日洞察。"""

from __future__ import annotations
import logging
from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.daily_insight")


class DailyInsightHandler(SchedulerJobHandler):
    action_type = "generate_daily_insight"
    description = "生成洞察（指标聚合+异常检测+LLM复盘，支持可变周期）"

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def execute(self, params: dict) -> JobResult:
        from datetime import datetime, timezone, timedelta
        beijing_tz = timezone(timedelta(hours=8))
        date = params.get("date", datetime.now(beijing_tz).strftime("%Y-%m-%d"))
        days = max(1, params.get("days", 1))

        try:
            from emily_core.services.evolution.insight_generator import InsightGenerator
            gen = InsightGenerator(llm_client=self._llm)
            result = await gen.generate(date, days=days)
            period_label = f"{days}天" if days > 1 else "日"
            return JobResult(
                success=True,
                summary=f"{period_label}洞察 {date} 生成完成，健康评分={result.get('insight', {}).get('health_score', 'N/A')}",
            )
        except Exception as e:
            logger.error("DailyInsightHandler failed: %s", e, exc_info=True)
            return JobResult(success=False, summary=str(e))
