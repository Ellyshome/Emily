# emily-core/emily_core/workitem/langgraph_engine/__init__.py
"""LangGraph 执行引擎 —— 替换 PipelineBUS 的 WorkItem 内部执行层。

架构关系：
  SessionAgent（编排者，不变）
    └─ SessionScheduler._run_one（feature flag 切换）
         ├─ workitem_engine="pipeline_bus" → PipelineBUS.run（旧引擎，保留回退）
         └─ workitem_engine="langgraph"    → graph.ainvoke（新引擎）

新引擎组件：
  - state.py           WorkItemGraphState（BusContext 容器 + graph 控制字段 + 错误分析字段）
  - error_analysis.py  ErrorAnalyzer（错误分类 + LLM 分析根因）
  - nodes.py           5 节点适配函数（node1~node4 + error_analysis）
  - graph.py           StateGraph 构建 + 条件边（node3 失败→error_analysis→node2 重规划）
  - hook_adapter.py    声明式 Hook 桥接到 graph 节点回调

纠错闭环（Self-Reflection）：
  node3 失败 → error_analysis（分析根因+分类）→ [route_after_analysis]
    ├─ param_error / tool_mismatch → node2（带 replan_hint 重规划）
    ├─ transient_failure → node3（直接重试，省 LLM 重新规划）
    └─ permission_denied / permanent_failure / missing_info → END

保留不变：WorkItemAgent / BusContext / Hook 体系 / WorkItem 状态机 / SessionAgent
"""

from .state import WorkItemGraphState, set_bus_context, get_bus_context, clear_bus_context, make_initial_state
from .graph import build_workitem_graph
from .hook_adapter import build_hook_adapter_from_config

__all__ = [
    "WorkItemGraphState",
    "set_bus_context",
    "get_bus_context",
    "clear_bus_context",
    "make_initial_state",
    "build_workitem_graph",
    "build_hook_adapter_from_config",
]
