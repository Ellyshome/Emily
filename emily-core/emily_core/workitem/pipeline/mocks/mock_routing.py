"""MockRouter — 返回固定的 RouteDecision。

Phase 0 占位。总线跑通后由真实 MasterAgent 路由模式替换。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..interfaces.routing import RouteDecision, SubTask

if TYPE_CHECKING:
    from ..work_order import WorkOrder


class MockRouter:
    """Mock 路由器 — 返回固定的单意图 RouteDecision。"""

    async def route(self, work_order: "WorkOrder") -> RouteDecision:
        """执行路由决策。

        始终返回 SOP-002-REC（事件记录），置信度 high。

        Args:
            work_order: 流转单

        Returns:
            RouteDecision: 固定的路由决策
        """
        return RouteDecision(
            intent_type="sop",
            sop_id="SOP-002-REC",
            confidence="high",
            is_compound=False,
            sub_tasks=[
                SubTask(
                    id="subtask-001",
                    sop_id="SOP-002-REC",
                    user_input=work_order.message_content or "",
                )
            ],
            fallback_reason="",
            _source="mock",
        )
