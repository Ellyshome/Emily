"""守护 Agent 接口。

定义守护审核的数据结构和 Guardian 接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class GuardianVerdict(Enum):
    """守护审核决策枚举。"""
    PASS = "pass"
    FLAG = "flag"
    REJECT = "reject"


class Guardian(ABC):
    """守护 Agent 接口。

    陪跑模式 (Step 5): 逐步审核 step_result → GuardianVerdict
    出站模式 (Step 7): 审核 draft_reply → GuardianVerdict

    Mock: 始终 PASS
    真实: LLM 驱动的审核
    """

    @abstractmethod
    async def review_step(
        self,
        step_result: Any,
        plan_step: Any,
        criteria: list[str],
    ) -> GuardianVerdict:
        """逐步审核（陪跑模式）。

        Args:
            step_result: StepResult 对象
            plan_step: PlanStep 对象
            criteria: 验收标准列表

        Returns:
            GuardianVerdict: 审核决策
        """
        ...

    @abstractmethod
    async def review_reply(
        self, draft_reply: str, context: Any
    ) -> GuardianVerdict:
        """出站审核（出站模式）。

        Args:
            draft_reply: 回复草稿
            context: 总线上下文（包含完整上下文）

        Returns:
            GuardianVerdict: 审核决策
        """
        ...
