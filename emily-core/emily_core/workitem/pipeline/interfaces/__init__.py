"""Pipeline 模块接口定义。

定义了所有管道阶段间的数据契约和引擎接口。
Mock 实现和真实实现共享这些接口，替换只需改一行 import。

注: risk.py (RiskGrader) 和 auth.py (AuthEngine) 的 ABC 已移除具体实现（MockAuthEngine/MockRiskGrader），
仅保留接口定义作为未来扩展占位。当前 workitem_agent.py 中 grade_risk()/authorize() 为自包含方法。
"""

from .routing import IntentType, SubTask, RouteDecision
from .planning import PlanStep, ExecutionPlan
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
    "PlanStep",
    "ExecutionPlan",
    "ToolCallRecord",
    "RagChunk",
    "RagResult",
    "DbResult",
    "GuardianStepVerdict",
    "StepResult",
    "WorkAgent",
    "GuardianVerdict",
]
