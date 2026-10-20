"""video_download_tool —— 公开视频下载（yt-dlp）基座能力。

定位：
  · 基座能力，但属于“写”操作：permission_flag=write → 仅经 SOP 授权调用；
  · SOP 准入门槛 L3（参建管理及以上），handler 内再做一次 check_min_level（fail-closed）；
  · 只下载单个公开视频，不处理播放列表、直播、登录态、付费/DRM 内容；
  · 下载后经 FileManager.archive_upload 归档，并显式授权给操作人；
  · 返回 file_no，由 SOP 调用已有的 send_file 把视频发给当前会话。

边界：
  · 本工具不直接向用户发消息（发送统一走 send_file / FileManager.send_to_user）；
  · 不把本地路径暴露给 LLM 或用户；
  · 不绕过任何平台的登录、付费或版权限制。

依赖：yt-dlp（requirements.txt）。未安装时工具仍可注册，调用时返回明确提示。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import shutil
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("emily.tool.video_download")

#: SOP 与 handler 双门槛（线性“不低于”语义）
DOWNLOAD_MIN_LEVEL = 3

#: 单视频上限：30 分钟 / 200MB（防止一次调用拖垮会话或占满磁盘）
MAX_DURATION_SECONDS = 30 * 60
MAX_FILESIZE_BYTES = 200 * 1024 * 1024

#: 下载等待上限：按视频时长动态计算（先探测 metadata，再定上限）
DEFAULT_WAIT_SECONDS = 240          # 时长未知时的保守值
MIN_WAIT_SECONDS = 120
MAX_WAIT_SECONDS = 600              # 与 console/IM 侧可接受的最长等待对齐
WAIT_BUFFER_SECONDS = 90
WAIT_FACTOR = 2.0

#: 允许的站点（可在此按需扩展；不在名单内一律拒绝，避免把 yt-dlp 当任意 URL 抓取器）
ALLOWED_HOSTS: frozenset[str] = frozenset({
    "bilibili.com", "www.bilibili.com", "b23.tv", "bilibili.tv",
    "youtube.com", "www.youtube.com", "youtu.be",
    "vimeo.com", "www.vimeo.com",
})

_VIDEO_DOWNLOAD_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": (
                "公开视频页面地址（http/https）。支持 B站（bilibili.com / b23.tv）、"
                "YouTube（youtube.com / youtu.be）、Vimeo。必须是单个视频，不接受播放列表、"
                "直播、需要登录或付费的内容。"
            ),
        },
    },
    "required": ["url"],
}

_VIDEO_DOWNLOAD_DESCRIPTION = (
    "下载一个公开视频并归档（yt-dlp）。\n"
    "\n"
    "必填字段：\n"
    "  url — 公开视频页面地址（B站 / YouTube / Vimeo；单个视频，不含播放列表/直播）\n"
    "\n"
    "限制：\n"
    "  · 仅 http/https，且只允许名单内站点；内网/本机地址一律拒绝\n"
    "  · 单视频不超过 30 分钟、200MB\n"
    "  · 不绕过登录、付费或 DRM；不支持需要 cookies 的私有内容\n"
    "  · 调用时先探测视频 metadata（标题/时长），再按“2×时长+90 秒”设置等待上限\n"
    "    （120–600 秒）；超时会返回明确提示，换更短视频即可\n"
    "  · 演示环境默认跳过证书校验（yt-dlp 不读 mitmproxy 的 SSL_CERT_FILE）\n"
    "\n"
    "成功返回 file_no；随后应调用 send_file 把视频发送给当前用户。\n"
    "权限：L3 参建管理及以上，且须经「视频下载」SOP 调用。"
)


class VideoDownloadError(Exception):
    """可转述给用户的下载失败原因。"""

    def __init__(self, reply: str, code: str = "download_failed"):
        super().__init__(reply)
        self.reply = reply
        self.code = code


# ══════════════════════════════════════════════════════════════════════════════
# URL 校验（纯函数 + DNS 解析分离，便于测试）
# ══════════════════════════════════════════════════════════════════════════════

def host_allowed(host: str) -> bool:
    """主机是否在允许名单内（支持子域名，严格后缀匹配）。"""
    host = (host or "").lower().strip(".")
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def check_url_shape(raw: str) -> tuple[str, str, str]:
    """校验 URL 形状与站点名单，不做 DNS 解析。

    Returns:
        (normalized_url, host, error)。error 为空表示通过。
    """
    raw = (raw or "").strip()
    if not raw:
        return "", "", "请提供视频页面地址。"
    try:
        parsed = urlparse(raw)
    except Exception:
        return "", "", "视频地址格式不正确。"
    if parsed.scheme not in ("http", "https"):
        return "", "", "只支持 http/https 视频地址。"
    if parsed.username or parsed.password:
        return "", "", "视频地址不能包含用户名或密码。"
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return "", "", "视频地址缺少有效域名。"
    try:
        port = parsed.port
    except ValueError:
        return "", "", "视频地址端口不合法。"
    if port not in (None, 80, 443):
        return "", "", "视频地址端口不在允许范围内。"
    if not host_allowed(host):
        return "", "", (
            "暂不支持该站点。当前支持 B站（bilibili.com / b23.tv）、"
            "YouTube（youtube.com / youtu.be）和 Vimeo。"
        )
    return raw, host, ""


def resolve_public_host(host: str) -> str:
    """DNS 解析并确认没有落到内网/本机/保留地址；不通过则返回错误文案。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return "无法解析视频站点域名，请检查地址是否正确。"
    if not infos:
        return "无法解析视频站点域名，请检查地址是否正确。"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return "视频站点解析结果不合法，已拒绝访问。"
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return "该地址指向内网或本机，已拒绝访问。"
    return ""


def validate_url(raw: str) -> tuple[str, str]:
    """完整校验：形状 + 站点名单 + DNS 公网性。返回 (url, error)。"""
    normalized, host, err = check_url_shape(raw)
    if err:
        return "", err
    resolve_err = resolve_public_host(host)
    if resolve_err:
        return "", resolve_err
    return normalized, ""


# ══════════════════════════════════════════════════════════════════════════════
# yt-dlp 下载（在线程中执行，避免阻塞事件循环）
# ══════════════════════════════════════════════════════════════════════════════

def _mime_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
    }.get(ext, "application/octet-stream")


def _pick_downloaded_file(tmpdir: str, info: dict, ydl) -> str:
    """定位下载后的实际文件（兼容 merge/格式回退）。"""
    try:
        prepared = ydl.prepare_filename(info)
    except Exception:
        prepared = ""
    if prepared and os.path.isfile(prepared):
        return prepared
    candidates: list[str] = []
    for name in os.listdir(tmpdir):
        path = os.path.join(tmpdir, name)
        if os.path.isfile(path) and not name.endswith((".part", ".ytdl")):
            candidates.append(path)
    if not candidates:
        return ""
    return max(candidates, key=os.path.getsize)


def _common_ytdlp_opts() -> dict:
    """探测与下载共用的 yt-dlp 选项。

    nocheckcertificate 默认开启：当前演示环境经 mitmproxy 抓包，yt-dlp 的
    certifi 不读 SSL_CERT_FILE；站点已在 ALLOWED_HOSTS 白名单内，故默认跳过
    证书校验，避免 B 站下载被代理 CA 卡死。
    """
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
    }


def compute_wait_seconds(duration: float | int | None) -> int:
    """按视频时长估算下载等待上限（秒）。

    规则：2×时长 + 90 秒缓冲，夹在 [120, 600] 之间；时长未知时用 240 秒。
    """
    try:
        d = float(duration or 0)
    except (TypeError, ValueError):
        d = 0
    if d <= 0:
        return DEFAULT_WAIT_SECONDS
    return int(min(MAX_WAIT_SECONDS, max(MIN_WAIT_SECONDS, d * WAIT_FACTOR + WAIT_BUFFER_SECONDS)))


def probe_video_info(url: str) -> dict:
    """只解析页面 metadata，不下载视频。

    返回 {title, duration, filesize_approx, webpage_url}；失败抛 VideoDownloadError。
    """
    try:
        import yt_dlp
    except Exception as e:  # noqa: BLE001
        logger.error("yt-dlp 未安装: %s", e)
        raise VideoDownloadError(
            "视频下载组件未安装（yt-dlp），请联系管理员检查依赖。",
            "dependency_missing",
        )

    opts = _common_ytdlp_opts()
    # 不指定 format：B 站等站点在“仅探测 metadata”时不接受 best 选择器
    opts.update({"skip_download": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("yt-dlp probe failed url=%s: %s", url[:160], e)
        raise VideoDownloadError(
            "视频信息解析失败：地址可能失效、需要登录，或平台暂时不可用。",
            "probe_failed",
        )

    if not info:
        raise VideoDownloadError("没有从该地址解析到视频。", "no_video")
    if info.get("_type") == "playlist":
        raise VideoDownloadError(
            "该地址是播放列表，请提供单个视频的页面地址。", "playlist_not_allowed"
        )
    duration = int(float(info.get("duration") or 0))
    if duration and duration > MAX_DURATION_SECONDS:
        raise VideoDownloadError(
            f"视频时长约 {duration // 60} 分钟，超过 30 分钟上限。",
            "too_long",
        )
    return {
        "title": str(info.get("title") or "")[:120],
        "duration": duration,
        "filesize_approx": int(info.get("filesize_approx") or 0),
        "webpage_url": str(info.get("webpage_url") or url),
    }


def _ffmpeg_path() -> str:
    """定位 ffmpeg：优先 PATH，其次 imageio-ffmpeg 自带静态二进制。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return ""


def download_with_ytdlp(url: str, wait_seconds: int | None = None) -> dict:
    """同步下载一个公开视频，返回 {filename, data, mime, duration, size}。

    在线程中调用；异常转为 VideoDownloadError。
    """
    ffmpeg_path = _ffmpeg_path()
    if not ffmpeg_path:
        raise VideoDownloadError(
            "视频合并组件 ffmpeg 未安装，无法处理 B 站等分离音视频流；请联系管理员检查镜像依赖。",
            "dependency_missing",
        )

    try:
        import yt_dlp
    except Exception as e:  # noqa: BLE001
        logger.error("yt-dlp 未安装: %s", e)
        raise VideoDownloadError(
            "视频下载组件未安装（yt-dlp），请联系管理员检查依赖。",
            "dependency_missing",
        )

    def _match_filter(info, *, incomplete=False):
        """下载前按时长拦截，避免先下载再拒绝。"""
        duration = info.get("duration") or 0
        if duration and float(duration) > MAX_DURATION_SECONDS:
            return f"视频时长超过 {MAX_DURATION_SECONDS // 60} 分钟上限"
        return None

    tmpdir = tempfile.mkdtemp(prefix="emily_video_")
    try:
        opts = _common_ytdlp_opts()
        opts.update({
            # B 站等站点多数只提供分离的音/视频流，需要 ffmpeg 合并；
            # 优先最佳视频+音频，退化到最佳单文件。
            "format": "bv*+ba/b",
            "outtmpl": os.path.join(tmpdir, "%(title).80s.%(ext)s"),
            "windowsfilenames": True,
            "restrictfilenames": False,
            "max_filesize": MAX_FILESIZE_BYTES,
            "match_filter": _match_filter,
            "ffmpeg_location": ffmpeg_path,
        })
        if wait_seconds:
            # 单连接 socket 超时不超过总等待上限的一半，避免一个慢连接拖死全局
            opts["socket_timeout"] = max(20, min(60, int(wait_seconds) // 2))
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise VideoDownloadError("没有从该地址解析到视频。", "no_video")
                if info.get("_type") == "playlist":
                    raise VideoDownloadError(
                        "该地址是播放列表，请提供单个视频的页面地址。", "playlist_not_allowed"
                    )
                duration = info.get("duration") or 0
                if duration and float(duration) > MAX_DURATION_SECONDS:
                    raise VideoDownloadError(
                        f"视频时长约 {int(float(duration)) // 60} 分钟，超过 30 分钟上限。",
                        "too_long",
                    )
                path = _pick_downloaded_file(tmpdir, info, ydl)
        except VideoDownloadError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("yt-dlp download failed url=%s: %s", url[:160], e)
            raise VideoDownloadError(
                "视频下载失败：地址可能失效、需要登录，或平台暂时不可用。", "download_failed"
            )

        if not path or not os.path.isfile(path):
            raise VideoDownloadError("视频下载完成但未找到文件。", "file_missing")

        size = os.path.getsize(path)
        if size <= 0:
            raise VideoDownloadError("下载到的视频为空文件。", "empty_file")
        if size > MAX_FILESIZE_BYTES:
            raise VideoDownloadError(
                f"视频大小约 {size // (1024 * 1024)}MB，超过 200MB 上限。", "too_large"
            )

        title = (info.get("title") or Path(path).stem)[:120]
        ext = os.path.splitext(path)[1] or ".mp4"
        filename = f"{title}{ext}"
        data = Path(path).read_bytes()
        return {
            "filename": filename,
            "data": data,
            "mime": _mime_for(path),
            "duration": int(float(info.get("duration") or 0)),
            "size": len(data),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# Handler
# ══════════════════════════════════════════════════════════════════════════════

async def handle_download_video(
    params: dict,
    user_id: str = "",
    file_manager=None,
    outbound_bus=None,
    **kwargs,
) -> dict:
    """下载公开视频、归档并授权给操作人；返回 file_no 供 send_file 使用。"""
    from .file_tool import check_min_level

    denied = check_min_level(user_id, DOWNLOAD_MIN_LEVEL, "视频下载")
    if denied is not None:
        return denied

    if file_manager is None:
        return {
            "success": False,
            "error_code": "service_unavailable",
            "reply": "文件服务未就绪，暂时无法归档视频。",
        }

    raw_url = str((params or {}).get("url") or "").strip()
    normalized, err = await asyncio.to_thread(validate_url, raw_url)
    if err:
        return {"success": False, "error_code": "invalid_url", "reply": err}

    # 先探测 metadata（不下载），按视频时长设置等待上限
    try:
        meta = await asyncio.to_thread(probe_video_info, normalized)
    except VideoDownloadError as e:
        return {"success": False, "error_code": e.code, "reply": e.reply}
    except Exception as e:  # noqa: BLE001
        logger.error("download_video probe error url=%s: %s", normalized, e, exc_info=True)
        return {
            "success": False,
            "error_code": "probe_failed",
            "reply": f"视频信息解析失败：{e}",
        }

    duration = int(meta.get("duration") or 0)
    wait_seconds = compute_wait_seconds(duration)
    title = str(meta.get("title") or "")[:120]

    # 下载前先告知用户：视频信息 + 预计最长等待时间（QQ/console 均消费 progress 事件）
    conv_id = str((params or {}).get("_conversation_id") or "")
    if outbound_bus is not None and conv_id:
        try:
            from ..outbound_bus import is_test_session

            duration_text = f"{duration} 秒" if duration else "未知"
            outbound_bus.publish("progress", {
                "content": (
                    f"已获取视频信息：「{title or '未命名'}」，时长约 {duration_text}。"
                    f"预计最长 {wait_seconds} 秒完成下载，请稍候…"
                ),
                "conversation_id": conv_id,
                "test_session": is_test_session(conv_id),
            })
            logger.info("download_video progress published: conv=%s duration=%ss wait=%ss",
                        conv_id, duration, wait_seconds)
        except Exception as e:  # noqa: BLE001
            logger.warning("download_video progress publish failed: %s", e)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(download_with_ytdlp, normalized, wait_seconds),
            timeout=wait_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("download_video timeout url=%s duration=%ss wait=%ss",
                       normalized[:160], duration, wait_seconds)
        return {
            "success": False,
            "error_code": "timeout",
            "reply": (
                f"视频「{title or '未命名'}」时长约 {duration or '未知'} 秒，"
                f"下载等待超过 {wait_seconds} 秒，已中止。请换更短的视频或稍后重试。"
            ),
            "duration": duration,
            "wait_seconds": wait_seconds,
        }
    except VideoDownloadError as e:
        return {"success": False, "error_code": e.code, "reply": e.reply}
    except Exception as e:  # noqa: BLE001
        logger.error("download_video unexpected error url=%s: %s", normalized, e, exc_info=True)
        return {
            "success": False,
            "error_code": "download_failed",
            "reply": f"视频下载失败：{e}",
        }

    try:
        archive = await asyncio.to_thread(
            file_manager.archive_upload,
            result["filename"],
            result["data"],
            uploaded_by=user_id,
            content_type=result["mime"],
            confidentiality=1,
            file_category="OTHER",
            purpose="REFERENCE",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("download_video archive failed: %s", e, exc_info=True)
        return {
            "success": False,
            "error_code": "archive_failed",
            "reply": f"视频已下载，但归档失败：{e}",
        }

    if not archive.get("success") or not archive.get("file_id"):
        return {
            "success": False,
            "error_code": "archive_failed",
            "reply": "视频已下载，但归档失败（文件记录未创建）。",
        }

    # 下载动作产生的文件对操作人显式可见，否则 send_file 的 can_access 会拒绝
    try:
        from ..repositories.session_accessible_file_repo import SessionAccessibleFileRepo

        granted = await asyncio.to_thread(
            SessionAccessibleFileRepo.add_explicit_grant,
            user_id,
            archive.get("file_id", ""),
            "system:video_download",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("download_video grant failed user=%s: %s", user_id, e)
        granted = False
    if not granted:
        return {
            "success": False,
            "error_code": "grant_failed",
            "reply": "视频已下载，但文件授权失败，暂时无法发送给当前用户。",
        }

    file_no = archive.get("file_no", "")
    final_duration = int(result.get("duration") or duration or 0)
    return {
        "success": True,
        "file_no": file_no,
        "filename": archive.get("filename", result["filename"]),
        "title": title,
        "size": result["size"],
        "duration": final_duration,
        "wait_seconds": wait_seconds,
        "reply": (
            f"视频「{title or archive.get('filename', result['filename'])}」"
            f"（时长约 {final_duration or '未知'} 秒）已下载并归档："
            f"{archive.get('filename', result['filename'])}"
            f"（{file_no}，{result['size'] // (1024 * 1024)}MB；等待上限 {wait_seconds} 秒）。"
            f"请调用 send_file 发送给当前用户。"
        ),
    }
