"""全景节点图 V2 业务工具 — 注册到 BusinessFlowToolRegistry。

供 SOP-011-SYS-node_manage 调用，在 WorkItem Pipeline 的 execute 节点中直调。

8 个核心工具：
  - create_node: 创建全景节点（支持单节点 + 批量模式）
  - query_node: 查询节点详情
  - update_node_progress: 更新节点成果进度（触发状态流转）
  - add_node_dependency: 添加前置依赖
  - mount_child_node: 挂载子节点
  - update_nodes: 批量更新节点字段
  - activate_nodes: 批量激活（审批）节点
  - discard_nodes: 批量废弃节点
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.tools.node_tool")


# ── JSON Schema ──

_CREATE_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "项目归属ID"},
        "node_id": {"type": "string", "description": "节点编号（业务主键），如 SG-JG-01-2026。单节点模式必填"},
        "node_name": {"type": "string", "description": "节点名称/工作项描述。单节点模式必填"},
        "deadline": {"type": "string", "description": "截止时间（ISO8601格式）。单节点模式必填"},
        "owner_dept_id": {"type": "string", "description": "主责条线/部门，默认'项目总'"},
        "stage_id": {"type": "integer", "description": "阶段ID: 0=立项 1=规划 2=施工 3=交付，默认0"},
        "parent_node_id": {"type": "string", "description": "父节点编号（如果是子节点则填写）"},
        "remark": {"type": "string", "description": "备注/说明"},
        "nodes": {
            "type": "array",
            "description": "批量创建模式：节点树列表。每项含 node_id/node_name/deadline/deliverables/dependencies/children",
            "items": {"type": "object"},
        },
    },
    "required": ["project_id"],
}

_CREATE_NODE_DESCRIPTION = (
    "创建项目全景节点。支持两种模式："
    "1) 单节点：提供 node_id+node_name+deadline 创建单个节点；"
    "2) 批量创建：提供 nodes 列表，一次性创建节点树（含成果、依赖、子节点）。"
    "批量模式下 nodes 中每个节点可含 deliverables/dependencies/children 字段。"
    "创建后节点初始状态为 CONDITIONS_NOT_MET（条件不足），需进一步添加成果和依赖。"
)

_QUERY_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "description": "节点编号（业务主键）"},
    },
    "required": ["node_id"],
}

_QUERY_NODE_DESCRIPTION = (
    "查询节点详情。返回节点的状态、进度、成果列表、依赖关系、子节点等信息。"
)

_UPDATE_PROGRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {"type": "string", "description": "成果编号"},
        "current_amount": {"type": "number", "description": "当前完成量"},
        "file_id": {"type": "string", "description": "关联文件ID（上传文件后获得）"},
    },
    "required": ["deliverable_id", "current_amount"],
}

_UPDATE_PROGRESS_DESCRIPTION = (
    "更新节点成果进度。更新后自动触发状态机重算："
    "当所有必需成果 100% 完成且前置依赖满足时，节点自动流转至 COMPLETED（已完成）。"
    "当有阻塞条件（权重999）未满足时，节点回退至 CONDITIONS_NOT_MET。"
)

_ADD_DEPENDENCY_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "description": "下游节点编号（需要等待的节点）"},
        "depends_on_deliverable_id": {"type": "string", "description": "依赖的上游成果编号"},
        "weight": {"type": "number", "description": "权重（0.0000-1.0000，阻塞场景用≥999）"},
    },
    "required": ["node_id", "depends_on_deliverable_id"],
}

_ADD_DEPENDENCY_DESCRIPTION = (
    "为节点添加前置依赖。节点需等待上游成果完成才能启动。"
    "系统自动进行循环依赖检测（BFS），非法依赖会被拒绝。"
    "设置 weight≥999 可创建人工阻塞条件。"
)

_MOUNT_CHILD_SCHEMA = {
    "type": "object",
    "properties": {
        "parent_node_id": {"type": "string", "description": "父节点编号"},
        "child_node_id": {"type": "string", "description": "子节点编号"},
        "child_weight": {"type": "number", "description": "子节点权重（0.0-1.0），默认为1.0"},
    },
    "required": ["parent_node_id", "child_node_id"],
}

_MOUNT_CHILD_DESCRIPTION = (
    "将子节点挂载到父节点。父节点进度由子节点进度加权汇总。"
    "嵌套深度上限3层，子节点上限100个。"
    "系统自动检测父子循环。"
)


# ── Handler 函数 ──


async def handle_create_node(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """创建全景节点。

    支持两种模式：
      - 单节点：params 含 node_id + node_name（原逻辑）
      - 批量创建：params 含 nodes 列表，委托 emily_core.services.node_batch.create_node_tree
    """
    # ── 批量创建路径 ──
    batch_nodes = params.get("nodes")
    if batch_nodes and isinstance(batch_nodes, list):
        from emily_core.services.node_batch import create_node_tree

        results = await create_node_tree(
            project_id=params.get("project_id", ""),
            creator_id=user_id,
            nodes=batch_nodes,
            auto_activate=True,
            dry_run=False,
        )
        success_count = sum(1 for r in results if r.get("success"))
        fail_count = sum(1 for r in results if not r.get("success"))
        return {
            "success": fail_count == 0,
            "batch": True,
            "total": len(results),
            "success_count": success_count,
            "fail_count": fail_count,
            "results": results,
            "message": f"批量创建完成：{success_count} 成功，{fail_count} 失败",
        }

    # ── 单节点创建路径（原逻辑）──
    from emily_core.services.node_commands import CreateNodeCommand
    from emily_core.services.node_service import NodeService
    from emily_core.repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    cmd = CreateNodeCommand(
        project_id=params.get("project_id", ""),
        node_id=params.get("node_id", ""),
        node_name=params.get("node_name", ""),
        deadline=params.get("deadline", ""),
        owner_dept_id=params.get("owner_dept_id", "项目总"),
        stage_id=params.get("stage_id", 0),
        parent_node_id=params.get("parent_node_id", ""),
        remark=params.get("remark", ""),
        creator_id=user_id,
    )
    result = await svc.create_node(cmd)
    return {
        "success": result.success,
        "node_id": result.node_id,
        "status": result.status,
        "message": result.message,
    }


async def handle_query_node(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """查询节点详情。"""
    from emily_core.services.node_service import NodeService
    from emily_core.repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    node_id = params.get("node_id", "")
    detail = await svc.get_node_detail(node_id)
    if detail is None:
        return {"success": False, "message": f"节点 {node_id} 不存在"}
    return {
        "success": True,
        "data": detail,
        "message": f"节点「{detail['node_name']}」当前状态: {detail['status']}, 进度: {detail['progress']}%",
    }


async def handle_update_node_progress(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """更新成果进度——触发状态流转。"""
    from emily_core.services.node_commands import UpdateDeliverableProgressCommand
    from emily_core.services.node_service import NodeService
    from emily_core.repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    cmd = UpdateDeliverableProgressCommand(
        deliverable_id=params.get("deliverable_id", ""),
        current_amount=float(params.get("current_amount", 0)),
        file_id=params.get("file_id", ""),
        operator_id=user_id,
    )
    result = await svc.update_deliverable_progress(cmd)
    return {
        "success": result.success,
        "node_id": result.node_id,
        "status": result.status,
        "progress": result.progress,
        "message": result.message,
        "affected_ancestors": result.affected_downstream,
    }


async def handle_add_node_dependency(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """添加前置依赖。"""
    from emily_core.services.node_commands import AddDependencyCommand
    from emily_core.services.node_service import NodeService
    from emily_core.repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    cmd = AddDependencyCommand(
        node_id=params.get("node_id", ""),
        depends_on_deliverable_id=params.get("depends_on_deliverable_id", ""),
        weight=float(params.get("weight", 1.0)),
        operator_id=user_id,
    )
    result = await svc.add_dependency(cmd)
    return {
        "success": result.success,
        "node_id": result.node_id,
        "message": result.message,
        "error_code": result.error_code,
    }


async def handle_mount_child_node(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """挂载子节点。"""
    from emily_core.services.node_commands import MountChildCommand
    from emily_core.services.node_service import NodeService
    from emily_core.repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    cmd = MountChildCommand(
        parent_node_id=params.get("parent_node_id", ""),
        child_node_id=params.get("child_node_id", ""),
        child_weight=float(params.get("child_weight", 1.0)),
        operator_id=user_id,
    )
    result = await svc.mount_child(cmd)
    return {
        "success": result.success,
        "node_id": result.node_id,
        "message": result.message,
        "error_code": result.error_code,
    }


# ── 批量更新 Schema ──

_UPDATE_NODES_SCHEMA = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "description": "节点更新列表。每项含 node_id（必填）+ 要更新的字段（node_name/deadline/owner_dept_id/remark/stage_id 等）",
            "items": {"type": "object"},
        },
    },
    "required": ["updates"],
}

_UPDATE_NODES_DESCRIPTION = (
    "批量更新节点字段。每项指定 node_id + 要修改的字段（只填要改的），"
    "支持：node_name/deadline/owner_dept_id/related_company_id/remark/stage_id/sort_order。"
)

_ACTIVATE_NODES_SCHEMA = {
    "type": "object",
    "properties": {
        "node_ids": {
            "type": "array",
            "description": "要激活（审批通过）的节点编号列表",
            "items": {"type": "string"},
        },
        "approved": {
            "type": "boolean",
            "description": "True=审批通过，False=审批拒绝（默认 True）",
        },
        "remark": {"type": "string", "description": "审批备注"},
    },
    "required": ["node_ids"],
}

_ACTIVATE_NODES_DESCRIPTION = (
    "批量激活（审批通过/拒绝）节点。审批通过：NOT_ACTIVATED → CONDITIONS_NOT_MET。"
    "审批拒绝：节点废弃。需部门负责人或 L5+ 管理员权限。"
)

_DISCARD_NODES_SCHEMA = {
    "type": "object",
    "properties": {
        "node_ids": {
            "type": "array",
            "description": "要废弃的节点编号列表",
            "items": {"type": "string"},
        },
    },
    "required": ["node_ids"],
}

_DISCARD_NODES_DESCRIPTION = (
    "批量废弃节点。已完成子节点的父节点不可废弃。废弃为软删除。"
)


# ── 批量更新 Handler ──


async def handle_update_nodes(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """批量更新节点字段。"""
    from emily_core.services.node_batch_update import batch_update_nodes

    updates = params.get("updates", [])
    if not updates:
        return {"success": False, "message": "updates 列表为空"}

    results = await batch_update_nodes(
        updates=updates,
        operator_id=user_id,
    )
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = sum(1 for r in results if not r.get("success"))
    return {
        "success": fail_count == 0,
        "total": len(results),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results,
        "message": f"批量更新完成：{success_count} 成功，{fail_count} 失败",
    }


async def handle_activate_nodes(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """批量激活（审批）节点。"""
    from emily_core.services.node_batch_update import batch_activate_nodes

    node_ids = params.get("node_ids", [])
    if not node_ids:
        return {"success": False, "message": "node_ids 列表为空"}

    results = await batch_activate_nodes(
        node_ids=node_ids,
        approver_id=user_id,
        approved=params.get("approved", True),
        remark=params.get("remark", ""),
    )
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = sum(1 for r in results if not r.get("success"))
    return {
        "success": fail_count == 0,
        "total": len(results),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results,
        "message": f"批量审批完成：{success_count} 成功，{fail_count} 失败",
    }


async def handle_discard_nodes(
    params: dict[str, Any],
    user_id: str = "",
    message_id: str = "",
    **kw,
) -> dict[str, Any]:
    """批量废弃节点。"""
    from emily_core.services.node_batch_update import batch_discard_nodes

    node_ids = params.get("node_ids", [])
    if not node_ids:
        return {"success": False, "message": "node_ids 列表为空"}

    results = await batch_discard_nodes(
        node_ids=node_ids,
        operator_id=user_id,
    )
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = sum(1 for r in results if not r.get("success"))
    return {
        "success": fail_count == 0,
        "total": len(results),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results,
        "message": f"批量废弃完成：{success_count} 成功，{fail_count} 失败",
    }
