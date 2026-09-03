"""晨报生成 + 推送 Handler。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.morning_report")


class MorningReportHandler(SchedulerJobHandler):
    """生成晨报并推送到指定群。"""

    action_type = "generate_morning_report"
    description = "生成晨报并推送到指定群"

    def __init__(self, outbound_bus=None, llm_client=None):
        self._outbound_bus = outbound_bus
        self._llm = llm_client

    async def execute(self, params: dict) -> JobResult:
        push_group = params.get("push_to_group", "项目群")

        report_text = f"🌅 {push_group} 晨报"

        # 追加进化信息（管理员专属）
        try:
            from datetime import datetime, timezone, timedelta
            beijing_tz = timezone(timedelta(hours=8))
            yesterday = (datetime.now(beijing_tz) - timedelta(days=1)).strftime("%Y-%m-%d")

            from emily_core.repositories.evolution_repo import EvolutionRepo

            insight = EvolutionRepo.get_insight_by_date(yesterday)
            draft_patches = EvolutionRepo.get_patches_by_status("DRAFT")

            if insight or draft_patches:
                evolution_section = "\n\n📊 系统进化摘要（昨日）\n"
                if insight:
                    evolution_section += f"健康评分: {insight.health_score}/100\n"
                    evolution_section += f"SOP 命中率: {insight.sop_hit_rate:.1%}\n"
                if draft_patches:
                    evolution_section += "\n🔧 待审批补丁\n"
                    for p in draft_patches:
                        evolution_section += f"  {p.patch_no}: {p.rule_no} [{p.risk_level}] — {p.target_path}\n"
                report_text += evolution_section
        except Exception as e:
            logger.warning("晨报进化信息追加失败: %s", e)

        if self._outbound_bus:
            try:
                self._outbound_bus.publish("reply", {
                    "content": report_text,
                    "source": "scheduler:morning_report",
                })
            except Exception as e:
                logger.error("晨报推送失败: %s", e)
                return JobResult(success=False, summary=f"推送失败: {e}")

        logger.info("Morning report sent to %s", push_group)
        return JobResult(success=True, summary=f"晨报已推送到 {push_group}")
