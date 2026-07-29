"""会议纪要录入业务逻辑 — M14 重构为业务流工具。

M14: 核心逻辑提取为独立 handler 函数，供 BusinessFlowTool 使用。
"""

import logging

from ..adapters.standard.result import RouteResult
from ..application.meeting_app import MeetingApplication

logger = logging.getLogger("emily.tool.meeting")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler — 框架直接调用（不经过 LLM function calling）
# ══════════════════════════════════════════════════════════════════════════════

_MEETING_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {
            "type": "string",
            "description": "项目名称（如 '翠湖庭院'）。若只知道名称不知 UUID，先调 resolve_project 拿 project_id",
        },
        "project_id": {
            "type": "string",
            "format": "uuid",
            "description": "项目 UUID。若未提供但有 project_name，必须先调 resolve_project 解析",
            "fk_target": "projects.id",
            "resolvable_from": "project_name",
            "resolver": "resolve_project",
        },
        "data": {
            "type": "object",
            "description": "会议详细参数",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "会议名称或主题",
                },
                "summary": {
                    "type": "string",
                    "description": "会议摘要/纪要内容",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "参会人员名单（字符串数组）",
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

_MEETING_TOOL_DESCRIPTION = (
    "记录一场会议。\n"
    "⚠️ 调用前必须完成拟录入单流程（见系统指令第10条）。\n"
    "\n"
    "字段分级：\n"
    "  [必有] title — 会议名称或主题\n"
    "  [应有] summary — 会议摘要/纪要内容\n"
    "  [应有] attendees — 参会人员（字符串数组）\n"
    "  [应有] project_name — 关联项目名称\n"
    "\n"
    "守护核验三选一（仅在核验不通过时）：\n"
    "  force=false — 正常录入（核验不通过时会返回 needs_review 信号）\n"
    "  force=true + guardian_notes — 坚持录入（自动写入待解决清单）"
)


async def handle_record_meeting(
    params: dict,
    meeting_app: MeetingApplication,
    user_id: str = "",
    message_id: str = "",
    pending_issues=None,
    config=None,
) -> dict:
    """处理会议录入（M14 业务流工具 handler）。"""
    # Skill 路径传平铺参数，LLM 规划路径传嵌套 data；二者兼容
    data = params.get("data") or {}
    if not data:
        data = {k: v for k, v in params.items()
                if not k.startswith("_") and k not in ("data", "force", "guardian_notes",
                                                        "project_name", "project_id")}
    force = params.get("force", False)
    guardian_notes = params.get("guardian_notes", "")

    # ── 正常录入流程 ──
    route_result = RouteResult(
        intent="meeting_record",
        project_name=params.get("project_name"),
        project_id=params.get("project_id"),
        data={
            "title": data.get("title", "未命名会议"),
            "summary": data.get("summary", ""),
            "attendees": data.get("attendees") or [],
        },
    )
    result = await meeting_app.handle_meeting(route_result, user_id, message_id)

    reply_text = result.reply or ""
    if force and guardian_notes and result.success and pending_issues is not None:
        try:
            issue_id = pending_issues.add(
                raised_by=user_id or "用户",
                source=f"录入会议「{data.get('title', '未命名')}」时守护核验发现异常",
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
