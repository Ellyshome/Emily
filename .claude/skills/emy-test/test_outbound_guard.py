"""插件出站守卫单元测试（纯本地，不连容器）。

覆盖 data/plugins/emily_agent/adapters/sse_listener.py 的几道闸门：
  A. 测试会话：Core 下发 test_session=True → reply/进度/文件都不发真实 IM；
     字段缺失或 False → 按真实投递（不误伤）
  B. 事件映射 TTL：会话→IM event 映射过期判废 → 不发真实 IM
  C. 正常路径不误伤：真实会话 + 有效映射 → 照常投递
  D. session_closed 清理映射：清理后再来出站事件不再投递

运行：
    python -m pytest .claude/skills/emy-test/test_outbound_guard.py -q
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# 插件目录（adapters/*）：.claude/skills/emy-test → 项目根 → data/plugins/emily_agent
_PLUGIN_DIR = Path(__file__).resolve().parents[3] / "data" / "plugins" / "emily_agent"
sys.path.insert(0, str(_PLUGIN_DIR))

from adapters.sse_listener import SSEListener  # noqa: E402  (需先补 sys.path)


class _FakeOutbound:
    """记录出站调用，替代 AstrBotOutboundSender。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.progress: list[str] = []
        self.files: list[dict] = []

    async def send(self, reply, event):
        self.sent.append({"conv": reply.conversation_id, "content": reply.content})

    async def send_progress(self, text, event):
        self.progress.append(text)

    async def send_files(self, file_paths, event, caption=""):
        self.files.append({"paths": file_paths, "caption": caption})


def _reply_data(conv: str, **extra) -> dict:
    return {"conversation_id": conv, "content": "回复内容", **extra}


def _listener(**kwargs) -> tuple[SSEListener, _FakeOutbound]:
    outbound = _FakeOutbound()
    return SSEListener(outbound, **kwargs), outbound


# ══════════════════════════════════════════════════════════════════════════════
# A. 测试会话判定（Core 单点下发 test_session）
# ══════════════════════════════════════════════════════════════════════════════

def test_test_session_reply_not_sent():
    """Core 标记 test_session=True → 只走 SSE，不发真实 IM。"""
    listener, outbound = _listener()
    listener.register("test_guard_01", object())
    asyncio.run(listener._handle_reply(
        _reply_data("test_guard_01", platform="napcat", test_session=True)))
    assert outbound.sent == []


def test_test_session_progress_and_file_not_sent():
    """测试会话的进度 / 文件同样不外发。"""
    listener, outbound = _listener()
    listener.register("test_guard_01", object())
    asyncio.run(listener._handle_progress(
        {"conversation_id": "test_guard_01", "content": "处理中", "test_session": True}))
    asyncio.run(listener._handle_file_send({
        "conversation_id": "test_guard_01", "test_session": True,
        "file_paths": [{"path": "/tmp/a.pdf", "name": "a.pdf"}],
    }))
    assert outbound.progress == [] and outbound.files == []


def test_flag_false_means_real_delivery():
    """Core 显式 test_session=False（前缀未命中）→ 按真实投递处理。"""
    listener, outbound = _listener()
    listener.register("123456010", object())
    asyncio.run(listener._handle_reply(
        _reply_data("123456010", platform="napcat", test_session=False)))
    assert len(outbound.sent) == 1


def test_flag_missing_means_real_delivery():
    """字段缺失（旧版 Core / 非会话出站）→ 不拦截，避免误伤真实投递。"""
    listener, outbound = _listener()
    listener.register("123456010", object())
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="simulator")))
    assert len(outbound.sent) == 1


# ══════════════════════════════════════════════════════════════════════════════
# B. 事件映射 TTL
# ══════════════════════════════════════════════════════════════════════════════

def test_registry_ttl_expires():
    """映射过期（残留 event）→ 判废且不外发，并从映射表移除。"""
    registry: dict = {}
    listener, outbound = _listener(event_registry=registry, registry_ttl_seconds=0.05)
    listener.register("123456010", object())
    time.sleep(0.08)
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="napcat")))
    assert outbound.sent == []
    assert "123456010" not in registry


def test_registry_ttl_disabled():
    """TTL=0（仅调试）→ 不判废，照常投递。"""
    listener, outbound = _listener(registry_ttl_seconds=0)
    listener.register("123456010", object())
    time.sleep(0.05)
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="napcat")))
    assert len(outbound.sent) == 1


def test_registry_refresh_resets_age():
    """同一会话再次收到入站消息 → 重新登记，年龄归零后仍可投递。"""
    listener, outbound = _listener(registry_ttl_seconds=0.05)
    listener.register("123456010", object())
    time.sleep(0.04)
    listener.register("123456010", object())   # 新一轮入站消息刷新映射
    time.sleep(0.04)
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="napcat")))
    assert len(outbound.sent) == 1


# ══════════════════════════════════════════════════════════════════════════════
# C/D. 正常路径与清理
# ══════════════════════════════════════════════════════════════════════════════

def test_real_session_delivered():
    """真实会话 + 有效映射 → 照常投递（不误伤）。"""
    listener, outbound = _listener()
    listener.register("123456010", object())
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="napcat")))
    assert outbound.sent == [{"conv": "123456010", "content": "回复内容"}]


def test_missing_registry_entry_dropped():
    """无映射（如已被同步回复消费）→ 不投递，保持原有行为。"""
    listener, outbound = _listener()
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="napcat")))
    assert outbound.sent == []


def test_session_closed_clears_registry():
    """收到 session_closed → 清理映射，后续出站事件不再投递。"""
    registry: dict = {}
    listener, outbound = _listener(event_registry=registry)
    listener.register("123456010", object())
    asyncio.run(listener._handle_session_closed({"conversation_id": "123456010"}))
    assert "123456010" not in registry
    asyncio.run(listener._handle_reply(_reply_data("123456010", platform="napcat")))
    assert outbound.sent == []
