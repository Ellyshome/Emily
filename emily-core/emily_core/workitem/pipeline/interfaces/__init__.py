"""Pipeline 模块接口定义。

定义了所有管道阶段间的数据契约和引擎接口。
Mock 实现和真实实现共享这些接口，替换只需改一行 import。
"""

from .auth import AuthDecision, AuthResult, AuthEngine
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
from .guardian import GuardianVerdict, Guardian
from .risk import RiskGrader

__all__ = [
    "AuthDecision",
    "AuthResult",
    "AuthEngine",
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
    "Guardian",
    "RiskGrader",
]
