"""MockWorkAgent — 返回固定的 StepResult[]。

Phase 0 占位。总线跑通后由真实 WorkAgent 替换。

提供两个变体：
- MockWorkAgent: 写操作场景（事件录入）
- MockWorkAgentQuery: 查询场景（含 RAG 结果）
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..interfaces.execution import (
    WorkAgent,
    StepResult,
    ToolCallRecord,
    RagResult,
    RagChunk,
    DbResult,
    GuardianStepVerdict,
)
from ..interfaces.planning import ExecutionPlan, PlanStep

if TYPE_CHECKING:
    from ..interfaces.routing import RouteDecision


class MockWorkAgent(WorkAgent):
    """Mock 执行 Agent — 模拟写操作场景（事件录入）。"""

    async def plan(
        self, route_decision: "RouteDecision", context: Any
    ) -> ExecutionPlan:
        """制定执行计划。

        Args:
            route_decision: 路由决策
            context: 管道上下文

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

    async def execute(
        self, plan: ExecutionPlan, context: Any
    ) -> list[StepResult]:
        """执行计划。

        Args:
            plan: 执行计划
            context: 管道上下文

        Returns:
            list[StepResult]: 3 个步骤结果（全部成功）
        """
        return [
            StepResult(
                step_id="step-01",
                success=True,
                output="已解析：事件=样板段放线完成，日期=2026-06-22，位置=样板段",
                tool_calls=[],
                rag_results=[],
                db_results=[],
                business_data={
                    "event_title": "样板段放线完成",
                    "event_date": "2026-06-22",
                },
                guardian=None,
            ),
            StepResult(
                step_id="step-02",
                success=True,
                output="事件 #42 已创建",
                tool_calls=[
                    ToolCallRecord(
                        tool_name="record_event",
                        tool_input={
                            "title": "样板段放线完成",
                            "date": "2026-06-22",
                        },
                        tool_output={
                            "event_id": "mock-42",
                            "status": "created",
                        },
                        success=True,
                    )
                ],
                rag_results=[],
                db_results=[
                    DbResult(
                        operation="insert",
                        table="events",
                        affected_rows=1,
                        result_data={
                            "event_id": "mock-42",
                            "title": "样板段放线完成",
                        },
                    )
                ],
                business_data={"event_id": "mock-42"},
                guardian=GuardianStepVerdict(
                    verdict="PASS", reason="要素完整，写入成功"
                ),
            ),
            StepResult(
                step_id="step-03",
                success=True,
                output="操作完成，事件已录入",
                tool_calls=[],
                rag_results=[],
                db_results=[],
                business_data={},
                guardian=None,
            ),
        ]


class MockWorkAgentQuery(WorkAgent):
    """Mock 执行 Agent — 模拟知识查询场景（含 RAG 结果）。"""

    async def plan(
        self, route_decision: "RouteDecision", context: Any
    ) -> ExecutionPlan:
        """制定查询执行计划。

        Args:
            route_decision: 路由决策
            context: 管道上下文

        Returns:
            ExecutionPlan: 固定的 1 步查询计划
        """
        return ExecutionPlan(
            risk_level="L1",
            steps=[
                PlanStep(
                    step_id="step-01",
                    description="知识库检索",
                    tool_name="knowledge_search",
                    expected_output="返回相关文档片段",
                ),
            ],
            acceptance_criteria=["检索结果相关"],
            estimated_steps=1,
            _source="mock",
        )

    async def execute(
        self, plan: ExecutionPlan, context: Any
    ) -> list[StepResult]:
        """执行查询。

        Args:
            plan: 执行计划
            context: 管道上下文

        Returns:
            list[StepResult]: 1 个步骤结果（含 RAG 检索结果）
        """
        return [
            StepResult(
                step_id="step-01",
                success=True,
                output="已检索到 3 条相关知识",
                tool_calls=[
                    ToolCallRecord(
                        tool_name="knowledge_search",
                        tool_input={"query": "消防验收材料", "top_k": 3},
                        tool_output={"hit_count": 3},
                        success=True,
                    )
                ],
                rag_results=[
                    RagResult(
                        query="消防验收材料",
                        provider="maxkb",
                        chunks=[
                            RagChunk(
                                content="消防验收需提交：1.竣工验收报告 2.消防设施检测报告 3.消防产品质量证明文件",
                                score=0.92,
                                doc_name="消防验收指南_v2.pdf",
                            ),
                            RagChunk(
                                content="根据《消防法》第十三条，建设工程竣工后建设单位应当向公安机关消防机构申请消防验收",
                                score=0.85,
                                doc_name="消防法规汇编.pdf",
                            ),
                            RagChunk(
                                content="消防验收申请表需加盖建设单位公章，并附施工单位自检报告",
                                score=0.71,
                                doc_name="验收流程手册.pdf",
                            ),
                        ],
                        hit_count=3,
                        elapsed_ms=120,
                    )
                ],
                db_results=[],
                business_data={"knowledge_hits": 3},
                guardian=None,
            ),
        ]
