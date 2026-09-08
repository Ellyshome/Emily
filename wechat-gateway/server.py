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
import uuid
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core_client import CoreApiClient, load_env_token

logger = logging.getLogger("wxmp.server")

CORE_BASE_URL = os.environ.get("WXMP_CORE_URL", "http://127.0.0.1:18080")
REPLY_TIMEOUT = float(os.environ.get("WXMP_REPLY_TIMEOUT", "55"))  # 秒；配合前端 wx.request timeout 60s
CORE_POST_TIMEOUT = 10.0  # 秒；POST 本身不承载回复，短超时即可

# per-conversation 待回复队列：conversation_id -> deque[Future[str]]
_pending: dict[str, deque[asyncio.Future]] = {}
# 后台未完成的 POST 任务强引用，防 GC（完成后自清理）
_bg_tasks: set[asyncio.Task] = set()

_client = CoreApiClient(
    base_url=CORE_BASE_URL,
    api_token=load_env_token(),
    request_timeout=CORE_POST_TIMEOUT,
)


class ChatIn(BaseModel):
    """小程序 /chat 请求体（与前端 askBackend 对齐）。"""

    message: str = ""
    history: list[dict] | None = Field(default=None)


def _sanitize_dev_id(dev_id: str) -> str:
    """收敛外部传入的 dev-id：仅保留字母数字与 _-，限长 64。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", dev_id or "")
    return cleaned[:64] or "dev-user"


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


async def _on_sse(event_type: str, data: dict) -> None:
    """SSE 事件分发：reply 关联队首 future，session_closed 清残留。"""
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
    """小程序聊天入口：返回 {"reply": "...", "conversation_id": "..."}。"""
    content = (body.message or "").strip()
    if not content:
        return JSONResponse({"error": "message 不能为空"}, status_code=400)

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
        "msg_type": 1,
        "attachments": [],
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

    if fut.done() and not fut.cancelled():
        reply = fut.result() or ""
        return {"reply": reply, "conversation_id": conv}

    logger.warning("回复等待超时 %.0fs: conv=%s", REPLY_TIMEOUT, conv)
    return JSONResponse(
        {"error": "Emily 处理超时，请稍后重试", "conversation_id": conv, "reply": ""},
        status_code=504,
    )
