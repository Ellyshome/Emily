"""文件工具 — 含业务流工具 + M13 文件传输工具。

M13: send_file（主动发送文件）和 read_local_file（按需读取本地文件）。
M14: record_file 核心逻辑提取为独立 handler，供 BusinessFlowTool 使用。
"""

import logging
import os
from pathlib import Path

from ..adapters.standard.result import RouteResult
from ..agent.tool_registry import ToolDefinition
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


# ════════════════════════════════════════════════════════════════════════════════
# M13: send_file — Agent 主动发送文件给用户
# ════════════════════════════════════════════════════════════════════════════════

def create_send_file_tool(send_file_callback=None, file_storage_service=None) -> ToolDefinition:
    """将文件发送能力包装为 send_file 工具。

    Agent 调用此工具将本地文件直接发送到 IM 会话。
    使用 send_file_callback（类似 progress_sender 模式）立即发送。

    Args:
        send_file_callback: async fn(file_path: str, file_name: str, caption: str) -> None
        file_storage_service: FileStorageService 实例（用于解析 file_no → 本地路径）
    """

    async def execute(args: dict) -> dict:
        file_path = args.get("file_path", "")
        file_name = args.get("file_name", "")
        caption = args.get("caption", "")
        file_no = args.get("file_no", "")  # 可通过 file_no 查找本地路径

        # 通过 file_no 解析路径
        if file_no and not file_path and file_storage_service is not None:
            resolved = file_storage_service.get_local_path(file_no)
            if resolved:
                file_path = resolved
                if not file_name:
                    file_name = os.path.basename(resolved)

        # 验证路径
        if not file_path:
            return {"success": False, "error": "请提供 file_path 或 file_no"}

        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "error": f"文件不存在: {file_path}",
                "suggestion": "请检查文件路径是否正确。如果是之前会话中收到的文件，可使用 file_no 查找。",
            }

        if not file_name:
            file_name = path.name

        # 通过回调发送
        if send_file_callback is not None:
            try:
                await send_file_callback(
                    file_path=str(path.absolute()),
                    file_name=file_name,
                    caption=caption,
                )
                return {
                    "success": True,
                    "file_name": file_name,
                    "file_path": str(path.absolute()),
                    "file_size": path.stat().st_size,
                    "message": f"文件「{file_name}」已发送",
                }
            except Exception as e:
                logger.warning("send_file callback failed: %s", e)
                return {"success": False, "error": f"发送失败: {e}"}
        else:
            return {
                "success": False,
                "error": "文件发送通道未就绪（send_file_callback 未注入）",
            }

    return ToolDefinition(
        name="send_file",
        description=(
            "主动发送一个本地文件给用户。\n"
            "当用户索要文件（如施工图、规范文档等）时调用此工具。\n"
            "⚠️ 只能发送已在服务端的本地文件，不能发送网络文件。\n"
            "\n"
            "参数说明：\n"
            "  [必有] file_path — 本地文件的绝对路径\n"
            "  [可选] file_name — 展示给用户的文件名（为空则用路径中的文件名）\n"
            "  [可选] caption — 附带文本说明\n"
            "  [可选] file_no — 文件编号（FIL-YYYYMMDD-NNNN），可通过此编号查找之前下载的文件"
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "本地文件的绝对路径",
                },
                "file_name": {
                    "type": "string",
                    "description": "展示给用户的文件名",
                },
                "caption": {
                    "type": "string",
                    "description": "附带文本说明",
                },
                "file_no": {
                    "type": "string",
                    "description": "文件编号 FIL-YYYYMMDD-NNNN，可通过此编号查找之前下载并存储的文件",
                },
            },
            "required": [],
        },
        execute=execute,
    )


# ════════════════════════════════════════════════════════════════════════════════
# M13: read_local_file — Agent 按需读取本地文件内容
# ════════════════════════════════════════════════════════════════════════════════

def create_read_local_file_tool(file_storage_service=None) -> ToolDefinition:
    """将本地文件读取能力包装为 read_local_file 工具。

    Agent 仅在需要时调用此工具读取文件内容，不会默认加载。
    支持通过 file_no 或 file_path 定位文件。

    Args:
        file_storage_service: FileStorageService 实例（用于解析 file_no → 本地路径）
    """

    async def execute(args: dict) -> dict:
        file_path = args.get("file_path", "")
        file_no = args.get("file_no", "")
        max_chars = args.get("max_chars", 8000)

        # 通过 file_no 解析路径
        if file_no and not file_path and file_storage_service is not None:
            resolved = file_storage_service.get_local_path(file_no)
            if resolved:
                file_path = resolved

        if not file_path:
            return {"success": False, "error": "请提供 file_path 或 file_no"}

        path = Path(file_path)
        if not path.exists():
            # 尝试在 storage_root 下查找
            if file_storage_service is not None:
                alt = file_storage_service._storage_root / file_path.lstrip("/")
                if alt.exists():
                    path = alt
            if not path.exists():
                return {"success": False, "error": f"文件不存在: {file_path}"}

        try:
            # 按扩展名决定读取方式
            ext = path.suffix.lower()
            if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".dwg", ".dxf"}:
                return {
                    "success": True,
                    "file_name": path.name,
                    "file_size": path.stat().st_size,
                    "file_type": ext.lstrip("."),
                    "content": f"[二进制文件，{ext} 格式，{path.stat().st_size} bytes，无法以文本方式预览]",
                    "suggestion": "此文件类型无法以文本方式读取。如需发送给用户，请使用 send_file 工具。",
                }

            content = path.read_text(encoding="utf-8", errors="replace")
            truncated = len(content) > max_chars
            if truncated:
                content = content[:max_chars] + f"\n\n... (截断，共 {len(path.read_text(encoding='utf-8', errors='replace'))} 字符)"

            return {
                "success": True,
                "file_name": path.name,
                "file_size": path.stat().st_size,
                "file_type": ext.lstrip(".") if ext else "text",
                "content": content,
                "truncated": truncated,
            }
        except UnicodeDecodeError:
            return {
                "success": True,
                "file_name": path.name,
                "file_size": path.stat().st_size,
                "content": f"[二进制文件，{path.stat().st_size} bytes]",
            }
        except Exception as e:
            return {"success": False, "error": f"读取失败: {e}"}

    return ToolDefinition(
        name="read_local_file",
        description=(
            "读取本地文件的内容（仅在你需要了解文件内容时调用）。\n"
            "支持文本文件（.md/.txt/.py/.json/.log 等）的全文读取。\n"
            "二进制文件（图片、图纸等）只能返回元信息。\n"
            "\n"
            "参数说明：\n"
            "  [可选] file_path — 本地文件的绝对路径\n"
            "  [可选] file_no — 文件编号（FIL-YYYYMMDD-NNNN），可通过此编号查找之前下载的文件\n"
            "  [可选] max_chars — 最大返回字符数（默认 8000）"
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "本地文件的绝对路径",
                },
                "file_no": {
                    "type": "string",
                    "description": "文件编号 FIL-YYYYMMDD-NNNN",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最大返回字符数（默认 8000）",
                },
            },
            "required": [],
        },
        execute=execute,
        require_admin=False,
    )
