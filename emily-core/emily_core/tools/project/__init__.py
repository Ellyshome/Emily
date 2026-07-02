"""项目级工具 —— 仅管理员（permission_level ≥ 5）或 ProjectAgent 可调用。"""

from ..node_tool import (
    handle_create_node, handle_query_node, handle_update_node_progress,
    handle_add_node_dependency, handle_mount_child_node,
    _CREATE_NODE_SCHEMA, _CREATE_NODE_DESCRIPTION,
    _QUERY_NODE_SCHEMA, _QUERY_NODE_DESCRIPTION,
    _UPDATE_PROGRESS_SCHEMA, _UPDATE_PROGRESS_DESCRIPTION,
    _ADD_DEPENDENCY_SCHEMA, _ADD_DEPENDENCY_DESCRIPTION,
    _MOUNT_CHILD_SCHEMA, _MOUNT_CHILD_DESCRIPTION,
)

from ..email_tool import create_send_email_tool, create_fetch_inbox_tool
from ..chat_archive_tool import create_chat_archive_tool
from ..pending_issue_tool import create_pending_issue_tool


async def handle_send_email(params: dict, email_service=None, config=None, **kw) -> dict:
    if email_service is None:
        return {"success": False, "error": "邮件服务未启用"}
    return await create_send_email_tool(email_service, config).execute(params)


async def handle_fetch_inbox(params: dict, email_service=None, config=None, **kw) -> dict:
    if email_service is None:
        return {"success": False, "error": "邮件服务未启用"}
    return await create_fetch_inbox_tool(email_service, config).execute(params)


async def handle_chat_archive(params: dict, chat_archive_service=None, **kw) -> dict:
    if chat_archive_service is None:
        return {"success": False, "error": "聊天存档服务未启用"}
    return await create_chat_archive_tool(chat_archive_service).execute(params)


async def handle_manage_pending_issues(params: dict, pending_issues_service=None,
                                       is_admin=False, **kw) -> dict:
    if pending_issues_service is None:
        return {"success": False, "error": "待解决问题服务未启用"}
    return await create_pending_issue_tool(pending_issues_service, is_admin=is_admin).execute(params)


async def handle_voice_entry(params: dict, **kw) -> dict:
    return {"success": False, "message": "voice_entry 工具待接入 VoiceEntryState 管理"}


_SEND_EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "收件人邮箱"},
        "subject": {"type": "string", "description": "邮件主题"},
        "body": {"type": "string", "description": "邮件正文"},
    },
    "required": ["to", "subject", "body"],
}
_SEND_EMAIL_DESCRIPTION = "发送邮件。需要管理员权限或用户已配置邮箱凭证。"

_FETCH_INBOX_SCHEMA = {
    "type": "object",
    "properties": {"limit": {"type": "integer", "description": "获取最近 N 封邮件，默认 10"}},
}
_FETCH_INBOX_DESCRIPTION = "获取收件箱最近邮件。需要管理员权限。"

_CHAT_ARCHIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["search", "history", "user"], "description": "search/history/user"},
        "keyword": {"type": "string", "description": "搜索关键词（action=search 时必填）"},
        "conversation_id": {"type": "string", "description": "会话 ID（action=history 时）"},
        "user_name": {"type": "string", "description": "用户名（action=user 时）"},
        "limit": {"type": "integer", "description": "返回条数，默认 20"},
    },
    "required": ["action"],
}
_CHAT_ARCHIVE_DESCRIPTION = "检索聊天记录。支持按关键词搜索、按会话/用户查询历史。"

_PENDING_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["list_pending", "list_resolved", "resolve", "resolve_all"], "description": "操作类型"},
        "issue_id": {"type": "string", "description": "问题 ID（resolve 时必填）"},
        "decision": {"type": "string", "description": "处理决策描述（resolve 时必填）"},
    },
    "required": ["action"],
}
_PENDING_ISSUE_DESCRIPTION = "管理待解决问题清单。查看/处理待解决问题，需要管理员权限。"

_VOICE_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "用户口述的自然语言文本"},
        "project_id": {"type": "string", "description": "目标项目 ID"},
    },
    "required": ["text"],
}
_VOICE_ENTRY_DESCRIPTION = "通过自然语言口述录入全景节点信息（交互式引导）。"


__all__ = [
    "handle_create_node", "handle_query_node", "handle_update_node_progress",
    "handle_add_node_dependency", "handle_mount_child_node",
    "handle_send_email", "handle_fetch_inbox", "handle_chat_archive",
    "handle_manage_pending_issues", "handle_voice_entry",
    "_CREATE_NODE_SCHEMA", "_CREATE_NODE_DESCRIPTION",
    "_QUERY_NODE_SCHEMA", "_QUERY_NODE_DESCRIPTION",
    "_UPDATE_PROGRESS_SCHEMA", "_UPDATE_PROGRESS_DESCRIPTION",
    "_ADD_DEPENDENCY_SCHEMA", "_ADD_DEPENDENCY_DESCRIPTION",
    "_MOUNT_CHILD_SCHEMA", "_MOUNT_CHILD_DESCRIPTION",
    "_SEND_EMAIL_SCHEMA", "_SEND_EMAIL_DESCRIPTION",
    "_FETCH_INBOX_SCHEMA", "_FETCH_INBOX_DESCRIPTION",
    "_CHAT_ARCHIVE_SCHEMA", "_CHAT_ARCHIVE_DESCRIPTION",
    "_PENDING_ISSUE_SCHEMA", "_PENDING_ISSUE_DESCRIPTION",
    "_VOICE_ENTRY_SCHEMA", "_VOICE_ENTRY_DESCRIPTION",
]
