# emily-core/emily_core/workitem/langgraph_engine/graph.py
"""StateGraph 构建 —— 5 节点 + 条件边（Self-Reflection 纠错闭环）。

Graph 拓扑：
  START → node1 → node2 → node3 → [route_after_node3] → node4 → END
                 ↑       │            │
                 │       │            └→ error_analysis → [route_after_analysis]
                 │       │                      │
                 │       │                      ├→ node2（param_error/tool_mismatch，带 replan_hint 重规划）
                 ↑───────┘ ←── replan ──────────┤
                 │                              ├→ node3（transient_failure，直接重试）
                 │                              └→ END（permission_denied/permanent_failure/missing_info）
                 │
                 └── node3 ←── retry ────────────┘

条件边路由：
  route_after_node3:
    - should_abort → END
    - 有失败 step 且 replan_count < max_replan → error_analysis（先分析再决定重规划/重试）
    - 否则 → node4

  route_after_analysis:
    - should_abort / ABORT_TYPES → END
    - RETRY_TYPES (transient_failure) → node3（直接重试，省 LLM 重新规划）
    - REPLAN_TYPES (param_error/tool_mismatch) → node2（带 replan_hint 重规划）
    - 兜底 → END

Checkpoint：本期用 MemorySaver，后续切 PostgresSaver。
thread_id = pipeline_run_id，对接现有 trace/归档/LLM 日志。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import WorkItemGraphState
from .nodes import (
    make_node1, make_node2, make_node3, make_node4, make_error_analysis,
    node_retry_policies,
)
from .error_analysis import REPLAN_TYPES, RETRY_TYPES, ABORT_TYPES

logger = logging.getLogger("emily.langgraph.graph")


def _has_failed_step(ctx) -> bool:
    """检查 BusContext.work_item 是否有失败的 step。"""
    wi = ctx.work_item
    if wi is None:
        return False
    for sr in getattr(wi, "step_results", []) or []:
        if not getattr(sr, "success", True):
            return True
    return False


def route_after_node3(state: WorkItemGraphState) -> str:
    """node3 之后的条件边路由。

    路由优先级：
      1. should_abort → "end"
      2. 有失败 step 且 replan_count < max_replan → "error_analysis"
      3. 否则 → "node4"
    """
    ctx = state["context"]
    max_replan = state.get("_max_replan", 1)
    replan_count = state.get("replan_count", 0)

    if ctx.should_abort:
        logger.info("route_after_node3: should_abort=True → end")
        return "end"

    if _has_failed_step(ctx) and replan_count < max_replan:
        logger.info("route_after_node3: failed step + replan_count=%d < %d → error_analysis",
                    replan_count, max_replan)
        return "error_analysis"

    return "node4"


def route_after_analysis(state: WorkItemGraphState) -> str:
    """error_analysis 之后的条件边路由（按错误类型路由）。

    路由规则：
      - should_abort / ABORT_TYPES → end
      - RETRY_TYPES (transient_failure) → node3（直接重试）
      - REPLAN_TYPES (param_error/tool_mismatch) → node2（带 replan_hint 重规划）
      - 兜底 → end
    """
    ctx = state["context"]
    if ctx.should_abort:
        logger.info("route_after_analysis: should_abort=True → end")
        return "end"

    error_type = state.get("error_type", "")
    analysis = state.get("error_analysis", {})

    if error_type in ABORT_TYPES or analysis.get("should_abort"):
        logger.info("route_after_analysis: error_type=%s (ABORT) → end", error_type)
        return "end"

    if error_type in RETRY_TYPES or analysis.get("should_retry"):
        logger.info("route_after_analysis: error_type=%s (RETRY) → node3", error_type)
        return "node3"

    if error_type in REPLAN_TYPES or analysis.get("should_replan"):
        logger.info("route_after_analysis: error_type=%s (REPLAN) → node2", error_type)
        return "node2"

    logger.warning("route_after_analysis: unknown error_type=%s → end", error_type)
    return "end"


def route_after_node2(state: WorkItemGraphState) -> str:
    """node2 之后的路由：should_abort → end，否则 → node3。"""
    ctx = state["context"]
    if ctx.should_abort:
        return "end"
    return "node3"


def build_workitem_graph(
    agent,
    hook_adapter,
    max_replan: int = 1,
    checkpointer: Any = None,
) -> Any:
    """构建 WorkItem 执行 StateGraph（含 error_analysis 纠错闭环）。

    Args:
        agent: WorkItemAgent 实例（提供 4 个 node handler + _llm 供 ErrorAnalyzer）
        hook_adapter: HookAdapter 实例
        max_replan: 最大重规划次数
        checkpointer: Checkpoint 实例，None 用 MemorySaver

    Returns:
        编译后的 LangGraph CompiledGraph
    """
    graph_builder = StateGraph(WorkItemGraphState)

    # ── 注册节点（5 个）──
    graph_builder.add_node("wi_node1", make_node1(agent, hook_adapter))
    graph_builder.add_node("wi_node2", make_node2(agent, hook_adapter))
    graph_builder.add_node("wi_node3", make_node3(agent, hook_adapter))
    graph_builder.add_node("wi_node4", make_node4(agent, hook_adapter))
    graph_builder.add_node("error_analysis", make_error_analysis(agent, hook_adapter))

    # ── 边 ──
    graph_builder.add_edge(START, "wi_node1")
    graph_builder.add_edge("wi_node1", "wi_node2")

    # node2 → node3（带 should_abort 条件）
    graph_builder.add_conditional_edges(
        "wi_node2",
        route_after_node2,
        {"node3": "wi_node3", "end": END},
    )

    # node3 → 条件路由（error_analysis / node4 / end）
    graph_builder.add_conditional_edges(
        "wi_node3",
        route_after_node3,
        {"error_analysis": "error_analysis", "node4": "wi_node4", "end": END},
    )

    # error_analysis → 条件路由（node2 重规划 / node3 重试 / end）
    graph_builder.add_conditional_edges(
        "error_analysis",
        route_after_analysis,
        {"node2": "wi_node2", "node3": "wi_node3", "end": END},
    )

    # node4 → END
    graph_builder.add_edge("wi_node4", END)

    # ── 编译（禁用 checkpoint——BusContext 不可 msgpack 序列化，MemorySaver 不适用）
    # 后续切 PostgresSaver 时需为 context 实现自定义 serializer。
    graph = graph_builder.compile(checkpointer=False)
    graph._max_replan = max_replan  # type: ignore[attr-defined]

    logger.info(
        "WorkItem graph built: 5 nodes, max_replan=%d, checkpointer=None (disabled)",
        max_replan,
    )
    return graph


def make_initial_state(context, max_replan: int = 1) -> WorkItemGraphState:
    """构建 graph 初始 State。"""
    return WorkItemGraphState(
        context=context,
        replan_count=0,
        node_timings={},
        started_at="",
        error_analysis={},
        replan_hint="",
        error_type="",
        pipeline_run_id=context.pipeline_run_id,
        current_stage="",
        _entered_node2=False,
        _max_replan=max_replan,
    )
