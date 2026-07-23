"""诊断：Skill YAML 与 BusinessFlowToolRegistry 一致性检查

增强版：处理 record_* 的 data 包装（handler 内置扁平参数兼容），
以及 manage_pending_issues 的真实 handler schema。
"""
import sys, yaml, importlib
from pathlib import Path
sys.path.insert(0, 'emily-core')

REGISTERED_TOOLS = {
    "query_data", "knowledge_search",
    "record_event", "record_task", "record_meeting", "record_file",
    "query_files", "update_file_category", "write_user_memory",
    "create_task_node", "submit_node_deliverable", "confirm_node_deliverable",
    "return_node_deliverable", "query_my_nodes",
    "create_node", "query_node", "update_node_progress", "add_node_dependency",
    "mount_child_node", "update_nodes", "activate_nodes", "discard_nodes",
    "send_email", "fetch_inbox", "chat_archive", "manage_pending_issues", "voice_entry",
}

# 工具 schema 加载（包括 data 嵌套展开）
TOOL_SCHEMA_MAP = {
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
    "voice_entry": ("emily_core.tools.project", "_VOICE_ENTRY_SCHEMA"),
}

# manage_pending_issues: 使用 pending_issue_tool.py handler 的真实 schema
# （_PENDING_ISSUE_SCHEMA 与 handler 不一致，以 handler 为准）
_PENDING_ISSUE_HANDLER_SCHEMA = {
    "action", "issue_id", "raised_by", "source", "description",
    "suggestion", "related_events", "handler", "decision", "decision_event_id",
}

tool_params_valid = {}
for tool, (mod_path, schema_var) in TOOL_SCHEMA_MAP.items():
    try:
        m = importlib.import_module(mod_path)
        schema = getattr(m, schema_var, None)
        if isinstance(schema, dict) and "properties" in schema:
            top_level = set(schema["properties"].keys())
            # 展开 data 嵌套：record_* 工具 handler 兼容扁平参数传递
            data_prop = schema["properties"].get("data", {})
            if isinstance(data_prop, dict) and "properties" in data_prop:
                nested = set(data_prop["properties"].keys())
                tool_params_valid[tool] = top_level | nested
            else:
                tool_params_valid[tool] = top_level
    except Exception:
        tool_params_valid[tool] = None

# manage_pending_issues 使用 handler schema
tool_params_valid["manage_pending_issues"] = _PENDING_ISSUE_HANDLER_SCHEMA

skills_dir = Path("emily-data/skills")
problems = []
for yfile in sorted(skills_dir.glob("*.skill.yaml")):
    data = yaml.safe_load(yfile.read_text(encoding="utf-8"))
    skill_id = data.get("skill_id", yfile.stem)
    for t in data.get("tools", []) or []:
        if isinstance(t, dict) and "name" in t and t["name"] not in REGISTERED_TOOLS:
            problems.append(f"{skill_id} tools 引用不存在工具: {t['name']}")
    for s in data.get("steps", []) or []:
        if not isinstance(s, dict): continue
        tn = s.get("tool_name")
        if not tn: continue
        if tn not in REGISTERED_TOOLS:
            problems.append(f"{skill_id} {s.get('id')} steps 引用不存在工具: {tn}")
            continue
        expected = tool_params_valid.get(tn)
        if expected is None: continue
        actual = {p["name"] for p in (s.get("tool_params") or []) if isinstance(p, dict) and "name" in p}
        extra = actual - expected
        if extra:
            problems.append(f"{skill_id} {s.get('id')} 调 {tn}() 传了 schema 外参数: {sorted(extra)}，schema 实际允许: {sorted(expected)}")

if problems:
    print(f"❌ 发现 {len(problems)} 处不一致:")
    for p in problems: print(f"  - {p}")
    sys.exit(1)
else:
    print("✅ 所有 Skill YAML 与 BusinessFlowToolRegistry 一致")
    sys.exit(0)
