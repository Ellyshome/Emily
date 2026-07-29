"""事件录入业务逻辑 — M14 重构为业务流工具。

M14: 核心逻辑提取为独立 handler 函数，供 BusinessFlowTool 使用。
     ToolRegistry 已移除，工具不再包装为 LLM function calling 格式。
"""

import json
import logging

from ..adapters.standard.result import RouteResult
from ..application.event_app import EventApplication

logger = logging.getLogger("emily.tool.event")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler — 框架直接调用（不经过 LLM function calling）
# ══════════════════════════════════════════════════════════════════════════════

_EVENT_TOOL_SCHEMA = {
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
            "description": "事件详细参数",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "事件简述（10字以内）",
                },
                "event_type": {
                    "type": "string",
                    "enum": [
                        "construction_progress",
                        "inspection",
                        "material_arrival",
                        "quality_issue",
                        "safety_issue",
                        "weather",
                        "design_change",
                        "decision",
                        "general",
                    ],
                    "description": "事件类型（decision=决策事件，用于处理待解决问题）",
                },
                "event_date": {
                    "type": "string",
                    "description": "事件日期（YYYY-MM-DD 格式）",
                },
                "description": {
                    "type": "string",
                    "description": "事件完整描述",
                },
            },
            "required": ["title", "event_type"],
        },
        "force": {
            "type": "boolean",
            "description": "是否强制录入（跳过核验，默认 false）。核验不通过且用户坚持录入时设为 true。",
        },
        "guardian_notes": {
            "type": "string",
            "description": "守护核验发现的问题描述（force=true 时填写，将标记在事件备注中）",
        },
        "related_event_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "关联事件编号列表（如 ['EVT-20260612-0001']）",
        },
    },
    "required": ["data"],
}

_EVENT_TOOL_DESCRIPTION = (
    "记录一个工程项目现场事件。\n"
    "⚠️ 调用前必须完成拟录入单流程（见系统指令第10条）。\n"
    "\n"
    "字段分级：\n"
    "  [必有] title — 事件简述（10字以内）\n"
    "  [应有] event_type — construction_progress/inspection/material_arrival/"
    "quality_issue/safety_issue/weather/design_change/general\n"
    "  [应有] event_date — 发生日期 YYYY-MM-DD（默认今天）\n"
    "  [应有] project_name — 关联项目名称\n"
    "  [可有] description — 事件完整描述\n"
    "  [可有] related_event_ids — 关联事件编号列表\n"
    "\n"
    "守护核验三选一（仅在核验不通过时）：\n"
    "  force=false — 正常录入（核验不通过时会返回 needs_review 信号）\n"
    "  force=true + guardian_notes — 坚持录入（自动标记异常备注 + 写入待解决清单）"
)


async def handle_record_event(
    params: dict,
    event_app: EventApplication,
    user_id: str = "",
    message_id: str = "",
    pending_issues=None,
    config=None,
) -> dict:
    """处理事件录入（M14 业务流工具 handler）。

    由 BusinessFlowAgent 在 LLM 结构化输出后直接调用，
    不再通过 LLM function calling 触发。

    Args:
        params: LLM 提取的结构化参数
        event_app: EventApplication 实例
        user_id: 当前用户 ID
        message_id: 当前消息 ID
        pending_issues: PendingIssuesService 实例（可选）
        config: 全局配置（可选）

    Returns:
        dict: {success, object_type, object_id, reply, pending_confirmation, error_code, needs_review}
    """
    data = params.get("data", {})
    force = params.get("force", False)
    guardian_notes = params.get("guardian_notes", "")
    related_event_ids = params.get("related_event_ids", [])

    # BUG-003 修复：兼容 LLM 扁平输出——如果 data 为空但顶层有 title/event_type，
    # 说明 LLM 输出了扁平结构而非嵌套的 {"data": {...}} 结构，自动包装
    if not data and ("title" in params or "event_type" in params):
        data = {
            "title": params.get("title", "未命名事件"),
            "event_type": params.get("event_type", "general"),
            "event_date": params.get("event_date"),
            "description": params.get("description", ""),
        }
        logger.debug("event_tool: flat params detected, auto-wrapped into data dict")

    # ── 正常录入流程 ──
    route_result = RouteResult(
        intent="event_record",
        project_name=params.get("project_name"),
        project_id=params.get("project_id"),
        data={
            "title": data.get("title", "未命名事件"),
            "event_type": data.get("event_type", "general"),
            "event_date": data.get("event_date"),
            "description": data.get("description", ""),
            "related_event_ids": related_event_ids,
            "_conversation_id": params.get("_conversation_id", ""),  # BUG-005: 从 tool_params 透传
        },
    )
    result = await event_app.handle_event(route_result, user_id, message_id)

    # M8a: force 模式 → 追加 [守护标记] 到 remarks + 写待解决问题清单
    if force and guardian_notes and result.success:
        try:
            from ..infrastructure.database.session import get_session
            from ..infrastructure.database.models import Event

            with get_session() as session:
                event = session.query(Event).filter(Event.id == result.object_id).first()
                if event:
                    original_remarks = event.remarks or ""
                    event.remarks = (
                        original_remarks
                        + ("\n" if original_remarks else "")
                        + f"[守护标记] 核验发现：{guardian_notes}"
                    )
                    session.commit()
                    logger.info("M8a guardian mark added to event %s", result.object_id)
        except Exception as e:
            logger.warning("M8a failed to add guardian mark to event: %s", e)

    reply_text = result.reply or ""
    if force and guardian_notes and result.success and pending_issues is not None:
        try:
            issue_id = pending_issues.add(
                raised_by=user_id or "用户",
                source=f"录入事件「{data.get('title', '未命名')}」时守护核验发现异常",
                description=f"核验发现：{guardian_notes}。用户坚持录入。",
                suggestion="请核实并给出处理意见。",
                related_events=[result.object_id] if result.object_id else [],
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
        "pending_confirmation": result.pending_confirmation,
        "error_code": result.error_code,
        "needs_review": False,
    }
