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

        # TODO: 接入 OpsMonitor 的晨报组装逻辑
        report_text = f"🌅 {push_group} 晨报（待接入完整逻辑）"

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
