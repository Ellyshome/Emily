"""身份解析兜底 + 测试会话判定的单元测试。

覆盖：
  A. UserBindingService 解析顺序：绑定表 → UUID 直查 → 通道账号兜底 → 准入门禁
     重点回归：通道账号兜底命中时不受 auto_create_user=False 影响
     （2026-09-16 huang_zq_qq 事故的根因修复）
  B. is_test_session：测试前缀命中/未命中、空前缀＝不拦截、显式前缀便于单测
  C. 测试会话设置：运行期覆盖 > Config 默认（测试前缀 / 交互通道）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emily_core.outbound_bus import is_test_session
from emily_core.services.test_settings import (
    resolve_interaction_channel,
    resolve_test_prefixes,
    set_config_defaults,
)
from emily_core.services.user_binding_service import UserBindingService, UserNotAllowedError


# ══════════════════════════════════════════════════════════════════════════════
# A. 身份解析顺序
# ══════════════════════════════════════════════════════════════════════════════

class _FakeUser:
    def __init__(self, uid: str = "", username: str = "") -> None:
        self.id = uid
        self.username = username


class _SpyRepo:
    """替身 repo：按编排返回三路命中结果，并记录补绑定调用。"""

    def __init__(self, bound=None, direct=None, contact=None) -> None:
        self.bound = bound
        self.direct = direct
        self.contact = contact
        self.ensure_binding_calls: list[dict] = []

    def get_by_im(self, im_platform, im_user_id):
        return self.bound

    def get_by_id(self, user_id):
        return self.direct

    def find_by_contact(self, im_platform, im_user_id):
        return self.contact

    def ensure_binding(self, **kwargs):
        self.ensure_binding_calls.append(kwargs)
        return True


def _service_with(repo: _SpyRepo) -> UserBindingService:
    # auto_create=False 复现生产默认配置：未登记即拒绝
    svc = UserBindingService(auto_create=False)
    svc.repo = repo
    return svc


def test_binding_hit_returns_user_without_fallback():
    """① 绑定表命中即返回，不触发兜底与补绑定。"""
    bound = _FakeUser("u-1", "黄志强")
    repo = _SpyRepo(bound=bound, contact=_FakeUser("u-9", "他人"))
    user, is_new = _service_with(repo).get_or_create_user("napcat", "123456010")
    assert user is bound and is_new is False
    assert repo.ensure_binding_calls == []


def test_uuid_direct_lookup_still_works():
    """② sender_id 是 UUID 且 users 表存在 → 直查命中。"""
    direct = _FakeUser("c4b8e33f-7f15-41f4-a773-7841bcdf80e2", "黄志强")
    repo = _SpyRepo(direct=direct)
    user, is_new = _service_with(repo).get_or_create_user(
        "napcat", "c4b8e33f-7f15-41f4-a773-7841bcdf80e2")
    assert user is direct and is_new is False


def test_contact_fallback_resolves_when_binding_missing():
    """③ 绑定表未建行但人事档案有该通道账号 → 兜底解析成功（不受门禁影响）。"""
    contact = _FakeUser("u-2", "黄志强")
    repo = _SpyRepo(contact=contact)
    user, is_new = _service_with(repo).get_or_create_user(
        "wecom", "wx_黄志强", im_display_name="黄志强")
    assert user is contact and is_new is False


def test_contact_fallback_backfills_binding():
    """③′ 兜底命中后补建绑定行，后续消息走绑定表快路径。"""
    contact = _FakeUser("u-3", "黄志强")
    repo = _SpyRepo(contact=contact)
    _service_with(repo).get_or_create_user("wecom", "wx_黄志强", im_display_name="黄志强")
    assert repo.ensure_binding_calls == [{
        "user_id": "u-3",
        "im_platform": "wecom",
        "im_user_id": "wx_黄志强",
        "im_display_name": "黄志强",
    }]


def test_unknown_sender_still_denied():
    """④ 三路都不命中且未开自动建号 → 仍按门禁拒绝（兜底不等于放开闸门）。"""
    repo = _SpyRepo()
    with pytest.raises(UserNotAllowedError):
        _service_with(repo).get_or_create_user("napcat", "huang_zq_qq")
    assert repo.ensure_binding_calls == []


def test_contact_lookup_failure_degrades_to_denied():
    """⑤ 兜底查询异常时按未命中处理，不得静默放行。"""
    class _BrokenRepo(_SpyRepo):
        def find_by_contact(self, im_platform, im_user_id):
            raise RuntimeError("db down")

    with pytest.raises(UserNotAllowedError):
        _service_with(_BrokenRepo()).get_or_create_user("napcat", "huang_zq_qq")


# ══════════════════════════════════════════════════════════════════════════════
# B. 测试会话判定（显式传前缀，不读运行期设置）
# ══════════════════════════════════════════════════════════════════════════════

_PREFIXES = ["test_", "conv_"]


def test_test_session_hit():
    """命中测试前缀 → 测试会话（出站只走 SSE）。"""
    assert is_test_session("test_guard_01", prefixes=_PREFIXES) is True
    assert is_test_session("conv_ident_01", prefixes=_PREFIXES) is True


def test_test_session_miss_real_conversation():
    """真实用户会话（QQ 号 / wxmp_xxx）不是测试会话。"""
    assert is_test_session("123456010", prefixes=_PREFIXES) is False
    assert is_test_session("wxmp_dev_tester", prefixes=_PREFIXES) is False


def test_empty_prefixes_never_intercept():
    """前缀为空 = 不做前缀判定（按真实投递处理）。"""
    assert is_test_session("test_guard_01", prefixes=[]) is False
    assert is_test_session("conv_ident_01", prefixes=[]) is False


def test_empty_conversation_id_not_test_session():
    assert is_test_session("", prefixes=_PREFIXES) is False


def test_prefix_match_is_case_sensitive_prefix():
    """前缀匹配区分大小写、按开头匹配（不做包含匹配）。"""
    assert is_test_session("Test_guard_01", prefixes=_PREFIXES) is False
    assert is_test_session("my_test_01", prefixes=_PREFIXES) is False


# ══════════════════════════════════════════════════════════════════════════════
# C. 测试会话设置：运行期覆盖 > Config 默认
# ══════════════════════════════════════════════════════════════════════════════

class _Cfg:
    test_conv_prefixes = ["cfg_"]
    default_interaction_channel = "wecom"


def test_runtime_prefixes_win_over_config():
    """控制台保存的前缀优先于 Config。"""
    rules = resolve_test_prefixes(None, override={"test_prefixes": ["test_"]})
    assert rules == ["test_"]


def test_runtime_empty_prefixes_means_no_intercept():
    """控制台清空前缀 → 空列表（不拦截），不得回退到 Config 默认。"""
    assert resolve_test_prefixes(None, override={"test_prefixes": []}) == []


def test_config_prefixes_used_without_override():
    assert resolve_test_prefixes(_Cfg(), override=None, use_override=False) == ["cfg_"]


def test_module_default_when_config_absent():
    assert resolve_test_prefixes(None, override=None, use_override=False) == ["test_", "conv_"]


def test_interaction_channel_resolution():
    """交互通道：运行期覆盖 > Config > 模块默认。"""
    assert resolve_interaction_channel(
        _Cfg(), override={"interaction_channel": "napcat"}) == "napcat"
    assert resolve_interaction_channel(_Cfg(), override=None, use_override=False) == "wecom"
    assert resolve_interaction_channel(None, override=None, use_override=False) == "simulator"


def test_set_config_defaults_feeds_unconfigured_callers():
    """Core 启动时推送 Config 后，无 config 句柄的调用点也拿到同一口径。"""
    try:
        set_config_defaults(["pushed_"], "napcat")
        assert resolve_test_prefixes(None, override=None, use_override=False) == ["pushed_"]
        assert resolve_interaction_channel(None, override=None, use_override=False) == "napcat"
    finally:
        set_config_defaults(["test_", "conv_"], "simulator")
