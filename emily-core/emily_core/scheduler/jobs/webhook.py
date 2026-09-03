"""Webhook 回调 Handler —— 定时调用外部 Webhook。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.webhook")


class WebhookHandler(SchedulerJobHandler):
    """定时调用外部 Webhook。"""

    action_type = "call_external_webhook"
    description = "定时调用外部 Webhook URL"

    async def execute(self, params: dict) -> JobResult:
        url = params.get("url", "")
        method = params.get("method", "POST")

        if not url:
            return JobResult(success=False, summary="未配置 webhook URL")

        # TODO: 接入 httpx 异步请求
        # 未实现前返回失败而非假成功；接入前不得在 scheduler_config.json 注册为 enabled。
        logger.info("Webhook call: %s %s", method, url)

        return JobResult(success=False, summary=f"Webhook 未实现（未发请求）: {method} {url}")
