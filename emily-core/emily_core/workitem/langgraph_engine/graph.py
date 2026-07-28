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

State 设计（方案 C：纯可序列化 + contextvars）：
  - State 仅含基础类型（str/int/bool/dict），100% msgpack 兼容 → MemorySaver 可用
  - BusContext 通过 contextvars 传递，node handler 零改动
  - 条件边从 state["flow_control"] 读流程控制信号

thread_id = pipeline_run_id，对接现有 trace/归档/LLM 日志。
"""

from __future__ import annotations

import logging

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import WorkItemGraphState, get_bus_context
from .nodes import (
    make_node1, make_node2, make_node3, make_node4, make_error_analysis,
    node_retry_policies,
)
from .error_analysis import REPLAN_TYPES, RETRY_TYPES, ABORT_TYPES

logger = logging.getLogger("emily.langgraph.graph")


def route_after_node3(state: dict) -> str:
    """node3 之后的条件边路由。

    路由优先级（从 state 读 flow_control，从 contextvars 读 BusContext）：
      1. flow_control["should_abort"] → "end"
      2. flow_control["has_failed_step"] 且 replan_count < max_replan → "error_analysis"
      3. 否则 → "node4"
    """
    fc = state.get("flow_control", {})
    max_replan = state.get("_max_replan", 1)
    replan_count = state.get("replan_count", 0)

    if fc.get("should_abort"):
        logger.info("route_after_node3: should_abort=True → end")
        return "end"

    if fc.get("has_failed_step") and replan_count < max_replan:
        logger.info("route_after_node3: failed step + replan_count=%d < %d → error_analysis",
                    replan_count, max_replan)
        return "error_analysis"

    return "node4"


def route_after_analysis(state: dict) -> str:
    """error_analysis 之后的条件边路由（按错误类型路由）。"""
    fc = state.get("flow_control", {})
    if fc.get("should_abort"):
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


def route_after_node2(state: dict) -> str:
    """node2 之后的路由：should_abort → end，否则 → node3。"""
    fc = state.get("flow_control", {})
    if fc.get("should_abort"):
        return "end"
    return "node3"


def build_workitem_graph(
    agent,
    hook_adapter,
    max_replan: int = 1,
) -> StateGraph:
    """构建 WorkItem 执行 StateGraph（含 error_analysis 纠错闭环）。

    Args:
        agent: WorkItemAgent 实例
        hook_adapter: HookAdapter 实例
        max_replan: 最大重规划次数

    Returns:
        编译后的 LangGraph CompiledGraph（带 MemorySaver checkpoint）
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

    # ── 编译（MemorySaver — State 纯可序列化，checkpoint 可用）──
    graph = graph_builder.compile(checkpointer=MemorySaver())
    graph._max_replan = max_replan  # type: ignore[attr-defined]

    logger.info(
        "WorkItem graph built: 5 nodes (含 error_analysis), max_replan=%d, checkpointer=MemorySaver",
        max_replan,
    )
    return graph
