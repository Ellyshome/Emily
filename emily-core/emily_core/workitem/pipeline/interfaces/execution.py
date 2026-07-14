"""执行 Agent 接口。

定义执行相关的数据结构和 WorkAgent 接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .routing import RouteDecision
    from .planning import ExecutionPlan


@dataclass
class ToolCallRecord:
    """工具调用记录。

    Attributes:
        tool_name: 工具名称
        tool_input: 工具入参
        tool_output: 工具出参
        success: 调用是否成功
        elapsed_ms: 耗时（毫秒）
    """
    tool_name: str
    tool_input: dict = field(default_factory=dict)
    tool_output: dict = field(default_factory=dict)
    success: bool = True
    elapsed_ms: int = 0


@dataclass
class RagChunk:
    """RAG 检索到的文档片段。

    Attributes:
        content: 片段文本内容
        score: 相关性得分
        doc_name: 来源文档名
    """
    content: str
    score: float = 0.0
    doc_name: str = ""


@dataclass
class RagResult:
    """RAG 检索结果。

    Attributes:
        query: 检索查询文本
        provider: 检索提供者（maxkb/local_fallback）
        chunks: 检索到的文档片段列表
        hit_count: 命中数
        elapsed_ms: 耗时（毫秒）
    """
    query: str
    provider: str = ""           # "maxkb" | "local_fallback"
    chunks: list[RagChunk] = field(default_factory=list)
    hit_count: int = 0
    elapsed_ms: int = 0


@dataclass
class DbResult:
    """数据库操作结果。

    Attributes:
        operation: 操作类型（insert/query/update/delete）
        table: 表名
        affected_rows: 影响行数
        result_data: 查询结果 / 插入后的记录
        elapsed_ms: 耗时（毫秒）
    """
    operation: str = ""          # "insert" | "query" | "update" | "delete"
    table: str = ""
    affected_rows: int = 0
    result_data: dict = field(default_factory=dict)
    elapsed_ms: int = 0


@dataclass
class GuardianStepVerdict:
    """守护 Agent 逐步审核结果。

    Attributes:
        verdict: 审核决策（PASS/FLAG/REJECT）
        reason: 审核意见
        suggestions: 改进建议列表
    """
    verdict: str = "PASS"        # "PASS" | "FLAG" | "REJECT"
    reason: str = ""
    suggestions: list[str] = field(default_factory=list)


@dataclass
class StepResult:
    """单步执行结果。

    Attributes:
        step_id: 步骤 ID
        success: 是否成功
        output: 自然语言描述
        tool_calls: 本步调用的工具链记录
        rag_results: RAG 检索反馈
        db_results: 数据库操作结果
        business_data: 业务产出数据
        guardian: 守护陪跑审核结果
    """
    step_id: str
    success: bool = True
    output: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    rag_results: list[RagResult] = field(default_factory=list)
    db_results: list[DbResult] = field(default_factory=list)
    business_data: dict = field(default_factory=dict)
    guardian: GuardianStepVerdict | None = None


class WorkAgent(ABC):
    """执行 Agent 接口。

    Plan 模式 (Step 4): 输入 RouteDecision → 输出 ExecutionPlan
    Execute 模式 (Step 5): 输入 ExecutionPlan → 逐步输出 StepResult[]

    WorkAgent ABC 保留作为扩展接口，当前主路径由 WorkItemAgent 直接实现。
    """

    @abstractmethod
    async def plan(
        self, route_decision: "RouteDecision", context: Any
    ) -> "ExecutionPlan":
        """制定执行计划（Plan 模式）。

        Args:
            route_decision: 路由决策结果
            context: 管道上下文

        Returns:
            ExecutionPlan: 执行计划
        """
        ...

    @abstractmethod
    async def execute(
        self, plan: "ExecutionPlan", context: Any
    ) -> list[StepResult]:
        """执行计划（Execute 模式）。

        Args:
            plan: 执行计划
            context: 管道上下文

        Returns:
            list[StepResult]: 逐步执行结果
        """
        ...
