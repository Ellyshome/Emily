"""全景节点图 V2 Application 层 —— 编排 Service 调用 + 生成回复。

参照模式：plan_task_app.py。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.node_service import NodeService
    from ..services.node_commands import (
        CreateNodeCommand,
        UpdateNodeCommand,
        DiscardNodeCommand,
        CreateDeliverableCommand,
        UpdateDeliverableProgressCommand,
        AddDependencyCommand,
        RemoveDependencyCommand,
        MountChildCommand,
        UnmountChildCommand,
    )

logger = logging.getLogger("emily.node_app")


class NodeApplication:
    """全景节点图 Application —— 编排 Service 调用并生成统一响应。"""

    def __init__(self, service: "NodeService", auth_engine=None):
        self._service = service
        self._auth_engine = auth_engine

    # ── 权限辅助 ──

    async def _check_node_permission(self, node_id: str, operator_id: str,
                                      operation: str = "write") -> bool:
        """检查用户对节点的操作权限。

        映射到现有权限级别（需求文档 §4.2）：
          - 系统管理员（Level0）：全部操作
          - 项目总监（Level2）：节点废弃
          - 主责条线负责人（Level3）：编辑/成果更新/依赖调整
          - 指定经办人（Level4）：成果进度更新

        Args:
            node_id: 目标节点
            operator_id: 操作人
            operation: read / write / delete / mount_child

        Returns:
            True 如果有权限
        """
        if not self._auth_engine or not operator_id:
            return True  # 无鉴权引擎或未指定操作人 → 放行

        try:
            result = await self._auth_engine.check_access(
                perms=None,  # 由 auth_engine 内部从 session context 获取
                resource_type="NODE",
                resource_id=node_id,
                operation=operation,
            )
            return result.allowed
        except Exception:
            logger.warning("Permission check failed for node=%s user=%s", node_id, operator_id)
            return True  # 鉴权异常时 fail-open

    def _require_permission(self, allowed: bool, node_id: str, operation: str) -> None:
        """权限不足时抛出异常（由 Application 调用方处理）。"""
        if not allowed:
            raise PermissionError(f"无权限对节点 {node_id} 执行 {operation} 操作")

    # ── 业务方法 ──

    async def create_node(self, cmd: "CreateNodeCommand") -> dict:
        """创建节点。"""
        # 创建节点默认需 write 权限（Phase 1-4 默认可关闭）
        result = await self._service.create_node(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "status": result.status,
            "progress": result.progress,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def update_node(self, cmd: "UpdateNodeCommand") -> dict:
        """更新节点字段。"""
        if cmd.operator_id:
            allowed = await self._check_node_permission(cmd.node_id, cmd.operator_id, "write")
            self._require_permission(allowed, cmd.node_id, "write")
        result = await self._service.update_node(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "status": result.status,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def discard_node(self, cmd: "DiscardNodeCommand") -> dict:
        """废弃节点。"""
        if cmd.operator_id:
            allowed = await self._check_node_permission(cmd.node_id, cmd.operator_id, "delete")
            self._require_permission(allowed, cmd.node_id, "delete")
        result = await self._service.discard_node(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def create_deliverable(self, cmd: "CreateDeliverableCommand") -> dict:
        """新增成果。"""
        result = await self._service.create_deliverable(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def update_deliverable_progress(self, cmd: "UpdateDeliverableProgressCommand") -> dict:
        """更新成果进度——触发状态流转。"""
        result = await self._service.update_deliverable_progress(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "status": result.status,
            "progress": result.progress,
            "reply": result.message,
            "affected_ancestors": result.affected_downstream,
            "error_code": result.error_code,
        }

    async def add_dependency(self, cmd: "AddDependencyCommand") -> dict:
        """添加依赖——含循环检测。"""
        result = await self._service.add_dependency(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def remove_dependency(self, cmd: "RemoveDependencyCommand") -> dict:
        """移除依赖。"""
        result = await self._service.remove_dependency(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def mount_child(self, cmd: "MountChildCommand") -> dict:
        """挂载子节点。"""
        result = await self._service.mount_child(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def unmount_child(self, cmd: "UnmountChildCommand") -> dict:
        """移除子节点。"""
        result = await self._service.unmount_child(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def get_node_detail(self, node_id: str) -> dict:
        """查询节点详情。"""
        detail = await self._service.get_node_detail(node_id)
        if detail is None:
            return {"success": False, "reply": f"节点 {node_id} 不存在"}
        return {"success": True, "data": detail, "reply": f"节点「{detail['node_name']}」查询成功"}
