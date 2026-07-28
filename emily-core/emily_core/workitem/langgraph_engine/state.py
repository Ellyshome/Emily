# emily-core/emily_core/workitem/langgraph_engine/state.py
"""WorkItemGraphState —— LangGraph State，纯可序列化字段。

设计决策（方案 C：State 纯数据 + contextvars 承载 BusContext）：
  - State 仅含基础类型（str/int/dict），100% msgpack 可序列化 → checkpoint 可用
  - BusContext 通过 contextvars.ContextVar 传递，async 安全，每个 ainvoke 独立
  - Hook 和 node handler 仍接收完整 BusContext（零改动），适配层透明桥接

字段说明：
  flow_control: dict，聚合节点间流程控制信号
    - should_abort: bool       — 是否终止执行
    - abort_reason: str        — 终止原因
    - has_failed_step: bool    — 是否有失败的 step（route_after_node3 读）
  error_analysis: dict         — error_analysis 节点的分析结果
  replan_hint: str             — 给 node2 的修复建议
  error_type: str              — 错误分类
  replan_count: int            — 重规划次数
  node_timings: dict[str,int]  — 节点耗时 ms
  started_at: str              — graph 开始时间 ISO
  pipeline_run_id: str         — = BusContext.pipeline_run_id = thread_id
  current_stage: str           — 当前节点名
  _entered_node2: bool         — 是否已进入 node2
  _max_replan: int             — 最大重规划次数
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Annotated

# contextvars 承载 BusContext（async 安全，每个 Task/ainvoke 独立）
_bus_context: ContextVar = ContextVar("langgraph_bus_context", default=None)


def set_bus_context(ctx) -> None:
    """设置当前 ainvoke 的 BusContext（由 _run_graph 调用）。"""
    _bus_context.set(ctx)


def get_bus_context():
    """获取当前 ainvoke 的 BusContext（节点函数 + 条件边调用）。"""
    ctx = _bus_context.get()
    if ctx is None:
        raise RuntimeError("BusContext not set in contextvars — call set_bus_context() before ainvoke")
    return ctx


def clear_bus_context() -> None:
    """清除当前 BusContext（ainvoke 结束后调用，防止泄漏到下一个 ainvoke）。"""
    _bus_context.set(None)


# ════════════════════════════════════════════════════════════════════
# WorkItemGraphState — TypedDict（langgraph StateGraph 原生支持）
# ════════════════════════════════════════════════════════════════════

from typing import TypedDict


class WorkItemGraphState(TypedDict, total=False):
    """LangGraph State —— 纯可序列化 TypedDict。

    所有字段均为基础类型（str/int/bool/dict），100% msgpack 兼容。
    BusContext 通过 contextvars.get_bus_context() 获取，不存 state。

    注意：使用 TypedDict 而非 dict 子类——langgraph 1.x 的 reducer（add_messages 等）
    需要 TypedDict 的 __annotations__ 来确定合并策略。
    """

    flow_control: dict         # {"should_abort": bool, "abort_reason": str, "has_failed_step": bool}
    error_analysis: dict       # error_analysis 节点的分析结果
    replan_hint: str           # 给 node2 的修复建议
    error_type: str            # 错误分类
    replan_count: int          # 重规划次数
    node_timings: dict         # 节点耗时 ms
    started_at: str            # graph 开始时间 ISO
    pipeline_run_id: str       # = BusContext.pipeline_run_id = thread_id
    current_stage: str         # 当前节点名
    _entered_node2: bool       # 是否已进入 node2
    _max_replan: int           # 最大重规划次数


def make_initial_state(*, pipeline_run_id: str, max_replan: int = 1) -> dict:
    """构建 graph 初始 state。"""
    return {
        "flow_control": {
            "should_abort": False,
            "abort_reason": "",
            "has_failed_step": False,
        },
        "error_analysis": {},
        "replan_hint": "",
        "error_type": "",
        "replan_count": 0,
        "node_timings": {},
        "started_at": "",
        "pipeline_run_id": pipeline_run_id,
        "current_stage": "",
        "_entered_node2": False,
        "_max_replan": max_replan,
    }
