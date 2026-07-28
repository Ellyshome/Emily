# emily-core/emily_core/workitem/langgraph_engine/state.py
"""WorkItemGraphState —— LangGraph State，BusContext 容器 + graph 控制字段 + 错误分析字段。

设计决策（方案 B：State 持有 BusContext 引用）：
  - Hook 子类全部读 BusContext 字段，若 State 替代 BusContext 需改所有 Hook
  - State 作为 BusContext 容器，节点函数从 state["context"] 取 BusContext
    传给现有 handler —— handler 零改动

字段说明：
  - context: BusContext 实例（节点 handler 和 Hook 通过它交互）
  - replan_count: 重规划次数（条件边防死循环，上限由 config.langgraph_max_replan 控制）
  - node_timings: 节点耗时 ms（对接 PipelineExecutionLogger）
  - started_at: graph 开始时间 ISO
  - error_analysis: error_analysis 节点的分析结果 dict（error_type/root_cause/replan_hint/...）
  - replan_hint: 给 node2 的修复建议（由 error_analysis 产出，注入 _llm_plan）
  - error_type: 错误分类（由 error_analysis 产出，route_after_analysis 据此路由）
"""

from __future__ import annotations

from typing import TypedDict


class WorkItemGraphState(TypedDict, total=False):
    """LangGraph State —— BusContext 容器 + graph 控制字段 + 错误分析字段。

    total=False：所有字段可选，初始 invoke 只传 context，其余由节点逐步填充。
    """
    # ── 核心载体 ──
    context: object  # BusContext 实例，节点 handler 和 Hook 通过它交互

    # ── graph 控制字段 ──
    replan_count: int                      # 重规划次数（node3→error_analysis→node2 循环计数）
    node_timings: dict[str, int]           # 各节点耗时 ms
    started_at: str                        # graph 开始时间 ISO
    _entered_node2: bool                   # 是否已进入过 node2（区分首次规划 vs 重规划）

    # ── 错误分析字段（error_analysis 节点产出）──
    error_analysis: dict                   # 完整分析结果（error_type/root_cause/replan_hint/should_*/user_prompt）
    replan_hint: str                       # 给 node2 的修复建议（注入 _llm_plan prompt）
    error_type: str                        # 错误分类（route_after_analysis 据此路由）

    # ── 日志/trace 对接 ──
    pipeline_run_id: str                   # = BusContext.pipeline_run_id = thread_id
    current_stage: str                     # 当前节点名（对接 LLMInteractionLogger.set_stage）

    # ── 内部控制（非持久化）──
    _max_replan: int                       # 最大重规划次数（由 build_workitem_graph 注入）
