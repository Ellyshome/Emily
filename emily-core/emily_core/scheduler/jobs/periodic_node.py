"""定期创建叶子节点 Handler。"""

from __future__ import annotations

import asyncio
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

        from ...services.node_commands import CreateNodeCommand
        from ...services.node_batch import generate_node_id
        from ...repositories.node_repo import ProjectNodeRepo

        project_id = params.get("project_id", "")
        node_name = params.get("node_name", "周期任务")
        # node_id 基于名称+项目生成（NODE-{hash4}），同名同项目幂等不重复创建
        node_id = generate_node_id(node_name, project_id)

        # 幂等：同名同项目节点已存在则跳过
        existing = await asyncio.to_thread(ProjectNodeRepo.get_by_node_id, node_id)
        if existing and not existing.is_discarded:
            logger.info("Periodic node already exists: %s, skip", node_id)
            return JobResult(success=True, summary=f"节点「{node_name}」已存在，跳过（{node_id}）")

        cmd = CreateNodeCommand(
            project_id=project_id,
            node_id=node_id,
            node_name=node_name,
            responsible_user_id=params.get("responsible_user_id", ""),
            deadline=params.get("deadline", ""),
            owner_dept_id=params.get("owner_dept_id", "项目总"),
            remark=params.get("description", ""),
            creator_id=params.get("creator_id", "scheduler"),
            node_type="TASK",
        )

        result = await self._node_service.create_node(cmd)

        if result.success:
            logger.info("Periodic node created: %s (node_id=%s)", cmd.node_name, result.node_id)
            return JobResult(success=True, summary=f"节点「{cmd.node_name}」创建成功（{result.node_id}）")
        else:
            logger.error("Periodic node failed: %s", result.message)
            return JobResult(success=False, summary=result.message)
