"""定期创建叶子节点 Handler。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.periodic_node")


class PeriodicNodeHandler(SchedulerJobHandler):
    """定期创建 TASK 类型叶子节点。"""

    action_type = "create_periodic_node"
    description = "定期创建叶子节点（循环业务任务）"

    def __init__(self, node_service=None):
        self._node_service = node_service

    async def execute(self, params: dict) -> JobResult:
        if self._node_service is None:
            return JobResult(success=False, summary="NodeService 未注入")

        from ...services.node_commands import CreateTaskNodeCommand

        cmd = CreateTaskNodeCommand(
            project_id=params.get("project_id", ""),
            node_name=params.get("node_name", "周期任务"),
            responsible_user_id=params.get("responsible_user_id", ""),
            deadline=params.get("deadline", ""),
            parent_node_id=params.get("parent_node_id", ""),
            owner_dept_id=params.get("owner_dept_id", "项目总"),
            description=params.get("description", ""),
            creator_id=params.get("creator_id", "scheduler"),
        )

        result = await self._node_service.create_node(cmd)

        if result.success:
            logger.info("Periodic node created: %s", result.node_id)
            return JobResult(success=True, summary=f"节点「{cmd.node_name}」创建成功")
        else:
            logger.error("Periodic node failed: %s", result.message)
            return JobResult(success=False, summary=result.message)
