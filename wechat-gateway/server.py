"""wechat-gateway —— 微信小程序薄网关（方案 A MVP，本机/局域网联调）。

链路：小程序 wx.request → POST /chat → StandardMessage → emily-core
/api/v1/message/send；回复不依赖该 POST 响应体，统一由常驻 SSE
/api/v1/events/outbound 按 conversation_id 捞回并喂给 /chat 请求。

身份：MVP 用本地假身份（X-Dev-User 头），未接 wx.login/code2session。

启动（仓库根）：
    .venv\\Scripts\\python.exe -m uvicorn server:app --app-dir wechat-gateway \
        --host 0.0.0.0 --port 18090
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI, File, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from core_client import CoreApiClient, load_env_token

logger = logging.getLogger("wxmp.server")
logging.basicConfig(
    level=os.environ.get("WXMP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

CORE_BASE_URL = os.environ.get("WXMP_CORE_URL", "http://127.0.0.1:18080")
REPLY_TIMEOUT = float(os.environ.get("WXMP_REPLY_TIMEOUT", "55"))  # 秒；配合前端 wx.request timeout 60s
CORE_POST_TIMEOUT = 10.0  # 秒；POST 本身不承载回复，短超时即可

# 网关对 Core 可见的地址：Core 按此回拉暂存附件（入站上传链路）。
# 容器内默认走 compose 服务名；本机联调若 Core 在容器、网关在宿主机，
# 需改为 http://host.docker.internal:18090（Windows/Mac）或宿主机局域网 IP。
SELF_URL_INTERNAL = os.environ.get("WXMP_SELF_URL", "http://wechat-gateway:18090").rstrip("/")
# 入站附件暂存目录（Core 拉走后可手工清理）
UPLOAD_DIR = Path(os.environ.get("WXMP_UPLOAD_DIR", str(Path(__file__).resolve().parent / "uploads")))
# 下载令牌有效期（秒）：过期即失效，避免链接被长期复用
FILE_TOKEN_TTL = float(os.environ.get("WXMP_FILE_TOKEN_TTL", "1800"))
# 单文件上限（字节）：对齐微信 wx.downloadFile / wx.uploadFile 的 200MB 约束
MAX_FILE_BYTES = int(os.environ.get("WXMP_MAX_FILE_MB", "200")) * 1024 * 1024

# ── 小程序登录（wx.login → code2session → 自有签名令牌）────────────
WXMP_APP_ID = os.environ.get("WXMP_APP_ID", "").strip()
WXMP_APP_SECRET = os.environ.get("WXMP_APP_SECRET", "").strip()
TOKEN_SECRET = (os.environ.get("WXMP_TOKEN_SECRET", "").strip() or WXMP_APP_SECRET)
TOKEN_TTL = int(os.environ.get("WXMP_TOKEN_TTL", str(7 * 24 * 3600)))
REQUIRE_AUTH = os.environ.get("WXMP_REQUIRE_AUTH", "0") == "1"

# ── 微信客服桥接（轮询 sync_msg，不依赖回调，绕开域名主体校验）──────
KF_ENABLED = os.environ.get("WXMP_KF_ENABLED", "0") == "1"
KF_CORP_ID = os.environ.get("WXMP_KF_CORP_ID", "").strip()
KF_SECRET = os.environ.get("WXMP_KF_CORP_SECRET", "").strip()
KF_OPEN_KFID = os.environ.get("WXMP_KF_OPEN_KFID", "").strip()
KF_POLL_INTERVAL = float(os.environ.get("WXMP_KF_POLL_INTERVAL", "12"))
KF_CONV_PREFIX = "wecom_"
KF_API = "https://qyapi.weixin.qq.com"
# 微信客服素材大小上限（字节）：图片 10M / 语音 2M / 视频 10M / 文件 20M
KF_MEDIA_LIMITS = {
    "image": 10 * 1024 * 1024,
    "voice": 2 * 1024 * 1024,
    "video": 10 * 1024 * 1024,
    "file": 20 * 1024 * 1024,
}

_kf_token: dict = {"value": "", "exp": 0.0}
_kf_cursor: str = ""
_kf_seen: dict[str, float] = {}

# ── 企业微信应用回调（自建应用「接收消息」）──────────────────────
WECOM_CB_TOKEN = os.environ.get("WXMP_WECOM_CB_TOKEN", "").strip()
WECOM_CB_AES_KEY = os.environ.get("WXMP_WECOM_CB_AES_KEY", "").strip()
WECOM_CB_CORP_ID = os.environ.get("WXMP_WECOM_CB_CORP_ID", "").strip()
WECOM_CONV_PREFIX = "wecomapp_"
_wecom_state: dict = {
    "agent_id": os.environ.get("WXMP_WECOM_AGENT_ID", "").strip(),
}
_wecom_seen: dict[str, float] = {}

# per-conversation 待回复队列：conversation_id -> deque[Future[str]]
_pending: dict[str, deque[asyncio.Future]] = {}
# 后台未完成的 POST 任务强引用，防 GC（完成后自清理）
_bg_tasks: set[asyncio.Task] = set()

# conversation_id -> deque[待下载文件条目]。
# Core 的 file_send 事件在工具执行期发出、reply 在其后合成，故通常先到；
# 这里缓存起来，等 reply 落地时随 /chat 响应一并交给小程序。
_pending_files: dict[str, deque[dict]] = {}
# 下载令牌 -> {file_no, name, conv, exp}（小程序凭 token 下载，不暴露 file_no 与 Core Token）
_file_tokens: dict[str, dict] = {}
# 上传令牌 -> {path, name, exp}（供 Core 回拉暂存附件）
_upload_tokens: dict[str, dict] = {}

_client = CoreApiClient(
    base_url=CORE_BASE_URL,
    api_token=load_env_token(),
    request_timeout=CORE_POST_TIMEOUT,
)


class LoginIn(BaseModel):
    """wx.login 拿到的临时 code。"""

    code: str = ""


class ChatIn(BaseModel):
    """小程序 /chat 请求体（与前端 askBackend 对齐）。"""

    message: str = ""
    history: list[dict] | None = Field(default=None)
    # 已上传附件的引用：[{type, url, file_name, file_size}, ...]
    # url 指向本网关 /attachment/{token}，Core 收到后据此回拉并落盘归档
    attachments: list[dict] = Field(default_factory=list)


def _sanitize_dev_id(dev_id: str) -> str:
    """收敛外部传入的 dev-id：仅保留字母数字与 _-，限长 64。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", dev_id or "")
    return cleaned[:64] or "dev-user"


# ── 登录令牌：HMAC-SHA256 签名，避免端上伪造身份 ──────────────────
def _sign_token(uid: str, exp: int) -> str:
    msg = f"{uid}.{exp}".encode()
    return hmac.new(TOKEN_SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _issue_token(uid: str) -> str:
    exp = int(time.time()) + TOKEN_TTL
    return f"{uid}.{exp}.{_sign_token(uid, exp)}"


def _verify_token(token: str) -> str | None:
    """校验令牌，返回 uid；签名不符或过期返回 None。"""
    if not token or not TOKEN_SECRET:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    uid, exp_raw, sig = parts
    try:
        exp = int(exp_raw)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    if not hmac.compare_digest(sig, _sign_token(uid, exp)):
        return None
    return uid


def _identity(x_dev_user: str | None, x_dev_token: str | None) -> str:
    """优先用签名令牌确认身份；未带令牌时按开关决定是否放行旧 dev-id。"""
    uid = _verify_token(x_dev_token or "")
    if uid:
        return _sanitize_dev_id(uid)
    if REQUIRE_AUTH:
        # 由调用方转成 401
        return ""
    if x_dev_user:
        logger.warning("未带登录令牌，回退 X-Dev-User=%s（仅限联调）", x_dev_user)
    return _sanitize_dev_id(x_dev_user or "dev-user")


def _msg_type_of(attachments: list[dict]) -> int:
    """按首个附件推断消息类型（1=text 2=图片 3=文件 4=语音 5=视频）。"""
    if not attachments:
        return 1
    first = attachments[0].get("type", 3)
    return first if first in (2, 3, 4, 5) else 3


def _attachment_type_of(filename: str) -> int:
    """按扩展名推断附件类型（与 Core 侧口径一致）。"""
    ext = Path(filename or "").suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return 2
    if ext in (".mp3", ".wav", ".ogg", ".aac", ".m4a"):
        return 4
    if ext in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        return 5
    return 3


def _register(conv: str) -> asyncio.Future:
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending.setdefault(conv, deque()).append(fut)
    return fut


def _unregister(conv: str, fut: asyncio.Future) -> None:
    q = _pending.get(conv)
    if not q:
        return
    if q and q[0] is fut:
        q.popleft()
    elif fut in q:
        q.remove(fut)
    if not q:
        _pending.pop(conv, None)


def _collect_file_send(conv: str, data: dict) -> None:
    """把 file_send 事件的 file_paths 转成小程序可下载的条目。

    只登记 file_no 与文件名，具体字节由 /files/{token} 现取现转 ——
    既不把容器内绝对路径暴露给小程序，也不把 Core Token 下发到端上。
    """
    for fp in data.get("file_paths") or []:
        if not isinstance(fp, dict):
            continue
        file_no = str(fp.get("file_no") or "")
        name = str(fp.get("name") or "")
        if not file_no:
            logger.warning("file_send 缺少 file_no，跳过: conv=%s name=%s", conv, name)
            continue
        token = uuid.uuid4().hex
        _file_tokens[token] = {
            "file_no": file_no,
            "name": name,
            "conv": conv,
            "exp": time.monotonic() + FILE_TOKEN_TTL,
        }
        _pending_files.setdefault(conv, deque()).append({
            "token": token,
            "name": name or file_no,
            "url": f"/files/{token}",
        })
        logger.info("file_send 已登记: conv=%s file_no=%s name=%s", conv, file_no, name)


def _drain_files(conv: str) -> list[dict]:
    """取出并清空某会话的待下载文件（FIFO 顺序）。"""
    q = _pending_files.pop(conv, None)
    return list(q) if q else []


async def _on_sse(event_type: str, data: dict) -> None:
    """SSE 事件分发：reply 关联队首 future，file_send 入待取队列，session_closed 清残留。"""
    conv = data.get("conversation_id", "")
    if not conv:
        return
    if event_type == "reply":
        # 企业微信应用会话：走应用消息接口回发
        if conv.startswith(WECOM_CONV_PREFIX):
            content = data.get("content") or ""
            if content:
                asyncio.create_task(
                    _wecom_send_text(conv[len(WECOM_CONV_PREFIX):], content)
                )
            return
        # 微信客服会话：直接回发到普通微信用户
        if KF_ENABLED and conv.startswith(KF_CONV_PREFIX):
            content = data.get("content", "")
            if content:
                asyncio.create_task(
                    _kf_send_text(conv[len(KF_CONV_PREFIX):], content)
                )
            return
        q = _pending.get(conv)
        if not q:
            logger.debug("reply 无待回复请求，忽略 conv=%s", conv)
            return
        # FIFO：core 对同一 conversation 串行处理，回复按请求到达顺序弹出队首
        fut = q.popleft()
        if not q:
            _pending.pop(conv, None)
        if not fut.done():
            fut.set_result(data.get("content", ""))
    elif event_type == "file_send":
        if KF_ENABLED and conv.startswith(KF_CONV_PREFIX):
            uid_kf = conv[len(KF_CONV_PREFIX):]
            for fp in data.get("file_paths") or []:
                if not isinstance(fp, dict):
                    continue
                file_no = str(fp.get("file_no") or "")
                if not file_no:
                    continue
                asyncio.create_task(
                    _kf_send_core_file(uid_kf, file_no, str(fp.get("name") or ""))
                )
            return
        # 小程序：只登记，不在此消费 —— 等 reply 落地时随 /chat 响应一起返回；
        # 若 file_send 晚于 reply（少数时序），前端可用 /files/pending 兜底补取。
        _collect_file_send(conv, data)
    elif event_type == "session_closed":
        q = _pending.pop(conv, None)
        if q:
            for fut in q:
                if not fut.done():
                    fut.set_result("")
            logger.info("会话关闭清除待回复 %d 个: conv=%s", len(q), conv)
    else:
        logger.debug("SSE %s conv=%s (暂不转发)", event_type, conv)


async def _build_and_send(payload: dict) -> tuple[int, dict]:
    """后台发送入站消息到 core（结果透传，供调用方判断 core 可达性）。"""
    status, body = await _client.send_message(payload)
    if status == 200 and body.get("content"):
        # 信息性：同步完成回复也会走 SSE，此处不消费，避免双发。
        logger.debug("core 同步回复(SSE 亦会推送): %.80s", body.get("content", ""))
    return status, body


# ══════════════════════════════════════════════════════════════════════════════
# 微信客服桥接：普通微信用户 ⇄ Emily
#   入站：轮询 /cgi-bin/kf/sync_msg（token 传空，不依赖事件回调）
#   出站：core SSE reply → /cgi-bin/kf/send_msg
# ══════════════════════════════════════════════════════════════════════════════


async def _kf_access_token(force: bool = False) -> str:
    """获取并缓存微信客服 access_token（与应用 Secret 共用）。"""
    now = time.time()
    if not force and _kf_token["value"] and _kf_token["exp"] > now + 60:
        return _kf_token["value"]
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{KF_API}/cgi-bin/gettoken",
            params={"corpid": KF_CORP_ID, "corpsecret": KF_SECRET},
        ) as resp:
            data = await resp.json(content_type=None)
    if not data.get("access_token"):
        raise RuntimeError(f"gettoken 失败: {data}")
    _kf_token["value"] = data["access_token"]
    _kf_token["exp"] = now + int(data.get("expires_in", 7200))
    return _kf_token["value"]


async def _kf_call(path: str, payload: dict, retry: bool = True) -> dict:
    """调用微信客服 API，token 失效时刷新重试一次。"""
    token = await _kf_access_token()
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{KF_API}{path}", params={"access_token": token}, json=payload
        ) as resp:
            data = await resp.json(content_type=None)
    if data.get("errcode") in (40014, 42001) and retry:
        await _kf_access_token(force=True)
        return await _kf_call(path, payload, retry=False)
    return data


async def _kf_send_text(external_userid: str, content: str) -> None:
    """把 Emily 的回复发给普通微信用户。"""
    if not content.strip():
        return
    payload = {
        "touser": external_userid,
        "open_kfid": KF_OPEN_KFID,
        "msgtype": "text",
        "text": {"content": content[:2048]},
    }
    data = await _kf_call("/cgi-bin/kf/send_msg", payload)
    if data.get("errcode") == 0:
        logger.info("客服回复已发送: user=%s len=%d", external_userid, len(content))
    else:
        logger.warning("客服回复失败: user=%s err=%s", external_userid, data)


async def _kf_download_media(media_id: str) -> tuple[bytes, str, str]:
    """下载客服临时素材，返回 (内容, 文件名, Content-Type)。"""
    token = await _kf_access_token()
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{KF_API}/cgi-bin/media/get",
            params={"access_token": token, "media_id": media_id},
        ) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                data = await resp.json(content_type=None)
                # 视频素材在部分场景只返回 video_url
                vurl = data.get("video_url")
                if vurl:
                    async with session.get(vurl, timeout=timeout) as r2:
                        return (
                            await r2.read(),
                            "video.mp4",
                            r2.headers.get("Content-Type", "video/mp4"),
                        )
                raise RuntimeError(f"media/get 失败: {data}")
            body = await resp.read()
            disp = resp.headers.get("Content-Disposition", "")
            m = re.search(r'filename="?([^\";]+)"?', disp)
            name = unquote(m.group(1)) if m else ""
            return body, name, ctype


async def _kf_upload_media(data: bytes, filename: str, mtype: str) -> str:
    """上传临时素材到微信客服，返回 media_id。"""
    token = await _kf_access_token()
    form = aiohttp.FormData()
    form.add_field("media", data, filename=filename or f"{mtype}.bin")
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{KF_API}/cgi-bin/media/upload",
            params={"access_token": token, "type": mtype},
            data=form,
        ) as resp:
            d = await resp.json(content_type=None)
    if d.get("errcode") == 0 and d.get("media_id"):
        return d["media_id"]
    raise RuntimeError(f"media/upload 失败: {d}")


async def _kf_send_media(external_userid: str, mtype: str, media_id: str) -> None:
    """按素材类型发送图片/语音/视频/文件。"""
    payload = {
        "touser": external_userid,
        "open_kfid": KF_OPEN_KFID,
        "msgtype": mtype,
        mtype: {"media_id": media_id},
    }
    data = await _kf_call("/cgi-bin/kf/send_msg", payload)
    if data.get("errcode") == 0:
        logger.info("客服媒体已发送: user=%s type=%s", external_userid, mtype)
    else:
        logger.warning("客服媒体发送失败: user=%s type=%s err=%s",
                       external_userid, mtype, data)


def _kf_media_type_for(name: str) -> str:
    """按扩展名选择客服素材类型（仅 .amr 走 voice，其余音频按文件发）。"""
    ext = Path(name or "").suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
        return "image"
    if ext == ".amr":
        return "voice"
    if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        return "video"
    return "file"


def _kf_ext_for(mtype: str, ctype: str) -> str:
    """入站素材缺文件名时，按类型/Content-Type 兜底扩展名。"""
    by_ctype = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "audio/amr": ".amr", "audio/mpeg": ".mp3", "video/mp4": ".mp4",
    }
    if ctype in by_ctype:
        return by_ctype[ctype]
    return {"image": ".jpg", "voice": ".amr", "video": ".mp4", "file": ".bin"}[mtype]


async def _kf_send_core_file(uid: str, file_no: str, name: str) -> None:
    """Emily 主动发出的文件：从 core 取回 → 上传客服素材 → 发送。"""
    mtype = _kf_media_type_for(name)
    limit = KF_MEDIA_LIMITS[mtype]
    timeout = aiohttp.ClientTimeout(total=180)
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, headers=_client.auth_headers()
        ) as session:
            async with session.get(_client.file_download_url(file_no)) as resp:
                if resp.status != 200:
                    logger.warning("客服文件取回失败: file_no=%s status=%s",
                                   file_no, resp.status)
                    return
                data = await resp.read()
    except Exception as e:  # noqa: BLE001
        logger.warning("客服文件取回异常: file_no=%s %s", file_no, e)
        return

    if len(data) > limit:
        logger.warning(
            "客服文件超过上限已跳过: %s %d>%d (type=%s)", name, len(data), limit, mtype
        )
        return
    try:
        media_id = await _kf_upload_media(data, name or f"{file_no}", mtype)
    except Exception as e:  # noqa: BLE001
        logger.warning("客服素材上传失败: %s %s", name, e)
        return
    await _kf_send_media(uid, mtype, media_id)


async def _kf_handle_message(msg: dict) -> None:
    """单条客服消息 → core 入站。只处理微信客户发来的文本消息。"""
    if msg.get("origin") != 3:  # 3=微信客户发送
        return
    uid = (msg.get("external_userid") or "").strip()
    msgid = (msg.get("msgid") or "").strip()
    if not uid or not msgid:
        return
    now = time.time()
    if msgid in _kf_seen:
        return
    _kf_seen[msgid] = now
    for k in [k for k, v in _kf_seen.items() if v < now - 3600]:
        _kf_seen.pop(k, None)
    _sweep_expired()

    msgtype = (msg.get("msgtype") or "").strip()
    content = ""
    attachments: list[dict] = []

    if msgtype == "text":
        content = ((msg.get("text") or {}).get("content") or "").strip()
    elif msgtype in ("image", "voice", "video", "file"):
        node = msg.get(msgtype) or {}
        media_id = (node.get("media_id") or "").strip()
        if not media_id:
            logger.warning("客服媒体消息缺少 media_id: user=%s type=%s", uid, msgtype)
            return
        try:
            body, fname, ctype = await _kf_download_media(media_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("客服素材下载失败: user=%s type=%s %s", uid, msgtype, e)
            return
        if not body:
            return
        if not fname:
            fname = f"kf_{msgid}{_kf_ext_for(msgtype, ctype)}"
        fname = Path(fname).name
        token_ = uuid.uuid4().hex
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            target = UPLOAD_DIR / f"{token_}_{fname}"
            await asyncio.to_thread(target.write_bytes, body)
        except OSError as e:
            logger.warning("客服素材暂存失败: %s", e)
            return
        _upload_tokens[token_] = {
            "path": str(target),
            "name": fname,
            "exp": time.monotonic() + FILE_TOKEN_TTL,
        }
        att_type = {"image": 2, "file": 3, "voice": 4, "video": 5}[msgtype]
        attachments.append({
            "type": att_type,
            "url": f"{SELF_URL_INTERNAL}/attachment/{token_}",
            "file_name": fname,
            "file_size": len(body),
        })
        logger.info("客服素材已缓存: user=%s type=%s name=%s size=%d",
                    uid, msgtype, fname, len(body))
    elif msgtype == "location":
        loc = msg.get("location") or {}
        content = " ".join(x for x in [
            f"[位置] {loc.get('name') or ''}".strip(),
            (loc.get("address") or "").strip(),
            f"({loc.get('latitude')},{loc.get('longitude')})",
        ] if x)
    else:
        logger.info("客服消息类型 %s 暂未转发: user=%s", msgtype, uid)
        return

    if not content and not attachments:
        return

    payload = {
        "message_id": msgid,
        "platform": "wecom",
        "conversation_type": "private",
        "conversation_id": f"{KF_CONV_PREFIX}{uid}",
        "sender_id": uid,
        "sender_name": uid,
        "group_id": None,
        "group_name": None,
        "content": content,
        "is_at_bot": False,
        "mentioned_user_ids": [],
        "reply_to_message_id": None,
        "msg_type": attachments[0]["type"] if attachments else 1,
        "attachments": attachments,
        "event_id": str(uuid.uuid4()),
    }
    status, _ = await _build_and_send(payload)
    logger.info("客服入站已转发 core: user=%s status=%s type=%s text=%.40s",
                uid, status, msgtype, content)


async def _kf_poll_loop() -> None:
    """常驻轮询微信客服消息（游标持久化，重启不丢消息）。"""
    global _kf_cursor
    await asyncio.sleep(3)
    state_file = UPLOAD_DIR / "kf_cursor.json"
    if state_file.is_file():
        try:
            _kf_cursor = (json.loads(state_file.read_text()).get("cursor") or "")
            logger.info("微信客服游标已恢复: %s", _kf_cursor[:16])
        except Exception as e:  # noqa: BLE001
            logger.warning("读取客服游标失败: %s", e)
    first_round = not _kf_cursor
    while True:
        try:
            payload = {
                "token": "",
                "limit": 1000,
                "cursor": _kf_cursor,
                "voice_format": 0,
                "open_kfid": KF_OPEN_KFID,
            }
            data = await _kf_call("/cgi-bin/kf/sync_msg", payload)
            if data.get("errcode") == 0:
                msgs = data.get("msg_list") or []
                _kf_cursor = data.get("next_cursor") or _kf_cursor
                try:
                    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                    state_file.write_text(json.dumps({"cursor": _kf_cursor}))
                except OSError as e:
                    logger.debug("写入客服游标失败: %s", e)
                if first_round:
                    logger.info(
                        "微信客服桥接启动 kfid=%s，跳过历史 %d 条", KF_OPEN_KFID, len(msgs)
                    )
                    first_round = False
                else:
                    for m in msgs:
                        try:
                            await _kf_handle_message(m)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("处理客服消息异常: %s", e)
            else:
                logger.warning("sync_msg 失败: %s", data)
        except Exception as e:  # noqa: BLE001
            logger.warning("微信客服轮询异常: %s", e)
        await asyncio.sleep(KF_POLL_INTERVAL)


# ══════════════════════════════════════════════════════════════════════════════
# 企业微信应用回调：接收消息（GET 验证 + POST 收消息）
#   加解密规则与官方一致：sha1 签名 + AES-256-CBC（key = Base64Decode(AESKey+"=")）
# ══════════════════════════════════════════════════════════════════════════════


def _wecom_signature(timestamp: str, nonce: str, encrypt: str) -> str:
    """企微回调签名：token/timestamp/nonce/encrypt 字典序拼接后 sha1。"""
    raw = "".join(sorted([WECOM_CB_TOKEN, timestamp, nonce, encrypt]))
    return hashlib.sha1(raw.encode()).hexdigest()


def _wecom_decrypt(encrypt: str) -> str:
    """AES-256-CBC 解密，返回明文消息体（已校验 CorpID）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(WECOM_CB_AES_KEY + "=")
    if len(key) != 32:
        raise ValueError(f"EncodingAESKey 非法，解出 {len(key)} 字节")
    data = base64.b64decode(encrypt)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    raw = decryptor.update(data) + decryptor.finalize()
    pad = raw[-1]
    if pad < 1 or pad > 32:
        raise ValueError("PKCS7 填充非法")
    raw = raw[:-pad]
    msg_len = struct.unpack(">I", raw[16:20])[0]
    msg = raw[20:20 + msg_len].decode("utf-8")
    corp = raw[20 + msg_len:].decode("utf-8")
    if WECOM_CB_CORP_ID and corp != WECOM_CB_CORP_ID:
        raise ValueError(f"CorpID 不匹配: {corp}")
    return msg


async def _wecom_send_text(userid: str, content: str) -> None:
    """通过自建应用给企业成员发文本消息。"""
    agent_id = _wecom_state.get("agent_id", "")
    if not agent_id:
        logger.warning("企微应用未记录 AgentID，无法回复 user=%s", userid)
        return
    if not content.strip():
        return
    payload = {
        "touser": userid,
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": content[:2048]},
    }
    token = await _kf_access_token()
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{KF_API}/cgi-bin/message/send",
            params={"access_token": token},
            json=payload,
        ) as resp:
            data = await resp.json(content_type=None)
    if data.get("errcode") == 0:
        logger.info("企微回复已发送: user=%s len=%d", userid, len(content))
    else:
        logger.warning("企微回复失败: user=%s err=%s", userid, data)


async def _wecom_handle_inbound(xml_text: str) -> None:
    """解析企微消息 XML → core 入站。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("企微消息 XML 解析失败: %s", e)
        return

    def field(name: str) -> str:
        node = root.find(name)
        return (node.text or "").strip() if node is not None else ""

    agent_id = field("AgentID")
    if agent_id and not _wecom_state.get("agent_id"):
        _wecom_state["agent_id"] = agent_id
        logger.info("企微应用 AgentID 已记录: %s", agent_id)

    sender = field("FromUserName")
    msg_type = field("MsgType")
    content = field("Content")
    msg_id = field("MsgId") or f"{sender}-{field('CreateTime')}"

    if not sender:
        return
    if msg_type != "text":
        logger.info("企微消息类型 %s 暂未转发: user=%s", msg_type, sender)
        return

    now = time.time()
    if msg_id in _wecom_seen:
        return
    _wecom_seen[msg_id] = now
    for k in [k for k, v in _wecom_seen.items() if v < now - 3600]:
        _wecom_seen.pop(k, None)

    payload = {
        "message_id": msg_id,
        "platform": "wecom",
        "conversation_type": "private",
        "conversation_id": f"{WECOM_CONV_PREFIX}{sender}",
        "sender_id": sender,
        "sender_name": sender,
        "group_id": None,
        "group_name": None,
        "content": content,
        "is_at_bot": False,
        "mentioned_user_ids": [],
        "reply_to_message_id": None,
        "msg_type": 1,
        "attachments": [],
        "event_id": str(uuid.uuid4()),
    }
    status, _ = await _build_and_send(payload)
    logger.info("企微入站已转发 core: user=%s status=%s text=%.40s", sender, status, content)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_client.subscribe_sse(_on_sse))
    logger.info("SSE 订阅任务已启动 -> %s", CORE_BASE_URL)
    kf_task = None
    if KF_ENABLED:
        if not (KF_CORP_ID and KF_SECRET and KF_OPEN_KFID):
            logger.error(
                "微信客服已启用但缺少配置: corpid=%s secret=%s kfid=%s",
                bool(KF_CORP_ID), bool(KF_SECRET), bool(KF_OPEN_KFID),
            )
        else:
            kf_task = asyncio.create_task(_kf_poll_loop())
            logger.info("微信客服轮询已启动 kfid=%s interval=%.0fs",
                        KF_OPEN_KFID, KF_POLL_INTERVAL)
    try:
        yield
    finally:
        for t in (task, kf_task):
            if t is None:
                continue
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="wechat-gateway", version="0.1.0", lifespan=_lifespan)


@app.get("/wecom/callback", response_model=None)
async def wecom_verify(
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
):
    """企微保存回调 URL 时的 GET 验证：验签 → 解密 → 原样返回明文。"""
    if not (WECOM_CB_TOKEN and WECOM_CB_AES_KEY):
        return PlainTextResponse("wecom callback not configured", status_code=500)
    if _wecom_signature(timestamp, nonce, echostr) != msg_signature:
        return PlainTextResponse("invalid signature", status_code=403)
    try:
        plain = _wecom_decrypt(echostr)
    except Exception as e:  # noqa: BLE001
        logger.warning("企微回调解密失败: %s", e)
        return PlainTextResponse("decrypt failed", status_code=400)
    logger.info("企微回调验证成功")
    return PlainTextResponse(plain)


@app.post("/wecom/callback", response_model=None)
async def wecom_receive(
    request: Request,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
):
    """接收企微消息：验签 → 解密 → 异步转 core → 5 秒内返回 success。"""
    body = (await request.body()).decode("utf-8", "ignore")
    try:
        root = ET.fromstring(body)
        encrypt = (root.findtext("Encrypt") or "").strip()
    except Exception:  # noqa: BLE001
        return PlainTextResponse("bad xml", status_code=400)
    if not encrypt or _wecom_signature(timestamp, nonce, encrypt) != msg_signature:
        return PlainTextResponse("invalid signature", status_code=403)
    try:
        plain = _wecom_decrypt(encrypt)
    except Exception as e:  # noqa: BLE001
        logger.warning("企微消息解密失败: %s", e)
        return PlainTextResponse("decrypt failed", status_code=400)
    asyncio.create_task(_wecom_handle_inbound(plain))
    return PlainTextResponse("success")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "core": CORE_BASE_URL}


@app.post("/login", response_model=None)
async def login(body: LoginIn):
    """用 wx.login 的 code 换 openid，签发网关自有令牌。"""
    if not WXMP_APP_ID or not WXMP_APP_SECRET:
        return JSONResponse(
            {"error": "网关未配置 WXMP_APP_ID / WXMP_APP_SECRET"}, status_code=500
        )
    if not body.code:
        return JSONResponse({"error": "缺少 code"}, status_code=400)

    params = {
        "appid": WXMP_APP_ID,
        "secret": WXMP_APP_SECRET,
        "js_code": body.code,
        "grant_type": "authorization_code",
    }
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.weixin.qq.com/sns/jscode2session", params=params
            ) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:  # noqa: BLE001
        logger.warning("jscode2session 请求失败: %s", e)
        return JSONResponse({"error": f"调用微信登录失败：{e}"}, status_code=502)

    if not data.get("openid"):
        logger.warning("jscode2session 返回异常: %s", data)
        return JSONResponse({"error": "微信登录失败", "detail": data}, status_code=401)

    uid = _sanitize_dev_id("wx_" + data["openid"])
    return {"token": _issue_token(uid), "dev_id": uid, "expires_in": TOKEN_TTL}


@app.post("/chat", response_model=None)
async def chat(
    body: ChatIn,
    x_dev_user: str | None = Header(default=None),
    x_dev_token: str | None = Header(default=None),
):
    """小程序聊天入口：返回 {"reply", "conversation_id", "files"}。

    files 为本轮 Emily 主动发出的文件（[{token, name, url}]），前端凭 url 下载。
    """
    content = (body.message or "").strip()
    attachments = [a for a in (body.attachments or []) if isinstance(a, dict)]
    if not content and not attachments:
        return JSONResponse({"error": "message 与 attachments 不能同时为空"}, status_code=400)

    dev_id = _identity(x_dev_user, x_dev_token)
    if not dev_id:
        return JSONResponse({"error": "未登录或登录已过期，请重新进入小程序"}, status_code=401)
    conv = f"wxmp_{dev_id}"
    payload = {
        "message_id": "",
        "platform": "wxmp",
        "conversation_type": "private",
        "conversation_id": conv,
        "sender_id": dev_id,
        "sender_name": dev_id,
        "group_id": None,
        "group_name": None,
        "content": content,
        "is_at_bot": False,
        "mentioned_user_ids": [],
        "reply_to_message_id": None,
        "msg_type": _msg_type_of(attachments),
        "attachments": attachments,
        "event_id": str(uuid.uuid4()),
    }

    fut = _register(conv)
    send_task = asyncio.create_task(_build_and_send(payload))
    _bg_tasks.add(send_task)
    send_task.add_done_callback(_bg_tasks.discard)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + REPLY_TIMEOUT
    try:
        while not fut.done():
            remain = deadline - loop.time()
            if remain <= 0:
                break
            if send_task.done():
                status, _body = send_task.result()
                if status == 0:
                    _unregister(conv, fut)
                    logger.error("core 不可达: conv=%s", conv)
                    return JSONResponse(
                        {"error": "emily-core 不可达，请确认 18080 已启动", "conversation_id": conv},
                        status_code=502,
                    )
                wait_for = [fut]
            else:
                wait_for = [fut, send_task]
            done, _ = await asyncio.wait(
                wait_for, timeout=remain, return_when=asyncio.FIRST_COMPLETED
            )
            if fut in done:
                break
    finally:
        # 超时/异常时若仍排队则摘除；后台 POST 任务交由 _bg_tasks 收尾
        if not fut.done():
            _unregister(conv, fut)

    # 回复落地后再取待下载文件：file_send 通常先于 reply 到达，此处一并交付
    files = _drain_files(conv)

    if fut.done() and not fut.cancelled():
        reply = fut.result() or ""
        return {"reply": reply, "conversation_id": conv, "files": files}

    logger.warning("回复等待超时 %.0fs: conv=%s", REPLY_TIMEOUT, conv)
    return JSONResponse(
        {
            "error": "Emily 处理超时，请稍后重试",
            "conversation_id": conv,
            "reply": "",
            "files": files,
        },
        status_code=504,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 出站文件：Emily → 小程序
#   file_send 事件登记令牌 → 小程序凭 /files/{token} 下载 → 网关流式转发 Core
#   网关持有 Core Token，端上只拿一次性 token，Core 凭据不下发
# ══════════════════════════════════════════════════════════════════════════════


def _sweep_expired() -> None:
    """清理过期令牌（顺带在文件请求时执行，避免引入后台任务）。"""
    now = time.monotonic()
    for token in [t for t, v in _file_tokens.items() if v.get("exp", 0) < now]:
        _file_tokens.pop(token, None)
    for token in [t for t, v in _upload_tokens.items() if v.get("exp", 0) < now]:
        spec = _upload_tokens.pop(token, None)
        if spec:
            try:
                Path(spec["path"]).unlink(missing_ok=True)
            except OSError as e:
                logger.debug("清理过期暂存附件失败 %s: %s", spec.get("path"), e)


def _content_disposition(name: str) -> str:
    """构造兼容非 ASCII 文件名的 Content-Disposition（RFC 5987）。"""
    from urllib.parse import quote

    ascii_fallback = re.sub(r"[^A-Za-z0-9._-]", "_", name or "") or "file"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(name)}"


@app.get("/files/pending")
async def files_pending(
    x_dev_user: str | None = Header(default=None),
    x_dev_token: str | None = Header(default=None),
) -> dict:
    """兜底补取：file_send 晚于 reply 到达时，前端可再拉一次。"""
    dev_id = _identity(x_dev_user, x_dev_token)
    if not dev_id:
        return JSONResponse({"error": "未登录"}, status_code=401)
    conv = f"wxmp_{dev_id}"
    return {"files": _drain_files(conv)}


@app.get("/files/{token}", response_model=None)
async def files_download(token: str):
    """把 Core 的归档文件流式转发给小程序。

    先完成状态判定再开流 —— 流一旦开始就无法再改 HTTP 状态码，
    故 Core 返回非 200 时直接以该状态回错，不进 StreamingResponse。
    """
    _sweep_expired()
    spec = _file_tokens.get(token)
    if not spec or spec.get("exp", 0) < time.monotonic():
        _file_tokens.pop(token, None)
        return JSONResponse({"error": "下载链接不存在或已过期"}, status_code=410)

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15.0, sock_read=120.0)
    session = aiohttp.ClientSession(timeout=timeout, headers=_client.auth_headers())
    try:
        resp = await session.get(_client.file_download_url(spec["file_no"]))
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        await session.close()
        logger.warning("转发 Core 下载失败 file_no=%s: %s", spec["file_no"], e)
        return JSONResponse({"error": f"下载失败：{e}"}, status_code=502)

    if resp.status != 200:
        detail = await resp.text()
        resp.release()
        await session.close()
        logger.warning("Core 拒绝下载 file_no=%s status=%s body=%.200s",
                       spec["file_no"], resp.status, detail)
        return JSONResponse(
            {"error": "文件不可下载", "detail": detail[:200]}, status_code=resp.status
        )

    async def _stream():
        try:
            async for chunk in resp.content.iter_chunked(64 * 1024):
                yield chunk
        finally:
            resp.release()
            await session.close()

    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(spec.get("name") or "file")},
    )


# ══════════════════════════════════════════════════════════════════════════════
# 入站文件：小程序 → Emily
#   /upload 暂存 → Core 按 /attachment/{token} 回拉 → 归档进 files 表
# ══════════════════════════════════════════════════════════════════════════════


@app.post("/upload", response_model=None)
async def upload_file(
    file: UploadFile = File(...),
    x_dev_user: str | None = Header(default=None),
    x_dev_token: str | None = Header(default=None),
):
    """暂存小程序选中的文件，返回可被 Core 回拉的附件引用。

    返回的 url 指向本网关（SELF_URL_INTERNAL），因为回拉方是 Core 容器，
    不是小程序；小程序只需把该 url 原样放进 /chat 的 attachments 里。
    """
    _sweep_expired()
    dev_id = _identity(x_dev_user, x_dev_token)
    if not dev_id:
        return JSONResponse({"error": "未登录"}, status_code=401)
    data = await file.read()
    if not data:
        return JSONResponse({"error": "文件内容为空"}, status_code=400)
    if len(data) > MAX_FILE_BYTES:
        return JSONResponse(
            {"error": f"文件超过上限 {MAX_FILE_BYTES // (1024 * 1024)}MB"}, status_code=413
        )

    name = Path(file.filename or "file").name or "file"
    token = uuid.uuid4().hex
    target = UPLOAD_DIR / f"{token}_{name}"
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)
    except OSError as e:
        logger.exception("暂存上传文件失败: %s", name)
        return JSONResponse({"error": f"暂存失败：{e}"}, status_code=500)

    _upload_tokens[token] = {
        "path": str(target), "name": name, "exp": time.monotonic() + FILE_TOKEN_TTL,
    }
    logger.info("上传已暂存: user=%s name=%s size=%d", dev_id, name, len(data))
    return {
        "token": token,
        "file_name": name,
        "file_size": len(data),
        "type": _attachment_type_of(name),
        "url": f"{SELF_URL_INTERNAL}/attachment/{token}",
    }


@app.get("/attachment/{token}", response_model=None)
async def attachment(token: str):
    """向 Core 提供暂存附件（token 为 32 位十六进制，杜绝路径穿越）。"""
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return JSONResponse({"error": "非法附件标识"}, status_code=400)
    spec = _upload_tokens.get(token)
    if not spec or spec.get("exp", 0) < time.monotonic():
        _upload_tokens.pop(token, None)
        return JSONResponse({"error": "附件不存在或已过期"}, status_code=404)
    path = Path(spec["path"])
    if not path.is_file():
        return JSONResponse({"error": "附件已被清理"}, status_code=404)
    return FileResponse(str(path), filename=spec["name"])
