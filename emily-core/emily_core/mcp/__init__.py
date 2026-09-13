# emily-core/emily_core/mcp/__init__.py
"""MCP（Model Context Protocol）扩展层。

把外部 MCP Server 暴露的工具，适配为 emily 的 BusinessFlowTool，
注册进 BusinessFlowToolRegistry，从而接入 LangGraph 执行引擎。

用法（在 EmilyCore._ensure_initialized 中，register_all() 之后）：
    from emily_core.mcp import load_mcp_tools
    load_mcp_tools(core)

接入新 MCP Server：编辑 emily-data/config/mcp_servers.json，新增一项即可。
"""

from .config import McpConfig, McpServerConfig, load_config, resolve_config_path
from .manager import load_mcp_tools, probe_server, probe_server_async

__all__ = [
    "load_mcp_tools",
    "probe_server",
    "probe_server_async",
    "McpConfig",
    "McpServerConfig",
    "load_config",
    "resolve_config_path",
]
