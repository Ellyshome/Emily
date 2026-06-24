"""HookRegistry — Hook 注册表。

管理所有 hook，按挂载点索引。借鉴 ToolRegistry 的 register/get/list 模式。
挂载点命名规则: "{phase}:{stage_name}"，如 "before:execute"、"after:archive"、"on_error:execute"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hook import Hook

logger = logging.getLogger("emily.pipeline.registry")


class HookRegistry:
    """Hook 注册表。

    管理所有 hook，按挂载点索引。借鉴 ToolRegistry 的 register/get/list 模式。
    挂载点命名规则: "{phase}:{stage_name}"，如 "before:execute"、"after:archive"、"on_error:execute"

    Usage:
        registry = HookRegistry()
        registry.register("before:execute", AuthHook("auth.project_read"))
        hooks = registry.get("before:execute")  # -> [AuthHook, ...]
    """

    def __init__(self):
        self._hooks: dict[str, list["Hook"]] = {}

    def register(self, mount_point: str, hook: "Hook") -> None:
        """注册 hook 到指定挂载点。自动按 priority 排序。

        Args:
            mount_point: 挂载点名称，格式 "{phase}:{stage_name}"
            hook: Hook 实例
        """
        hook_name = getattr(hook, "name", "?")
        if mount_point not in self._hooks:
            self._hooks[mount_point] = []
        self._hooks[mount_point].append(hook)
        self._hooks[mount_point].sort(key=lambda h: getattr(h, "priority", 10))
        logger.info(
            "Hook registered: %s -> %s (priority=%d)",
            mount_point, hook_name, getattr(hook, "priority", 10),
        )

    def unregister(self, mount_point: str, hook_name: str) -> bool:
        """卸载指定 hook。

        Args:
            mount_point: 挂载点名称
            hook_name: 要卸载的 hook 名称

        Returns:
            True 如果成功卸载，False 如果未找到
        """
        if mount_point not in self._hooks:
            return False
        before = len(self._hooks[mount_point])
        self._hooks[mount_point] = [
            h for h in self._hooks[mount_point]
            if getattr(h, "name", "") != hook_name
        ]
        removed = len(self._hooks[mount_point]) < before
        if removed:
            logger.info("Hook unregistered: %s from %s", hook_name, mount_point)
        return removed

    def get(self, mount_point: str) -> list["Hook"]:
        """获取指定挂载点的所有 hook（已按 priority 排序）。

        Args:
            mount_point: 挂载点名称

        Returns:
            Hook 列表（可能为空）
        """
        return self._hooks.get(mount_point, [])

    def get_enabled(self, mount_point: str) -> list["Hook"]:
        """获取指定挂载点已启用的 hook。

        Args:
            mount_point: 挂载点名称

        Returns:
            已启用的 hook 列表
        """
        return [
            h for h in self.get(mount_point)
            if getattr(h, "enabled", True)
        ]

    def list_all(self) -> dict[str, list[str]]:
        """列出所有挂载点及其 hook 名称。

        Returns:
            {挂载点: [hook_name, ...]}
        """
        return {
            mp: [getattr(h, "name", "?") for h in hooks]
            for mp, hooks in self._hooks.items()
        }

    def clear(self) -> None:
        """清空所有注册。"""
        self._hooks.clear()
        logger.info("HookRegistry cleared")

    def mount_point_count(self) -> int:
        """获取挂载点数量。"""
        return len(self._hooks)

    def hook_count(self) -> int:
        """获取 hook 总数。"""
        return sum(len(hooks) for hooks in self._hooks.values())
