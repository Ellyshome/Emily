"""Pipeline 模块接口定义。

定义了所有管道阶段间的数据契约。
鉴权/风险评估由 workitem_agent.py 中 authorize()/grade_risk() 自包含实现。
"""

from .routing import IntentType, SubTask, RouteDecision
# M8 清理：planning.py（ExecutionPlan/PlanStep）已删除
from .execution import (
    ToolCallRecord,
    RagChunk,
    RagResult,
    DbResult,
    GuardianStepVerdict,
    StepResult,
    WorkAgent,
)
from .guardian import GuardianVerdict

__all__ = [
    "IntentType",
    "SubTask",
    "RouteDecision",
    "ToolCallRecord",
    "RagChunk",
    "RagResult",
    "DbResult",
    "GuardianStepVerdict",
    "StepResult",
    "WorkAgent",
    "GuardianVerdict",
]
