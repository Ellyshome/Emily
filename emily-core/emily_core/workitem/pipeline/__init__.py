"""workitem.pipeline —— 公共执行总线（系统级，非 WorkItem 私有）。

蓝图 §5.4：Session-Scheduler → Pipeline BUS（公共总线）→ WorkItem。
4 个固定节点，每节点 3 个 Hook 挂载点（before/after/on_error），三态决策。
Hook 系统 / interfaces 完整复用 M15 实现。
"""

from .bus import PipelineBUS
from .node import PipelineNode
from .context import BusContext
from .hook import Hook, HookResult, HookDecision, HOOK_TYPE_MAP
from .hook_registry import HookRegistry

__all__ = [
    "PipelineBUS",
    "PipelineNode",
    "BusContext",
    "Hook",
    "HookResult",
    "HookDecision",
    "HOOK_TYPE_MAP",
    "HookRegistry",
]
