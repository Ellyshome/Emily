"""workitem —— WorkItem 层 + 公共 Pipeline BUS（蓝图 §5）。

WorkItem 是最小任务执行单元，全局单例 WorkItem-Agent 异步处理所有 WorkItem。
Session-Scheduler 将 WorkItem 分配到系统级公共 Pipeline BUS（4 节点）执行。
"""

from .workitem import WorkItem
from .workitem_state import WorkItemState, TRANSITIONS, TERMINAL_STATES
from .scheduler import SessionScheduler
from .pipeline.context import BusContext

# M8 清理：WorkItemAgent / KnowledgeInjector / PipelineBUS / PipelineNode 已删除
# 执行引擎已迁移至 langgraph_engine

__all__ = [
    "WorkItem",
    "WorkItemState",
    "TRANSITIONS",
    "TERMINAL_STATES",
    "SessionScheduler",
    "BusContext",
]
