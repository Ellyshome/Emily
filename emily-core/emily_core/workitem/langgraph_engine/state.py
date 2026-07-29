# emily-core/emily_core/workitem/langgraph_engine/state.py
"""AgentLoopState —— 统一生命周期图 State，纯可序列化字段。

设计：State 仅含基础类型（str/int/dict/list），100% msgpack 可序列化 → MemorySaver 可用。
BusContext 通过 contextvars 传递（沿用旧设计）。
状态即对话历史：messages list 是 agent loop 唯一状态。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TypedDict

_bus_context: ContextVar = ContextVar("langgraph_bus_context", default=None)


def set_bus_context(ctx) -> None:
    _bus_context.set(ctx)


def get_bus_context():
    ctx = _bus_context.get()
    if ctx is None:
        raise RuntimeError("BusContext not set — call set_bus_context() before ainvoke")
    return ctx


def clear_bus_context() -> None:
    _bus_context.set(None)


class AgentLoopState(TypedDict, total=False):
    """统一生命周期图 State。

    messages 是 agent loop 唯一状态（system+user+assistant(tool_call)+tool_result）。
    """
    # ── 生命周期状态 ──
    wi_state: str               # created/routing/executing/waiting_for_input/summarizing/done/failed/error_analysis
    # ── agent loop 核心 ──
    messages: list              # 对话历史（OpenAI 格式）
    current_sop_id: str         # routing 匹配到的 SOP
    iteration_count: int        # agent loop 迭代次数
    _tool_specs: list           # 缓存 tool spec（避免每轮重建）
    _pending_tool_call: dict    # 当前待执行的 tool_call
    # ── WAITING_FOR_INPUT ──
    waiting_question: str       # interrupt 时的问题
    # ── 兜底 ──
    error_analysis: dict        # iteration cap / LLM 异常时的兜底分析
    # ── 元数据 ──
    node_timings: dict
    pipeline_run_id: str
    current_stage: str
    _max_iterations: int


def make_initial_state(*, pipeline_run_id: str, max_iterations: int = 12) -> dict:
    """构建 graph 初始 state。"""
    return {
        "wi_state": "created",
        "messages": [],
        "current_sop_id": "",
        "iteration_count": 0,
        "_tool_specs": [],
        "_pending_tool_call": None,
        "waiting_question": "",
        "error_analysis": {},
        "node_timings": {},
        "pipeline_run_id": pipeline_run_id,
        "current_stage": "",
        "_max_iterations": max_iterations,
    }
