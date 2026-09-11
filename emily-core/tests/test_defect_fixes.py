"""缺陷治理修复的单元测试。

覆盖：
  A. 用户记忆链路：文件记忆优先、DB 兜底、超长截断
  B. expert_review_enabled：闭包工厂两分支 + 节点防御性兜底
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


# ══════════════════════════════════════════════════════════════════════════════
# B. expert_review_enabled 接线
# ══════════════════════════════════════════════════════════════════════════════

def _bind_expert_wi():
    from emily_core.workitem.workitem import WorkItem
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.langgraph_engine.state import set_bus_context
    wi = WorkItem(id="wi-test")
    wi.expert_required = True
    wi.expert_id = "expert-1"
    set_bus_context(BusContext(work_item=wi))
    return wi


def test_expert_review_disabled_routes_to_executing():
    from emily_core.config import Config
    from emily_core.workitem.langgraph_engine.graph import make_route_after_routing
    _bind_expert_wi()
    assert make_route_after_routing(Config(expert_review_enabled=False))({}) == "executing"


def test_expert_review_enabled_routes_to_review():
    from emily_core.config import Config
    from emily_core.workitem.langgraph_engine.graph import make_route_after_routing
    _bind_expert_wi()
    assert make_route_after_routing(Config(expert_review_enabled=True))({}) == "expert_review"


def test_expert_review_disabled_takes_precedence_over_binding():
    """开关关闭优先于专家绑定（核心缺陷点：旧实现只看绑定）。"""
    from emily_core.config import Config
    from emily_core.workitem.langgraph_engine.graph import make_route_after_routing
    _bind_expert_wi()
    route = make_route_after_routing(Config(expert_review_enabled=False))
    assert route({}) == "executing"   # 绑定存在也跳过


def test_backward_compat_route_after_routing():
    from emily_core.workitem.langgraph_engine.graph import route_after_routing
    _bind_expert_wi()
    assert route_after_routing({}) == "expert_review"


def test_env_bool_mapping():
    """bootstrap env_map 应把 EMILY_EXPERT_REVIEW_ENABLED=false 解析为 False。"""
    import os
    from emily_core.bootstrap import _config_from_env
    os.environ["EMILY_EXPERT_REVIEW_ENABLED"] = "false"
    try:
        data = _config_from_env({})
        assert data.get("expert_review_enabled") is False
    finally:
        os.environ.pop("EMILY_EXPERT_REVIEW_ENABLED", None)
