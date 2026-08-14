"""数据查询业务逻辑 — M14 重构为业务流工具。

M14: 核心逻辑提取为独立 handler，供 BusinessFlowTool 使用。
     ToolRegistry 已移除，不再需要 LLM ToolDefinition 包装器。
"""

import logging

from ..adapters.standard.command import QueryCommand
from ..services.query_service import QueryService

logger = logging.getLogger("emily.tool.query")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler — query_data（框架直接调用）
# ══════════════════════════════════════════════════════════════════════════════

_QUERY_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query_type": {
            "type": "string",
            "enum": [
                "event", "task", "meeting", "file",
                "message", "conversation", "user",
                "project", "summary", "my_nodes",
            ],
            "description": "查询类型",
        },
        "project_id": {
            "type": "string",
            "description": "项目 UUID，可选",
        },
        "project_name": {
            "type": "string",
            "description": "项目名称，可选",
        },
        "time_range": {
            "type": "string",
            "enum": ["today", "this_week", "this_month", "all"],
            "description": "时间范围，默认 all",
        },
        "status_filter": {
            "type": "string",
            "description": "按状态筛选（event: pending/confirmed/cancelled, task: todo/doing/done）",
        },
        "assignee": {
            "type": "string",
            "description": "按负责人筛选（仅 task 类型）",
        },
        "sender_name": {
            "type": "string",
            "description": "按发送者筛选（仅 message 类型）",
        },
        "keyword": {
            "type": "string",
            "description": "关键词搜索（仅 message 类型）",
        },
        "intent": {
            "type": "string",
            "description": "按意图筛选（仅 message 类型）",
        },
        "file_type": {
            "type": "string",
            "description": "按文件类型筛选（仅 file 类型）",
        },
        "conversation_id": {
            "type": "string",
            "description": "按会话 ID 筛选（仅 message 类型）",
        },
        "limit": {
            "type": "integer",
            "description": "返回结果上限，默认 50",
        },
    },
    "required": ["query_type"],
}

_QUERY_TOOL_DESCRIPTION = (
    "查询项目管理系统中的数据。支持 10 种查询类型：\n"
    "- event: 事件查询（支持按项目、时间范围、状态筛选）\n"
    "- task: 任务查询（支持按项目、时间、状态、负责人筛选）\n"
    "- meeting: 会议查询\n"
    "- file: 文件查询（支持按文件类型筛选）\n"
    "- message: 通讯记录查询（支持按发送者、关键词、意图筛选）\n"
    "- conversation: 活跃会话排行\n"
    "- user: 用户列表\n"
    "- project: 项目列表\n"
    "- summary: 全局统计概览（事件数/任务数/消息数等）\n"
    "- my_nodes: 查询当前用户负责或参与的全景节点\n"
    "时间范围可选 today / this_week / this_month / all（默认 all）。"
)


_QUERY_TYPE_TO_TABLE = {
    "event": "events", "task": "tasks", "meeting": "meetings",
    "file": "files", "message": "messages", "conversation": "conversations",
    "user": "users", "project": "projects", "journal": "events",
    "summary": "summary",
}

# db_perms 管控的核心表 key（单数，与 permission_service._derive_db_perms 对齐）
# 未列入的 query_type（summary/file/message/conversation/user）不受表级权限管控，
# 由 query_service 内部按 project_ids / 行级安全过滤
_DB_PERM_MANAGED_KEYS = frozenset({"project", "event", "task", "meeting", "financial"})


async def handle_query_data(
    params: dict,
    query_service: QueryService,
) -> dict:
    """处理数据查询（M14 业务流工具 handler + session_scope 过滤）。"""
    try:
        # ── session_scope 数据边界 ──
        session_scope = params.pop("_session_scope", None) or {}

        # 1. db_perms 检查
        # db_perms 的 key 为单数（与 permission_service._derive_db_perms 对齐）。
        # 仅对核心表做表级权限校验；journal 查 events 表，映射到 event；
        # 其他 query_type（summary/file/message 等）不受表级权限管控，
        # 由 query_service 内部按 project_ids / 行级安全过滤
        query_type = params.get("query_type", "event")
        perm_key = "event" if query_type == "journal" else query_type
        db_perms = session_scope.get("db_perms", {})
        if db_perms and perm_key in _DB_PERM_MANAGED_KEYS and perm_key not in db_perms:
            logger.info("query_data: db_perms denied perm_key=%s for query_type=%s", perm_key, query_type)
            return {"success": False, "reply": f"无权限查询{query_type}", "total": 0}

        # 2. project_ids 自动注入
        project_ids = session_scope.get("project_ids", [])
        if project_ids and not params.get("project_id"):
            params["project_ids"] = project_ids

        # ── 原有逻辑 ──
        cmd = QueryCommand(
            query_type=params.get("query_type", "event"),
            project_id=params.get("project_id"),
            project_ids=params.get("project_ids"),
            project_name=params.get("project_name"),
            time_range=params.get("time_range", "all"),
            status_filter=params.get("status_filter"),
            assignee=params.get("assignee"),
            sender_name=params.get("sender_name") or params.get("_user_id", ""),
            keyword=params.get("keyword"),
            intent=params.get("intent"),
            file_type=params.get("file_type"),
            conversation_id=params.get("conversation_id"),
            limit=params.get("limit", 50),
        )

        logger.info(
            "query_data tool: type=%s, project=%s, time=%s",
            cmd.query_type, cmd.project_id, cmd.time_range,
        )

        results = query_service.execute(cmd)
        reply = query_service.format_reply(cmd.query_type, results)

        # 溯源到人：附带结构化溯源数据，默认不渲染进 reply 文本，
        # LLM 在用户追问「谁记录的/谁确认的/谁负责的」时据此回答。
        trace = query_service.build_trace(cmd.query_type, results.get("items") or [])

        return {
            "success": True,
            "query_type": cmd.query_type,
            "total": results.get("total", 0),
            "reply": reply,
            "trace": trace,
        }
    except Exception as e:
        logger.error("query_data tool failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
