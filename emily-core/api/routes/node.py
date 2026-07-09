"""全景节点图 REST API 路由 —— 需求文档 §8。

端点：
    POST   /api/v1/project-nodes                          — 创建节点
    GET    /api/v1/project-nodes/{node_id}                — 查询节点详情
    PATCH  /api/v1/project-nodes/{node_id}                — 更新节点字段
    DELETE /api/v1/project-nodes/{node_id}                — 废弃节点
    POST   /api/v1/project-nodes/{node_id}/activate       — 激活节点（审批 NOT_ACTIVATED → CONDITIONS_NOT_MET）
    POST   /api/v1/project-nodes/{node_id}/deliverables   — 新增成果
    PATCH  /api/v1/node-deliverables/{deliverable_id}     — 更新成果进度
    POST   /api/v1/project-nodes/{node_id}/dependencies   — 添加依赖
    DELETE /api/v1/node-dependencies/{dependency_id}      — 移除依赖
    POST   /api/v1/project-nodes/{parent_node_id}/children — 挂载子节点
    DELETE /api/v1/project-nodes/{parent_node_id}/children/{child_node_id} — 移除子节点

参照模式：api/routes/permission.py。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from .node_schemas import (
    CreateNodeRequest,
    UpdateNodeRequest,
    ActivateNodeRequest,
    CreateDeliverableRequest,
    UpdateDeliverableProgressRequest,
    AddDependencyRequest,
    MountChildRequest,
    AssignNodeRequest,
    SubmitDeliverableRequest,
    ConfirmDeliverableRequest,
    ReturnDeliverableRequest,
    ResubmitDeliverableRequest,
    ApiResponse,
)

logger = logging.getLogger("emily.api.node")

router = APIRouter(prefix="/project-nodes", tags=["project-nodes"])

# 跨节点路由（成果进度更新、依赖删除等不限于单节点前缀的操作）
cross_router = APIRouter(tags=["node-cross"])

# 延迟初始化（与 permission 路由同模式）
_service = None


def set_node_service(service) -> None:
    """由 EmilyCore 注入 Service 实例。"""
    global _service
    _service = service


def _get_service():
    """惰性获取 NodeService。"""
    global _service
    if _service is not None:
        return _service
    try:
        from api.server import get_core
        core = get_core()
        core._ensure_initialized()
        _service = core._node_service
    except Exception:
        pass
    if _service is None:
        raise HTTPException(status_code=503, detail="Node graph module not initialized")
    return _service


# ══════════════════════════════════════════════════════════════════════════════
# 节点管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("", status_code=201)
async def create_node(body: CreateNodeRequest):
    """创建节点 —— 需求文档 §8.1.1。"""
    from emily_core.services.node_commands import CreateNodeCommand

    svc = _get_service()
    cmd = CreateNodeCommand(
        project_id=body.project_id,
        node_id=body.node_id,
        node_name=body.node_name,
        owner_dept_id=body.owner_dept_id,
        related_company_id=body.related_company_id,
        deadline=body.deadline,
        creator_id=body.creator_id,
        parent_node_id=body.parent_node_id,
        stage_id=body.stage_id,
        child_weight=body.child_weight,
        remark=body.remark,
        land_parcel_id=body.land_parcel_id,
        startup_doc_id=body.startup_doc_id,
        sort_order=body.sort_order,
        responsible_user_id=getattr(body, 'responsible_user_id', ''),
        node_type=getattr(body, 'node_type', 'WORK_PACKAGE'),
    )
    result = await svc.create_node(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message, data={"node_id": result.node_id, "status": result.status})


@router.get("/my-tasks")
async def my_tasks(user_id: str = Query(..., description="当前用户ID"),
                    project_id: str = Query(default=""),
                    submission_status: str = Query(default=""),
                    page: int = Query(default=1),
                    page_size: int = Query(default=20)):
    """查询我负责的待办任务。"""
    import asyncio
    from emily_core.repositories.node_repo import NodeDeliverableRepo

    rows = await asyncio.to_thread(
        NodeDeliverableRepo.find_pending_by_responsible_user,
        user_id, project_id or None, submission_status or "", "IN_PROGRESS", page_size, (page - 1) * page_size,
    )
    total = await asyncio.to_thread(
        NodeDeliverableRepo.count_pending_by_responsible_user,
        user_id, project_id or None, submission_status or "", "IN_PROGRESS",
    )
    items = []
    for deliv, node in rows:
        items.append({
            "node_id": node.node_id,
            "node_name": node.node_name,
            "project_id": node.project_id,
            "deadline": node.deadline,
            "node_type": getattr(node, "node_type", ""),
            "submission_status": deliv.submission_status,
            "parent_node_name": "",
            "deliverables": [{
                "deliverable_name": deliv.deliverable_name,
                "target_amount": deliv.target_amount,
                "current_amount": deliv.current_amount,
            }],
        })
    return ApiResponse(data={"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/{node_id}")
async def get_node_detail(
    node_id: str,
    include: str = Query(default="", description="children,deliverables,dependencies（逗号分隔，预留）"),
):
    """查询节点详情 —— 需求文档 §8.1.2。"""
    svc = _get_service()
    detail = await svc.get_node_detail(node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")
    return ApiResponse(data=detail)


@router.patch("/{node_id}")
async def update_node(node_id: str, body: UpdateNodeRequest):
    """更新节点字段 —— 需求文档 §8.1.3。"""
    from emily_core.services.node_commands import UpdateNodeCommand

    svc = _get_service()
    cmd = UpdateNodeCommand(
        node_id=node_id,
        operator_id=body.operator_id,
        node_name=body.node_name,
        deadline=body.deadline,
        owner_dept_id=body.owner_dept_id,
        related_company_id=body.related_company_id,
        remark=body.remark,
        stage_id=body.stage_id,
        sort_order=body.sort_order,
        land_parcel_id=body.land_parcel_id,
        startup_doc_id=body.startup_doc_id,
    )
    result = await svc.update_node(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


# ══════════════════════════════════════════════════════════════════════════════
# 节点责任人 + 任务成果提交确认
# ══════════════════════════════════════════════════════════════════════════════


@router.patch("/{node_id}/assign")
async def assign_node(node_id: str, body: AssignNodeRequest):
    """变更节点责任人。"""
    from emily_core.services.node_commands import AssignNodeCommand

    svc = _get_service()
    cmd = AssignNodeCommand(
        node_id=node_id,
        responsible_user_id=body.responsible_user_id,
        operator_id=body.operator_id,
    )
    result = await svc.assign_node(cmd)
    if not result.success:
        status_code = 403 if result.error_code == "40302" else 400
        raise HTTPException(status_code=status_code, detail=result.message)
    return ApiResponse(message=result.message)


@cross_router.post("/node-deliverables/{deliverable_id}/submit")
async def submit_deliverable(deliverable_id: str, body: SubmitDeliverableRequest,
                              user_id: str = Query(default="")):
    """提交节点成果（PENDING → SUBMITTED）。"""
    from emily_core.services.node_commands import SubmitNodeDeliverableCommand

    svc = _get_service()
    cmd = SubmitNodeDeliverableCommand(
        deliverable_id=deliverable_id,
        content=body.content,
        file_url=body.file_url,
        file_name=body.file_name,
        attachment_file_id=body.attachment_file_id,
        submitted_by=user_id,
        is_acceptance_check=body.is_acceptance_check,
    )
    result = await svc.submit_deliverable(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@cross_router.post("/node-deliverables/{deliverable_id}/confirm")
async def confirm_deliverable(deliverable_id: str, body: ConfirmDeliverableRequest,
                               user_id: str = Query(default="")):
    """确认节点成果（SUBMITTED → CONFIRMED）。"""
    from emily_core.services.node_commands import ConfirmNodeDeliverableCommand

    svc = _get_service()
    cmd = ConfirmNodeDeliverableCommand(
        deliverable_id=deliverable_id,
        confirmed_by=user_id,
    )
    result = await svc.confirm_deliverable(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@cross_router.post("/node-deliverables/{deliverable_id}/return")
async def return_deliverable(deliverable_id: str, body: ReturnDeliverableRequest,
                              user_id: str = Query(default="")):
    """退回节点成果（SUBMITTED → RETURNED）。"""
    from emily_core.services.node_commands import ReturnNodeDeliverableCommand

    svc = _get_service()
    cmd = ReturnNodeDeliverableCommand(
        deliverable_id=deliverable_id,
        returned_by=user_id,
        reason=body.reason,
    )
    result = await svc.return_deliverable(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@cross_router.post("/node-deliverables/{deliverable_id}/resubmit")
async def resubmit_deliverable(deliverable_id: str, body: ResubmitDeliverableRequest,
                                user_id: str = Query(default="")):
    """重新提交节点成果（RETURNED → SUBMITTED）。"""
    from emily_core.services.node_commands import ResubmitNodeDeliverableCommand

    svc = _get_service()
    cmd = ResubmitNodeDeliverableCommand(
        deliverable_id=deliverable_id,
        content=body.content,
        file_url=body.file_url,
        file_name=body.file_name,
        attachment_file_id=body.attachment_file_id,
        submitted_by=user_id,
    )
    result = await svc.resubmit_deliverable(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@router.delete("/{node_id}")
async def discard_node(node_id: str, operator_id: str = Query(default="")):
    """废弃节点。"""
    from emily_core.services.node_commands import DiscardNodeCommand

    svc = _get_service()
    cmd = DiscardNodeCommand(node_id=node_id, operator_id=operator_id)
    result = await svc.discard_node(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@router.post("/{node_id}/activate")
async def activate_node(node_id: str, body: ActivateNodeRequest):
    """激活节点 —— 部门负责人审批 NOT_ACTIVATED 节点。

    审批通过 → 流转到 CONDITIONS_NOT_MET，正式纳入全景图。
    审批拒绝 → 节点废弃（is_discarded=True）。
    """
    from emily_core.services.node_commands import ActivateNodeCommand

    svc = _get_service()
    cmd = ActivateNodeCommand(
        node_id=node_id,
        approver_id=body.approver_id,
        approved=body.approved,
        remark=body.remark,
    )
    result = await svc.activate_node(cmd)
    if not result.success:
        status_code = 403 if result.error_code == "40302" else 400
        raise HTTPException(status_code=status_code, detail=result.message)
    return ApiResponse(
        message=result.message,
        data={"node_id": result.node_id, "status": result.status},
    )


# ══════════════════════════════════════════════════════════════════════════════
# 成果管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{node_id}/deliverables", status_code=201)
async def create_deliverable(node_id: str, body: CreateDeliverableRequest):
    """新增成果 —— 需求文档 §8.2.1。"""
    from emily_core.services.node_commands import CreateDeliverableCommand

    svc = _get_service()
    cmd = CreateDeliverableCommand(
        node_id=node_id,
        deliverable_name=body.deliverable_name,
        target_amount=body.target_amount,
        unit=body.unit,
        is_required=body.is_required,
        operator_id=body.operator_id,
    )
    result = await svc.create_deliverable(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@cross_router.patch("/node-deliverables/{deliverable_id}")
async def update_deliverable_progress(deliverable_id: str, body: UpdateDeliverableProgressRequest):
    """更新成果进度 —— 需求文档 §8.2.2。
    这是核心入口：更新后自动触发状态流转。
    """
    from emily_core.services.node_commands import UpdateDeliverableProgressCommand

    svc = _get_service()
    cmd = UpdateDeliverableProgressCommand(
        deliverable_id=deliverable_id,
        current_amount=body.current_amount,
        file_id=body.file_id,
        operator_id=body.operator_id,
    )
    result = await svc.update_deliverable_progress(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(
        message=result.message,
        data={
            "node_id": result.node_id,
            "status": result.status,
            "progress": result.progress,
            "affected_ancestors": result.affected_downstream,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# 依赖管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{node_id}/dependencies", status_code=201)
async def add_dependency(node_id: str, body: AddDependencyRequest):
    """添加依赖 —— 需求文档 §8.3.1。含循环检测前置。"""
    from emily_core.services.node_commands import AddDependencyCommand

    svc = _get_service()
    cmd = AddDependencyCommand(
        node_id=node_id,
        depends_on_deliverable_id=body.depends_on_deliverable_id,
        weight=body.weight,
        dependency_type=body.dependency_type,
        operator_id=body.operator_id,
    )
    result = await svc.add_dependency(cmd)
    if not result.success:
        status_code = 400
        if result.error_code == "40001":
            status_code = 400  # 循环依赖
        raise HTTPException(status_code=status_code, detail=result.message)
    return ApiResponse(message=result.message)


@cross_router.delete("/node-dependencies/{dependency_id}")
async def remove_dependency(dependency_id: str, operator_id: str = Query(default="")):
    """移除依赖 —— 需求文档 §8.3.2。"""
    from emily_core.services.node_commands import RemoveDependencyCommand

    svc = _get_service()
    cmd = RemoveDependencyCommand(dependency_id=dependency_id, operator_id=operator_id)
    result = await svc.remove_dependency(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


# ══════════════════════════════════════════════════════════════════════════════
# 子节点管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{parent_node_id}/children", status_code=201)
async def mount_child(parent_node_id: str, body: MountChildRequest):
    """挂载子节点 —— 需求文档 §8.4.1。"""
    from emily_core.services.node_commands import MountChildCommand

    svc = _get_service()
    cmd = MountChildCommand(
        parent_node_id=parent_node_id,
        child_node_id=body.child_node_id,
        child_weight=body.child_weight,
        operator_id=body.operator_id,
    )
    result = await svc.mount_child(cmd)
    if not result.success:
        status_code = 400
        if result.error_code == "40001":
            status_code = 400
        elif result.error_code == "40002":
            status_code = 400
        raise HTTPException(status_code=status_code, detail=result.message)
    return ApiResponse(message=result.message)


@router.delete("/{parent_node_id}/children/{child_node_id}")
async def unmount_child(parent_node_id: str, child_node_id: str, operator_id: str = Query(default="")):
    """移除子节点。"""
    from emily_core.services.node_commands import UnmountChildCommand

    svc = _get_service()
    cmd = UnmountChildCommand(
        parent_node_id=parent_node_id,
        child_node_id=child_node_id,
        operator_id=operator_id,
    )
    result = await svc.unmount_child(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)
