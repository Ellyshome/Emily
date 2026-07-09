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
                "file_category": {
                    "type": "string",
                    "description": "文件业务分类：PROJECT_LICENSE(项目证照)/CONTRACT(承包合同)/WORK_RECORD(工作记录)/PHASE_DELIVERABLE(阶段成果)/PROCESS_DOC(过程文件)/MANAGEMENT_SPEC(管理规程)/OTHER(其他文件)",
                    "default": "OTHER",
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
    """处理文件归档（M14 业务流工具 handler）。

    M13 (TC-A01): 从 params 提取附件 URL 信息并传递给 FileApplication。
    """
    data = params.get("data", {})
    force = params.get("force", False)
    guardian_notes = params.get("guardian_notes", "")

    # M13 (TC-A01): 提取附件 URL 信息
    attachment_url = params.get("_attachment_url", "")
    attachment_type = params.get("_attachment_type", 0)
    source_filename = data.get("filename", "")

    # ── 正常录入流程 ──
    route_result = RouteResult(
        intent="file_record",
        project_name=params.get("project_name"),
        project_id=params.get("project_id"),
        data={
            "filename": source_filename or "未命名文件",
            "file_type": data.get("file_type", ""),
            "file_category": data.get("file_category", "OTHER"),
        },
    )
    result = await file_app.handle_file(
        route_result, user_id, message_id,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
        source_filename=source_filename,
    )

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


# ══════════════════════════════════════════════════════════════════════════════
# 文件查询工具 — query_files
# ══════════════════════════════════════════════════════════════════════════════

_QUERY_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "file_category": {
            "type": "string",
            "description": "按分类过滤：PROJECT_LICENSE/CONTRACT/WORK_RECORD/PHASE_DELIVERABLE/PROCESS_DOC/MANAGEMENT_SPEC/OTHER",
        },
        "keyword": {
            "type": "string",
            "description": "按文件名关键词搜索（模糊匹配）",
        },
        "project_id": {
            "type": "string",
            "description": "项目 UUID（可选，默认取当前用户项目）",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量上限，默认 10",
            "default": 10,
        },
    },
}

_QUERY_FILES_DESCRIPTION = (
    "按分类或关键词查询项目文件列表。\n"
    "\n"
    "字段分级：\n"
    "  [应有] file_category — 按业务分类过滤（如 CONTRACT 查承包合同类）\n"
    "  [应有] keyword — 按文件名关键词模糊搜索\n"
    "  [可选] project_id — 指定项目范围\n"
    "  [可选] limit — 返回数量上限"
)


async def handle_query_files(
    params: dict,
    file_app: FileApplication,
    user_id: str = "",
    message_id: str = "",
    project_ids: list[str] | None = None,
    **kwargs,
) -> dict:
    """处理文件分类查询。"""
    file_category = params.get("file_category")
    keyword = params.get("keyword", "")
    project_id = params.get("project_id")
    limit = params.get("limit", 10)

    result = await file_app.handle_list_by_category(
        file_category=file_category,
        project_id=project_id,
        project_ids=project_ids,
        keyword=keyword,
        limit=limit,
    )

    return {
        "success": result.success,
        "reply": result.reply,
        "data": getattr(result, 'data', {}),
        "error_code": result.error_code,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 文件分类修改工具 — update_file_category
# ══════════════════════════════════════════════════════════════════════════════

_UPDATE_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001）",
        },
        "file_category": {
            "type": "string",
            "description": "目标分类：PROJECT_LICENSE/CONTRACT/WORK_RECORD/PHASE_DELIVERABLE/PROCESS_DOC/MANAGEMENT_SPEC/OTHER",
        },
    },
    "required": ["file_no", "file_category"],
}

_UPDATE_CATEGORY_DESCRIPTION = (
    "修改文件的分类归属。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号\n"
    "  file_category — 目标分类枚举值"
)


async def handle_update_file_category(
    params: dict,
    file_app: FileApplication,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """处理文件分类修改。"""
    file_no = params.get("file_no", "")
    file_category = params.get("file_category", "OTHER")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号", "error_code": "missing_file_no"}

    result = await file_app.handle_update_category(
        file_no=file_no,
        file_category=file_category,
        user_id=user_id,
    )

    return {
        "success": result.success,
        "reply": result.reply,
        "data": getattr(result, 'data', {}),
        "error_code": result.error_code,
    }
