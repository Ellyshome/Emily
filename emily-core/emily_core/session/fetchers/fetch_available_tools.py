"""fetch_available_tools —— 获取用户可用的 API 工具列表。

被 SessionDataFetcher._sub_fetch_available_tools() 调用。
也可独立运行：python -m emily_core.session.fetchers.fetch_available_tools --user-id <UUID>

权限过滤规则：
  - category=base → 全部可用
  - category=business → 检查 sop_allow 或 level >= 3
  - category=project → level >= 5
  - 访客（L1）→ 不开放 MCP 接入工具
"""

from __future__ import annotations

import json
import logging
import argparse

logger = logging.getLogger("emily.session.fetchers.fetch_available_tools")

import os
DB_URL_DEFAULT = os.getenv(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily"
)

# MCP 工具在 tool_registry 中的来源标记前缀（见 mcp/manager._sync_to_registry）
MCP_HANDLER_PREFIX = "mcp:"


def fetch(perms: dict) -> list[dict]:
    """获取用户可用的 API 工具列表。

    Args:
        perms: 权限字典，含 level / sop_allow 等

    Returns:
        [{"api_id": "search_files", "display_name": "根据自然语言描述搜索可见文件"}, ...]
    """
    try:
        from ...repositories.tool_registry_repo import ToolRegistryRepo
        from ...permission.level import PermissionLevel

        level = perms.get("level", 1)
        sop_allow = perms.get("sop_allow", [])
        tools = ToolRegistryRepo.get_available(
            level=level,
            sop_allow=sop_allow,
        )

        # 访客（L1）不开放 MCP 接入工具：外部检索/抓取类能力不对访客开放
        try:
            if int(level or 1) <= PermissionLevel.GUEST.value:
                tools = [
                    t for t in tools
                    if not str(t.get("handler_module", "")).startswith(MCP_HANDLER_PREFIX)
                ]
        except (TypeError, ValueError):
            pass

        return tools
    except Exception as e:
        logger.error("fetch_available_tools failed: %s", e)
        return []


def main():
    """独立运行入口。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="获取用户可用的 API 工具列表")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    from ...infrastructure.database import init_db
    init_db(db_url=args.db_url)

    from ...services.permission_service import PermissionService
    perms = PermissionService().build_permission_dict(args.user_id)

    result = fetch(perms)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
