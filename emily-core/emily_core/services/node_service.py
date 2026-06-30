"""全景节点图 V2 Service 层 —— 核心业务逻辑。

职责：
  - 节点/成果/依赖的 CRUD 编排
  - 调用状态机引擎 + 写入 DB
  - 循环依赖检测前置（BFS）
  - 父子进度重算（递归 ≤3 层）
  - 事件记录（状态流转、操作审计）

基于需求文档 §4.1–§4.5。参照模式：plan_task_service.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .node_commands import (
    CreateNodeCommand,
    UpdateNodeCommand,
    DiscardNodeCommand,
    CreateDeliverableCommand,
    UpdateDeliverableProgressCommand,
    AddDependencyCommand,
    RemoveDependencyCommand,
    MountChildCommand,
    UnmountChildCommand,
    NodeOperationResult,
    CycleCheckResult,
    StateTransitionResult,
)
from .node_state_machine import (
    CONDITIONS_NOT_MET,
    IN_PROGRESS,
    COMPLETED,
    NodeSnapshot,
    DependencySnapshot,
    DeliverableSnapshot,
    ChildSnapshot,
    calc_dependency_satisfaction,
    calc_deliverable_completion,
    determine_node_status,
    calc_parent_progress,
    detect_cycle,
    check_parent_child_cycle,
)
from ..repositories.node_repo import (
    ProjectNodeRepo,
    NodeDependencyRepo,
    NodeDeliverableRepo,
    NodeEventRepo,
    _parse_decimal,
    _to_decimal_str,
)
from ..infrastructure.database.models import _new_id

if TYPE_CHECKING:
    from ..infrastructure.database.models import (
        ProjectNode,
        NodeDependency,
        NodeDeliverable,
    )

logger = logging.getLogger("emily.node_service")

BEIJING_TZ = timezone(timedelta(hours=8))

# 子节点数量上限
MAX_CHILDREN_PER_PARENT = 100
# 最大递归深度
MAX_ANCESTOR_RECALC_DEPTH = 3


# ══════════════════════════════════════════════════════════════════════════════
# NodeService
# ══════════════════════════════════════════════════════════════════════════════

class NodeService:
    """全景节点图核心业务 Service。"""

    def __init__(
        self,
        node_repo: ProjectNodeRepo | None = None,
        dependency_repo: NodeDependencyRepo | None = None,
        deliverable_repo: NodeDeliverableRepo | None = None,
        event_repo: NodeEventRepo | None = None,
    ):
        self._node_repo = node_repo or ProjectNodeRepo()
        self._dep_repo = dependency_repo or NodeDependencyRepo()
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()
        self._event_repo = event_repo or NodeEventRepo()

    # ── 辅助方法 ──

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _record_event(self, node_id: str, event_type: str,
                      old_value: str = "", new_value: str = "",
                      operator_id: str = "", remark: str = "") -> None:
        """记录事件（同步写入，fire-and-forget）。"""
        try:
            self._event_repo.create(
                event_id=_new_id("EVT"),
                node_id=node_id,
                event_type=event_type,
                old_value=old_value,
                new_value=new_value,
                operator_id=operator_id,
                remark=remark,
            )
        except Exception:
            logger.exception("Failed to record event for node %s", node_id)

    # ── 节点 CRUD ──

    async def create_node(self, cmd: CreateNodeCommand) -> NodeOperationResult:
        """创建节点。"""
        child_weight_str = _to_decimal_str(cmd.child_weight, precision=4)

        node = await asyncio.to_thread(
            self._node_repo.create,
            project_id=cmd.project_id,
            node_id=cmd.node_id,
            node_name=cmd.node_name,
            owner_dept_id=cmd.owner_dept_id,
            related_company_id=cmd.related_company_id,
            deadline=cmd.deadline,
            creator_id=cmd.creator_id,
            parent_node_id=cmd.parent_node_id,
            stage_id=cmd.stage_id,
            child_weight=child_weight_str,
            remark=cmd.remark,
            land_parcel_id=cmd.land_parcel_id,
            startup_doc_id=cmd.startup_doc_id,
            sort_order=cmd.sort_order,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_created",
            new_value=json.dumps({"node_name": cmd.node_name, "project_id": cmd.project_id}),
            operator_id=cmd.creator_id,
            remark="节点创建",
        )

        logger.info("Node created: %s (project=%s)", cmd.node_id, cmd.project_id)
        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            message=f"节点「{cmd.node_name}」创建成功",
        )

    async def update_node(self, cmd: UpdateNodeCommand) -> NodeOperationResult:
        """更新节点字段。"""
        updates = {}
        if cmd.node_name is not None:
            updates["node_name"] = cmd.node_name
        if cmd.deadline is not None:
            updates["deadline"] = cmd.deadline
        if cmd.owner_dept_id is not None:
            updates["owner_dept_id"] = cmd.owner_dept_id
        if cmd.related_company_id is not None:
            updates["related_company_id"] = cmd.related_company_id
        if cmd.remark is not None:
            updates["remark"] = cmd.remark
        if cmd.stage_id is not None:
            updates["stage_id"] = cmd.stage_id
        if cmd.sort_order is not None:
            updates["sort_order"] = cmd.sort_order
        if cmd.land_parcel_id is not None:
            updates["land_parcel_id"] = cmd.land_parcel_id
        if cmd.startup_doc_id is not None:
            updates["startup_doc_id"] = cmd.startup_doc_id

        if not updates:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="无更新字段")

        old_node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if old_node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        node = await asyncio.to_thread(self._node_repo.update_fields, cmd.node_id, **updates)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="更新失败")

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_updated",
            old_value=json.dumps({"node_name": old_node.node_name, "deadline": old_node.deadline}),
            new_value=json.dumps(updates),
            operator_id=cmd.operator_id,
            remark="节点字段更新",
        )

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            message="节点更新成功",
        )

    async def discard_node(self, cmd: DiscardNodeCommand) -> NodeOperationResult:
        """废弃节点。已完成或未完成的子节点存在时阻止。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        # 检查子节点：已完成的不可废弃
        children = await asyncio.to_thread(self._node_repo.find_by_parent, cmd.node_id)
        for child in children:
            if child.status == COMPLETED:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"子节点「{child.node_id}」已完成，不可废弃父节点",
                )

        await asyncio.to_thread(self._node_repo.discard, cmd.node_id)

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_discarded",
            old_value=json.dumps({"status": node.status}),
            operator_id=cmd.operator_id,
            remark="节点废弃",
        )

        return NodeOperationResult(success=True, node_id=cmd.node_id, message="节点已废弃")

    # ── 成果管理 ──

    async def create_deliverable(self, cmd: CreateDeliverableCommand) -> NodeOperationResult:
        """为节点新增成果。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        seq = await asyncio.to_thread(self._deliv_repo.get_next_seq, cmd.node_id)
        deliverable_id = self._deliv_repo.generate_deliverable_id(cmd.node_id, seq)

        await asyncio.to_thread(
            self._deliv_repo.create,
            deliverable_id=deliverable_id,
            node_id=cmd.node_id,
            deliverable_name=cmd.deliverable_name,
            target_amount=_to_decimal_str(cmd.target_amount, precision=2),
            unit=cmd.unit,
            is_required=cmd.is_required,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="deliverable_updated",
            new_value=json.dumps({"deliverable_id": deliverable_id, "name": cmd.deliverable_name}),
            operator_id=cmd.operator_id,
            remark=f"新增成果：{cmd.deliverable_name}",
        )

        # 新增成果可能改变完成度，触发状态重算
        await self._recalc_node_status(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message=f"成果「{cmd.deliverable_name}」创建成功",
        )

    async def update_deliverable_progress(self, cmd: UpdateDeliverableProgressCommand) -> NodeOperationResult:
        """更新成果进度——核心入口，触发状态流转。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(
                success=False, node_id="",
                message=f"成果 {cmd.deliverable_id} 不存在",
            )

        old_amount = deliv.current_amount
        amount_str = _to_decimal_str(cmd.current_amount, precision=2)

        await asyncio.to_thread(
            self._deliv_repo.update_progress,
            cmd.deliverable_id,
            amount_str,
            cmd.file_id,
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_updated",
            old_value=json.dumps({"current_amount": old_amount}),
            new_value=json.dumps({"current_amount": amount_str, "file_id": cmd.file_id}),
            operator_id=cmd.operator_id,
            remark=f"成果进度更新：{old_amount} → {amount_str}",
        )

        # 关键：成果进度更新 → 触发状态重算
        result = await self._recalc_node_status(deliv.node_id)
        return result

    # ── 依赖管理 ──

    async def add_dependency(self, cmd: AddDependencyCommand) -> NodeOperationResult:
        """添加依赖——含循环检测前置。"""
        # 1. 查上游成果所属节点
        dep_deliv = await asyncio.to_thread(
            self._deliv_repo.get_by_deliverable_id, cmd.depends_on_deliverable_id,
        )
        if dep_deliv is None:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"成果 {cmd.depends_on_deliverable_id} 不存在",
            )

        upstream_node_id = dep_deliv.node_id

        # 2. 禁止自己依赖自己
        if upstream_node_id == cmd.node_id:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="节点不能依赖自己的成果",
                error_code="40001",
            )

        # 3. 禁止子节点依赖父节点（任何层级）
        if await self._is_ancestor_dependency(cmd.node_id, upstream_node_id):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="子节点不能依赖父节点（任何层级）",
                error_code="40001",
            )

        # 4. BFS 循环检测
        cycle_result = await self._check_cycle(cmd.node_id, cmd.depends_on_deliverable_id)
        if cycle_result.has_cycle:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"循环依赖：{' → '.join(cycle_result.cycle_path)}",
                error_code="40001",
            )

        # 5. 检查重复
        if await asyncio.to_thread(
            self._dep_repo.exists, cmd.node_id, cmd.depends_on_deliverable_id,
        ):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="该依赖关系已存在",
            )

        # 6. 创建依赖
        weight_str = _to_decimal_str(cmd.weight, precision=4)
        await asyncio.to_thread(
            self._dep_repo.create,
            node_id=cmd.node_id,
            depends_on_deliverable_id=cmd.depends_on_deliverable_id,
            depends_on_node_id=upstream_node_id,
            weight=weight_str,
            dependency_type=cmd.dependency_type,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="dependency_added",
            new_value=json.dumps({
                "depends_on_deliverable_id": cmd.depends_on_deliverable_id,
                "depends_on_node_id": upstream_node_id,
                "weight": weight_str,
            }),
            operator_id=cmd.operator_id,
            remark=f"新增依赖：{cmd.depends_on_deliverable_id} (权重{weight_str})",
        )

        if cmd.weight >= 999.0:
            self._record_event(
                node_id=cmd.node_id,
                event_type="BLOCKING_CONDITION_ADDED",
                new_value=json.dumps({"deliverable_id": cmd.depends_on_deliverable_id}),
                operator_id=cmd.operator_id,
                remark="人工阻塞条件",
            )

        # 7. 依赖变更 → 重新计算状态
        await self._recalc_node_status(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message="依赖添加成功",
        )

    async def remove_dependency(self, cmd: RemoveDependencyCommand) -> NodeOperationResult:
        """移除依赖。"""
        dep = await asyncio.to_thread(self._dep_repo.get_by_id, cmd.dependency_id)
        if dep is None:
            return NodeOperationResult(success=False, message="依赖不存在")

        node_id = dep.node_id
        is_blocking = _parse_decimal(dep.weight) >= 999.0

        await asyncio.to_thread(self._dep_repo.delete, cmd.dependency_id)

        self._record_event(
            node_id=node_id,
            event_type="dependency_removed",
            old_value=json.dumps({"depends_on_deliverable_id": dep.depends_on_deliverable_id}),
            operator_id=cmd.operator_id,
            remark="移除依赖",
        )

        if is_blocking:
            self._record_event(
                node_id=node_id,
                event_type="BLOCKING_CONDITION_REMOVED",
                operator_id=cmd.operator_id,
                remark="解除阻塞条件",
            )

        # 依赖移除 → 重新计算状态
        await self._recalc_node_status(node_id)

        return NodeOperationResult(success=True, node_id=node_id, message="依赖已移除")

    # ── 子节点管理 ──

    async def mount_child(self, cmd: MountChildCommand) -> NodeOperationResult:
        """挂载子节点。"""
        # 1. 数量上限检查
        count = await asyncio.to_thread(self._node_repo.count_children, cmd.parent_node_id)
        if count >= MAX_CHILDREN_PER_PARENT:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message=f"子节点数量已达上限（{MAX_CHILDREN_PER_PARENT}）",
                error_code="40002",
            )

        # 2. 深度检查：追溯到根，最多2级（挂载后最多3级）
        ancestors = await asyncio.to_thread(
            self._node_repo.get_ancestor_chain, cmd.parent_node_id, max_depth=2,
        )
        if len(ancestors) >= 2:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message="嵌套深度已达上限（3层），无法继续挂载子节点",
            )

        # 3. 循环检查：parent 不能是 child 的后代
        all_parents = {cmd.parent_node_id}
        for a in ancestors:
            all_parents.add(a.node_id)
        child_ancestors = await asyncio.to_thread(
            self._node_repo.get_ancestor_chain, cmd.child_node_id, max_depth=3,
        )
        for ca in child_ancestors:
            if ca.node_id in all_parents:
                return NodeOperationResult(
                    success=False, node_id=cmd.parent_node_id,
                    message="不能将祖先节点挂载为子节点",
                    error_code="40001",
                )

        # 4. 更新子节点
        weight_str = _to_decimal_str(cmd.child_weight, precision=4)
        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.child_node_id,
            parent_node_id=cmd.parent_node_id,
            child_weight=weight_str,
        )

        self._record_event(
            node_id=cmd.child_node_id,
            event_type="child_node_mounted",
            new_value=json.dumps({"parent_node_id": cmd.parent_node_id}),
            operator_id=cmd.operator_id,
            remark=f"挂载到父节点 {cmd.parent_node_id}",
        )

        # 更新父节点进度
        await self._recalc_node_status(cmd.parent_node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.child_node_id,
            message=f"子节点已挂载到 {cmd.parent_node_id}",
        )

    async def unmount_child(self, cmd: UnmountChildCommand) -> NodeOperationResult:
        """移除子节点关联。"""
        child = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.child_node_id)
        if child is None or child.parent_node_id != cmd.parent_node_id:
            return NodeOperationResult(
                success=False, node_id=cmd.child_node_id,
                message="父子关系不匹配",
            )

        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.child_node_id,
            parent_node_id="",
            child_weight="1.0000",
        )

        self._record_event(
            node_id=cmd.child_node_id,
            event_type="child_node_unmounted",
            old_value=json.dumps({"parent_node_id": cmd.parent_node_id}),
            operator_id=cmd.operator_id,
            remark=f"从父节点 {cmd.parent_node_id} 移除",
        )

        await self._recalc_node_status(cmd.parent_node_id)

        return NodeOperationResult(
            success=True, node_id=cmd.child_node_id, message="子节点已移除",
        )

    # ── 查询方法 ──

    async def get_node_detail(self, node_id: str) -> dict | None:
        """查询节点详情（含子节点、成果、依赖）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        children = await asyncio.to_thread(self._node_repo.find_by_parent, node_id)
        delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, node_id)
        deps = await asyncio.to_thread(self._dep_repo.find_by_node, node_id)

        return {
            "node_id": node.node_id,
            "node_name": node.node_name,
            "project_id": node.project_id,
            "status": node.status,
            "progress": _parse_decimal(node.progress),
            "deadline": node.deadline,
            "owner_dept_id": node.owner_dept_id,
            "related_company_id": node.related_company_id,
            "parent_node_id": node.parent_node_id,
            "stage_id": node.stage_id,
            "remark": node.remark,
            "is_discarded": node.is_discarded,
            "created_at": node.created_at,
            "children": [
                {
                    "node_id": c.node_id,
                    "node_name": c.node_name,
                    "status": c.status,
                    "progress": _parse_decimal(c.progress),
                }
                for c in children
            ],
            "deliverables": [
                {
                    "deliverable_id": d.deliverable_id,
                    "deliverable_name": d.deliverable_name,
                    "target_amount": _parse_decimal(d.target_amount),
                    "current_amount": _parse_decimal(d.current_amount),
                    "unit": d.unit,
                    "is_required": d.is_required,
                    "file_id": d.file_id,
                    "completed_at": d.completed_at,
                }
                for d in delivs
            ],
            "dependencies": [
                {
                    "id": d.id,
                    "depends_on_deliverable_id": d.depends_on_deliverable_id,
                    "depends_on_node_id": d.depends_on_node_id,
                    "weight": _parse_decimal(d.weight),
                    "dependency_type": d.dependency_type,
                }
                for d in deps
            ],
        }

    # ── 状态重算核心 ──

    async def _recalc_node_status(self, node_id: str) -> NodeOperationResult:
        """重新计算节点状态（文件上传/成果更新/依赖变更时触发）。

        流程：
        1. 加载节点快照（含子节点、成果、依赖）
        2. 构建 deliverable_file_status 映射
        3. 调用引擎 determine_node_status
        4. 如有变更，写入 DB + 记录事件
        5. 递归更新祖先节点进度（最多 3 层）
        """
        snap = await self._build_snapshot(node_id)
        if snap is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")

        # 构建 deliverable_file_status
        file_status = {}
        for dep in snap.dependencies:
            # 检查依赖的成果文件是否已上传完成
            dep_deliv = await asyncio.to_thread(
                self._deliv_repo.get_by_deliverable_id, dep.depends_on_deliverable_id,
            )
            if dep_deliv:
                current = _parse_decimal(dep_deliv.current_amount)
                target = max(_parse_decimal(dep_deliv.target_amount), 0.001)
                file_status[dep.depends_on_deliverable_id] = (current >= target)

        old_status = snap.status
        old_progress = snap.progress

        # 调用引擎
        new_status = determine_node_status(
            snap.dependencies, snap.deliverables, file_status, snap.children,
        )

        # 进度计算
        if snap.children:
            new_progress = calc_parent_progress(snap.children)
        else:
            new_progress = calc_deliverable_completion(snap.deliverables) * 100.0

        # 检查是否需要更新
        status_changed = (new_status != old_status)
        progress_changed = abs(new_progress - old_progress) > 0.001

        if not status_changed and not progress_changed:
            return NodeOperationResult(
                success=True, node_id=node_id, status=old_status, progress=old_progress,
                message="状态无变化",
            )

        # 写入 DB
        await asyncio.to_thread(self._node_repo.update_progress, node_id, new_progress)
        if status_changed:
            await asyncio.to_thread(self._node_repo.update_status, node_id, new_status)

            # 记录状态变更事件
            self._record_event(
                node_id=node_id,
                event_type="status_changed",
                old_value=json.dumps({"status": old_status}),
                new_value=json.dumps({"status": new_status, "progress": new_progress}),
                remark="状态自动流转",
            )
            if new_status == COMPLETED:
                self._record_event(
                    node_id=node_id,
                    event_type="auto_triggered",
                    remark="节点已完成",
                )

        # 递归更新祖先（最多 3 层）
        affected = []
        current_id = node_id
        for depth in range(MAX_ANCESTOR_RECALC_DEPTH):
            current = await asyncio.to_thread(self._node_repo.get_by_node_id, current_id)
            if current is None or not current.parent_node_id:
                break
            parent_id = current.parent_node_id
            await self._recalc_parent_progress(parent_id)
            affected.append(parent_id)
            current_id = parent_id

        logger.info(
            "Node %s recalc: status %s->%s, progress %.2f->%.2f, ancestors=%s",
            node_id, old_status, new_status, old_progress, new_progress, affected,
        )

        return NodeOperationResult(
            success=True,
            node_id=node_id,
            status=new_status,
            progress=new_progress,
            message=f"状态重算完成：{old_status} → {new_status}",
            affected_downstream=affected,
        )

    async def _recalc_parent_progress(self, parent_node_id: str) -> None:
        """重算父节点进度（不递归，仅当前层）。"""
        children = await asyncio.to_thread(self._node_repo.find_by_parent, parent_node_id)
        if not children:
            return

        child_snapshots = [
            ChildSnapshot(
                node_id=c.node_id,
                status=c.status,
                progress=_parse_decimal(c.progress),
                child_weight=_parse_decimal(c.child_weight),
            )
            for c in children
        ]

        new_progress = calc_parent_progress(child_snapshots)
        new_status = determine_node_status([], [], {}, child_snapshots)

        node = await asyncio.to_thread(self._node_repo.get_by_node_id, parent_node_id)
        if node:
            old_status = node.status
            await asyncio.to_thread(self._node_repo.update_progress, parent_node_id, new_progress)
            if new_status != old_status:
                await asyncio.to_thread(self._node_repo.update_status, parent_node_id, new_status)

    async def _build_snapshot(self, node_id: str) -> NodeSnapshot | None:
        """构建节点快照（供引擎计算）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        snap = NodeSnapshot(
            node_id=node.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            parent_node_id=node.parent_node_id,
        )

        # 加载依赖
        deps = await asyncio.to_thread(self._dep_repo.find_by_node, node_id)
        snap.dependencies = [
            DependencySnapshot(
                depends_on_deliverable_id=d.depends_on_deliverable_id,
                depends_on_node_id=d.depends_on_node_id,
                weight=_parse_decimal(d.weight),
                dependency_type=d.dependency_type,
            )
            for d in deps
        ]

        # 加载成果
        delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, node_id)
        snap.deliverables = [
            DeliverableSnapshot(
                deliverable_id=d.deliverable_id,
                target_amount=_parse_decimal(d.target_amount),
                current_amount=_parse_decimal(d.current_amount),
                is_required=d.is_required,
                file_id=d.file_id,
            )
            for d in delivs
        ]

        # 加载子节点
        children = await asyncio.to_thread(self._node_repo.find_by_parent, node_id)
        snap.children = [
            ChildSnapshot(
                node_id=c.node_id,
                status=c.status,
                progress=_parse_decimal(c.progress),
                child_weight=_parse_decimal(c.child_weight),
            )
            for c in children
        ]

        return snap

    # ── 循环检测辅助 ──

    async def _check_cycle(self, node_id: str, depends_on_deliverable_id: str) -> CycleCheckResult:
        """BFS 循环依赖检测。"""
        # 查询成果所属上游节点
        dep_deliv = await asyncio.to_thread(
            self._deliv_repo.get_by_deliverable_id, depends_on_deliverable_id,
        )
        if dep_deliv is None:
            return CycleCheckResult(has_cycle=False)

        upstream_node = dep_deliv.node_id
        if upstream_node == node_id:
            return CycleCheckResult(has_cycle=True, cycle_path=[node_id, node_id],
                                   message="节点不能依赖自己的成果")

        # 获取 node_id 的项目上下文
        node_obj = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node_obj is None:
            return CycleCheckResult(has_cycle=False)

        # 构建 {deliverable_id: node_id} 映射（项目范围内）
        all_nodes = await asyncio.to_thread(self._node_repo.find_by_project, node_obj.project_id)
        all_node_ids = [n.node_id for n in all_nodes]

        deliverable_to_node: dict[str, str] = {}
        for nid in all_node_ids:
            delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, nid)
            for d in delivs:
                deliverable_to_node[d.deliverable_id] = d.node_id

        # 构建 {node_id: [upstream_node_id]} — node 依赖了哪些上游节点
        node_deps: dict[str, list[str]] = {}
        for nid in all_node_ids:
            deps = await asyncio.to_thread(self._dep_repo.find_by_node, nid)
            upstream_ids = list(set(d.depends_on_node_id for d in deps))
            node_deps[nid] = upstream_ids

        has_cycle, path = detect_cycle(
            node_id, depends_on_deliverable_id, deliverable_to_node, node_deps,
        )

        return CycleCheckResult(
            has_cycle=has_cycle,
            cycle_path=path,
            message=" → ".join(path) if has_cycle else "",
        )

    async def _is_ancestor_dependency(self, node_id: str, upstream_node_id: str) -> bool:
        """检查 upstream_node_id 是否是 node_id 的祖先（父子层级）。"""
        ancestors = await asyncio.to_thread(self._node_repo.get_ancestor_chain, node_id, max_depth=3)
        for a in ancestors:
            if a.node_id == upstream_node_id:
                return True
        return False
