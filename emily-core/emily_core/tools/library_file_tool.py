"""library_file_tool —— 资料库内 Office/图片文件的现读、现发。

与 `read_knowledge_library` 的分工：
  · `read_knowledge_library`：列目录 / 读索引 / 读文本文件（既有 SOP-013 主通道）
  · `read_library_file`：读取资料库内的 Office 文件（docx/xlsx/pptx），交给 officeCLI
  · `send_library_file`：把资料库内的文件/图片发给当前会话

不做批量 RAG，不做 PDF；路径仍受 KnowledgeLibraryFs 的根目录越界保护。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("emily.tool.library_file")

_READ_MIN_LEVEL = 3
_OFFICE_EXT = {".docx", ".xlsx", ".pptx"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
_TEXT_EXT = {".md", ".markdown", ".txt", ".text", ".json", ".csv", ".tsv", ".log",
             ".yml", ".yaml", ".ini", ".cfg", ".conf", ".sql", ".html", ".htm", ".xml"}

_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "资料库内的相对路径，例如 '演示项目/02_材料与做法/橡胶草坪.xlsx'",
        },
        "max_lines": {
            "type": "integer",
            "description": "可选，Office 文本模式最大行数，默认 300",
        },
    },
    "required": ["path"],
}

_READ_DESCRIPTION = (
    "读取资料库内的一个文件。文本类直接返回正文；docx/xlsx/pptx 用 officeCLI 现转现读；"
    "图片返回元数据（需要看图时再用 send_library_file 发送）；PDF 本次演示不支持。"
    "读取前会校验同目录 .emily.md 索引存在。"
)

_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "资料库内的相对路径（文件），例如 '演示项目/02_材料与做法/图片/草坪.jpg'",
        },
        "caption": {
            "type": "string",
            "description": "可选，发送时的文字说明",
        },
    },
    "required": ["path"],
}

_SEND_DESCRIPTION = (
    "把资料库内的一个文件/图片发送到当前会话。用于“查材料/查做法”时附图或附件；"
    "只允许资料库范围内、当前用户有权限访问的文件；路径越界会被拒绝。"
)


def _fs():
    from ..services.knowledge_library import KnowledgeLibraryFs

    return KnowledgeLibraryFs()


def _safe_path(path: str) -> Path:
    """解析资料库相对路径并校验同目录索引存在。"""
    from ..services.knowledge_library import (
        KnowledgeLibraryError,
        KnowledgeLibraryNotFoundError,
        KnowledgeLibraryPathError,
        KnowledgeLibraryTypeError,
    )

    rel = str(path or "").strip().strip("/")
    if not rel:
        raise KnowledgeLibraryNotFoundError("缺少资料路径 path")

    fs = _fs()
    p = fs.resolve(rel)
    if not p.exists():
        raise KnowledgeLibraryNotFoundError(f"资料文件不存在：{rel}")
    if p.is_dir():
        raise KnowledgeLibraryTypeError(f"{rel} 是目录，不能按文件读取/发送")
    if not fs.index_exists(rel):
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        raise KnowledgeLibraryNotFoundError(
            f"「{parent or '资料库根目录'}」未入库（缺索引），不能直接读取/发送文件。"
            f"请先由管理员对该目录执行入库。"
        )
    return p


async def handle_read_library_file(params: dict, user_id: str = "") -> dict:
    """读取资料库内文件（文本 / Office / 图片元数据）。"""
    from .file_tool import check_min_level

    denied = check_min_level(user_id, _READ_MIN_LEVEL, "资料库文件读取")
    if denied is not None:
        return denied

    path = str((params or {}).get("path") or "").strip()
    try:
        p = await asyncio.to_thread(_safe_path, path)
    except Exception as e:  # noqa: BLE001 — 统一转为用户可读错误
        return _error_from_exception(e, path)

    ext = p.suffix.lower()
    if ext in _OFFICE_EXT:
        # officeCLI handler 自带 L3 门禁；这里把 user_id 透传过去
        from .office_cli_tool import handle_office_cli

        max_lines = (params or {}).get("max_lines", 300)
        result = await handle_office_cli({
            "file_path": str(p),
            "mode": "text",
            "max_lines": max_lines,
        }, user_id=user_id)
        if not result.get("success"):
            return result
        return {
            "success": True,
            "path": path,
            "file_name": p.name,
            "engine": "officeCLI",
            "content": result.get("data"),
            "reply": f"已读取资料库文件：{p.name}",
        }

    if ext in _IMAGE_EXT:
        return {
            "success": True,
            "path": path,
            "file_name": p.name,
            "file_size": p.stat().st_size,
            "kind": "image",
            "reply": (f"图片已就绪：{p.name}。如需展示，请调用 send_library_file；"
                      f"如需识别图片内容，请调用 ocr_document 并传入该文件路径。"),
        }

    if ext in _TEXT_EXT:
        fs = _fs()
        try:
            text = await asyncio.to_thread(fs.read_text_file, path)
        except Exception as e:  # noqa: BLE001
            return _error_from_exception(e, path)
        return {
            "success": True,
            "path": path,
            "file_name": p.name,
            "kind": "text",
            "content": text,
            "reply": f"已读取资料库文件：{p.name}",
        }

    if ext == ".pdf":
        return {
            "success": False,
            "error_code": "pdf_not_supported",
            "path": path,
            "reply": "本次演示不支持 PDF；请提供 xlsx/docx/pptx 或图片。",
        }

    return {
        "success": False,
        "error_code": "unsupported_type",
        "path": path,
        "reply": f"暂不支持读取该类型（{ext or '无扩展名'}）：{p.name}",
    }


async def handle_send_library_file(
    params: dict,
    outbound_bus=None,
    user_id: str = "",
    conversation_id: str = "",
    **kwargs,
) -> dict:
    """把资料库内文件/图片发送到当前会话。"""
    from .file_tool import check_min_level

    denied = check_min_level(user_id, _READ_MIN_LEVEL, "资料库文件发送")
    if denied is not None:
        return denied
    if outbound_bus is None:
        return {"success": False, "error": "出站通道未就绪", "error_code": "service_unavailable"}
    # 分发层未注入具名 conversation_id，回退取运行时参数（与 file_tool.handle_send_file 同口径）
    conversation_id = conversation_id or (params or {}).get("_conversation_id", "")
    if not conversation_id:
        return {"success": False, "error": "缺少会话 ID", "error_code": "missing_conversation_id"}

    path = str((params or {}).get("path") or "").strip()
    try:
        p = await asyncio.to_thread(_safe_path, path)
    except Exception as e:  # noqa: BLE001
        return _error_from_exception(e, path)

    caption = str((params or {}).get("caption") or "").strip()
    outbound_bus.publish("file_send", {
        "conversation_id": conversation_id,
        "file_paths": [{"path": str(p), "name": p.name}],
        "caption": caption,
    })
    logger.info("library file_send: %s → %s", p.name, conversation_id)
    return {
        "success": True,
        "path": path,
        "file_name": p.name,
        "reply": f"已发送资料库文件：{p.name}",
    }


def _error_from_exception(e: Exception, path: str) -> dict:
    """把资料库异常转为用户可读错误。"""
    from ..services.knowledge_library import (
        KnowledgeLibraryNotFoundError,
        KnowledgeLibraryPathError,
        KnowledgeLibraryTypeError,
    )

    if isinstance(e, KnowledgeLibraryPathError):
        return {"success": False, "error_code": "path_out_of_range", "path": path,
                "reply": f"已拒绝越界访问：{e}。只能查阅资料库范围内的内容。"}
    if isinstance(e, KnowledgeLibraryNotFoundError):
        return {"success": False, "error_code": "not_found", "path": path,
                "reply": f"{e}。可先用 read_knowledge_library(action=\"list\") 查看目录。"}
    if isinstance(e, KnowledgeLibraryTypeError):
        return {"success": False, "error_code": "unsupported_type", "path": path,
                "reply": f"该文件不能按文件读取/发送：{e}"}
    return {"success": False, "error_code": "read_failed", "path": path,
            "reply": f"资料库文件读取失败：{e}"}
