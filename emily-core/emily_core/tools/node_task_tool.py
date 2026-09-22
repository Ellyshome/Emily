"""节点任务工具 —— 替代 plan_task_tool 中的 4 个业务工具。

参照模式：plan_task_tool.py。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("emily.tools.node_task")

# ── Schema 常量（供 registry.py 注册时传递）──

_CREATE_TASK_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "项目ID（UUID）"},
        "title": {"type": "string", "description": "任务标题（即任务名称）"},
        "node_name": {"type": "string", "description": "节点名称（与title二选一）"},
        "executor_id": {"type": "string", "description": "执行人用户ID（UUID）"},
        "responsible_user_id": {"type": "string", "description": "负责人用户ID（UUID，与executor_id二选一）"},
        "deadline_at": {"type": "string", "description": "截止日期（ISO格式）"},
        "parent_node_id": {"type": "string", "description": "要挂载到的父节点ID（通常是里程碑；挂载不改父节点类型）"},
        "description": {"type": "string", "description": "任务描述"},
        "deliverable_name": {"type": "string", "description": "可计量成果名称（如“乔木种植”“铺装面层”）；缺省取任务标题"},
        "target_amount": {"type": "number", "description": "目标量（如 100 棵、200 平方米）。必填——无成果的节点没有任何完成判据，将永远无法完结"},
        "unit": {"type": "string", "description": "量纲（棵/平方米/米/份/项…）"},
        "deliverables": {
            "type": "array",
            "description": "成果清单（可选，多成果时使用）。每项含 deliverable_name / target_amount / unit / is_required；"
                           "提供时以本字段为准，且至少一项 is_required=true",
            "items": {"type": "object"},
        },
    },
    "required": ["project_id", "title"],
}

_SUBMIT_DELIVERABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {"type": "string", "description": "成果ID（UUID）"},
        "content": {"type": "string", "description": "成果内容描述"},
        "file_url": {"type": "string", "description": "附件URL"},
        "file_name": {"type": "string", "description": "附件文件名"},
        "attachment_file_id": {"type": "string", "description": "附件文件ID（UUID）"},
        "is_acceptance_check": {"type": "boolean", "description": "是否为验收检查"},
    },
    "required": ["content"],
}

_CONFIRM_DELIVERABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {"type": "string", "description": "成果ID（UUID）"},
        "reason": {"type": "string", "description": "确认理由/备注"},
    },
    "required": ["deliverable_id"],
}

_RETURN_DELIVERABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {"type": "string", "description": "成果ID（UUID）"},
        "reason": {"type": "string", "description": "退回原因"},
    },
    "required": ["deliverable_id", "reason"],
}

_QUERY_MY_NODES_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "项目ID（UUID，可选筛选）"},
        "node_type": {"type": "string", "description": "节点类型筛选（TASK/MILESTONE等）"},
        "limit": {"type": "integer", "description": "返回数量上限（默认20）"},
    },
    "required": [],
}


async def handle_create_task_node(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """创建 TASK 类型叶子节点（含可计量成果）。

    两层制下任务必须带目标量：无成果的任务无法上报进度，也无法被完工上报
    匹配。父节点挂载走 service 的 mount_child，由结构派生节点类型。
    """
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..services.node_commands import CreateNodeCommand, MountChildCommand
    from ..services.node_batch import generate_node_id

    project_id = params.get("project_id", "")
    node_name = params.get("title", params.get("node_name", ""))
    parent_node_id = params.get("parent_node_id", "")

    if not node_name:
        return {"success": False, "reply": "缺少任务名称（title）"}

    # ── 成果声明（成果必备把关，节点状态自证化 US-04 / R4）──
    # 优先取 deliverables 清单；未提供时由 deliverable_name/target_amount/unit 组成单条成果。
    declared = params.get("deliverables")
    if not (isinstance(declared, list) and declared):
        target_amount = float(params.get("target_amount") or 0)
        unit = (params.get("unit") or "").strip()
        if target_amount <= 0:
            return {
                "success": False,
                "reply": "缺少成果目标量（target_amount）：任务必须携带可计量成果"
                         "（如 100 棵乔木、200 平方米铺装）——无成果的节点没有任何完成判据，"
                         "将永远无法完结。请先向用户确认数量与量纲",
            }
        declared = [{
            "deliverable_name": params.get("deliverable_name", "") or node_name,
            "target_amount": target_amount,
            "unit": unit or "项",
            "is_required": True,
        }]

    # 幂等：同名同项目生成确定性 node_id，已存在则跳过
    node_id = generate_node_id(node_name, project_id)
    existing = await node_service.get_node_detail(node_id)
    if existing:
        return {
            "success": True,
            "object_type": "node",
            "object_id": node_id,
            "skipped": True,
            "reply": f"任务「{node_name}」已存在（{node_id}），未重复创建",
        }

    cmd = CreateNodeCommand(
        project_id=project_id,
        node_id=node_id,
        node_name=node_name,
        responsible_user_id=params.get("executor_id", params.get("responsible_user_id", "")),
        deadline=params.get("deadline_at", ""),
        remark=params.get("description", ""),
        creator_id=user_id,
        deliverables=declared,
    )
    result = await node_service.create_node(cmd)
    if not result.success:
        return {"success": False, "reply": result.message}

    # 挂载到父节点（不改父节点类型——类型由声明决定）
    if parent_node_id:
        mount = await node_service.mount_child(MountChildCommand(
            parent_node_id=parent_node_id,
            child_node_id=node_id,
            operator_id=user_id,
        ))
        if not mount.success:
            return {
                "success": True,
                "object_type": "node",
                "object_id": node_id,
                "reply": f"任务「{node_name}」已创建（{node_id}），但挂载到 {parent_node_id} 失败：{mount.message}",
            }

    summary = "；".join(
        f"{d.get('deliverable_name', '')} 目标 {float(d.get('target_amount', 1) or 1):g}{d.get('unit', '') or ''}"
        for d in declared
    )
    return {
        "success": True,
        "object_type": "node",
        "object_id": node_id,
        "reply": (
            f"任务「{node_name}」已创建（{node_id}），成果：{summary}"
            + (f"；已挂载到 {parent_node_id}" if parent_node_id else "")
        ),
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
    """查询我负责或参与的节点（含各节点成果名，供录入时判断成果归属）。

    状态口径：
      - 责任人：**不按状态过滤**——节点未启动时，责任人仍需看到它才能把成果/记录挂对位置；
      - 参与人：返回未完结（未启动 + 进行中）节点——未启动任务需可被首报，已完结节点不再相关。
    """
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    from ..repositories.node_repo import NodeDeliverableRepo, ProjectNodeRepo

    project_id = params.get("project_id") or None
    node_type = params.get("node_type") or None
    limit = int(params.get("limit", 20))

    # 查责任人（不限状态）+ 参与人（未完结），合并去重
    resp_nodes = await asyncio.to_thread(
        ProjectNodeRepo.find_by_responsible_user,
        user_id,
        project_id=project_id,
        node_type=node_type,
        limit=limit,
    )
    part_nodes = await asyncio.to_thread(
        ProjectNodeRepo.find_by_participant_user,
        user_id,
        project_id=project_id,
        node_type=node_type,
        status="",
        limit=limit,
    )
    part_nodes = [n for n in part_nodes if n.status != "COMPLETED"]

    seen: set[str] = set()
    merged: list[dict] = []
    for n in resp_nodes + part_nodes:
        if n.node_id in seen:
            continue
        seen.add(n.node_id)
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

    # 批量补成果（成果名 + 状态），供"本次成果该挂哪个任务节点"的判定
    deliv_map = await asyncio.to_thread(
        NodeDeliverableRepo.find_by_nodes, [item["node_id"] for item in merged],
    )
    for item in merged:
        item["deliverables"] = [
            {
                "deliverable_id": d.deliverable_id,
                "name": d.deliverable_name,
                "status": d.submission_status,
                "current_amount": d.current_amount,
                "target_amount": d.target_amount,
                "unit": d.unit,
            }
            for d in deliv_map.get(item["node_id"], [])
        ]

    return {"success": True, "reply": f"找到 {len(merged)} 个节点", "data": merged}
