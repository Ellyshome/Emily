# emily-core/emily_core/workitem/langgraph_engine/graph.py
"""统一生命周期 StateGraph —— created→routing→executing(agent loop)→summarizing→done/failed。

executing 内嵌 agent loop：executing(agent_node) ↔ tool_node，由条件边驱动循环。
WAITING_FOR_INPUT 用 LangGraph interrupt()（在 tool_node 的 ask_user 分支触发）。
error_analysis 作 iteration cap 兜底。
"""
from __future__ import annotations

import logging

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import AgentLoopState
from .nodes import (
    make_created, make_routing, make_executing, make_summarizing, make_error_analysis,
    make_quality_gate,
)
from .agent.loop import route_after_agent, route_after_tool

logger = logging.getLogger("emily.langgraph.graph")


def build_workitem_graph(
    *,
    hook_adapter,
    llm_client,
    business_tools,
    resolvers,
    config,
    max_iterations: int = 12,
) -> "StateGraph":
    """构建统一生命周期图。

    Args:
        hook_adapter: HookAdapter 实例
        llm_client: LLMClient 实例
        business_tools: BusinessFlowToolRegistry 实例
        resolvers: ResolverRegistry 实例
        config: Config 实例
        max_iterations: agent loop 最大迭代数
    """
    gs = StateGraph(AgentLoopState)

    # ── 注册节点 ──
    gs.add_node("created", make_created(
        hook_adapter, business_tools=business_tools, resolvers=resolvers))
    gs.add_node("routing", make_routing(hook_adapter))
    gs.add_node("executing", make_executing(
        hook_adapter, llm_client=llm_client, business_tools=business_tools,
        resolvers=resolvers, config=config))
    gs.add_node("agent_node", _make_agent_loop_entry(
        llm_client=llm_client, business_tools=business_tools,
        resolvers=resolvers, config=config))
    gs.add_node("tool_node", _make_tool_loop_entry(
        llm_client=llm_client, business_tools=business_tools, resolvers=resolvers))
    gs.add_node("summarizing", make_summarizing(hook_adapter))
    gs.add_node("error_analysis", make_error_analysis(
        hook_adapter, llm_client=llm_client, config=config))
    gs.add_node("quality_gate", make_quality_gate())

    # ── 边 ──
    gs.add_edge(START, "created")
    gs.add_edge("created", "routing")
    gs.add_edge("routing", "executing")

    # executing 触发首轮 agent_node
    gs.add_edge("executing", "agent_node")

    # agent_node → 条件路由（tool_node / summarizing / error_analysis / agent_node 自循环）
    gs.add_conditional_edges(
        "agent_node",
        route_after_agent,
        {"tool_node": "tool_node", "summarizing": "summarizing",
         "error_analysis": "error_analysis", "agent_node": "agent_node"},
    )

    # tool_node → 条件路由（complete_work→summarizing / 否则→agent_node 循环）
    gs.add_conditional_edges(
        "tool_node",
        route_after_tool,
        {"agent_node": "agent_node", "summarizing": "summarizing"},
    )

    # summarizing → quality_gate → 条件路由（pass→END / reject→agent_node）
    gs.add_edge("summarizing", "quality_gate")
    gs.add_conditional_edges(
        "quality_gate",
        route_after_quality_gate,
        {"agent_node": "agent_node", "done": END},
    )

    # error_analysis → 条件路由（failed→END / agent_node 重试）
    gs.add_conditional_edges(
        "error_analysis",
        route_after_error,
        {"failed": END, "agent_node": "agent_node"},
    )

    graph = gs.compile(checkpointer=MemorySaver())
    logger.info("Unified lifecycle graph built: created→routing→executing(agent loop)→summarizing, "
                "max_iterations=%d, checkpointer=MemorySaver", max_iterations)
    return graph


def route_after_error(state: dict) -> str:
    """error_analysis 之后路由：should_abort→failed(END)，否则→agent_node 重试。"""
    ea = state.get("error_analysis", {}) or {}
    if ea.get("should_abort"):
        return "failed"
    return "agent_node"


def route_after_quality_gate(state: dict) -> str:
    """quality_gate 之后路由：executing → agent_node 重做，否则 → done(END)。"""
    wi_state = state.get("wi_state", "")
    if wi_state == "executing":
        return "agent_node"
    return "done"


def _make_agent_loop_entry(*, llm_client, business_tools, resolvers, config):
    """agent_node 节点入口（直接调 loop.agent_node，不经 hook 包装——executing 节点已 fire hook）。"""
    from .agent.loop import agent_node

    async def _node(state: dict) -> dict:
        ctx = None
        try:
            from .state import get_bus_context
            ctx = get_bus_context()
        except RuntimeError:
            pass
        sop_text = ctx.get("sop_text", "") if ctx else ""
        return await agent_node(
            state, llm_client=llm_client, business_tools=business_tools,
            resolvers=resolvers, sop_text=sop_text, config=config)
    _node.__name__ = "agent_node"
    return _node


def _make_tool_loop_entry(*, llm_client, business_tools, resolvers):
    """tool_node 节点入口（直接调 loop.tool_node）。"""
    from .agent.loop import tool_node

    async def _node(state: dict) -> dict:
        return await tool_node(state, llm_client=llm_client,
                               business_tools=business_tools, resolvers=resolvers)
    _node.__name__ = "tool_node"
    return _node
