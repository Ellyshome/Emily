"""Mock 实现 — Phase 0 占位模块。

每个 mock 返回确定性结果，标注 _source: "mock"。
总线跑通后，替换为真实实现只需改 __init__.py 中的一行 import。

已废弃并移除: MockAuthEngine, MockRouter, MockRiskGrader, MockGuardian
当前保留: MockPlanner, MockWorkAgent（仍在 mock 模式中使用）
"""

from .mock_planning import MockPlanner
from .mock_execution import MockWorkAgent, MockWorkAgentQuery  # MockWorkAgentQuery: 冷备（暂无调用者）

__all__ = [
    "MockPlanner",
    "MockWorkAgent",
]
