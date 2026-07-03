"""EmysTester 核心测试引擎。

通过复用 emily_agent 插件的 EmilyApiClient（HTTP）和内置轻量 SSE 监听器，
向 emily-core 容器发送模拟消息并观察回复。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from config_loader import get_core_url, get_api_token, get_pg_config, _PROJECT_ROOT

_logger = logging.getLogger("emys.tester")


class EmysTester:
    """EmysTester —— Emily Core 生产环境实战测试器。

    通过 HTTP + SSE 与 emily-core 容器通信，模拟 astrbot 插件收发消息。
    连接生产 PostgreSQL 数据库用于查询诊断（get_messages / get_users）。

    推荐用法::

        with EmysTester() as emy:
            reply = emy.send_sync("你好")
            print(reply.content)
    """

    def __init__(
        self,
        *,
        use_llm: bool = False,
        capture_progress: bool = True,
    ):
        """初始化测试器（不启动，调用 start() 或使用上下文管理器）。

        Args:
            use_llm: 是否启用 LLM。当前 emily-core 的 LLM 配置由服务端 .env 控制，
                     此参数保留用于未来扩展。
            capture_progress: 是否自动捕获 on_progress 回调消息。
        """
        self._use_llm: bool = use_llm
        self._capture_progress: bool = capture_progress

        # 运行时状态
        self._client = None        # EmilyApiClient
        self._started: bool = False
        self._progress_log: list[dict] = []
        self._last_reply = None
        self._sent_files: list[dict] = []

        # SSE 相关（每个 send_message 调用内管理 SSE 生命周期）
        self._reply_events: dict[str, asyncio.Event] = {}
        self._pending_replies: dict[str, dict] = {}
        self._pending_progress: dict[str, list[str]] = {}
        self._pending_files: dict[str, list[dict]] = {}

        # PostgreSQL 引擎（惰性初始化，用于 get_messages / get_users）
        self._pg_engine = None

    # ── 生命周期 ──

    def start(self) -> "EmysTester":
        """启动测试器：创建 HTTP 客户端 + SSE 监听器。幂等。"""
        if self._started:
            return self

        # 导入 emily_agent 插件的 EmilyApiClient（复用，不重复造轮子）
        from adapters.api_client import EmilyApiClient

        core_url = get_core_url()
        api_token = get_api_token()
        self._client = EmilyApiClient(
            base_url=core_url,
            api_token=api_token,
            timeout=120.0,
        )

        _logger.info("EmysTester started, core=%s", core_url)
        self._started = True
        return self

    def stop(self) -> None:
        """停止测试器：关闭 HTTP 客户端。安全地多次调用。"""
        if not self._started:
            return

        # 关闭 HTTP 客户端
        if self._client is not None:
            try:
                asyncio.run(self._client.close())
            except Exception:
                pass
            self._client = None

        # 关闭 PG 引擎
        if self._pg_engine is not None:
            self._pg_engine.dispose()
            self._pg_engine = None

        self._started = False
        self._progress_log.clear()
        self._last_reply = None
        self._sent_files.clear()
        self._reply_events.clear()
        self._pending_replies.clear()
        self._pending_progress.clear()
        self._pending_files.clear()

    # ── 上下文管理器 ──

    def __enter__(self) -> "EmysTester":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    async def __aenter__(self) -> "EmysTester":
        self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    # ── SSE 监听 ──

    async def _listen_sse(self) -> None:
        """SSE 事件流监听器（后台任务）。

        解析 emily-core 的 SSE 出站事件流，分发到对应 conversation_id 的等待者。
        事件类型：reply / progress / file_send / session_closed
        """
        import aiohttp

        sse_url = f"{get_core_url()}/api/v1/events/outbound"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(sse_url) as resp:
                    _logger.info("SSE connected: %s (status=%d)", sse_url, resp.status)
                    event_type = "message"
                    data_buf: list[str] = []
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
                        if line == "":
                            # 帧结束 → 分发
                            if data_buf:
                                await self._dispatch_sse(event_type, "\n".join(data_buf))
                            event_type = "message"
                            data_buf = []
                            continue
                        if line.startswith(":"):
                            continue  # 心跳/注释
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_buf.append(line[len("data:"):].strip())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _logger.warning("SSE connection lost: %s", e)

    async def _dispatch_sse(self, event_type: str, data_str: str) -> None:
        """分发单个 SSE 事件。"""
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            _logger.warning("SSE bad data for %s: %s", event_type, data_str[:120])
            return

        conv_id = data.get("conversation_id", "")

        if event_type == "reply":
            # 存储回复，通知等待者
            self._pending_replies[conv_id] = data
            event = self._reply_events.get(conv_id)
            if event is not None:
                event.set()

        elif event_type == "progress":
            # 存储进度消息
            text = data.get("content", "")
            if text:
                self._progress_log.append({"text": text, "timestamp": time.time()})
                if conv_id not in self._pending_progress:
                    self._pending_progress[conv_id] = []
                self._pending_progress[conv_id].append(text)

        elif event_type == "file_send":
            # 存储文件发送事件
            file_paths = data.get("file_paths", [])
            caption = data.get("caption", "")
            for fp in file_paths:
                self._sent_files.append({
                    "path": fp,
                    "name": Path(fp).name,
                    "caption": caption,
                })
            if conv_id not in self._pending_files:
                self._pending_files[conv_id] = []
            self._pending_files[conv_id].extend(
                {"path": fp, "name": Path(fp).name, "caption": caption}
                for fp in file_paths
            )

        elif event_type == "session_closed":
            _logger.debug("SSE session_closed: conv=%s", conv_id)

    # ── 核心方法 ──

    async def send_message(
        self,
        text: str,
        *,
        sender_id: str | None = None,
        sender_name: str | None = None,
        platform: str = "napcat",
        conversation_type: str = "private",
        conversation_id: str | None = None,
        group_id: str | None = None,
        group_name: str | None = None,
        is_at_bot: bool = False,
        message_id: str | None = None,
        event_id: str | None = None,
        on_progress: Callable | None = None,
        attachments: list[dict] | None = None,
    ) -> "ReplyMessage | None":  # noqa: F821
        """发送一条模拟消息并返回 Emily 的回复。

        Args:
            text: 消息文本内容。
            sender_id: 发送者 ID，None 则自动生成。
            sender_name: 发送者昵称，None 则使用 sender_id。
            platform: 平台标识，默认 "simulator"。
            conversation_type: "private" 或 "group"。
            conversation_id: 会话 ID，None 则自动推导。
            group_id: 群号（群聊时有效），None 则自动生成。
            group_name: 群名。
            is_at_bot: 是否 @了机器人（群聊时决定是否接管）。
            message_id: 消息 ID，None 则自动生成。
            event_id: 事件指纹（用于去重），None 则自动生成。
            on_progress: 自定义进度回调 `async fn(text: str)`。
            attachments: 附件列表，模拟 QQ/微信文件消息。
                [{"type": 2, "url": "file:///...", "file_name": "..."}, ...]
                type: 2=image 3=file 4=voice 5=video

        Returns:
            ReplyMessage: Emily 的回复；None 表示不接管。

        Raises:
            RuntimeError: 如果未调用 start()。
        """
        self._ensure_started()

        from adapters.standard.message import StandardMessage

        # 重置 aiohttp session（确保在当前 event loop 中创建，避免 Closed event loop 错误）
        if self._client._session is not None and not self._client._session.closed:
            await self._client._session.close()
        self._client._session = None

        # 自动生成 ID
        sid = sender_id or f"user_{uuid.uuid4().hex[:8]}"
        sname = sender_name or sid
        mid = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        eid = event_id or f"evt_{uuid.uuid4().hex[:12]}"

        # 对话 ID 推导
        if conversation_id is None:
            if conversation_type == "group":
                conversation_id = group_id or f"group_{uuid.uuid4().hex[:8]}"
            else:
                conversation_id = sid

        # 群 ID 推导
        gid = group_id
        if conversation_type == "group" and gid is None:
            gid = conversation_id

        # 构建 StandardMessage（复用插件数据模型，与 emily_agent 行为一致）
        atts = list(attachments) if attachments else []
        msg_type = 1  # text
        if atts:
            first_type = atts[0].get("type", 3)
            msg_type = first_type if first_type in (2, 3, 4, 5) else 3

        msg = StandardMessage(
            message_id=mid,
            platform=platform,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            sender_id=sid,
            sender_name=sname,
            group_id=gid,
            group_name=group_name,
            content=text,
            is_at_bot=is_at_bot,
            msg_type=msg_type,
            attachments=atts,
        )

        # 清空上次结果
        self._sent_files.clear()

        # 启动 SSE 监听任务（本次调用范围内）
        sse_task = asyncio.ensure_future(self._listen_sse())

        # 为当前 conversation_id 注册 SSE reply 等待
        reply_event = asyncio.Event()
        self._reply_events[conversation_id] = reply_event

        try:
            # 发送消息（复用 EmilyApiClient）
            reply = await self._client.send_message(msg)

            if reply is not None:
                # 同步回复（200）
                self._last_reply = reply
                self._reply_events.pop(conversation_id, None)
                return reply

            # 异步处理（204）：等待 SSE reply 事件
            try:
                await asyncio.wait_for(reply_event.wait(), timeout=120.0)
            except asyncio.TimeoutError:
                _logger.warning("SSE reply timeout for conv=%s", conversation_id)
                self._reply_events.pop(conversation_id, None)
                return None
        finally:
            # 清理 SSE 监听
            sse_task.cancel()
            try:
                await sse_task
            except asyncio.CancelledError:
                pass

        # 获取 SSE 回复
        self._reply_events.pop(conversation_id, None)
        sse_data = self._pending_replies.pop(conversation_id, None)
        if sse_data is None:
            return None

        from adapters.standard.reply import ReplyMessage
        sse_reply = ReplyMessage(
            conversation_id=sse_data.get("conversation_id", conversation_id),
            content=sse_data.get("content", ""),
            reply_to_message_id=sse_data.get("reply_to_message_id"),
        )
        self._last_reply = sse_reply
        return sse_reply

    # ── 便捷方法 ──

    async def send_group(
        self,
        text: str,
        *,
        sender_id: str = "user_001",
        sender_name: str = "测试用户",
        group_id: str = "group_001",
        group_name: str = "测试群",
        conversation_id: str | None = None,
        is_at_bot: bool = True,
        **kwargs,
    ) -> "ReplyMessage | None":  # noqa: F821
        """发送群聊消息的便捷方法。默认 @了机器人（会被接管）。"""
        return await self.send_message(
            text=text,
            sender_id=sender_id,
            sender_name=sender_name,
            conversation_type="group",
            conversation_id=conversation_id or group_id,
            group_id=group_id,
            group_name=group_name,
            is_at_bot=is_at_bot,
            **kwargs,
        )

    async def send_private(
        self,
        text: str,
        *,
        sender_id: str = "user_001",
        sender_name: str = "测试用户",
        **kwargs,
    ) -> "ReplyMessage | None":  # noqa: F821
        """发送私聊消息的便捷方法。私聊消息总是会被接管。"""
        return await self.send_message(
            text=text,
            sender_id=sender_id,
            sender_name=sender_name,
            conversation_type="private",
            **kwargs,
        )

    def send_sync(
        self, text: str, **kwargs
    ) -> "ReplyMessage | None":  # noqa: F821
        """同步版本的 send_message()，内部使用 asyncio.run()。

        适合交互式 Python 会话或非异步脚本。
        注意：不能在有运行中的 event loop 的上下文中调用。
        """
        return asyncio.run(self.send_message(text, **kwargs))

    # ── 检查方法 ──

    def get_messages(
        self, conversation_id: str | None = None, limit: int = 10
    ) -> list[dict]:
        """查询最近持久化的消息（直接 PG 查询）。

        Args:
            conversation_id: 可选，按会话过滤。
            limit: 返回条数上限。

        Returns:
            list[dict]: 含 id, event_id, content, sender_name, takeover, status, created_at。
        """
        self._ensure_started()
        engine = self._get_pg_engine()

        from sqlalchemy import text

        with engine.connect() as conn:
            if conversation_id:
                rows = conn.execute(
                    text(
                        "SELECT id, event_id, content, sender_name, takeover, "
                        "status, created_at FROM messages "
                        "WHERE conversation_id = :cid "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"cid": conversation_id, "lim": limit},
                )
            else:
                rows = conn.execute(
                    text(
                        "SELECT id, event_id, content, sender_name, takeover, "
                        "status, created_at FROM messages "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ),
                    {"lim": limit},
                )
            return [
                {
                    "id": r.id,
                    "event_id": r.event_id,
                    "content": r.content,
                    "sender_name": r.sender_name or "",
                    "takeover": r.takeover,
                    "status": r.status or "",
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ]

    def get_users(self) -> list[dict]:
        """查询所有已注册用户及其 IM 绑定（直接 PG 查询）。

        Returns:
            list[dict]: 含 user_id, username, real_name, im_platform, im_user_id。
        """
        self._ensure_started()
        engine = self._get_pg_engine()

        from sqlalchemy import text

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT u.id AS user_id, u.username, u.real_name, "
                    "b.im_platform, b.im_user_id "
                    "FROM users u "
                    "LEFT JOIN user_im_bindings b ON u.id = b.user_id "
                    "ORDER BY u.created_at DESC"
                )
            )
            return [
                {
                    "user_id": r.user_id,
                    "username": r.username or "",
                    "real_name": r.real_name or "",
                    "im_platform": r.im_platform or "",
                    "im_user_id": r.im_user_id or "",
                }
                for r in rows
            ]

    @contextmanager
    def get_db_session(self):
        """获取原始 SQLAlchemy 连接的上下文管理器。

        用法::

            with emy.get_db_session() as conn:
                rows = conn.execute(text("SELECT * FROM events"))
        """
        self._ensure_started()
        engine = self._get_pg_engine()
        with engine.connect() as conn:
            yield conn

    def reset_conversation(self, conversation_id: str) -> None:
        """终止指定会话（调用 emily-core 的 session/terminate）。

        Args:
            conversation_id: 要终止的会话 ID。
        """
        self._ensure_started()
        try:
            asyncio.run(self._client.terminate_session(conversation_id))
        except Exception as e:
            _logger.warning("reset_conversation failed: %s", e)

    @property
    def captured_progress(self) -> list[str]:
        """已捕获的前导消息文本列表。"""
        return [entry["text"] for entry in self._progress_log]

    def clear_progress(self) -> None:
        """清空前导消息捕获日志。"""
        self._progress_log.clear()

    @property
    def last_reply(self):
        """最近一次 send_message 的返回值。"""
        return self._last_reply

    @property
    def sent_files(self) -> list[dict]:
        """最近一次 send_message 中 Agent 通过 send_file 工具发出的文件。

        Returns:
            [{"path": "/abs/path", "name": "图纸.dwg", "caption": "..."}, ...]
        """
        return list(self._sent_files)

    def clear_sent_files(self) -> None:
        """清空已发送文件列表。"""
        self._sent_files.clear()

    @property
    def client(self):
        """EmilyApiClient 实例（只读）。"""
        return self._client

    @property
    def is_llm_available(self) -> bool:
        """检查 LLM 是否可用（通过 health check）。"""
        return bool(os.environ.get("EMILY_LLM_API_KEY"))

    # ── 内部方法 ──

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError(
                "EmysTester 尚未启动，请先调用 start() 或使用上下文管理器"
            )

    def _get_pg_engine(self):
        """惰性创建 PG SQLAlchemy 引擎。"""
        if self._pg_engine is None:
            from sqlalchemy import create_engine
            pg = get_pg_config()
            db_url = (
                f"postgresql://{pg['user']}:{pg['password']}"
                f"@{pg['host']}:{pg['port']}/{pg['db']}"
            )
            self._pg_engine = create_engine(db_url, echo=False)
        return self._pg_engine


# ═══════════════════════════════════════════════════════════════════════════════
# ReplyMessage 类型引用（从插件导入，用于类型注解）
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from adapters.standard.reply import ReplyMessage
except ImportError:
    ReplyMessage = None  # type: ignore
