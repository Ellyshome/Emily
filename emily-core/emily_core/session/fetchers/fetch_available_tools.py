"""fetch_available_tools —— 获取用户可用的 API 工具列表。

被 SessionDataFetcher._sub_fetch_available_tools() 调用。
也可独立运行：python -m emily_core.session.fetchers.fetch_available_tools --user-id <UUID>

权限过滤规则（两层）：
  1. 级别层（ToolRegistryRepo.get_available）
     - category=base     → 全部可用
     - category=business → permission_flag=all 全员 / write 需 level>=3 / admin 需 level>=5
     - category=project  → level >= 5
  2. SOP 授权层（本模块）
     - 已授权 SOP（sop_allow）在 SOP 文档「3.2 使用的系统工具 / API」中声明的工具，
       即使级别层不放行，也并集进可见集——能力可见性与工具可见性必须一致，否则
       用户能调起某个业务流程却拿不到它的核心工具（表现为流程空转至超时）。
     - fail-closed：SOP 未声明或声明解析失败 → 不放行；未注册/已停用工具一律忽略。
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


def fetch(perms: dict, skill_registry=None) -> list[dict]:
    """获取用户可用的 API 工具列表。

    Args:
        perms: 权限字典，含 level / sop_allow 等
        skill_registry: SkillRegistry（SOP 索引器），用于取各 SOP 声明的工具。
            为 None 时跳过 SOP 授权层（仅级别层），保证调用方降级不炸。

    Returns:
        [{"api_id": "search_files", "display_name": "根据自然语言描述搜索可见文件"}, ...]
    """
    try:
        from ...repositories.tool_registry_repo import ToolRegistryRepo
        from ...permission.level import PermissionLevel

        level = perms.get("level", 1)
        sop_allow = perms.get("sop_allow", [])
        tools = ToolRegistryRepo.get_available(level=level)

        tools = _merge_sop_declared_tools(tools, sop_allow, skill_registry)

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


def _merge_sop_declared_tools(tools: list[dict], sop_allow, skill_registry) -> list[dict]:
    """把 sop_allow 中已授权 SOP 声明的工具并入可见集（fail-closed）。

    仅当 SOP 文档显式声明（3.2 节表格解析非空）且该工具在 tool_registry 中处于启用
    状态时才并入；其余情况一律不放行。
    """
    allow = {str(s) for s in (sop_allow or []) if s}
    if not allow or skill_registry is None:
        return tools

    from ..capability_runner import short_sop_id
    from ...repositories.tool_registry_repo import ToolRegistryRepo

    declared: set[str] = set()
    for doc in (skill_registry.list_skills() or []):
        if short_sop_id(getattr(doc, "sop_id", "") or "") not in allow:
            continue
        declared |= {t for t in (getattr(doc, "tools", None) or []) if t}

    missing = declared - {t.get("api_id") for t in tools}
    if not missing:
        return tools

    registered = {row.get("api_id"): row for row in ToolRegistryRepo.get_all_active()}
    merged = list(tools)
    released: list[str] = []
    unknown: list[str] = []
    for name in sorted(missing):
        row = registered.get(name)
        if row is None:
            unknown.append(name)
            continue
        merged.append(row)
        released.append(name)
    if unknown:
        logger.warning(
            "SOP 声明了 %d 个未注册/已停用的工具，已 fail-closed 不放行（请核对 SOP 3.2 节）: %s",
            len(unknown), unknown)
    if released:
        logger.info("SOP 授权放行 %d 个工具: %s", len(released), released)
    return merged


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

    result = fetch(perms, skill_registry=_load_skill_registry())
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _load_skill_registry():
    """为独立运行构造 SOP 索引器（主链路由调用方注入 core._skill_registry）。"""
    from ...infrastructure.paths import resolve_data_path
    from ...skill.registry import SkillRegistry

    registry = SkillRegistry(skill_directory=resolve_data_path("", "/app/skills", "emily-data/skills"))
    registry.load()
    return registry


if __name__ == "__main__":
    main()
