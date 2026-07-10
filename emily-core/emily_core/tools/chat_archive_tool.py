"""M11: chat_archive 工具 — 让 Agent 检索聊天记录。

支持:
- action="search": 全文搜索消息
- action="history": 会话历史
- action="user": 用户历史
"""

import logging

from .definitions import ToolDefinition
from ..services.chat_archive_service import ChatArchiveService

logger = logging.getLogger(__name__)


def create_chat_archive_tool(chat_archive_service) -> ToolDefinition:
    """创建 chat_archive 工具。

    Agent 可检索聊天记录，支持:
    - action="search": 全文搜索，参数 keyword/time_range/limit
    - action="history": 会话历史，参数 conversation_id/limit
    - action="user": 用户历史，参数 user_name/limit
    """

    async def execute(args: dict) -> dict:
        action = args.get("action", "search").strip()

        if action == "search":
            keyword = args.get("keyword", "").strip()
            if not keyword:
                return {"success": False, "error": "请提供搜索关键词"}

            time_range = args.get("time_range", "all")
            limit = min(int(args.get("limit", 20)), 50)
            conversation_id = args.get("conversation_id")

            messages = chat_archive_service.search_messages(
                keyword=keyword,
                conversation_id=conversation_id or None,
                time_range=time_range,
                limit=limit,
            )
            return {
                "success": True,
                "total": len(messages),
                "messages": [
                    {
                        "id": m.id,
                        "sender_name": m.sender_name,
                        "direction": getattr(m, "direction", "user_to_agent"),
                        "content": (m.content or "")[:300],
                        "created_at": str(m.created_at) if m.created_at else "",
                    }
                    for m in messages
                ],
            }

        elif action == "history":
            conversation_id = args.get("conversation_id", "").strip()
            if not conversation_id:
                return {"success": False, "error": "请提供会话ID（conversation_id）"}

            limit = min(int(args.get("limit", 50)), 100)
            include_progress = bool(args.get("include_progress", False))

            messages = chat_archive_service.get_conversation_history(
                conversation_id=conversation_id,
                limit=limit,
                include_progress=include_progress,
            )
            return {
                "success": True,
                "total": len(messages),
                "messages": [
                    {
                        "id": m.id,
                        "sender_name": m.sender_name,
                        "direction": getattr(m, "direction", "user_to_agent"),
                        "content": (m.content or "")[:300],
                        "created_at": str(m.created_at) if m.created_at else "",
                    }
                    for m in messages
                ],
            }

        elif action == "user":
            user_name = args.get("user_name", "").strip()
            user_id = args.get("user_id", "").strip()
            if not user_name and not user_id:
                return {"success": False, "error": "请提供用户名（user_name）或用户ID（user_id）"}

            limit = min(int(args.get("limit", 20)), 50)

            # 如果没有 user_id，尝试通过名称在 messages 中搜索
            messages = chat_archive_service.search_messages(
                keyword=user_name if user_name else "",
                user_id=user_id if user_id else None,
                limit=limit,
            )
            return {
                "success": True,
                "total": len(messages),
                "messages": [
                    {
                        "id": m.id,
                        "sender_name": m.sender_name,
                        "direction": getattr(m, "direction", "user_to_agent"),
                        "content": (m.content or "")[:300],
                        "created_at": str(m.created_at) if m.created_at else "",
                    }
                    for m in messages
                ],
            }

        else:
            return {
                "success": False,
                "error": f"不支持的操作类型: {action}。支持: search / history / user",
            }

    return ToolDefinition(
        name="chat_archive",
        description=(
            "检索聊天记录档案。支持三种操作：\n"
            "- action='search': 全文搜索消息（参数: keyword, time_range, limit, conversation_id）\n"
            "- action='history': 查看会话完整对话历史（参数: conversation_id, limit, include_progress）\n"
            "- action='user': 查看用户历史消息（参数: user_name 或 user_id, limit）"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型: search（全文搜索）/ history（会话历史）/ user（用户历史）",
                    "enum": ["search", "history", "user"],
                },
                "keyword": {
                    "type": "string",
                    "description": "搜索关键词（action=search时使用）",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "会话ID（action=history时使用）",
                },
                "user_name": {
                    "type": "string",
                    "description": "用户名（action=user时使用，与user_id二选一）",
                },
                "user_id": {
                    "type": "string",
                    "description": "用户ID（action=user时使用，与user_name二选一）",
                },
                "time_range": {
                    "type": "string",
                    "description": "时间范围: today / week / month / all（默认all）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数上限（默认20，最大50）",
                },
                "include_progress": {
                    "type": "boolean",
                    "description": "是否包含前导消息（action=history时，默认false）",
                },
            },
            "required": ["action"],
        },
        execute=execute,
    )
