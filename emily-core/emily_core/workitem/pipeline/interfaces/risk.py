"""风险分级器接口。

定义风险等级评估接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RiskGrader(ABC):
    """[reserved] 风险分级器接口 — 无具体实现（MockRiskGrader 已移除），workitem_agent.grade_risk() 为自包含方法。

    输入: RouteDecision (intent_type + confidence) + 操作类型
    输出: "L1" | "L2" | "L3"

    分级逻辑:
      - 快速通道 / 闲聊 → L1
      - 单意图 + 高置信度 + 读操作 → L1
      - 单意图 + 中低置信度 / 写操作 → L2
      - 复合意图 / fallback / 删除操作 → L3

    Mock: 始终返回 "L2"
    """

    @abstractmethod
    def grade(
        self, route_decision: Any, operation_type: str = ""
    ) -> str:
        """评估风险等级。

        Args:
            route_decision: RouteDecision 对象
            operation_type: 操作类型（read/write/delete）

        Returns:
            str: 风险等级（L1/L2/L3）
        """
        ...
