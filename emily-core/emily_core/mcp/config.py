# emily-core/emily_core/mcp/config.py
"""MCP 配置模型与加载。

配置文件为 ``mcp_servers.json``（示例见 emily-data/config/mcp_servers.json）。
每个 server 支持三种传输方式：
  - stdio：          本地子进程（如 npx / uv 启动的 MCP server）
  - sse：            远程 SSE 端点（旧版 HTTP 传输）
  - streamable_http：远程 Streamable HTTP 端点（MCP 新标准，推荐）
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("emily.mcp.config")

Transport = Literal["stdio", "sse", "streamable_http"]


class McpServerConfig(BaseModel):
    """单个 MCP Server 的连接与工具适配配置。"""

    name: str                            # 逻辑名（日志 / 工具命名用）
    description: str = ""                # 一句话介绍（配置页展示用）
    enabled: bool = True                 # 是否启用（默认关，接入时置 true）
    transport: Transport = "stdio"       # 传输方式

    # ── stdio 传输专用 ──
    command: str = ""                    # 可执行命令，如 "npx" / "python" / "uv"
    args: list[str] = Field(default_factory=list)       # 命令参数
    env: dict[str, str] = Field(default_factory=dict)   # 子进程环境变量
    cwd: str = ""                        # 子进程工作目录（空 = 继承）

    # ── sse / streamable_http 传输专用 ──
    url: str = ""                        # 端点地址
    headers: dict[str, str] = Field(default_factory=dict)  # 鉴权头（如 Authorization）

    # ── 工具适配 ──
    tool_prefix: str = ""                # 工具名前缀，避免与 emily 原生工具重名
    category: str = "base"               # base / business / project（同 tools/registry.py）
    permission_flag: str = "all"         # all / admin / write
    write_mode: str = "read"             # read/append/transition/overwrite/delete
    timeout_seconds: float = 30.0        # 单次调用超时（秒）


class McpConfig(BaseModel):
    """MCP 总配置。"""

    servers: list[McpServerConfig] = Field(default_factory=list)


def load_config(path: str | Path) -> McpConfig:
    """从 JSON 文件加载 MCP 配置。

    文件不存在 / 解析失败 / 校验失败时返回空配置（不阻断启动）。
    """
    p = Path(path)
    if not p.exists():
        logger.warning("MCP config not found: %s", p)
        return McpConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP config parse failed %s: %s", p, e)
        return McpConfig()
    try:
        return McpConfig(**data)
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP config validation failed %s: %s", p, e)
        return McpConfig()


def resolve_config_path(explicit: str = "") -> Path | None:
    """解析 mcp_servers.json 路径：显式配置 → 环境变量 → 容器 → 开发回退。

    返回第一个存在的路径；都不存在时返回容器默认路径（load_config 会降级为空配置）。
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("EMILY_MCP_CONFIG", "")
    if env:
        candidates.append(Path(env))
    candidates += [
        Path("/app/config/mcp_servers.json"),
        Path(__file__).resolve().parents[3] / "emily-data" / "config" / "mcp_servers.json",
        Path(__file__).resolve().parents[2] / "emily-data" / "config" / "mcp_servers.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]
