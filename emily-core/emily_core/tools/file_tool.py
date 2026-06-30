"""文件工具 — 含业务流工具 + M13 文件传输工具。

M13: send_file（主动发送文件）和 read_local_file（按需读取本地文件）。
M14: record_file 核心逻辑提取为独立 handler，供 BusinessFlowTool 使用。
     ToolRegistry 已移除，不再包装为 LLM ToolDefinition。
"""

import logging
import os
from pathlib import Path

from ..adapters.standard.result import RouteResult
from ..application.file_app import FileApplication

logger = logging.getLogger("emily.tool.file")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler — record_file（框架直接调用）
# ══════════════════════════════════════════════════════════════════════════════

_FILE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {
            "type": "string",
            "description": "项目名称，可选",
        },
        "project_id": {
            "type": "string",
            "description": "项目 UUID，可选",
        },
        "data": {
            "type": "object",
            "description": "文件详细参数",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "文件名（如 '施工图纸v3.pdf'）",
                },
                "file_type": {
                    "type": "string",
                    "description": "文件类型（如 pdf、docx、图纸）",
                },
            },
            "required": ["filename"],
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

_FILE_TOOL_DESCRIPTION = (
    "记录一个文件信息。\n"
    "⚠️ 调用前必须完成拟录入单流程（见系统指令第10条）。\n"
    "\n"
    "字段分级：\n"
    "  [必有] filename — 文件名\n"
    "  [应有] project_name — 关联项目名称\n"
    "  [应有] file_type — 文件类型（从后缀推断，如 pdf/docx/图纸）\n"
    "\n"
    "守护核验三选一（仅在核验不通过时）：\n"
    "  force=false — 正常录入（核验不通过时会返回 needs_review 信号）\n"
    "  force=true + guardian_notes — 坚持录入（自动写入待解决清单）"
)


async def handle_record_file(
    params: dict,
    file_app: FileApplication,
    user_id: str = "",
    message_id: str = "",
    pending_issues=None,
    config=None,
) -> dict:
    """处理文件归档（M14 业务流工具 handler）。"""
    data = params.get("data", {})
    force = params.get("force", False)
    guardian_notes = params.get("guardian_notes", "")

    # ── 正常录入流程 ──
    route_result = RouteResult(
        intent="file_record",
        project_name=params.get("project_name"),
        project_id=params.get("project_id"),
        data={
            "filename": data.get("filename", "未命名文件"),
            "file_type": data.get("file_type", ""),
        },
    )
    result = await file_app.handle_file(route_result, user_id, message_id)

    reply_text = result.reply or ""
    if force and guardian_notes and result.success and pending_issues is not None:
        try:
            issue_id = pending_issues.add(
                raised_by=user_id or "用户",
                source=f"录入文件「{data.get('filename', '未命名')}」时守护核验发现异常",
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
