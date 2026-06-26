"""任务管理业务逻辑 — M14 重构为业务流工具。

M14: 核心逻辑提取为独立 handler 函数，供 BusinessFlowTool 使用。
"""

import logging

from ..adapters.standard.result import RouteResult
from ..application.task_app import TaskApplication

logger = logging.getLogger("emily.tool.task")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler — 框架直接调用（不经过 LLM function calling）
# ══════════════════════════════════════════════════════════════════════════════

_TASK_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {
            "type": "string",
            "description": "项目名称（如 '未来城'），可选",
        },
        "project_id": {
            "type": "string",
            "description": "项目 UUID，可选",
        },
        "data": {
            "type": "object",
            "description": "任务详细参数",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "任务简述（10字以内）",
                },
                "description": {
                    "type": "string",
                    "description": "任务详细描述",
                },
                "assignee": {
                    "type": "string",
                    "description": "负责人姓名（如 '张三'），可选",
                },
                "due_date": {
                    "type": "string",
                    "description": "截止日期（YYYY-MM-DD），可选",
                },
                "due_text": {
                    "type": "string",
                    "description": "截止日期自然语言描述（如 '下周五前'），可选",
                },
            },
            "required": ["title"],
        },
        "force": {
            "type": "boolean",
            "description": "是否强制录入（跳过核验，默认 false）",
        },
        "guardian_notes": {
            "type": "string",
            "description": "守护核验发现的问题描述（force=true 时填写）",
        },
    },
    "required": ["data"],
}

_TASK_TOOL_DESCRIPTION = (
    "创建一个工作任务。\n"
    "⚠️ 调用前必须完成拟录入单流程（见系统指令第10条）。\n"
    "\n"
    "字段分级：\n"
    "  [必有] title — 任务简述（10字以内）\n"
    "  [应有] assignee — 负责人姓名\n"
    "  [应有] due_date — 截止日期 YYYY-MM-DD（从'明天/下周五'等短语换算）\n"
    "  [应有] project_name — 关联项目名称\n"
    "  [可有] description — 任务详细描述\n"
    "\n"
    "守护核验三选一（仅在核验不通过时）：\n"
    "  force=false — 正常录入（核验不通过时会返回 needs_review 信号）\n"
    "  force=true + guardian_notes — 坚持录入（自动写入待解决清单）"
)


async def handle_record_task(
    params: dict,
    task_app: TaskApplication,
    user_id: str = "",
    message_id: str = "",
    pending_issues=None,
    config=None,
) -> dict:
    """处理任务录入（M14 业务流工具 handler）。"""
    data = params.get("data", {})
    force = params.get("force", False)
    guardian_notes = params.get("guardian_notes", "")

    # ── 正常录入流程 ──
    route_result = RouteResult(
        intent="task_record",
        project_name=params.get("project_name"),
        project_id=params.get("project_id"),
        data={
            "title": data.get("title", "未命名任务"),
            "description": data.get("description", ""),
            "assignee": data.get("assignee", ""),
            "due_date": data.get("due_date"),
            "due_text": data.get("due_text", ""),
        },
    )
    result = await task_app.handle_task(route_result, user_id, message_id)

    reply_text = result.reply or ""
    if force and guardian_notes and result.success and pending_issues is not None:
        try:
            issue_id = pending_issues.add(
                raised_by=user_id or "用户",
                source=f"录入任务「{data.get('title', '未命名')}」时守护核验发现异常",
                description=f"核验发现：{guardian_notes}。用户坚持录入。",
                suggestion="请核实并给出处理意见。",
                related_events=[],
            )
            reply_text = (reply_text or "") + f"\n📋 已记录待处理：{issue_id}"
            logger.info("M8a pending issue created: %s", issue_id)
        except Exception as e:
            logger.warning("M8a failed to create pending issue: %s", e)

    return {
        "success": result.success,
        "object_type": result.object_type,
        "object_id": result.object_id,
        "reply": reply_text,
        "error_code": result.error_code,
        "needs_review": False,
    }
