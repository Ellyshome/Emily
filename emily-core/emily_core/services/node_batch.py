"""全景节点图 V2 批量创建服务 —— CLI 与工具共享的核心逻辑。

职责：
  - 解析 YAML / dict 格式的节点树定义
  - 4 阶段有序执行：创建节点 → 添加成果 → 挂载父子 → 添加依赖
  - 依赖按名称模糊解析（无需硬编码 deliverable_id）
  - 幂等：已存在的节点跳过而非报错
  - dry-run 模式：只校验不写入
  - node_id 可省略：自动按 {prefix}-{NNN} 编号
  - creator_id 支持用户名：自动查 users 表解析为 UUID

调用方：
  - scripts/manage_nodes.py（CLI 运维脚本）
  - emily_core/tools/node_tool.py（系统内批量创建工具 handler）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger("emily.node_batch")


# ══════════════════════════════════════════════════════════════════════════════
# 节点树展平
# ══════════════════════════════════════════════════════════════════════════════

def flatten_nodes(
    nodes: list[dict],
    *,
    project_id: str = "",
    parent_node_id: str = "",
) -> list[dict]:
    """将嵌套的节点树展平为列表，保留 parent_node_id 信息。

    YAML 中 children 嵌套 → 展平后通过 parent_node_id 标记层级。
    node_id 为空时自动生成：NODE-{hash4}（基于 node_name + project_id）。
    """
    flat: list[dict] = []

    for node_def in nodes:
        node_id = node_def.get("node_id", "")
        if not node_id:
            # 自动生成：NODE-{4位hash}
            node_name = node_def.get("node_name", "未命名")
            node_id = _generate_node_id(node_name, project_id)
            logger.debug("自动生成 node_id: %s → %s", node_name, node_id)

        record = {
            "node_id": node_id,
            "node_name": node_def.get("node_name", ""),
            "deadline": node_def.get("deadline", ""),
            "owner_dept_id": node_def.get("owner_dept_id", "项目总"),
            "related_company_id": node_def.get("related_company_id", "建设单位"),
            "stage_id": node_def.get("stage_id", 0),
            "remark": node_def.get("remark", ""),
            "sort_order": node_def.get("sort_order", 0),
            "child_weight": node_def.get("child_weight", 1.0),
            "deliverables": node_def.get("deliverables", []),
            "dependencies": node_def.get("dependencies", []),
            "parent_node_id": parent_node_id,
        }
        flat.append(record)

        # 递归展平子节点
        children = node_def.get("children", [])
        if children:
            child_records = flatten_nodes(
                children,
                project_id=project_id,
                parent_node_id=node_id,
            )
            flat.extend(child_records)

    return flat


def _generate_node_id(node_name: str, project_id: str) -> str:
    """根据节点名称+项目ID生成 node_id（4位哈希）。"""
    clean = re.sub(r'[^一-龥a-zA-Z0-9]', '', node_name)
    hash_part = hashlib.md5(f"{clean}:{project_id}".encode()).hexdigest()[:4].upper()
    return f"NODE-{hash_part}"


# ══════════════════════════════════════════════════════════════════════════════
# 依赖解析：成果名称 → deliverable_id
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_deliverable_id(
    node_id: str,
    deliverable_name: str,
) -> str | None:
    """根据 node_id + deliverable_name 查找成果编号。

    支持精确匹配和包含匹配（名称包含关键字即可）。
    """
    from ..repositories.node_repo import NodeDeliverableRepo

    delivs = await asyncio.to_thread(NodeDeliverableRepo.find_by_node, node_id)
    # 精确匹配优先
    for d in delivs:
        if d.deliverable_name == deliverable_name:
            return d.deliverable_id
    # 包含匹配兜底
    for d in delivs:
        if deliverable_name in d.deliverable_name or d.deliverable_name in deliverable_name:
            return d.deliverable_id
    return None


async def _resolve_node_id_by_name(
    project_id: str,
    node_name: str,
) -> str | None:
    """根据 project_id + node_name 模糊查找节点编号。"""
    from ..repositories.node_repo import ProjectNodeRepo

    nodes = await asyncio.to_thread(ProjectNodeRepo.find_by_project, project_id)
    # 精确匹配优先
    for n in nodes:
        if n.node_name == node_name:
            return n.node_id
    # 包含匹配兜底
    for n in nodes:
        if node_name in n.node_name or n.node_name in node_name:
            return n.node_id
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 核心逻辑：create_node_tree
# ══════════════════════════════════════════════════════════════════════════════

async def create_node_tree(
    project_id: str,
    creator_id: str,
    nodes: list[dict],
    *,
    auto_activate: bool = True,
    dry_run: bool = False,
) -> list[dict]:
    """批量创建全景节点树。

    按顺序执行：
      1. 创建所有节点（含子节点，递归展开）
      2. 为每个节点添加成果
      3. 挂载父子关系
      4. 添加依赖关系（依赖按名称解析）

    Args:
        project_id: 项目 ID
        creator_id: 创建人 UUID（管理员则自动激活）
        nodes: 节点列表，每个节点为字典格式，支持嵌套 children
        auto_activate: 管理员创建时自动激活（跳过审批）
        dry_run: 只校验不写入

    Returns:
        操作结果列表，每项包含 {"node_id": str, "success": bool, "message": str, ...}
    """
    from .node_service import NodeService
    from .node_commands import (
        CreateNodeCommand,
        CreateDeliverableCommand,
        AddDependencyCommand,
        MountChildCommand,
    )
    from ..repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    results: list[dict] = []

    # ── 展平节点树（保留层级信息，node_id 为空时自动生成）──
    flat_nodes = flatten_nodes(nodes, project_id=project_id)

    # ── Phase 1: 创建所有节点 ──
    logger.info("Phase 1: 创建 %d 个节点", len(flat_nodes))
    for fn in flat_nodes:
        node_id = fn["node_id"]

        # 幂等检查：已存在则跳过
        existing = await asyncio.to_thread(
            svc._node_repo.get_by_node_id, node_id,
        )
        if existing and not existing.is_discarded:
            logger.info("  节点 %s 已存在，跳过创建", node_id)
            results.append({
                "node_id": node_id,
                "success": True,
                "phase": "create_node",
                "message": f"节点「{fn['node_name']}」已存在，跳过",
                "skipped": True,
            })
            continue

        if dry_run:
            results.append({
                "node_id": node_id,
                "success": True,
                "phase": "create_node",
                "message": f"[DRY-RUN] 将创建节点「{fn['node_name']}」",
                "dry_run": True,
            })
            continue

        cmd = CreateNodeCommand(
            project_id=project_id,
            node_id=node_id,
            node_name=fn["node_name"],
            deadline=fn.get("deadline", ""),
            owner_dept_id=fn.get("owner_dept_id", "项目总"),
            related_company_id=fn.get("related_company_id", "建设单位"),
            stage_id=fn.get("stage_id", 0),
            parent_node_id="",  # 父子关系在 Phase 3 挂载
            creator_id=creator_id,
            remark=fn.get("remark", ""),
            sort_order=fn.get("sort_order", 0),
        )

        try:
            r = await svc.create_node(cmd)
            results.append({
                "node_id": node_id,
                "success": r.success,
                "phase": "create_node",
                "status": r.status,
                "message": r.message,
            })
            if r.success:
                logger.info("  ✓ 节点 %s 创建成功: %s", node_id, r.message)
            else:
                logger.error("  ✗ 节点 %s 创建失败: %s", node_id, r.message)
        except Exception as e:
            results.append({
                "node_id": node_id,
                "success": False,
                "phase": "create_node",
                "message": str(e),
            })
            logger.error("  ✗ 节点 %s 创建异常: %s", node_id, e)

    # ── Phase 2: 为每个节点添加成果 ──
    deliverable_specs: list[dict] = []
    for fn in flat_nodes:
        for d in fn.get("deliverables", []):
            deliverable_specs.append({"node_id": fn["node_id"], **d})

    if deliverable_specs:
        logger.info("Phase 2: 添加 %d 个成果", len(deliverable_specs))
    for ds in deliverable_specs:
        node_id = ds["node_id"]
        deliv_name = ds.get("name", ds.get("deliverable_name", "未命名成果"))

        if dry_run:
            results.append({
                "node_id": node_id,
                "success": True,
                "phase": "create_deliverable",
                "message": f"[DRY-RUN] 将为节点 {node_id} 添加成果「{deliv_name}」",
                "dry_run": True,
            })
            continue

        cmd = CreateDeliverableCommand(
            node_id=node_id,
            deliverable_name=deliv_name,
            target_amount=float(ds.get("target", ds.get("target_amount", 1))),
            unit=ds.get("unit", "份"),
            is_required=ds.get("is_required", True),
            operator_id=creator_id,
        )

        try:
            r = await svc.create_deliverable(cmd)
            results.append({
                "node_id": node_id,
                "success": r.success,
                "phase": "create_deliverable",
                "message": r.message,
            })
            if r.success:
                logger.info("  ✓ 成果「%s」→ %s", deliv_name, node_id)
            else:
                logger.error("  ✗ 成果「%s」→ %s: %s", deliv_name, node_id, r.message)
        except Exception as e:
            results.append({
                "node_id": node_id,
                "success": False,
                "phase": "create_deliverable",
                "message": str(e),
            })
            logger.error("  ✗ 成果「%s」异常: %s", deliv_name, e)

    # ── Phase 3: 挂载父子关系 ──
    mount_specs: list[dict] = []
    for fn in flat_nodes:
        parent_node_id = fn.get("parent_node_id")
        if parent_node_id:
            mount_specs.append({
                "parent_node_id": parent_node_id,
                "child_node_id": fn["node_id"],
                "child_weight": fn.get("child_weight", 1.0),
            })

    if mount_specs:
        logger.info("Phase 3: 挂载 %d 个父子关系", len(mount_specs))
    for ms in mount_specs:
        if dry_run:
            results.append({
                "node_id": ms["child_node_id"],
                "success": True,
                "phase": "mount_child",
                "message": f"[DRY-RUN] 将 {ms['child_node_id']} 挂载到 {ms['parent_node_id']}",
                "dry_run": True,
            })
            continue

        cmd = MountChildCommand(
            parent_node_id=ms["parent_node_id"],
            child_node_id=ms["child_node_id"],
            child_weight=ms["child_weight"],
            operator_id=creator_id,
        )

        try:
            r = await svc.mount_child(cmd)
            results.append({
                "node_id": ms["child_node_id"],
                "success": r.success,
                "phase": "mount_child",
                "message": r.message,
            })
            if r.success:
                logger.info("  ✓ 挂载 %s → %s", ms["child_node_id"], ms["parent_node_id"])
            else:
                logger.error("  ✗ 挂载 %s → %s: %s", ms["child_node_id"], ms["parent_node_id"], r.message)
        except Exception as e:
            results.append({
                "node_id": ms["child_node_id"],
                "success": False,
                "phase": "mount_child",
                "message": str(e),
            })
            logger.error("  ✗ 挂载异常: %s", e)

    # ── Phase 4: 添加依赖关系 ──
    dep_specs: list[dict] = []
    for fn in flat_nodes:
        for dep in fn.get("dependencies", []):
            dep_specs.append({"node_id": fn["node_id"], **dep})

    if dep_specs:
        logger.info("Phase 4: 添加 %d 个依赖关系", len(dep_specs))
    for ds in dep_specs:
        downstream_node_id = ds["node_id"]

        # 解析上游节点：支持 node_id 或 node_name
        upstream_node_id = ds.get("upstream_node_id", "")
        upstream_node_name = ds.get("upstream_node_name", ds.get("node_name", ""))
        if not upstream_node_id and upstream_node_name:
            upstream_node_id = await _resolve_node_id_by_name(project_id, upstream_node_name)

        # 解析上游成果：支持 deliverable_id 或 deliverable_name
        deliverable_id = ds.get("depends_on_deliverable_id", "")
        deliverable_name = ds.get("deliverable_name", "")
        if not deliverable_id and deliverable_name:
            if upstream_node_id:
                deliverable_id = await _resolve_deliverable_id(upstream_node_id, deliverable_name)
            else:
                # 跨节点搜索：在所有已创建节点中查找 deliverable
                for fn_other in flat_nodes:
                    candidate_id = await _resolve_deliverable_id(fn_other["node_id"], deliverable_name)
                    if candidate_id:
                        deliverable_id = candidate_id
                        upstream_node_id = fn_other["node_id"]
                        break

        if not deliverable_id:
            msg = (
                f"无法解析依赖：节点 {downstream_node_id} 的依赖"
                f"(upstream={upstream_node_id or upstream_node_name}, "
                f"deliverable={deliverable_id or deliverable_name}) 未找到"
            )
            logger.error("  ✗ %s", msg)
            results.append({
                "node_id": downstream_node_id,
                "success": False,
                "phase": "add_dependency",
                "message": msg,
            })
            continue

        weight = float(ds.get("weight", 1.0))

        if dry_run:
            results.append({
                "node_id": downstream_node_id,
                "success": True,
                "phase": "add_dependency",
                "message": f"[DRY-RUN] 将为 {downstream_node_id} 添加依赖 {deliverable_id} (weight={weight})",
                "dry_run": True,
            })
            continue

        cmd = AddDependencyCommand(
            node_id=downstream_node_id,
            depends_on_deliverable_id=deliverable_id,
            weight=weight,
            operator_id=creator_id,
        )

        try:
            r = await svc.add_dependency(cmd)
            results.append({
                "node_id": downstream_node_id,
                "success": r.success,
                "phase": "add_dependency",
                "message": r.message,
            })
            if r.success:
                logger.info("  ✓ 依赖 %s → %s", downstream_node_id, deliverable_id)
            else:
                logger.error("  ✗ 依赖 %s → %s: %s", downstream_node_id, deliverable_id, r.message)
        except Exception as e:
            results.append({
                "node_id": downstream_node_id,
                "success": False,
                "phase": "add_dependency",
                "message": str(e),
            })
            logger.error("  ✗ 依赖异常: %s", e)

    return results
