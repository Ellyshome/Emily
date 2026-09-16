"""缺陷治理修复的单元测试。

覆盖：
  A. 用户记忆链路：文件记忆优先、DB 兜底、超长截断

注：B 段（expert_review_enabled 接线）随专家模块整线退役（2026-09-16）移除。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ══════════════════════════════════════════════════════════════════════════════
# A. 用户记忆链路（resolve_long_term_memory）
# ══════════════════════════════════════════════════════════════════════════════

class _FakeUser:
    def __init__(self, long_term_memory=""):
        self.long_term_memory = long_term_memory


class _FakeMemorySvc:
    def __init__(self, text="", enabled=True, raises=False):
        self._text = text
        self.enabled = enabled
        self._raises = raises
        self.calls = 0

    def load_memory_context(self, user_name):
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._text


class _FakeCore:
    def __init__(self, mem_svc):
        self._user_memory_service = mem_svc


def test_memory_file_takes_priority_over_db():
    from emily_core.session.session_data_fetcher import resolve_long_term_memory
    core = _FakeCore(_FakeMemorySvc("文件记忆内容"))
    got = resolve_long_term_memory(_FakeUser("DB列内容"), "张三", core)
    assert got == "文件记忆内容"


def test_memory_falls_back_to_db_when_file_empty():
    from emily_core.session.session_data_fetcher import resolve_long_term_memory
    core = _FakeCore(_FakeMemorySvc(""))
    got = resolve_long_term_memory(_FakeUser("DB列内容"), "张三", core)
    assert got == "DB列内容"


def test_memory_db_only_without_core():
    from emily_core.session.session_data_fetcher import resolve_long_term_memory
    got = resolve_long_term_memory(_FakeUser("DB列内容"), "张三", None)
    assert got == "DB列内容"


def test_memory_service_disabled_ignored():
    from emily_core.session.session_data_fetcher import resolve_long_term_memory
    svc = _FakeMemorySvc("文件记忆", enabled=False)
    got = resolve_long_term_memory(_FakeUser("DB列内容"), "张三", _FakeCore(svc))
    assert got == "DB列内容"
    assert svc.calls == 0


def test_memory_service_exception_falls_back():
    from emily_core.session.session_data_fetcher import resolve_long_term_memory
    svc = _FakeMemorySvc("x", raises=True)
    got = resolve_long_term_memory(_FakeUser("DB列内容"), "张三", _FakeCore(svc))
    assert got == "DB列内容"
    assert svc.calls == 1


def test_memory_truncated_to_limit():
    from emily_core.session.session_data_fetcher import (
        resolve_long_term_memory, _MEMORY_CONTEXT_MAX_CHARS,
    )
    long_text = "字" * (_MEMORY_CONTEXT_MAX_CHARS + 500)
    got = resolve_long_term_memory(_FakeUser(long_text), "张三", None)
    assert len(got) == _MEMORY_CONTEXT_MAX_CHARS


def test_write_then_read_roundtrip(tmp_path):
    """端到端：save_memory 写入 → load_memory_context 读出 → resolve 注入。"""
    from emily_core.services.user_memory_service import UserMemoryService
    from emily_core.session.session_data_fetcher import resolve_long_term_memory

    svc = UserMemoryService(memory_dir=str(tmp_path), enabled=True)
    assert svc.save_memory("张三", "每周一提交周报", title="周报")
    got = resolve_long_term_memory(_FakeUser(""), "张三", _FakeCore(svc))
    assert "每周一提交周报" in got

