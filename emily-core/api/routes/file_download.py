"""文件下载端点 —— 供渠道网关按 file_no 拉取已归档文件。

场景：渠道（微信小程序网关等）收到 Core 的 file_send 出站事件后，凭事件里的
file_no 调本端点取回字节流，再由渠道以自身域名对外提供下载。Core 只负责
「鉴权 + 定位 + 交付」，不感知具体渠道协议。

安全：
  · 本端点挂在 /api/v1 下，受 AuthMiddleware 的 X-Emily-Token 约束。
    切勿挪到 /api/v1/console 前缀 —— 该前缀在中间件里是白名单无条件放行，
    挂过去等同于免鉴权开放全部归档文件。
  · actor 为可选参数：渠道若已解析出终端用户，应传入其 user_id，
    此时按可见文件集合做一次校验；不传则只依赖 Core Token（内部调用语义）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger("emily.api.file_download")

router = APIRouter()

# 单文件下载上限（字节）。默认 200MB —— 对齐微信小程序 wx.downloadFile 的上限，
# 超过则小程序侧必然失败，不如在源头拦掉并给出明确原因。
_MAX_DOWNLOAD_BYTES = int(os.environ.get("EMILY_FILE_DOWNLOAD_MAX_MB", "200")) * 1024 * 1024


def _fail(status: int, reason: str) -> JSONResponse:
    """统一错误响应（渠道侧可直接透传 reason 给用户）。"""
    return JSONResponse({"success": False, "reason": reason}, status_code=status)


@router.get("/files/{file_no}/download")
async def download_file(
    file_no: str,
    actor: str = Query("", description="可选：下载发起人 user_id；提供则做可见性校验"),
):
    """按文件编号下载文件字节流。

    返回 FileResponse（含 Content-Disposition 文件名）；异常路径统一返回
    {"success": false, "reason": ...}，便于渠道区分「文件不存在」与「无权限」。
    """
    from api.server import get_core

    try:
        core = get_core()
    except Exception as ex:  # noqa: BLE001
        return _fail(503, f"Emily 内核未就绪：{ex}")

    fm = getattr(core, "_file_manager", None)
    if fm is None:
        return _fail(503, "文件服务未就绪")

    record = fm.get_by_file_no(file_no)
    if record is None or getattr(record, "is_deleted", False):
        return _fail(404, f"文件不存在：{file_no}")

    if actor and not fm.can_access(actor, record.id):
        logger.warning("file download denied: file_no=%s actor=%s", file_no, actor)
        return _fail(403, "您无权访问该文件")

    local_path = fm.resolve_local_path(file_no)
    if not local_path or not Path(local_path).is_file():
        return _fail(404, f"文件未在本地存储：{file_no}")

    try:
        size = Path(local_path).stat().st_size
    except OSError as ex:
        return _fail(500, f"读取文件失败：{ex}")

    if size > _MAX_DOWNLOAD_BYTES:
        limit_mb = _MAX_DOWNLOAD_BYTES // (1024 * 1024)
        return _fail(413, f"文件 {size / 1024 / 1024:.1f}MB 超过下载上限 {limit_mb}MB")

    logger.info(
        "file download: file_no=%s size=%d actor=%s name=%s",
        file_no, size, actor or "-", record.filename,
    )
    return FileResponse(
        local_path,
        filename=record.filename or file_no,
        media_type=getattr(record, "file_type", "") or None,
    )
