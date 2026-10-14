"""项目级工具 —— 仅管理员（level ≥ 5）或 ProjectAgent 可调用。"""

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

# chat_archive 的参数面与描述在 chat_archive_tool.py 中**唯一定义**（历史遗留的
# "简版/完整版"两份定义已收敛）。此处仅再导出，保持既有导入路径可用。
from ..chat_archive_tool import (  # noqa: E402  （聚合面再导出，非第二份定义）
    _CHAT_ARCHIVE_SCHEMA,
    _CHAT_ARCHIVE_DESCRIPTION,
)

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


__all__ = [
    "handle_create_node", "handle_query_node", "handle_update_node_progress",
    "handle_add_node_dependency", "handle_mount_child_node",
    "handle_send_email", "handle_fetch_inbox", "handle_chat_archive",
    "handle_manage_pending_issues",
    "_CREATE_NODE_SCHEMA", "_CREATE_NODE_DESCRIPTION",
    "_QUERY_NODE_SCHEMA", "_QUERY_NODE_DESCRIPTION",
    "_UPDATE_PROGRESS_SCHEMA", "_UPDATE_PROGRESS_DESCRIPTION",
    "_ADD_DEPENDENCY_SCHEMA", "_ADD_DEPENDENCY_DESCRIPTION",
    "_MOUNT_CHILD_SCHEMA", "_MOUNT_CHILD_DESCRIPTION",
    "_SEND_EMAIL_SCHEMA", "_SEND_EMAIL_DESCRIPTION",
    "_FETCH_INBOX_SCHEMA", "_FETCH_INBOX_DESCRIPTION",
    "_CHAT_ARCHIVE_SCHEMA", "_CHAT_ARCHIVE_DESCRIPTION",
    "_PENDING_ISSUE_SCHEMA", "_PENDING_ISSUE_DESCRIPTION",
]
