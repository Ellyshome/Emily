"""全景节点图 V2 业务工具 — 注册到 BusinessFlowToolRegistry。

供 SOP-011-SYS-node_manage 调用，在 WorkItem Pipeline 的 execute 节点中直调。

8 个核心工具：
  - create_node: 创建全景节点（支持单节点 + 批量模式）
  - query_node: 查询节点详情
  - update_node_progress: 更新节点成果进度（触发状态流转）
  - add_node_dependency: 添加前置依赖
  - mount_child_node: 挂载子节点
  - update_nodes: 批量更新节点字段
  - acknowledge_nodes: 批量签认节点（替代原审批）
  - discard_nodes: 批量废弃节点
"""

from __future__ import annotations

import asyncio
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
        "remark": {"type": "string", "description": "备注/说明"},
        "node_type": {
            "type": "string",
            "description": "节点类型：MILESTONE（里程碑）/ TASK（任务）。由声明决定，不随结构变化；缺省 TASK",
        },
        "deliverables": {
            "type": "array",
            "description": "必需成果清单（**单节点模式必填**，至少一项 is_required=true）。"
                           "每项含 deliverable_name / target_amount / unit / is_required",
            "items": {"type": "object"},
        },
        "nodes": {
            "type": "array",
            "description": "批量创建模式：节点树列表。每项含 node_id/node_name/deadline/node_type/deliverables/dependencies/children",
            "items": {"type": "object"},
        },
    },
    "required": ["project_id"],
}

_CREATE_NODE_DESCRIPTION = (
    "创建项目全景节点。支持两种模式："
    "1) 单节点：提供 node_id+node_name+deadline+deliverables 创建单个节点；"
    "2) 批量创建：提供 nodes 列表，一次性创建节点树（含成果、依赖、子节点）。"
    "**成果必备**：任何节点都必须携带至少一条必需成果，否则创建被拒——"
    "节点是否完结只由自身必需成果决定，无成果的节点没有完成判据。"
    "创建后节点初始状态为 CONDITIONS_NOT_MET（条件不足）；节点类型由 node_type 声明决定。"
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
        "parent_node_id": {"type": "string", "description": "父节点编号（业务主键）"},
        "child_node_id": {"type": "string", "description": "子节点编号（业务主键）"},
        "child_weight": {"type": "number", "description": "子节点对父节点的权重（0.0000-1.0000，默认1.0）"},
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
    from emily_core.services.node_state_machine import NODE_TYPE_TASK
    from emily_core.repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    cmd = CreateNodeCommand(
        project_id=params.get("project_id", ""),
        node_id=params.get("node_id", ""),
        node_name=params.get("node_name", ""),
        deadline=params.get("deadline", ""),
        remark=params.get("remark", ""),
        creator_id=user_id,
        node_type=params.get("node_type", "") or NODE_TYPE_TASK,
        deliverables=params.get("deliverables", []) or [],
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
        "message": f"节点「{detail['node_name']}」当前状态: {detail['status']}",
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
            "description": "节点更新列表。每项含 node_id（必填）+ 要更新的字段（node_name/deadline/remark 等）",
            "items": {"type": "object"},
        },
    },
    "required": ["updates"],
}

_UPDATE_NODES_DESCRIPTION = (
    "批量更新节点字段。每项指定 node_id + 要修改的字段（只填要改的），"
    "支持：node_name/deadline/related_company_id/remark。"
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


# ══════════════════════════════════════════════════════════════════════════════
# 节点参与单位 / 参与人维护（缺口 G-6）· 节点共享文件关联（缺口 G-7）
# ══════════════════════════════════════════════════════════════════════════════

_MANAGE_NODE_PARTICIPANT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "remove"],
                   "description": "add=添加 / remove=移除"},
        "node_id": {"type": "string", "description": "节点编号（业务主键，如 SG-DX-01-2026）"},
        "company": {"type": "string",
                    "description": "参与单位（单位名称或 company_info.id）；与 user_id 二选一"},
        "user_id": {"type": "string",
                    "description": "参与人用户 UUID；与 company 二选一"},
        "role": {"type": "string", "enum": ["participant", "approver", "observer"],
                 "description": "参与人角色（仅 user_id 目标，默认 participant）"},
    },
    "required": ["action", "node_id"],
}

_MANAGE_NODE_PARTICIPANT_DESCRIPTION = (
    "维护全景节点的参与单位 / 参与人（决定该节点数据与共享文件的可见范围）。\n"
    "必填字段：\n"
    "  action — add / remove\n"
    "  node_id — 节点编号\n"
    "  company 或 user_id — 二选一（单位名称 / 单位 ID / 用户 UUID）\n"
    "\n"
    "授权口径：仅 L5/L6 管理员可执行（fail-closed），L4 及以下一律拒绝。"
)


async def handle_manage_node_participant(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kw,
) -> dict[str, Any]:
    """节点参与单位 / 参与人增删（授权 L5+，动作委托 NodeService）。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    action = (params.get("action") or "").strip().lower()
    node_id = (params.get("node_id") or "").strip()
    company_ref = (params.get("company") or "").strip()
    target_user_id = (params.get("user_id") or "").strip()

    if action not in ("add", "remove"):
        return {"success": False, "reply": "action 仅支持 add / remove"}
    if not node_id:
        return {"success": False, "reply": "缺少节点编号（node_id）"}
    if not company_ref and not target_user_id:
        return {"success": False, "reply": "请提供参与单位（company）或参与人（user_id）"}
    if company_ref and target_user_id:
        return {"success": False, "reply": "company 与 user_id 只能二选一"}

    # 授权：仅 L5/L6；无操作人或取不到等级一律拒绝（fail-closed）
    operator_level = 0
    if user_id:
        try:
            from ..repositories.permission_repo import PermissionRepository

            operator = PermissionRepository.get_user(user_id)
            operator_level = getattr(operator, "level", 0) or 0
        except Exception:
            operator_level = 0
    if operator_level < 5:
        return {"success": False, "reply": "仅 L5/L6 管理员可维护节点参与单位 / 参与人",
                "error_code": "permission_denied"}

    if target_user_id:
        if action == "add":
            result = await node_service.add_node_participant(
                node_id, target_user_id, user_id, params.get("role") or "participant")
        else:
            result = await node_service.remove_node_participant(node_id, target_user_id, user_id)
    else:
        from ..services.company_resolver import CompanyResolver
        from ..services.node_commands import (
            AddParticipantCompanyCommand, RemoveParticipantCompanyCommand,
        )

        detail = await node_service.get_node_detail(node_id)
        if detail is None:
            return {"success": False, "reply": f"节点不存在：{node_id}"}
        company_id = CompanyResolver.resolve(company_ref, detail.get("project_id", ""))
        if not company_id:
            return {"success": False,
                    "reply": f"无法唯一确定单位「{company_ref}」，请改用单位全称或 company_info.id"}

        if action == "add":
            result = await node_service.add_participant_company(
                AddParticipantCompanyCommand(node_id=node_id, company_id=company_id,
                                             operator_id=user_id))
        else:
            result = await node_service.remove_participant_company(
                RemoveParticipantCompanyCommand(node_id=node_id, company_id=company_id,
                                                operator_id=user_id))

    return {
        "success": bool(result.success),
        "object_type": "node",
        "object_id": node_id,
        "reply": result.message or ("操作成功" if result.success else "操作失败"),
        "error_code": "" if result.success else "node_participant_failed",
    }


_MANAGE_NODE_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "remove"],
                   "description": "add=把文件挂为节点共享文件 / remove=解除共享"},
        "node_id": {"type": "string", "description": "节点编号（业务主键）"},
        "file_no": {"type": "string", "description": "文件编号（如 FIL-20260709-0001）"},
    },
    "required": ["action", "node_id", "file_no"],
}

_MANAGE_NODE_FILE_DESCRIPTION = (
    "维护节点的共享文件关联（node_accessible_files）：挂上后该节点参与单位可见此文件。\n"
    "必填字段：\n"
    "  action — add / remove\n"
    "  node_id — 节点编号\n"
    "  file_no — 文件编号\n"
    "\n"
    "授权口径（服务层判定，fail-closed）：新增 → 节点责任人 / L5+ / 节点参与单位人员；移除 → 仅 L5+。"
)


async def handle_manage_node_file(
    params: dict[str, Any],
    node_service=None,
    user_id: str = "",
    **kw,
) -> dict[str, Any]:
    """节点共享文件增删（授权与动作均在 NodeService）。"""
    if node_service is None:
        return {"success": False, "reply": "NodeService 未初始化"}

    action = (params.get("action") or "").strip().lower()
    node_id = (params.get("node_id") or "").strip()
    file_no = (params.get("file_no") or "").strip()

    if action not in ("add", "remove"):
        return {"success": False, "reply": "action 仅支持 add / remove"}
    if not node_id or not file_no:
        return {"success": False, "reply": "缺少节点编号（node_id）或文件编号（file_no）"}

    from ..repositories.file_repo import FileRepository

    file_record = await asyncio.to_thread(FileRepository.get_by_file_no, file_no)
    if file_record is None or file_record.is_deleted:
        return {"success": False, "reply": f"找不到文件编号 {file_no}",
                "error_code": "file_not_found"}

    if action == "add":
        result = await node_service.add_node_file(node_id, str(file_record.id), user_id)
    else:
        result = await node_service.remove_node_file(node_id, str(file_record.id), user_id)

    return {
        "success": bool(result.success),
        "object_type": "node",
        "object_id": node_id,
        "file_no": file_no,
        "reply": result.message or ("操作成功" if result.success else "操作失败"),
        "error_code": "" if result.success else "node_file_failed",
    }
