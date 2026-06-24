"""Mock 实现 — Phase 0 占位模块。

每个 mock 返回确定性结果，标注 _source: "mock"。
总线跑通后，替换为真实实现只需改 __init__.py 中的一行 import。
"""

from .mock_auth import MockAuthEngine
from .mock_routing import MockRouter
from .mock_planning import MockPlanner
from .mock_execution import MockWorkAgent, MockWorkAgentQuery
from .mock_guardian import MockGuardian
from .mock_risk import MockRiskGrader

__all__ = [
    "MockAuthEngine",
    "MockRouter",
    "MockPlanner",
    "MockWorkAgent",
    "MockWorkAgentQuery",
    "MockGuardian",
    "MockRiskGrader",
]
