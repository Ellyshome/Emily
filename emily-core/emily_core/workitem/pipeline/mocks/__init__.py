"""Mock 实现 — Phase 0 占位模块。

每个 mock 返回确定性结果，标注 _source: "mock"。
总线跑通后，替换为真实实现只需改 __init__.py 中的一行 import。

已移除: MockAuthEngine（EmilyCore.auth 模块直接放行）, MockRouter（SessionAgent 意图识别替代）,
MockRiskGrader（workitem_agent.grade_risk 直接返回 L2）
"""

from .mock_planning import MockPlanner
from .mock_execution import MockWorkAgent, MockWorkAgentQuery
from .mock_guardian import MockGuardian

__all__ = [
    "MockPlanner",
    "MockWorkAgent",
    "MockWorkAgentQuery",
    "MockGuardian",
]
