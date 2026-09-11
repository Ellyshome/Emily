"""上下文感知 / 预算 / 压缩机制的回归测试。

覆盖 P0/P1 改动：
  - 模型元数据表（窗口 / 能力）
  - 溢出错特征识别
  - usage 锚点 + 窗口预算判据
  - 动态压低输出上限
  - 压缩的迭代更新 / 累积成果 / 独立摘要 role / 轮边界切点
"""

import asyncio

from emily_core.infrastructure.llm.errors import ContextOverflowError, is_overflow_error
from emily_core.infrastructure.llm.model_registry import get_model_meta
from emily_core.session.session_context import SessionContext


class _FakeLLM:
    def __init__(self, summary="摘要：讨论项目A，产出 report.md，节点N1，权限L3。" + "细节" * 50):
        self.summary = summary
        self.calls = 0

    async def chat_messages(self, messages, **kwargs):
        self.calls += 1
        return {
            "type": "text",
            "content": self.summary,
            "usage": {"prompt_tokens": 999, "completion_tokens": 50, "total_tokens": 1049},
        }


def _ctx(n_turns=0):
    c = SessionContext(user_id="u", conversation_id="c")
    for i in range(n_turns):
        c.record_turn(f"user-{i}-" + "x" * 200, f"assistant-{i}-" + "y" * 200, "测试")
    return c


def test_model_registry_window_and_override():
    assert get_model_meta("deepseek-v4-pro").context_window == 131072
    assert get_model_meta("deepseek-v4-flash").supports_temperature is False
    assert get_model_meta("totally-unknown").context_window == 65536
    assert get_model_meta("deepseek-v4-pro", window_override=200000).context_window == 200000


def test_overflow_signature_detection():
    assert is_overflow_error(Exception("maximum context length is 65536 tokens"))
    assert is_overflow_error(Exception("context_length_exceeded"))
    assert not is_overflow_error(Exception("invalid api key"))


def test_usage_anchor_beats_estimate():
    c = _ctx(3)
    estimate = c.estimate_context_tokens(system_prompt="S" * 4000)
    assert estimate > 0
    c.record_usage({"prompt_tokens": 12345})
    assert c.used_tokens(system_prompt="S" * 4000) == 12345


def test_should_compact_uses_window_budget():
    c = _ctx(3)
    c.record_usage({"prompt_tokens": 12345})
    assert c.should_compact("deepseek-v4-flash", reserve_tokens=16384) is False
    c.record_usage({"prompt_tokens": 60000})
    assert c.should_compact("deepseek-v4-flash", reserve_tokens=16384) is True


def test_cap_max_tokens_dynamic():
    c = _ctx(3)
    c.record_usage({"prompt_tokens": 60000})
    # window 65536 - used 60000 - reserve 4096 = 1440
    assert c.cap_max_tokens(8192, "deepseek-v4-flash") == 1440
    # 富余时不超过配置值
    c.record_usage({"prompt_tokens": 100})
    assert c.cap_max_tokens(8192, "deepseek-v4-flash") == 8192


def test_cut_point_on_user_boundary():
    c = _ctx(30)
    cut = c._pick_keep_start(2000)
    assert cut > 0
    assert c.message_history[cut]["role"] == "user"


def test_compress_iterates_and_carries_facts():
    c = _ctx(30)
    llm = _FakeLLM()
    assert asyncio.run(c.compress_overflow(llm, keep_recent_tokens=2000)) is True
    assert c.context_summary.startswith("摘要")
    assert "report.md" in c.carried_facts["files"]
    hist = c.get_llm_history()
    assert hist[0]["role"] == "system"
    assert "历史交接摘要" in hist[0]["content"]
    assert "<carried-files>" in hist[0]["content"]

    # 二次压缩：摘要不再作为 message_history 条目被反复摘要
    asyncio.run(c.compress_overflow(llm, keep_recent_tokens=2000))
    assert all("历史交接摘要" not in (m.get("content") or "") for m in c.message_history)


def test_compress_legacy_summary_migrated_out():
    c = _ctx(6)
    c.message_history.insert(0, {
        "role": "user", "name": "system", "content": "[对话历史摘要] 旧摘要内容",
    })
    asyncio.run(c.compress_overflow(_FakeLLM(), keep_recent_tokens=1000))
    assert c.context_summary
    assert all(m.get("name") != "system" for m in c.message_history)
