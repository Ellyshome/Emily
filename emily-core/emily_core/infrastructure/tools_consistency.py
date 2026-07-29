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
    "query_files", "update_file_category", "send_file", "write_user_memory",
    "link_file", "new_file_version", "delete_file", "list_file_versions",
    "link_to_master", "unlink_attachment", "list_attachments",
    "update_file_purpose",
    "create_task_node", "submit_node_deliverable", "confirm_node_deliverable",
    "return_node_deliverable", "query_my_nodes",
    # project
    "create_node", "query_node", "update_node_progress", "add_node_dependency",
    "mount_child_node", "update_nodes", "activate_nodes", "discard_nodes",
    "send_email", "fetch_inbox", "chat_archive", "manage_pending_issues", "voice_entry",
}

# ── 工具名 → (display_name, category, permission_flag, exposure_mode) ──
# 供 _ensure_tool_registry_seed() 自动种子与 register_api.py 保持一致
# ⚠️ 与 tools/registry.py 的 register_all 保持同步
# exposure_mode: meta=可被SOP-999直调(默认all类), sop_only=必须走专属SOP(默认write/admin类+破坏性工具)
TOOL_META_MAP: dict[str, tuple[str, str, str, str]] = {
    # base
    "query_data":         ("查询项目数据",          "base",     "all",   "meta"),
    "knowledge_search":   ("搜索知识库获取领域知识", "base",     "all",   "meta"),
    "send_email":         ("发送邮件",              "base",     "all",   "meta"),
    "fetch_inbox":        ("获取收件箱",            "base",     "all",   "meta"),
    "chat_archive":       ("聊天归档查询",          "base",     "all",   "meta"),
    "manage_pending_issues": ("管理待解决问题",     "base",     "all",   "meta"),
    "voice_entry":        ("语音入口",              "base",     "all",   "meta"),
    # business — permission_flag=all → exposure_mode=meta（只读可直调）
    "query_files":        ("按分类查询项目文件", "business", "all",   "meta"),
    "send_file":          ("向用户发送已有文件", "business", "all",   "meta"),
    "list_file_versions": ("列出文件版本",       "business", "all",   "meta"),
    "list_attachments":   ("列出主文件下的附件", "business", "all",   "meta"),
    "write_user_memory":  ("写入用户长期记忆",   "business", "all",   "meta"),
    # business — permission_flag=write → exposure_mode=sop_only（写操作默认走专属SOP）
    "record_event":       ("记录项目事件",   "business", "write", "sop_only"),
    "record_task":        ("创建任务",       "business", "write", "sop_only"),
    "record_meeting":     ("归档会议纪要",   "business", "write", "sop_only"),
    "record_file":        ("记录文件元数据", "business", "write", "sop_only"),
    "update_file_category": ("修改文件分类归属", "business", "write", "sop_only"),
    "link_file":          ("关联文件到业务对象", "business", "write", "sop_only"),
    "new_file_version":   ("创建文件新版本",     "business", "write", "sop_only"),
    "delete_file":        ("软删除文件",         "business", "write", "sop_only"),
    "link_to_master":     ("挂载附件到主文件",   "business", "write", "sop_only"),
    "unlink_attachment":  ("卸载附件为独立文件", "business", "write", "sop_only"),
    "update_file_purpose": ("校正文件的业务意图", "business", "write", "sop_only"),
    "create_task_node":        ("创建TASK类型叶子节点", "business", "write", "sop_only"),
    "submit_node_deliverable": ("提交节点成果",   "business", "write", "sop_only"),
    "confirm_node_deliverable":("确认节点成果",   "business", "write", "sop_only"),
    "return_node_deliverable": ("退回节点成果",   "business", "write", "sop_only"),
    "query_my_nodes":          ("查询我负责的节点","business", "write", "sop_only"),
    # project — permission_flag=admin → exposure_mode=sop_only
    "create_node":           ("创建全景节点",   "project", "admin", "sop_only"),
    "query_node":            ("查询全景节点",   "project", "admin", "sop_only"),
    "update_node_progress":  ("更新节点进度",   "project", "admin", "sop_only"),
    "add_node_dependency":   ("添加节点依赖",   "project", "admin", "sop_only"),
    "mount_child_node":      ("挂载子节点",     "project", "admin", "sop_only"),
    "update_nodes":          ("批量更新节点",   "project", "admin", "sop_only"),
    "activate_nodes":        ("批量激活节点",   "project", "admin", "sop_only"),
    "discard_nodes":         ("批量废弃节点",   "project", "admin", "sop_only"),
}

# ── 工具名 → (模块路径, schema 变量名) ─────────────────────────────
# 所有需要 LLM 填参的工具必须在此映射中有条目。V14 会检测缺失。
# write_user_memory 的 schema 由 create_memory_tool() 动态生成，不在此静态映射。
TOOL_SCHEMA_MAP: dict[str, tuple[str, str]] = {
    "query_data": ("emily_core.tools.query_tool", "_QUERY_TOOL_SCHEMA"),
    "knowledge_search": ("emily_core.tools.knowledge_search_tool", "_KNOWLEDGE_SEARCH_SCHEMA"),
    "record_event": ("emily_core.tools.event_tool", "_EVENT_TOOL_SCHEMA"),
    "record_task": ("emily_core.tools.task_tool", "_TASK_TOOL_SCHEMA"),
    "record_meeting": ("emily_core.tools.meeting_tool", "_MEETING_TOOL_SCHEMA"),
    "record_file": ("emily_core.tools.file_tool", "_FILE_TOOL_SCHEMA"),
    "query_files": ("emily_core.tools.file_tool", "_QUERY_FILES_SCHEMA"),
    "update_file_category": ("emily_core.tools.file_tool", "_UPDATE_CATEGORY_SCHEMA"),
    "send_file": ("emily_core.tools.file_tool", "_SEND_FILE_SCHEMA"),
    "link_file": ("emily_core.tools.file_tool", "_LINK_FILE_SCHEMA"),
    "new_file_version": ("emily_core.tools.file_tool", "_NEW_FILE_VERSION_SCHEMA"),
    "delete_file": ("emily_core.tools.file_tool", "_DELETE_FILE_SCHEMA"),
    "list_file_versions": ("emily_core.tools.file_tool", "_LIST_FILE_VERSIONS_SCHEMA"),
    "link_to_master": ("emily_core.tools.file_tool", "_LINK_TO_MASTER_SCHEMA"),
    "unlink_attachment": ("emily_core.tools.file_tool", "_UNLINK_ATTACHMENT_SCHEMA"),
    "list_attachments": ("emily_core.tools.file_tool", "_LIST_ATTACHMENTS_SCHEMA"),
    "update_file_purpose": ("emily_core.tools.file_tool", "_UPDATE_PURPOSE_SCHEMA"),
    "create_task_node": ("emily_core.tools.node_task_tool", "_CREATE_TASK_NODE_SCHEMA"),
    "submit_node_deliverable": ("emily_core.tools.node_task_tool", "_SUBMIT_DELIVERABLE_SCHEMA"),
    "confirm_node_deliverable": ("emily_core.tools.node_task_tool", "_CONFIRM_DELIVERABLE_SCHEMA"),
    "return_node_deliverable": ("emily_core.tools.node_task_tool", "_RETURN_DELIVERABLE_SCHEMA"),
    "query_my_nodes": ("emily_core.tools.node_task_tool", "_QUERY_MY_NODES_SCHEMA"),
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
        # V10: tools[].name 存在（M3: 跳过 auto_generate: true 的 tools 段）
        raw_tools = data.get("tools", [])
        if isinstance(raw_tools, dict) and raw_tools.get("auto_generate"):
            raw_tools = []  # 运行时派生，不检查
        for t in raw_tools or []:
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
            # M3: __DYNAMIC__ 是合法特殊值，跳过 V11 检查
            if tn == "__DYNAMIC__":
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


# ── M5: V14 + V15 新增检查 ──

def _check_meta_tools_whitelist(skills: list, issues: list[dict]) -> None:
    """V14: SOP-999 派生白名单 ⊆ REGISTERED_TOOLS。

    从 Skill YAML 检查 SOP-999 的 tools 是否引用了不存在的工具（仅检查非 auto_generate 的手写 tools）。
    auto_generate 的 tools 由 SkillRegistry 运行时派生，不在此处检查。
    """
    for skill_id, data, _yfile in skills:
        if "SOP-999" not in skill_id:
            continue
        raw_tools = data.get("tools", [])
        # auto_generate 的工具不在此处检查（运行时派生）
        if isinstance(raw_tools, dict) and raw_tools.get("auto_generate"):
            continue
        for t in raw_tools or []:
            if isinstance(t, dict) and "name" in t and t["name"] not in REGISTERED_TOOLS:
                issues.append({
                    "severity": "fatal", "check": "V14_meta_tool_not_registered",
                    "skill": skill_id,
                    "detail": f"SOP-999 tools 引用不存在的工具: {t['name']}",
                })


def _check_dark_tools(skills: list, issues: list[dict]) -> None:
    """V15: 暗工具检测——每个 REGISTERED_TOOLS 的工具必须满足以下之一，否则 warning：
    - 被某专属 SOP 的 tools[].name 引用
    - 在 tool_registry 中标 exposure_mode == 'sop_only'
    - 否则：将自动进入 SOP-999 派生白名单（warning 提示开发者确认）
    """
    # 收集所有 Skill YAML 的 tools[].name
    referenced: set[str] = set()
    for skill_id, data, _yfile in skills:
        raw_tools = data.get("tools", [])
        if isinstance(raw_tools, dict) and raw_tools.get("auto_generate"):
            continue
        for t in raw_tools or []:
            if isinstance(t, dict) and "name" in t:
                referenced.add(t["name"])

    # 从 tool_registry 取 exposure_mode 映射
    exposure_map: dict[str, str] = {}
    try:
        from emily_core.repositories.tool_registry_repo import ToolRegistryRepo
        db_tools = ToolRegistryRepo.get_all_active()
        for row in db_tools:
            exposure_map[row["api_id"]] = row.get("exposure_mode", "meta")
    except Exception as e:
        logger.warning("_check_dark_tools: tool_registry unavailable: %s", e)

    for tool in sorted(REGISTERED_TOOLS):
        if tool in referenced:
            continue  # 有专属 SOP 引用
        if exposure_map.get(tool) == "sop_only":
            continue  # 显式标为 sop_only
        # 暗工具：将自动进入 SOP-999 派生白名单
        issues.append({
            "severity": "warning", "check": "V15_dark_tool",
            "tool": tool,
            "detail": (
                f"工具 {tool} 无专属 SOP、未标 sop_only，"
                f"将自动进入 SOP-999 直调白名单。"
                f"若需专属流程，请创建 SOP-XXX；"
                f"若不应被 LLM 自主调用，标 sop_only"
            ),
        })


def _check_v14_schema_map_coverage(
    tool_schemas: dict[str, set[str] | None],
    issues: list[dict],
) -> None:
    """V14: REGISTERED_TOOLS 中的工具（write_user_memory 除外）必须出现在 TOOL_SCHEMA_MAP。

    防止新增工具时忘记将 schema 映射加到 TOOL_SCHEMA_MAP 和 TOOL_META_MAP。
    write_user_memory 的 schema 由 create_memory_tool() 动态生成，是唯一的例外。
    """
    for tool in sorted(REGISTERED_TOOLS):
        if tool in TOOL_SCHEMA_MAP or tool == "write_user_memory":
            continue
        issues.append({
            "severity": "error",
            "check": "V14_schema_map_missing",
            "tool": tool,
            "detail": (
                f"工具 '{tool}' 在 REGISTERED_TOOLS 中但不在 TOOL_SCHEMA_MAP 中。"
                f"请在 tools_consistency.py 的 TOOL_SCHEMA_MAP 中添加映射条目。"
            ),
        })
    # 反向检查：TOOL_SCHEMA_MAP 中的工具也应在 REGISTERED_TOOLS（避免僵尸条目）
    for tool in sorted(TOOL_SCHEMA_MAP):
        if tool not in REGISTERED_TOOLS:
            issues.append({
                "severity": "error",
                "check": "V14_zombie_schema_map",
                "tool": tool,
                "detail": (
                    f"工具 '{tool}' 在 TOOL_SCHEMA_MAP 中但不在 REGISTERED_TOOLS 中。"
                    f"可能是工具已被移除但映射条目残留。请清理 TOOL_SCHEMA_MAP。"
                ),
            })


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

    # V5: 空 schema 检测（升级为 error：LLM 填参场景 schema 为空是严重质量问题）
    empty_schema_tools = [
        tool for tool, params in tool_schemas.items()
        if params is not None and len(params) == 0
    ]
    empty_or_missing = [
        tool for tool in sorted(REGISTERED_TOOLS)
        if tool not in TOOL_SCHEMA_MAP
        or tool_schemas.get(tool) is None
        or len(tool_schemas.get(tool) or set()) == 0
    ]
    for tool in empty_or_missing:
        severity = "error" if tool != "write_user_memory" else "warning"
        issues.append({
            "severity": severity, "check": "V5_empty_schema",
            "tool": tool, "detail": (
                f"工具 {tool} 缺少参数 schema —— LLM 规划时将看不到该工具的参数约束。"
                f"请在源文件中定义 schema 常量并在注册时传入。"
            ),
        })

    # V14: 所有 REGISTERED_TOOLS 必须出现在 TOOL_SCHEMA_MAP 中（write_user_memory 除外）
    _check_v14_schema_map_coverage(tool_schemas, issues)

    # V10/V11/V12: Skill YAML 一致性
    skills = _load_skills(skill_dir)
    _check_skill_yaml(skills, tool_schemas, issues)

    # M5 V14: SOP-999 派生白名单 ⊆ REGISTERED_TOOLS
    _check_meta_tools_whitelist(skills, issues)

    # M5 V15: 暗工具检测
    _check_dark_tools(skills, issues)

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


def _ensure_tool_registry_seed() -> dict:
    """自动种子：每次启动从 TOOL_META_MAP 全量同步到 tool_registry 表。

    对每个 TOOL_META_MAP 条目执行 upsert，确保 category / permission_flag / exposure_mode
    等字段与代码中的 TOOL_META_MAP 保持一致。已有记录会更新，新记录会插入。

    fail-open：任何异常不阻断启动，返回 {"synced": N, "error": ...}。
    """
    try:
        from emily_core.repositories.tool_registry_repo import ToolRegistryRepo
        synced = 0
        updated = 0
        inserted = 0
        active = ToolRegistryRepo.get_all_active()
        existing_ids = {r["api_id"] for r in active}

        for tool_name, (display_name, category, perm_flag, exposure_mode) in TOOL_META_MAP.items():
            ok = ToolRegistryRepo.upsert(
                api_id=tool_name,
                display_name=display_name,
                category=category,
                permission_flag=perm_flag,
                exposure_mode=exposure_mode,
            )
            if ok:
                synced += 1
                if tool_name in existing_ids:
                    updated += 1
                else:
                    inserted += 1

        if synced:
            logger.info(
                "_ensure_tool_registry_seed: synced %d tools (%d updated, %d inserted)",
                synced, updated, inserted,
            )
        return {"synced": synced, "updated": updated, "inserted": inserted, "total": len(TOOL_META_MAP)}
    except Exception as e:
        logger.warning("_ensure_tool_registry_seed failed: %s", e)
        return {"synced": 0, "error": str(e)}


def check_quick(skill_dir: str) -> dict:
    """快速检查（供 self_check 启动集成）。启动时自动种子 tool_registry + Skill YAML 一致性。

    fail-open：任何异常返回 {"ok": False, "error": ...}，不阻断 self_check。

    Returns:
        {"skills": N, "issues": M, "fatal": K, "ok": bool}
    """
    # 自动种子（即使 check_quick 后续失败，种子也已写入）
    seed_result = _ensure_tool_registry_seed()
    try:
        tool_schemas = _load_tool_schemas()
        skills = _load_skills(skill_dir)
        issues: list[dict] = []
        _check_skill_yaml(skills, tool_schemas, issues)
        # M5 V14: SOP-999 派生白名单 ⊆ REGISTERED_TOOLS
        _check_meta_tools_whitelist(skills, issues)
        # M5 V15: 暗工具检测
        _check_dark_tools(skills, issues)
        fatal = sum(1 for i in issues if i["severity"] == "fatal")
        return {
            "skills": len(skills),
            "issues": len(issues),
            "fatal": fatal,
            "ok": fatal == 0,
            "seed": seed_result,
        }
    except Exception as e:
        logger.warning("check_quick failed: %s", e)
        return {"skills": 0, "issues": 0, "fatal": 0, "ok": False, "error": str(e), "seed": seed_result}
