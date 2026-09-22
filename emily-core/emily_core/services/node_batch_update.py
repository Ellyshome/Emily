"""全景节点图 V2 批量更新服务 —— CLI 与工具共享的核心逻辑。

职责：
  - 批量更新节点字段（名称/截止时间/阶段等）
  - 批量签认节点（替代原批量激活/审批）
  - 批量废弃节点
  - 批量更新成果进度
  - 批量管理节点文件关联（共享文件/条件文件/成果文件）
  - 依赖按名称模糊解析

调用方：
  - scripts/manage_nodes.py（CLI 运维脚本 update 子命令）
  - emily_core/tools/node_tool.py（系统内批量更新工具 handler）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("emily.node_batch_update")


# ══════════════════════════════════════════════════════════════════════════════
# 依赖解析（复用 node_batch 中的解析逻辑）
# ══════════════════════════════════════════════════════════════════════════════

async def _resolve_deliverable_id(node_id: str, deliverable_name: str) -> str | None:
    """根据 node_id + deliverable_name 查找成果编号。"""
    from ..repositories.node_repo import NodeDeliverableRepo

    delivs = await asyncio.to_thread(NodeDeliverableRepo.find_by_node, node_id)
    for d in delivs:
        if d.deliverable_name == deliverable_name:
            return d.deliverable_id
    for d in delivs:
        if deliverable_name in d.deliverable_name or d.deliverable_name in deliverable_name:
            return d.deliverable_id
    return None


async def _resolve_node_id_by_name(project_id: str, node_name: str) -> str | None:
    """根据 project_id + node_name 查找节点编号。

    口径（见 docs/Spec/项目归属与可见范围_Spec.md）：名称只用于**入口侧**定位，
    唯一命中才返回；多候选一律返回 None，避免静默取第一个写错节点。
    """
    from ..repositories.node_repo import ProjectNodeRepo

    nodes = await asyncio.to_thread(ProjectNodeRepo.find_by_project, project_id)
    exact = [n for n in nodes if n.node_name == node_name]
    if len(exact) == 1:
        return exact[0].node_id
    if len(exact) > 1:
        logger.warning("节点名「%s」在项目 %s 命中 %d 个同名节点，拒绝猜测",
                       node_name, project_id, len(exact))
        return None
    fuzzy = [n for n in nodes if node_name in n.node_name or n.node_name in node_name]
    if len(fuzzy) == 1:
        return fuzzy[0].node_id
    if len(fuzzy) > 1:
        logger.warning("节点名「%s」在项目 %s 模糊命中 %d 个节点，拒绝猜测",
                       node_name, project_id, len(fuzzy))
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 批量更新节点字段
# ══════════════════════════════════════════════════════════════════════════════

async def batch_update_nodes(
    updates: list[dict],
    *,
    operator_id: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """批量更新节点字段。

    Args:
        updates: 更新列表，每项含：
            - node_id: 节点编号（必填）
            - 可选字段: node_name, deadline, related_company_id, remark
        operator_id: 操作人 UUID
        dry_run: 只校验不写入

    Returns:
        操作结果列表
    """
    from .node_service import NodeService
    from .node_commands import UpdateNodeCommand
    from ..repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    results: list[dict] = []

    logger.info("批量更新 %d 个节点", len(updates))

    for u in updates:
        node_id = u.get("node_id", "")
        if not node_id:
            results.append({
                "node_id": "",
                "success": False,
                "phase": "update_node",
                "message": "缺少 node_id",
            })
            continue

        if dry_run:
            results.append({
                "node_id": node_id,
                "success": True,
                "phase": "update_node",
                "message": f"[DRY-RUN] 将更新节点 {node_id}: {list(k for k in u if k != 'node_id')}",
                "dry_run": True,
            })
            continue

        cmd = UpdateNodeCommand(
            node_id=node_id,
            operator_id=operator_id,
            node_name=u.get("node_name"),
            deadline=u.get("deadline"),
            related_company_id=u.get("related_company_id"),
            remark=u.get("remark"),
        )

        try:
            r = await svc.update_node(cmd)
            results.append({
                "node_id": node_id,
                "success": r.success,
                "phase": "update_node",
                "status": r.status,
                "message": r.message,
            })
        except Exception as e:
            results.append({
                "node_id": node_id,
                "success": False,
                "phase": "update_node",
                "message": str(e),
            })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 批量废弃节点
# ══════════════════════════════════════════════════════════════════════════════

async def batch_discard_nodes(
    node_ids: list[str],
    *,
    operator_id: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """批量废弃节点。

    Args:
        node_ids: 节点编号列表
        operator_id: 操作人 UUID
        dry_run: 只校验不写入
    """
    from .node_service import NodeService
    from .node_commands import DiscardNodeCommand
    from ..repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    results: list[dict] = []

    logger.info("批量废弃 %d 个节点", len(node_ids))

    for node_id in node_ids:
        if dry_run:
            results.append({
                "node_id": node_id,
                "success": True,
                "phase": "discard_node",
                "message": f"[DRY-RUN] 将废弃节点 {node_id}",
                "dry_run": True,
            })
            continue

        cmd = DiscardNodeCommand(
            node_id=node_id,
            operator_id=operator_id,
        )

        try:
            r = await svc.discard_node(cmd)
            results.append({
                "node_id": node_id,
                "success": r.success,
                "phase": "discard_node",
                "message": r.message,
            })
        except Exception as e:
            results.append({
                "node_id": node_id,
                "success": False,
                "phase": "discard_node",
                "message": str(e),
            })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 批量停用 / 恢复（US-17）
# ══════════════════════════════════════════════════════════════════════════════

async def batch_disable_nodes(
    node_ids: list[str],
    *,
    operator_id: str = "",
    reason: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """批量停用节点（L4+；非终态可恢复）。"""
    from .node_service import NodeService
    from .node_commands import DisableNodeCommand
    from ..repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    results: list[dict] = []
    for node_id in (node_ids or []):
        if dry_run:
            results.append({"node_id": node_id, "success": True, "dry_run": True,
                            "message": "将置为 DISABLED"})
            continue
        r = await svc.disable_node(DisableNodeCommand(
            node_id=node_id, operator_id=operator_id, reason=reason))
        results.append({"node_id": node_id, "success": r.success, "message": r.message})
    return results


async def batch_enable_nodes(
    node_ids: list[str],
    *,
    operator_id: str = "",
    remark: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """批量恢复被停用的节点（回到真实三态）。"""
    from .node_service import NodeService
    from .node_commands import EnableNodeCommand
    from ..repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    results: list[dict] = []
    for node_id in (node_ids or []):
        if dry_run:
            results.append({"node_id": node_id, "success": True, "dry_run": True,
                            "message": "将恢复管控"})
            continue
        r = await svc.enable_node(EnableNodeCommand(
            node_id=node_id, operator_id=operator_id, remark=remark))
        results.append({"node_id": node_id, "success": r.success,
                        "status": r.status, "message": r.message})
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 批量更新成果进度
# ══════════════════════════════════════════════════════════════════════════════

async def batch_update_progress(
    progress_updates: list[dict],
    *,
    operator_id: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """批量更新成果进度。

    每项含：
      - deliverable_id: 成果编号（与 deliverable_name 二选一）
      - deliverable_name: 成果名称（需配合 node_id 定位）
      - node_id: 节点编号（仅 deliverable_name 模式需要）
      - current_amount: 当前完成量
      - file_id: 关联文件 ID（可选）

    Args:
        progress_updates: 进度更新列表
        operator_id: 操作人 UUID
        dry_run: 只校验不写入
    """
    from .node_service import NodeService
    from .node_commands import UpdateDeliverableProgressCommand
    from ..repositories.permission_repo import PermissionRepository

    svc = NodeService(user_repo=PermissionRepository())
    results: list[dict] = []

    logger.info("批量更新 %d 个成果进度", len(progress_updates))

    for p in progress_updates:
        # 解析 deliverable_id
        deliverable_id = p.get("deliverable_id", "")
        if not deliverable_id:
            # 按名称解析
            deliverable_name = p.get("deliverable_name", "")
            node_id = p.get("node_id", "")
            if not deliverable_name or not node_id:
                results.append({
                    "node_id": node_id,
                    "success": False,
                    "phase": "update_progress",
                    "message": "缺少 deliverable_id 或 (deliverable_name + node_id)",
                })
                continue
            deliverable_id = await _resolve_deliverable_id(node_id, deliverable_name)
            if not deliverable_id:
                results.append({
                    "node_id": node_id,
                    "success": False,
                    "phase": "update_progress",
                    "message": f"成果「{deliverable_name}」在节点 {node_id} 中未找到",
                })
                continue

        current_amount = float(p.get("current_amount", 0))

        if dry_run:
            results.append({
                "node_id": p.get("node_id", ""),
                "success": True,
                "phase": "update_progress",
                "message": f"[DRY-RUN] 将更新成果 {deliverable_id} 进度为 {current_amount}",
                "dry_run": True,
            })
            continue

        cmd = UpdateDeliverableProgressCommand(
            deliverable_id=deliverable_id,
            current_amount=current_amount,
            file_id=p.get("file_id", ""),
            operator_id=operator_id,
        )

        try:
            r = await svc.update_deliverable_progress(cmd)
            results.append({
                "node_id": r.node_id,
                "success": r.success,
                "phase": "update_progress",
                "status": r.status,
                "message": r.message,
                "affected_ancestors": r.affected_downstream,
            })
        except Exception as e:
            results.append({
                "node_id": p.get("node_id", ""),
                "success": False,
                "phase": "update_progress",
                "message": str(e),
            })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 批量管理节点文件关联
# ══════════════════════════════════════════════════════════════════════════════

# 合法的 action 集合
_VALID_ACTIONS = frozenset({
    "add_shared_file",
    "remove_shared_file",
    "link_deliverable_file",
    "unlink_deliverable_file",
})

# 需要指定文件（file_no 或 file_id）的 action
_FILE_REQUIRED_ACTIONS = frozenset({
    "add_shared_file",
    "remove_shared_file",
    "link_deliverable_file",
})

# 涉及成果的 action（需要 deliverable_id 或 deliverable_name + node_id）
_DELIVERABLE_ACTIONS = frozenset({
    "link_deliverable_file",
    "unlink_deliverable_file",
})


async def batch_link_files(
    links: list[dict],
    *,
    operator_id: str = "",
    dry_run: bool = False,
) -> list[dict]:
    """批量管理节点文件关联。

    每项 link 含：
      - node_id: 节点编号（必填）
      - action: 操作类型，4 种之一（必填）
        - add_shared_file:       增加共享文件（node_accessible_files）
        - remove_shared_file:    移除共享文件
        - link_deliverable_file: 关联成果文件（node_deliverables.file_id）
        - unlink_deliverable_file: 取消成果文件关联
      - file_no: 文件编号（与 file_id 二选一，多数 action 必填）
      - file_id: 文件 UUID（与 file_no 二选一）
      - deliverable_id: 成果编号（成果文件 action 必填，或用 deliverable_name 替代）
      - deliverable_name: 成果名称（与 deliverable_id 二选一，需配合 node_id）

    Args:
        links: 操作列表
        operator_id: 操作人 UUID
        dry_run: 只校验不写入

    Returns:
        操作结果列表
    """
    from ..repositories.file_repo import FileRepository
    from ..repositories.node_repo import (
        NodeAccessibleFileRepo,
        NodeDeliverableRepo,
        ProjectNodeRepo,
    )

    results: list[dict] = []

    logger.info("批量文件关联 %d 条", len(links))

    for lk in links:
        node_id = lk.get("node_id", "")
        action = lk.get("action", "")

        # ── 校验 action ──
        if action not in _VALID_ACTIONS:
            results.append({
                "node_id": node_id,
                "success": False,
                "phase": action or "unknown",
                "message": f"未知 action「{action}」，合法值: {', '.join(sorted(_VALID_ACTIONS))}",
            })
            continue

        if not node_id:
            results.append({
                "node_id": "",
                "success": False,
                "phase": action,
                "message": "缺少 node_id",
            })
            continue

        # ── 解析 file_id ──
        file_id = ""
        if action in _FILE_REQUIRED_ACTIONS:
            file_id = lk.get("file_id", "")
            file_no = lk.get("file_no", "")
            if not file_id and not file_no:
                results.append({
                    "node_id": node_id,
                    "success": False,
                    "phase": action,
                    "message": f"action「{action}」需要 file_no 或 file_id",
                })
                continue
            if file_no and not file_id:
                file_record = await asyncio.to_thread(FileRepository.get_by_file_no, file_no)
                if file_record is None:
                    results.append({
                        "node_id": node_id,
                        "success": False,
                        "phase": action,
                        "message": f"文件编号「{file_no}」不存在",
                    })
                    continue
                file_id = file_record.id

        # ── 解析 deliverable_id ──
        deliverable_id = ""
        if action in _DELIVERABLE_ACTIONS:
            deliverable_id = lk.get("deliverable_id", "")
            if not deliverable_id:
                deliverable_name = lk.get("deliverable_name", "")
                if deliverable_name:
                    deliverable_id = await _resolve_deliverable_id(node_id, deliverable_name)
                if not deliverable_id:
                    results.append({
                        "node_id": node_id,
                        "success": False,
                        "phase": action,
                        "message": "缺少 deliverable_id 或 deliverable_name（在节点中未找到对应成果）",
                    })
                    continue

        # ── dry-run ──
        if dry_run:
            results.append({
                "node_id": node_id,
                "success": True,
                "phase": action,
                "message": f"[DRY-RUN] 将执行 {action} (node={node_id}, file={file_id or '-'}, deliverable={deliverable_id or '-'})",
                "dry_run": True,
            })
            continue

        # ── 执行 action ──
        try:
            if action == "add_shared_file":
                exists = await asyncio.to_thread(NodeAccessibleFileRepo.exists, node_id, file_id)
                if exists:
                    results.append({
                        "node_id": node_id,
                        "success": True,
                        "phase": action,
                        "message": f"共享文件已存在，跳过 (file_id={file_id})",
                        "skipped": True,
                    })
                else:
                    await asyncio.to_thread(
                        NodeAccessibleFileRepo.create,
                        node_id=node_id,
                        file_id=file_id,
                        added_by=operator_id,
                    )
                    results.append({
                        "node_id": node_id,
                        "success": True,
                        "phase": action,
                        "message": f"已增加共享文件 (file_id={file_id})",
                    })

            elif action == "remove_shared_file":
                exists = await asyncio.to_thread(NodeAccessibleFileRepo.exists, node_id, file_id)
                if not exists:
                    results.append({
                        "node_id": node_id,
                        "success": True,
                        "phase": action,
                        "message": f"共享文件不存在，跳过 (file_id={file_id})",
                        "skipped": True,
                    })
                else:
                    await asyncio.to_thread(NodeAccessibleFileRepo.remove, node_id, file_id)
                    results.append({
                        "node_id": node_id,
                        "success": True,
                        "phase": action,
                        "message": f"已移除共享文件 (file_id={file_id})",
                    })

            elif action == "link_deliverable_file":
                deliv = await asyncio.to_thread(
                    NodeDeliverableRepo.get_by_deliverable_id, deliverable_id,
                )
                if deliv is None:
                    results.append({
                        "node_id": node_id,
                        "success": False,
                        "phase": action,
                        "message": f"成果「{deliverable_id}」不存在",
                    })
                else:
                    await asyncio.to_thread(
                        NodeDeliverableRepo.update_file_id, deliverable_id, file_id,
                    )
                    results.append({
                        "node_id": node_id,
                        "success": True,
                        "phase": action,
                        "message": f"已关联成果文件 (deliverable={deliverable_id}, file_id={file_id})",
                    })

            elif action == "unlink_deliverable_file":
                deliv = await asyncio.to_thread(
                    NodeDeliverableRepo.get_by_deliverable_id, deliverable_id,
                )
                if deliv is None:
                    results.append({
                        "node_id": node_id,
                        "success": False,
                        "phase": action,
                        "message": f"成果「{deliverable_id}」不存在",
                    })
                else:
                    await asyncio.to_thread(
                        NodeDeliverableRepo.update_file_id, deliverable_id, "",
                    )
                    results.append({
                        "node_id": node_id,
                        "success": True,
                        "phase": action,
                        "message": f"已取消成果文件关联 (deliverable={deliverable_id})",
                    })

        except Exception as e:
            logger.exception("文件关联操作失败: %s node=%s", action, node_id)
            results.append({
                "node_id": node_id,
                "success": False,
                "phase": action,
                "message": str(e),
            })

    return results
