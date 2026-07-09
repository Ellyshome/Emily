"""JobHandler 注册表 —— 调度动作处理器注册与发现。

参照模式：emily_core/tools/business_flow_tools.py (BusinessFlowToolRegistry)。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger("emily.scheduler.handler_registry")


@dataclass
class JobResult:
    """Handler 执行结果。"""
    success: bool = True
    summary: str = ""
    data: dict = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}


class SchedulerJobHandler(ABC):
    """调度动作处理器基类。

    每个 handler 对应一种可调度的动作类型。
    新增调度动作 = 写一个 Handler 子类 + 在 scheduler_config.json 中添加条目，
    不改调度器核心代码。
    """

    @property
    @abstractmethod
    def action_type(self) -> str:
        """动作类型唯一标识。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """一句话描述。"""

    @abstractmethod
    async def execute(self, params: dict) -> JobResult:
        """执行调度动作。"""


class JobHandlerRegistry:
    """调度动作注册表。类似 BusinessFlowToolRegistry 的模式。"""

    def __init__(self):
        self._handlers: dict[str, SchedulerJobHandler] = {}

    def register(self, handler: SchedulerJobHandler) -> None:
        """注册一个动作处理器。"""
        if handler.action_type in self._handlers:
            raise ValueError(f"SchedulerJobHandler '{handler.action_type}' 已注册")
        self._handlers[handler.action_type] = handler
        logger.info("JobHandler registered: %s (%s)", handler.action_type, handler.description)

    def get(self, action_type: str) -> SchedulerJobHandler | None:
        """按 action_type 查找处理器。"""
        return self._handlers.get(action_type)

    def has(self, action_type: str) -> bool:
        """检查处理器是否已注册。"""
        return action_type in self._handlers

    def list_all(self) -> list[dict]:
        """列出所有已注册动作。"""
        return [
            {"action_type": h.action_type, "description": h.description}
            for h in self._handlers.values()
        ]

    def __len__(self) -> int:
        return len(self._handlers)
