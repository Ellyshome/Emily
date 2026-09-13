# emily-core/emily_core/mcp/manager.py
"""MCP 工具管理器 —— 连接外部 MCP Server，适配为 BusinessFlowTool 接入 LangGraph。

职责：
  1. 按配置连接 MCP Server（stdio / sse / streamable_http）。
  2. 列出 server 暴露的工具（tools/list）。
  3. 把 MCP 工具适配为 emily 的 BusinessFlowTool（name/description/parameters/handler）。
  4. 注册进 core._business_flow_tools，供 LangGraph 执行引擎统一装配。

连接策略（基础版）：每次工具调用建立独立连接、调用后关闭。后期可优化为连接池。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from .config import McpConfig, McpServerConfig, load_config, resolve_config_path

logger = logging.getLogger("emily.mcp.manager")

# ── MCP SDK 可用性探测（缺失时降级，不阻断启动）──
_MCP_AVAILABLE = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    _MCP_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    logger.warning("mcp SDK 未安装，MCP 扩展不可用（pip install 'mcp>=1.19,<2'）：%s", e)


@dataclass
class McpToolMeta:
    """MCP 工具元数据（来自 tools/list）。"""
    server: str
    name: str
    description: str
    input_schema: dict


@asynccontextmanager
async def _session(cfg: McpServerConfig) -> AsyncIterator[Any]:
    """按 transport 建立 MCP ClientSession。"""
    if not _MCP_AVAILABLE:
        raise RuntimeError("mcp SDK 未安装，无法连接 MCP Server")

    if cfg.transport == "stdio":
        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env) if cfg.env else None,
            cwd=cfg.cwd or None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    elif cfg.transport == "sse":
        async with sse_client(cfg.url, headers=cfg.headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:  # streamable_http
        async with streamable_http_client(cfg.url, headers=cfg.headers or None) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def _discover_server(cfg: McpServerConfig) -> list[McpToolMeta]:
    """连接单个 server 并列出其全部工具。"""
    metas: list[McpToolMeta] = []
    async with _session(cfg) as session:
        result = await session.list_tools()
        for t in result.tools:
            schema = getattr(t, "inputSchema", None) or {}
            if hasattr(schema, "model_dump"):
                schema = schema.model_dump()
            metas.append(McpToolMeta(
                server=cfg.name,
                name=t.name,
                description=getattr(t, "description", "") or "",
                input_schema=schema or {"type": "object", "properties": {}},
            ))
    return metas


def _extract_text(result: Any) -> str:
    """从 MCP CallToolResult 提取文本内容。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _build_tool(cfg: McpServerConfig, meta: McpToolMeta):
    """把单个 MCP 工具适配为 BusinessFlowTool。"""
    from ..tools.business_flow_tools import BusinessFlowTool

    tool_name = (cfg.tool_prefix + meta.name) if cfg.tool_prefix else f"{cfg.name}__{meta.name}"

    async def _handler(params: dict, **kwargs) -> dict:
        try:
            async with _session(cfg) as session:
                result = await asyncio.wait_for(
                    session.call_tool(meta.name, arguments=params or {}),
                    timeout=cfg.timeout_seconds,
                )
            text = _extract_text(result)
            return {
                "success": True,
                "reply": text or "(MCP 工具无文本返回)",
                "data": getattr(result, "structuredContent", None),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP 工具 %s 调用失败: %s", tool_name, e)
            return {"success": False, "reply": f"MCP 工具调用失败：{e}"}

    return BusinessFlowTool(
        name=tool_name,
        description=meta.description or f"MCP 工具 {meta.name}（来自 {cfg.name}）",
        parameters=meta.input_schema or {"type": "object", "properties": {}},
        handler=_handler,
        category=cfg.category,
        permission_flag=cfg.permission_flag,
        write_mode=cfg.write_mode,
    )


def _sync_to_registry(cfg: McpServerConfig, meta: McpToolMeta, tool) -> None:
    """把 MCP 工具写入 tool_registry 表，使其进入用户可用工具集。

    LLM 可见工具集由 ``tool_registry`` 表驱动（见 tool_adapter.build_tool_specs 的
    fail-closed 过滤），仅注册进内存注册表不会被暴露。可见范围由 server 配置的
    ``category`` / ``permission_flag`` 决定：
      - category=base     → 全部用户可用
      - category=business → all=全部 / write=L3+ / admin=L5+
      - category=project  → 仅 L5-L6

    ``handler_module`` 标记为 ``mcp:<server>``，便于识别来源（一致性检查据此排除）。
    """
    try:
        from ..repositories.tool_registry_repo import ToolRegistryRepo

        display = (meta.description or tool.name).strip().splitlines()[0][:200] or tool.name
        ToolRegistryRepo.upsert(
            api_id=tool.name,
            display_name=display,
            category=cfg.category,
            permission_flag=cfg.permission_flag,
            # 与内置工具约定一致：all → meta（可直调）；write/admin → sop_only
            exposure_mode="meta" if cfg.permission_flag == "all" else "sop_only",
            handler_module=f"mcp:{cfg.name}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP 工具 '%s' 写入 tool_registry 失败: %s", tool.name, e)


def _run_async(coro):
    """在同步上下文安全地执行 async 协程（处理已存在 event loop 的情况）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 已在运行中的 event loop（如 uvicorn 启动阶段），用独立线程跑
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def load_mcp_tools(core) -> int:
    """把配置的 MCP Server 工具注册进 core._business_flow_tools。

    在 EmilyCore._ensure_initialized 的 register_all() 之后调用。
    返回注册成功的工具数量；任何失败仅告警，不阻断启动。
    """
    reg = getattr(core, "_business_flow_tools", None)
    if reg is None:
        logger.warning("load_mcp_tools: _business_flow_tools 为 None — skip")
        return 0

    explicit = getattr(getattr(core, "config", None), "mcp_config_path", "") or ""
    path = resolve_config_path(explicit)
    mcp_cfg = load_config(path) if path else McpConfig()

    registered = 0
    read_only_names: list[str] = []
    for sc in mcp_cfg.servers:
        if not sc.enabled:
            continue
        if not _MCP_AVAILABLE:
            logger.warning("load_mcp_tools: mcp SDK 缺失，跳过 server '%s'", sc.name)
            continue
        try:
            metas = _run_async(_discover_server(sc))
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP server '%s' discover 失败: %s", sc.name, e)
            continue
        for meta in metas:
            tool = _build_tool(sc, meta)
            if reg.has(tool.name):
                logger.warning("MCP 工具 '%s' 与已有工具重名，跳过", tool.name)
                continue
            try:
                reg.register(tool)
                registered += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP 工具 '%s' 注册失败: %s", tool.name, e)
                continue
            # 同步进 tool_registry 表，否则会被 fail-closed 过滤，LLM 不可见
            _sync_to_registry(sc, meta, tool)
            if sc.write_mode == "read":
                read_only_names.append(tool.name)

    # 只读 MCP 工具登记进兜底白名单，否则会被档位门禁拦截（唯一事实源：FallbackPolicy）
    if read_only_names:
        try:
            from ..workitem.langgraph_engine.agent.fallback_policy import FallbackPolicy
            FallbackPolicy.register_dynamic_read_tools(read_only_names)
        except Exception as e:  # noqa: BLE001
            logger.warning("load_mcp_tools: 登记兜底只读白名单失败: %s", e)

    if registered:
        logger.info("load_mcp_tools: %d 个 MCP 工具已注册", registered)
    return registered


async def probe_server_async(cfg: McpServerConfig) -> dict:
    """连接单个 MCP Server 并列出工具，返回在线状态与工具清单（供 console 探测）。"""
    if not _MCP_AVAILABLE:
        return {"online": False, "tools": [], "tool_count": 0, "error": "mcp SDK 未安装"}
    try:
        metas = await _discover_server(cfg)
        return {
            "online": True,
            "tools": [{"name": m.name, "description": m.description} for m in metas],
            "tool_count": len(metas),
        }
    except Exception as e:  # noqa: BLE001
        return {"online": False, "tools": [], "tool_count": 0, "error": str(e)}


def probe_server(cfg: McpServerConfig, timeout: float = 15.0) -> dict:
    """同步封装：探测单个 MCP Server 是否在线（供 console 等同步上下文调用）。"""

    async def _run() -> dict:
        return await asyncio.wait_for(probe_server_async(cfg), timeout=timeout)

    try:
        return _run_async(_run())
    except Exception as e:  # noqa: BLE001
        return {"online": False, "tools": [], "tool_count": 0, "error": str(e)}
