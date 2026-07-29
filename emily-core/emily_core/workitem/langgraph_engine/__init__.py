# emily-core/emily_core/workitem/langgraph_engine/__init__.py
"""LangGraph 执行引擎 —— 统一生命周期图 + L3 agent loop。

M6 大爆炸重写：旧 5 节点图（created/plan/execute/summarize/error_analysis）替换为
统一生命周期图 created→routing→executing(agent loop)→summarizing→done/failed。

架构关系：
  SessionAgent（编排者，不变）
    └─ SessionScheduler._run_one（调 graph.ainvoke / Command(resume=...)）
         └─ graph.ainvoke → created→routing→executing(agent loop: agent_node↔tool_node)→summarizing

新引擎组件：
  - state.py           AgentLoopState（纯可序列化，messages 是 agent loop 唯一状态）
  - nodes.py           统一节点工厂（make_created/routing/executing/summarizing/error_analysis）
  - graph.py           统一生命周期图构建 + 条件边（agent↔tool 循环 + error_analysis 兜底）
  - hook_adapter.py    声明式 Hook 桥接到 graph 节点回调
  - error_analysis.py  ErrorAnalyzer（错误分类 + LLM 分析根因）
  - agent/             子包：resolver / tool_adapter / loop / prompt_builder

保留不变：BusContext / Hook 体系 / WorkItem 状态机 / SessionAgent
"""

# 延迟导入：graph 依赖 langgraph（仅 Docker 内可用），本地测试时延后加载

def __getattr__(name):
    if name == "AgentLoopState":
        from .state import AgentLoopState as _c; return _c
    if name == "set_bus_context":
        from .state import set_bus_context as _f; return _f
    if name == "get_bus_context":
        from .state import get_bus_context as _f; return _f
    if name == "clear_bus_context":
        from .state import clear_bus_context as _f; return _f
    if name == "make_initial_state":
        from .state import make_initial_state as _f; return _f
    if name == "build_workitem_graph":
        from .graph import build_workitem_graph as _f; return _f
    if name == "build_hook_adapter_from_config":
        from .hook_adapter import build_hook_adapter_from_config as _f; return _f
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AgentLoopState",
    "set_bus_context",
    "get_bus_context",
    "clear_bus_context",
    "make_initial_state",
    "build_workitem_graph",
    "build_hook_adapter_from_config",
]
