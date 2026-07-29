# emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py
"""Function-calling 工具适配 —— BusinessFlowTool + Resolver → OpenAI tool spec。

按 session_api_ids 过滤（fail-closed：用户无权限的工具不暴露）。
参照 registry.py:116 的 _tool() 桥接模式 + workitem_agent.py:547 的权限过滤。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...tools.business_flow_tools import BusinessFlowToolRegistry
    from .resolver import ResolverRegistry

logger = logging.getLogger("emily.langgraph.tool_adapter")


def build_tool_specs(
    business_tools: "BusinessFlowToolRegistry",
    resolvers: "ResolverRegistry",
    session_api_ids: set[str],
) -> list[dict]:
    """构建 LLM 可见的 tool spec 列表，按 session 权限过滤。

    Args:
        business_tools: BusinessFlowToolRegistry 实例
        resolvers: ResolverRegistry 实例
        session_api_ids: 用户可见工具 api_id 集合（来自 SessionContext.available_tools）

    Returns:
        list[dict]: OpenAI tool spec 列表
    """
    specs: list[dict] = []

    if not session_api_ids:
        logger.warning("build_tool_specs: session_api_ids 为空，tool_registry 表可能未填充，fail-closed")
        # fail-closed：仅暴露 resolver（resolver 内部做权限约束），不暴露任何业务工具
    else:
        for name in business_tools.list_names():
            if name not in session_api_ids:
                continue  # fail-closed：用户无权限的工具不暴露
            tool = business_tools.get(name)
            if tool is None:
                continue
            specs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            })

    # resolver 始终可见（其内部做权限约束，第二层）
    for r in resolvers.list_all():
        specs.append(r.spec)

    # 控制工具（complete_work / ask_user）始终可见——是 agent loop 的控制信号，
    # 非业务工具，不经过权限过滤
    from .control_tools import CONTROL_TOOL_SPECS
    specs.extend(CONTROL_TOOL_SPECS)

    resolver_count = len(list(resolvers.list_all()))
    control_count = len(CONTROL_TOOL_SPECS)
    logger.info("build_tool_specs: %d business tools + %d resolvers + %d control = %d specs",
                len(specs) - resolver_count - control_count, resolver_count,
                control_count, len(specs))
    return specs
