"""MockRiskGrader — 始终返回 "L2"。

Phase 0 占位。总线跑通后由真实 RiskGrader 替换。
"""

from __future__ import annotations

from typing import Any

from ..interfaces.risk import RiskGrader


class MockRiskGrader(RiskGrader):
    """Mock 风险分级器 — 始终返回 "L2"。"""

    def grade(
        self, route_decision: Any, operation_type: str = ""
    ) -> str:
        """评估风险等级。

        Args:
            route_decision: RouteDecision 对象
            operation_type: 操作类型

        Returns:
            str: 始终 "L2"
        """
        return "L2"
