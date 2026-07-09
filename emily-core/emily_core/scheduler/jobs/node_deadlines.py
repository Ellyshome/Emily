"""节点截止时间提醒 Handler。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.node_deadlines")


class NodeDeadlineHandler(SchedulerJobHandler):
    """查询即将到期/已超期节点并推送提醒。"""

    action_type = "check_node_deadlines"
    description = "节点截止时间提醒（临期 + 超期）"

    def __init__(self, node_service=None, outbound_bus=None):
        self._node_service = node_service
        self._outbound_bus = outbound_bus

    async def execute(self, params: dict) -> JobResult:
        if self._node_service is None:
            return JobResult(success=False, summary="NodeService 未注入")

        before_minutes = params.get("before_minutes", 60)
        check_overdue = params.get("overdue_check", True)

        reminders = []

        # 临近截止
        near = await self._node_service.find_near_deadline(before_minutes=before_minutes)
        for node in near:
            msg = f"⚠️ 节点「{node.node_name}」即将到期（截止：{node.deadline}）"
            reminders.append(msg)

        # 已超期
        if check_overdue:
            overdue = await self._node_service.find_overdue()
            for node in overdue:
                msg = f"🔴 节点「{node.node_name}」已超期（截止：{node.deadline}）"
                reminders.append(msg)

        if reminders and self._outbound_bus:
            full_text = "\n".join(reminders)
            try:
                self._outbound_bus.publish("reply", {
                    "content": full_text,
                    "source": "scheduler:node_deadlines",
                })
            except Exception as e:
                logger.error("节点提醒推送失败: %s", e)
                return JobResult(success=False, summary=f"推送失败: {e}")

        summary = f"临期 {len(near)} 个，超期 {len(overdue) if check_overdue else 0} 个"
        logger.info("Node deadline check: %s", summary)
        return JobResult(success=True, summary=summary)
