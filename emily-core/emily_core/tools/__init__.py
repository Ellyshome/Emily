"""工具注册中心 —— 统一入口，按 category 组织所有基座工具。

目录结构（按工具的可见范围和使用权限分层）：
  base/         基座能力 — 对所有 SOP 开放，无需权限审核（knowledge_search / query_data）
  business/     业务工具 — 受 SOP 约束，按 SOP §3.2 白名单调用（record_event / task / meeting / file / plan_task / memory）
  project/      项目级工具 — 管理员或 ProjectAgent 专用（node / voice_entry / email / pending_issue / chat_archive）
  registry.py   统一注册入口 register_all(core) → 一站式填充 BusinessFlowToolRegistry

权限模型：
  - base tools: 所有 SOP 可调用（需在 SOP §3.2 声明）
  - business tools: SOP 白名单约束（SOPIntentRegistry + AuthHook 前置拦截）
  - project tools: 仅管理员（level ≥ 5）或 ProjectAgent 可调用

M14 架构：工具通过 BusinessFlowToolRegistry 注册，框架在 LLM 结构化输出后直接调用 handler。
不再经过旧 ToolRegistry（Agent function-calling 模式）。
"""

# ── 基座能力 ──
from .base import handle_knowledge_search
from .base import _KNOWLEDGE_SEARCH_SCHEMA, _KNOWLEDGE_SEARCH_DESCRIPTION

# 业务工具 ──
from .business import (
    handle_record_event, handle_record_task, handle_record_meeting, handle_record_file,
    handle_record_plan_task, handle_submit_plan_task,
    handle_review_plan_task, handle_query_plan_tasks,
    handle_write_user_memory,
)
from .business import (
    _RECORD_PLAN_TASK_SCHEMA, _SUBMIT_PLAN_TASK_SCHEMA,
    _REVIEW_PLAN_TASK_SCHEMA, _QUERY_PLAN_TASKS_SCHEMA,
)

# ── 项目级工具 ──
from .project import (
    handle_create_node, handle_query_node, handle_update_node_progress,
    handle_add_node_dependency, handle_mount_child_node,
    handle_send_email, handle_fetch_inbox,
    handle_chat_archive,
    handle_manage_pending_issues, handle_voice_entry,
)
from .project import (
    _CREATE_NODE_SCHEMA, _CREATE_NODE_DESCRIPTION,
    _QUERY_NODE_SCHEMA, _QUERY_NODE_DESCRIPTION,
    _UPDATE_PROGRESS_SCHEMA, _UPDATE_PROGRESS_DESCRIPTION,
    _ADD_DEPENDENCY_SCHEMA, _ADD_DEPENDENCY_DESCRIPTION,
    _MOUNT_CHILD_SCHEMA, _MOUNT_CHILD_DESCRIPTION,
    _SEND_EMAIL_SCHEMA, _SEND_EMAIL_DESCRIPTION,
    _FETCH_INBOX_SCHEMA, _FETCH_INBOX_DESCRIPTION,
    _CHAT_ARCHIVE_SCHEMA, _CHAT_ARCHIVE_DESCRIPTION,
    _PENDING_ISSUE_SCHEMA, _PENDING_ISSUE_DESCRIPTION,
    _VOICE_ENTRY_SCHEMA, _VOICE_ENTRY_DESCRIPTION,
)

# 统一注册入口
from .registry import register_all

__all__ = [
    "register_all",
    "handle_knowledge_search",
    "handle_query_data",
    "handle_record_event",
    "handle_record_task",
    "handle_record_meeting",
    "handle_record_file",
    "handle_record_plan_task",
    "handle_submit_plan_task",
    "handle_review_plan_task",
    "handle_query_plan_tasks",
    "handle_write_user_memory",
    "handle_create_node",
    "handle_query_node",
    "handle_update_node_progress",
    "handle_add_node_dependency",
    "handle_mount_child_node",
    "handle_send_email",
    "handle_fetch_inbox",
    "handle_chat_archive",
    "handle_manage_pending_issues",
    "handle_voice_entry",
]
