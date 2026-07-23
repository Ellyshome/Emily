"""tools_consistency.py — BusinessFlowToolRegistry 一致性检查核心逻辑。

供 scripts/check_tools_consistency.py（薄壳 CLI）和 scripts/self_check.py（启动快速检查）复用。
方案 B：独立脚本 + self_check 集成，砍掉语义级检查（V6/V7/V9）和需要运行时实例的检查（V2/V3/V4）。

验证项：
  V1  — 注册工具数（硬编码集合大小）
  V5  — business 类空 schema 检测
  V10 — Skill YAML tools[].name 在 REGISTERED_TOOLS
  V11 — Skill YAML steps[].tool_name 在 REGISTERED_TOOLS
  V12 — Skill YAML steps[].tool_params 参数名在对应工具 schema
  V13a — 内存已注册工具在 tool_registry 表也存在
  V13b — tool_registry 表工具在内存也已注册

砍掉的验证项（理由见计划文档）：
  V2/V3/V4 — 需要 BusinessFlowToolRegistry 实例，独立脚本拿不到
  V6/V7/V9 — 语义级检查难机器化
  V8      — description 废弃概念（P2，后续按需加）
  V13c    — 同名工具字段一致性（P2）
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

logger = logging.getLogger("emily.infrastructure.tools_consistency")

# ── register_all 注册的工具名集合 ──────────────────────────────────
# ⚠️ 与 tools/registry.py 的 register_all 保持同步：
# 新增/删除工具时，需同步更新此集合（V13 的 DB 对比能间接发现遗漏，但显式维护更可靠）。
REGISTERED_TOOLS: set[str] = {
    # base
    "query_data", "knowledge_search",
    # business
    "record_event", "record_task", "record_meeting", "record_file",
    "query_files", "update_file_category", "write_user_memory",
    "create_task_node", "submit_node_deliverable", "confirm_node_deliverable",
    "return_node_deliverable", "query_my_nodes",
    # project
    "create_node", "query_node", "update_node_progress", "add_node_dependency",
    "mount_child_node", "update_nodes", "activate_nodes", "discard_nodes",
    "send_email", "fetch_inbox", "chat_archive", "manage_pending_issues", "voice_entry",
}

# ── 工具名 → (模块路径, schema 变量名) ─────────────────────────────
# 无 schema 常量的工具（write_user_memory / node_task 5 个）不在此映射，V12 对其跳过。
TOOL_SCHEMA_MAP: dict[str, tuple[str, str]] = {
    "query_data": ("emily_core.tools.query_tool", "_QUERY_TOOL_SCHEMA"),
    "knowledge_search": ("emily_core.tools.knowledge_search_tool", "_KNOWLEDGE_SEARCH_SCHEMA"),
    "record_event": ("emily_core.tools.event_tool", "_EVENT_TOOL_SCHEMA"),
    "record_task": ("emily_core.tools.task_tool", "_TASK_TOOL_SCHEMA"),
    "record_meeting": ("emily_core.tools.meeting_tool", "_MEETING_TOOL_SCHEMA"),
    "record_file": ("emily_core.tools.file_tool", "_FILE_TOOL_SCHEMA"),
    "query_files": ("emily_core.tools.file_tool", "_QUERY_FILES_SCHEMA"),
    "update_file_category": ("emily_core.tools.file_tool", "_UPDATE_CATEGORY_SCHEMA"),
    "create_node": ("emily_core.tools.node_tool", "_CREATE_NODE_SCHEMA"),
    "query_node": ("emily_core.tools.node_tool", "_QUERY_NODE_SCHEMA"),
    "update_node_progress": ("emily_core.tools.node_tool", "_UPDATE_PROGRESS_SCHEMA"),
    "add_node_dependency": ("emily_core.tools.node_tool", "_ADD_DEPENDENCY_SCHEMA"),
    "mount_child_node": ("emily_core.tools.node_tool", "_MOUNT_CHILD_SCHEMA"),
    "update_nodes": ("emily_core.tools.node_tool", "_UPDATE_NODES_SCHEMA"),
    "activate_nodes": ("emily_core.tools.node_tool", "_ACTIVATE_NODES_SCHEMA"),
    "discard_nodes": ("emily_core.tools.node_tool", "_DISCARD_NODES_SCHEMA"),
    "send_email": ("emily_core.tools.project", "_SEND_EMAIL_SCHEMA"),
    "fetch_inbox": ("emily_core.tools.project", "_FETCH_INBOX_SCHEMA"),
    "chat_archive": ("emily_core.tools.project", "_CHAT_ARCHIVE_SCHEMA"),
    "manage_pending_issues": ("emily_core.tools.project", "_PENDING_ISSUE_SCHEMA"),
    "voice_entry": ("emily_core.tools.project", "_VOICE_ENTRY_SCHEMA"),
}


def _load_tool_schemas() -> dict[str, set[str] | None]:
    """import 各 tool 模块，提取 schema 的 properties 参数集合。

    Returns:
        {tool_name: set(param_names) or None}，None 表示 schema 不可用或无 properties。
    """
    result: dict[str, set[str] | None] = {}
    for tool, (mod_path, schema_var) in TOOL_SCHEMA_MAP.items():
        try:
            m = importlib.import_module(mod_path)
            schema = getattr(m, schema_var, None)
            if isinstance(schema, dict) and "properties" in schema:
                result[tool] = set(schema["properties"].keys())
            else:
                result[tool] = None
        except Exception as e:
            logger.warning("load schema %s failed: %s", tool, e)
            result[tool] = None
    return result


def _load_skills(skill_dir: str | Path) -> list[tuple[str, dict, Path]]:
    """加载 Skill YAML 列表（轻量，不依赖 SkillRegistry 初始化）。

    Returns:
        [(skill_id, data_dict, file_path), ...]
    """
    import yaml
    skills: list[tuple[str, dict, Path]] = []
    skill_path = Path(skill_dir)
    if not skill_path.exists():
        return skills
    for yfile in sorted(skill_path.glob("*.skill.yaml")):
        try:
            data = yaml.safe_load(yfile.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                skills.append((data.get("skill_id", yfile.stem), data, yfile))
        except Exception as e:
            logger.warning("parse skill %s failed: %s", yfile.name, e)
    return skills


def _check_skill_yaml(
    skills: list[tuple[str, dict, Path]],
    tool_schemas: dict[str, set[str] | None],
    issues: list[dict],
) -> None:
    """V10/V11/V12: Skill YAML 工具名存在性 + 参数 schema 匹配。"""
    for skill_id, data, _yfile in skills:
        # V10: tools[].name 存在
        for t in data.get("tools", []) or []:
            if isinstance(t, dict) and "name" in t and t["name"] not in REGISTERED_TOOLS:
                issues.append({
                    "severity": "fatal", "check": "V10_tool_name_missing",
                    "skill": skill_id, "detail": f"tools 引用不存在的工具: {t['name']}",
                })
        # V11/V12: steps[].tool_name + tool_params
        for s in data.get("steps", []) or []:
            if not isinstance(s, dict):
                continue
            tn = s.get("tool_name")
            if not tn:
                continue
            if tn not in REGISTERED_TOOLS:
                issues.append({
                    "severity": "fatal", "check": "V11_step_tool_missing",
                    "skill": skill_id, "step": s.get("id"),
                    "detail": f"step 引用不存在的工具: {tn}",
                })
                continue
            # V12: tool_params 参数在 schema
            expected = tool_schemas.get(tn)
            if expected is None:
                continue  # 无 schema 的工具跳过参数检查
            actual: set[str] = set()
            for p in s.get("tool_params", []) or []:
                if isinstance(p, dict) and "name" in p:
                    actual.add(p["name"])
            extra = actual - expected
            if extra:
                issues.append({
                    "severity": "fatal", "check": "V12_param_mismatch",
                    "skill": skill_id, "step": s.get("id"), "tool": tn,
                    "detail": f"传了 schema 外参数: {sorted(extra)}，schema 实际: {sorted(expected)}",
                })


def _check_tool_registry(issues: list[dict]) -> dict:
    """V13a/V13b: tool_registry 表与内存 REGISTERED_TOOLS 一致性。"""
    try:
        from emily_core.repositories.tool_registry_repo import ToolRegistryRepo
        db_tools = {row["api_id"] for row in ToolRegistryRepo.get_all_active()}
    except Exception as e:
        logger.warning("check_tool_registry failed: %s", e)
        return {"error": str(e)}

    missing_in_db = REGISTERED_TOOLS - db_tools   # V13a: 内存有 DB 无
    extra_in_db = db_tools - REGISTERED_TOOLS     # V13b: DB 有内存无
    for t in sorted(missing_in_db):
        issues.append({
            "severity": "warning", "check": "V13a_missing_in_db",
            "tool": t, "detail": f"工具 {t} 内存已注册但 tool_registry 表缺失",
        })
    for t in sorted(extra_in_db):
        issues.append({
            "severity": "warning", "check": "V13b_extra_in_db",
            "tool": t, "detail": f"工具 {t} tool_registry 表有但内存未注册",
        })
    return {
        "db_count": len(db_tools),
        "missing_in_db": sorted(missing_in_db),
        "extra_in_db": sorted(extra_in_db),
    }


def check_all(skill_dir: str, check_tool_registry: bool = True) -> dict:
    """全量一致性检查。返回结构化报告 dict。

    Args:
        skill_dir: Skill YAML 目录路径
        check_tool_registry: 是否检查 tool_registry 表（需 DB 连接）

    Returns:
        {
            "summary": {registered, with_schema, skills, total_issues, fatal_issues},
            "empty_schema_tools": [...],   # V5
            "tool_registry": {...} or None,  # V13
            "issues": [{severity, check, ...}, ...],
        }
    """
    issues: list[dict] = []
    tool_schemas = _load_tool_schemas()

    # V5: business 类空 schema 检测
    empty_schema_tools = [
        tool for tool, params in tool_schemas.items()
        if params is not None and len(params) == 0
    ]
    for tool in empty_schema_tools:
        issues.append({
            "severity": "warning", "check": "V5_empty_schema",
            "tool": tool, "detail": f"工具 {tool} 的 schema properties 为空",
        })

    # V10/V11/V12: Skill YAML 一致性
    skills = _load_skills(skill_dir)
    _check_skill_yaml(skills, tool_schemas, issues)

    # V13: tool_registry 表同步（可选）
    tool_registry_report = None
    if check_tool_registry:
        tool_registry_report = _check_tool_registry(issues)

    fatal_count = sum(1 for i in issues if i["severity"] == "fatal")
    return {
        "summary": {
            "registered": len(REGISTERED_TOOLS),
            "with_schema": sum(1 for v in tool_schemas.values() if v is not None),
            "skills": len(skills),
            "total_issues": len(issues),
            "fatal_issues": fatal_count,
        },
        "empty_schema_tools": empty_schema_tools,
        "tool_registry": tool_registry_report,
        "issues": issues,
    }


def check_quick(skill_dir: str) -> dict:
    """快速检查（供 self_check 启动集成）。只做 Skill YAML 一致性，不查 DB。

    fail-open：任何异常返回 {"ok": False, "error": ...}，不阻断 self_check。

    Returns:
        {"skills": N, "issues": M, "fatal": K, "ok": bool}
    """
    try:
        tool_schemas = _load_tool_schemas()
        skills = _load_skills(skill_dir)
        issues: list[dict] = []
        _check_skill_yaml(skills, tool_schemas, issues)
        fatal = sum(1 for i in issues if i["severity"] == "fatal")
        return {
            "skills": len(skills),
            "issues": len(issues),
            "fatal": fatal,
            "ok": fatal == 0,
        }
    except Exception as e:
        logger.warning("check_quick failed: %s", e)
        return {"skills": 0, "issues": 0, "fatal": 0, "ok": False, "error": str(e)}
