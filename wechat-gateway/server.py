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
import logging
import os
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from fastapi import FastAPI, File, Header, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from core_client import CoreApiClient, load_env_token

logger = logging.getLogger("wxmp.server")

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
        # 只登记，不在此消费 —— 等 reply 落地时随 /chat 响应一起返回；
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_client.subscribe_sse(_on_sse))
    logger.info("SSE 订阅任务已启动 -> %s", CORE_BASE_URL)
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="wechat-gateway", version="0.1.0", lifespan=_lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "core": CORE_BASE_URL}


@app.post("/chat", response_model=None)
async def chat(
    body: ChatIn,
    x_dev_user: str | None = Header(default=None),
):
    """小程序聊天入口：返回 {"reply", "conversation_id", "files"}。

    files 为本轮 Emily 主动发出的文件（[{token, name, url}]），前端凭 url 下载。
    """
    content = (body.message or "").strip()
    attachments = [a for a in (body.attachments or []) if isinstance(a, dict)]
    if not content and not attachments:
        return JSONResponse({"error": "message 与 attachments 不能同时为空"}, status_code=400)

    dev_id = _sanitize_dev_id(x_dev_user or "dev-user")
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
async def files_pending(x_dev_user: str | None = Header(default=None)) -> dict:
    """兜底补取：file_send 晚于 reply 到达时，前端可再拉一次。"""
    conv = f"wxmp_{_sanitize_dev_id(x_dev_user or 'dev-user')}"
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
):
    """暂存小程序选中的文件，返回可被 Core 回拉的附件引用。

    返回的 url 指向本网关（SELF_URL_INTERNAL），因为回拉方是 Core 容器，
    不是小程序；小程序只需把该 url 原样放进 /chat 的 attachments 里。
    """
    _sweep_expired()
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
    logger.info("上传已暂存: user=%s name=%s size=%d", x_dev_user or "-", name, len(data))
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
