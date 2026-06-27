"""MockGuardian — 始终返回 PASS。

Phase 0 占位。总线跑通后由真实 Guardian 替换。
"""

from __future__ import annotations

from typing import Any

from ..interfaces.guardian import Guardian, GuardianVerdict


class MockGuardian(Guardian):
    """Mock 守护 Agent — 始终 PASS。"""

    async def review_step(
        self,
        step_result: Any,
        plan_step: Any,
        criteria: list[str],
    ) -> GuardianVerdict:
        """逐步审核（陪跑模式）— 始终 PASS。

        Args:
            step_result: StepResult 对象
            plan_step: PlanStep 对象
            criteria: 验收标准列表

        Returns:
            GuardianVerdict: 始终 PASS
        """
        return GuardianVerdict.PASS

    async def review_reply(
        self, draft_reply: str, context: Any
    ) -> GuardianVerdict:
        """出站审核（出站模式）— 始终 PASS。

        Args:
            draft_reply: 回复草稿
            context: 总线上下文

        Returns:
            GuardianVerdict: 始终 PASS
        """
        return GuardianVerdict.PASS
