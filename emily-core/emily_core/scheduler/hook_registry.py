"""Scheduler Hook 注册表 —— 对齐 PipelineBUS Hook 三态语义。

参照模式：emily_core/workitem/pipeline/hook_registry.py。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("emily.scheduler.hook_registry")


class HookDecision(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass
class HookResult:
    """Hook 执行结果。"""
    decision: HookDecision = HookDecision.ALLOW
    message: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.decision == HookDecision.BLOCK


class SchedulerHook(ABC):
    """调度 Hook 基类。对齐 PipelineBUS Hook 三态语义。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Hook 唯一名称。"""

    @property
    def priority(self) -> int:
        """优先级（数字越小越先执行）。"""
        return 10

    @property
    def enabled(self) -> bool:
        """是否启用。"""
        return True

    @abstractmethod
    async def execute(self, context: "SchedulerContext") -> HookResult:
        """执行 Hook 逻辑。"""


class SchedulerHookRegistry:
    """调度 Hook 注册表。按挂载点索引，自动按 priority 排序。"""

    def __init__(self):
        self._hooks: dict[str, list[SchedulerHook]] = {}

    def register(self, mount_point: str, hook: SchedulerHook) -> None:
        """注册 hook 到指定挂载点。自动按 priority 排序。"""
        if mount_point not in self._hooks:
            self._hooks[mount_point] = []
        self._hooks[mount_point].append(hook)
        self._hooks[mount_point].sort(key=lambda h: h.priority)
        logger.info("SchedulerHook registered: %s at %s (priority=%d)", hook.name, mount_point, hook.priority)

    def get_enabled(self, mount_point: str) -> list[SchedulerHook]:
        """获取指定挂载点已启用的 hook。"""
        return [h for h in self._hooks.get(mount_point, []) if h.enabled]

    def hook_count(self) -> int:
        """已注册 hook 总数。"""
        return sum(len(hooks) for hooks in self._hooks.values())
