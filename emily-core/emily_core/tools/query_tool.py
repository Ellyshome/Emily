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
                "project", "summary",
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
    "查询项目管理系统中的数据。支持 9 种查询类型：\n"
    "- event: 事件查询（支持按项目、时间范围、状态筛选）\n"
    "- task: 任务查询（支持按项目、时间、状态、负责人筛选）\n"
    "- meeting: 会议查询\n"
    "- file: 文件查询（支持按文件类型筛选）\n"
    "- message: 通讯记录查询（支持按发送者、关键词、意图筛选）\n"
    "- conversation: 活跃会话排行\n"
    "- user: 用户列表\n"
    "- project: 项目列表\n"
    "- summary: 全局统计概览（事件数/任务数/消息数等）\n"
    "时间范围可选 today / this_week / this_month / all（默认 all）。"
)


async def handle_query_data(
    params: dict,
    query_service: QueryService,
) -> dict:
    """处理数据查询（M14 业务流工具 handler）。"""
    try:
        cmd = QueryCommand(
            query_type=params.get("query_type", "event"),
            project_id=params.get("project_id"),
            project_name=params.get("project_name"),
            time_range=params.get("time_range", "all"),
            status_filter=params.get("status_filter"),
            assignee=params.get("assignee"),
            sender_name=params.get("sender_name"),
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

        return {
            "success": True,
            "query_type": cmd.query_type,
            "total": results.get("total", 0),
            "reply": reply,
        }
    except Exception as e:
        logger.error("query_data tool failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
