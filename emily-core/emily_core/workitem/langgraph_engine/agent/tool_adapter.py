# emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py
"""Function-calling 工具适配 —— BusinessFlowTool + Resolver → OpenAI tool spec。

按 session_api_ids 过滤（fail-closed：用户无权限的工具不暴露）。
参照 registry.py:116 的 _tool() 桥接模式 + 原 WorkItemAgent 的权限过滤。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...tools.business_flow_tools import BusinessFlowToolRegistry
    from .resolver import ResolverRegistry

logger = logging.getLogger("emily.langgraph.tool_adapter")


# fallback 路径白名单：意图识别失败时（无 SOP 约束），LLM 只能查询不能写入，
# 避免 LLM 自作主张调 record_event / create_node 等工具乱写 DB。
# 写入类工具必须由 SOP 路由命中后才暴露（intent_type="sop"）。
FALLBACK_SAFE_TOOLS: set[str] = {
    "query_data",
    "query_node",
    "query_my_nodes",
    "query_files",
    "query_experts",
    "knowledge_search",
    "list_attachments",
    "list_file_versions",
    "chat_archive",
    "fetch_inbox",
}


def _session_api_ids(ctx) -> set[str]:
    """从 SessionContext.available_tools 提取 api_id 集合。参照原 WorkItemAgent 实现。"""
    session_ctx = ctx.get_session_context() if ctx else None
    ids: set[str] = set()
    if session_ctx:
        for t in getattr(session_ctx, "available_tools", []) or []:
            api_id = t.get("api_id") if isinstance(t, dict) else None
            if api_id:
                ids.add(api_id)
    return ids


def build_tool_specs(
    business_tools: "BusinessFlowToolRegistry",
    resolvers: "ResolverRegistry",
    session_api_ids: set[str],
    *,
    fallback_mode: bool = False,
) -> list[dict]:
    """构建 LLM 可见的 tool spec 列表，按 session 权限过滤。

    Args:
        business_tools: BusinessFlowToolRegistry 实例
        resolvers: ResolverRegistry 实例
        session_api_ids: 用户可见工具 api_id 集合（来自 SessionContext.available_tools）
        fallback_mode: 意图识别失败时的兜底模式。True 时只暴露查询类白名单工具
            （FALLBACK_SAFE_TOOLS），不暴露任何写入类工具，避免 LLM 在无 SOP 约束时乱写 DB。

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
            if fallback_mode and name not in FALLBACK_SAFE_TOOLS:
                continue  # fallback 模式：只暴露查询类白名单工具
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
    business_count = len(specs) - resolver_count - control_count
    logger.info("build_tool_specs: %d business tools + %d resolvers + %d control = %d specs (fallback=%s)",
                business_count, resolver_count, control_count, len(specs), fallback_mode)
    return specs
