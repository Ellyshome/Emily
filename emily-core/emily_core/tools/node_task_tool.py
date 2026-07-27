"""节点任务工具 —— 替代 plan_task_tool 中的 4 个业务工具。

参照模式：plan_task_tool.py。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("emily.tools.node_task")


async def handle_create_task_node(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """创建 TASK 类型叶子节点。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..services.node_commands import CreateTaskNodeCommand

    cmd = CreateTaskNodeCommand(
        project_id=params.get("project_id", ""),
        node_name=params.get("title", params.get("node_name", "")),
        responsible_user_id=params.get("executor_id", params.get("responsible_user_id", "")),
        deadline=params.get("deadline_at", ""),
        parent_node_id=params.get("parent_node_id", params.get("node_id", "")),
        owner_dept_id=params.get("owner_dept_id", "项目总"),
        description=params.get("description", ""),
        creator_id=user_id,
    )

    result = await node_service.create_node(cmd)

    return {
        "success": result.success,
        "object_type": "node",
        "object_id": result.node_id,
        "reply": result.message,
    }


async def handle_submit_node_deliverable(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """提交节点成果。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..services.node_commands import SubmitNodeDeliverableCommand

    cmd = SubmitNodeDeliverableCommand(
        deliverable_id=params.get("deliverable_id", ""),
        content=params.get("content", ""),
        file_url=params.get("file_url", ""),
        file_name=params.get("file_name", ""),
        attachment_file_id=params.get("attachment_file_id", ""),
        submitted_by=user_id,
        is_acceptance_check=params.get("is_acceptance_check", False),
    )

    result = await node_service.submit_deliverable(cmd)
    return {"success": result.success, "reply": result.message}


async def handle_confirm_node_deliverable(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """确认节点成果。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..services.node_commands import ConfirmNodeDeliverableCommand

    cmd = ConfirmNodeDeliverableCommand(
        deliverable_id=params.get("deliverable_id", ""),
        confirmed_by=user_id,
        reason=params.get("reason", ""),
    )

    result = await node_service.confirm_deliverable(cmd)
    return {"success": result.success, "reply": result.message}


async def handle_return_node_deliverable(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """退回节点成果。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..services.node_commands import ReturnNodeDeliverableCommand

    cmd = ReturnNodeDeliverableCommand(
        deliverable_id=params.get("deliverable_id", ""),
        returned_by=user_id,
        reason=params.get("reason", ""),
    )

    result = await node_service.return_deliverable(cmd)
    return {"success": result.success, "reply": result.message}


async def handle_query_my_nodes(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """查询我负责或参与的节点。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..repositories.node_repo import ProjectNodeRepo

    project_id = params.get("project_id") or None
    node_type = params.get("node_type") or None
    limit = int(params.get("limit", 20))

    # 查责任人 + 参与人，合并去重
    resp_nodes = await asyncio.to_thread(
        ProjectNodeRepo.find_by_responsible_user,
        user_id,
        project_id=project_id,
        node_type=node_type,
        status="IN_PROGRESS",
        limit=limit,
    )
    part_nodes = await asyncio.to_thread(
        ProjectNodeRepo.find_by_participant_user,
        user_id,
        project_id=project_id,
        node_type=node_type,
        status="IN_PROGRESS",
        limit=limit,
    )

    seen: set[str] = set()
    merged: list[dict] = []
    for n in resp_nodes + part_nodes:
        if n.node_id in seen:
            nid = n.node_id
            continue
        seen.add(nid)
        merged.append({
            "node_id": n.node_id,
            "node_name": n.node_name,
            "node_type": getattr(n, "node_type", ""),
            "status": n.status,
            "deadline": n.deadline,
            "project_id": n.project_id,
        })

    # 标记来源：哪些是负责人、哪些是参与人
    resp_ids = {n.node_id for n in resp_nodes}
    for item in merged:
        item["role"] = "responsible" if item["node_id"] in resp_ids else "participant"

    return {"success": True, "reply": f"找到 {len(merged)} 个节点", "data": merged}
