"""attachment_tool —— 文件编号 → 本地路径解析（供 officeCLI/OCR 读取）。"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.tool.attachment")

_RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20261019-0001）",
        },
    },
    "required": ["file_no"],
}

_RESOLVE_DESCRIPTION = (
    "把文件编号解析为本地可读路径。"
    "用于当前消息附件或多轮对话中已归档的文件："
    "先拿到 file_no，再解析路径交给 officeCLI 读取 Office 文件。"
)


async def handle_resolve_file_path(
    params: dict,
    file_manager=None,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """解析 file_no 为本地路径（权限校验 + 当前消息附件放行）。"""
    file_no = str((params or {}).get("file_no") or "").strip()
    if not file_no:
        return {"success": False, "error": "缺少 file_no", "error_code": "missing_file_no"}
    if file_manager is None:
        return {"success": False, "error": "文件服务未就绪", "error_code": "service_unavailable"}

    record = file_manager.get_by_file_no(file_no)
    if record is None:
        return {"success": False, "error": f"找不到文件编号 {file_no}",
                "error_code": "file_not_found"}

    # 权限：有 session_accessible_files 授权，或文件属于当前入站消息（用户刚上传）
    allowed = False
    try:
        allowed = bool(user_id and file_manager.can_access(user_id, record.id))
    except Exception as e:  # noqa: BLE001
        logger.warning("resolve_file_path can_access failed: %s", e)
    if not allowed and message_id and getattr(record, "message_id", "") == message_id:
        allowed = True
    if not allowed:
        return {"success": False, "error": "您无权访问该文件", "error_code": "permission_denied"}

    local_path = file_manager.resolve_local_path(file_no)
    if not local_path:
        return {"success": False, "error": f"文件 {file_no} 未在本地存储",
                "error_code": "file_not_stored"}

    return {
        "success": True,
        "file_no": record.file_no,
        "file_name": record.filename,
        "file_size": record.file_size or 0,
        "file_path": local_path,
        "reply": f"文件已就绪：{record.filename}（{file_no}）",
    }
