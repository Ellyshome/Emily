"""MockPlanner — 返回固定的 ExecutionPlan。

Phase 0 占位。总线跑通后由真实 WorkAgent.plan() 替换。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..interfaces.planning import ExecutionPlan, PlanStep

if TYPE_CHECKING:
    from ..interfaces.routing import RouteDecision


class MockPlanner:
    """Mock 计划器 — 返回固定的 3 步 ExecutionPlan。"""

    async def plan(
        self, route_decision: "RouteDecision", context: Any
    ) -> ExecutionPlan:
        """制定执行计划。

        Args:
            route_decision: 路由决策
            context: 管道上下文（WorkOrder）

        Returns:
            ExecutionPlan: 固定的 3 步执行计划
        """
        return ExecutionPlan(
            risk_level="L2",
            steps=[
                PlanStep(
                    step_id="step-01",
                    description="解析用户输入",
                    tool_name=None,
                    expected_output="提取事件要素",
                ),
                PlanStep(
                    step_id="step-02",
                    description="执行业务操作",
                    tool_name="record_event",
                    expected_output="事件创建成功",
                ),
                PlanStep(
                    step_id="step-03",
                    description="确认结果",
                    tool_name=None,
                    expected_output="返回确认信息",
                ),
            ],
            acceptance_criteria=["事件要素完整", "数据写入成功"],
            estimated_steps=3,
            _source="mock",
        )
