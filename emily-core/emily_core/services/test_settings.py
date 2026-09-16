"""测试会话运行期设置 —— 测试前缀 + 交互通道（控制台左侧栏可改）。

两条语义（与「出站静默」无关，不做按通道截断真实 IM）：

  测试前缀 test_prefixes
    会话 ID 命中任前缀 → 判定为「测试会话」，其出站只走 SSE、不外发真实 IM；
    留空 = 不做前缀判定（此时只要通道账号能对上真实用户，回复照常发真实 IM）。

  交互通道 interaction_channel
    后续测试注入默认以哪个渠道发（模拟 QQ / 企微 / 小程序 / 模拟器）；
    供控制台「消息模拟器」脚本、emy-test CLI/探针、「与 Emily 对话」面板取默认值。

存储：/app/runtime/test_settings.json（/app/config 为只读挂载，运行期可改项落 /app/runtime）；
未配置覆盖时回退 Core 推送的 Config 默认（test_conv_prefixes / default_interaction_channel）。
读取带 mtime 失效的短缓存，控制台改完下一轮消息即生效，且不每条消息读盘。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("emily.service.test_settings")

_FILENAME = "test_settings.json"
_CACHE_TTL_SECONDS = 2.0

#: 硬编码兜底默认（Core 未推送 Config 时生效）
DEFAULT_TEST_PREFIXES = ("test_", "conv_")
DEFAULT_INTERACTION_CHANNEL = "simulator"

#: 可选交互通道（与通道账号列映射同口径：napcat→qq / wecom,wxmp→wechat）
INTERACTION_CHANNELS = ("simulator", "napcat", "wecom", "wxmp")

_cache: dict = {"at": 0.0, "mtime": None, "data": None}
_config_defaults: dict = {
    "prefixes": list(DEFAULT_TEST_PREFIXES),
    "channel": DEFAULT_INTERACTION_CHANNEL,
}


def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _override_path() -> Path:
    from ..infrastructure.paths import resolve_data_path

    return Path(
        resolve_data_path(
            "",
            f"/app/runtime/{_FILENAME}",
            f"emily-data/runtime/{_FILENAME}",
        )
    )


def set_config_defaults(prefixes=None, channel: str | None = None) -> None:
    """Core 启动时把 Config 值推入（供无 config 句柄的调用点也拿到同一口径）。"""
    if prefixes is not None:
        _config_defaults["prefixes"] = [str(x) for x in prefixes]
    if channel:
        _config_defaults["channel"] = str(channel)


# ══════════════════════════════════════════════════════════════════════
# 读写
# ══════════════════════════════════════════════════════════════════════

def load_override(force: bool = False) -> dict | None:
    """读取控制台保存的设置；从未保存过返回 None（回退 Config 默认）。"""
    now = time.monotonic()
    if not force and _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL_SECONDS:
        return _cache["data"]

    data: dict | None = None
    try:
        path = _override_path()
        if path.is_file():
            mtime = path.stat().st_mtime
            if not force and _cache["mtime"] == mtime and _cache["data"] is not None:
                _cache["at"] = now
                return _cache["data"]
            data = json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff")) or {}
            _cache["mtime"] = mtime
    except Exception as e:  # noqa: BLE001 — 读失败按未配置处理
        logger.warning("load test settings override failed: %s", e)
        data = None

    _cache["data"] = data
    _cache["at"] = now
    return data


def save_settings(test_prefixes: list | None, interaction_channel: str = "") -> dict:
    """保存测试前缀与交互通道（控制台调用）；返回落盘内容。"""
    payload = {
        "test_prefixes": [str(x).strip() for x in (test_prefixes or []) if str(x).strip()],
        "interaction_channel": str(interaction_channel or "").strip(),
        "updated_at": _beijing_now_str(),
    }
    path = _override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    load_override(force=True)
    logger.warning(
        "Test settings updated: prefixes=%s interaction_channel=%s",
        payload["test_prefixes"], payload["interaction_channel"] or "(follow default)",
    )
    return payload


# ══════════════════════════════════════════════════════════════════════
# 生效口径
# ══════════════════════════════════════════════════════════════════════

def config_defaults(config=None) -> tuple[list[str], str]:
    """Config 侧默认值：未声明该字段时回退模块默认。"""
    prefixes = getattr(config, "test_conv_prefixes", None)
    channel = getattr(config, "default_interaction_channel", None)
    return (
        [str(x) for x in (prefixes if prefixes is not None else _config_defaults["prefixes"])],
        str(channel or _config_defaults["channel"]),
    )


def resolve_test_prefixes(config=None, override: dict | None = None,
                          use_override: bool = True) -> list[str]:
    """生效的测试前缀（空列表 = 不做前缀判定）。"""
    if override is None and use_override:
        override = load_override()
    if isinstance(override, dict) and "test_prefixes" in override:
        return [str(x) for x in (override.get("test_prefixes") or []) if str(x).strip()]
    return config_defaults(config)[0]


def resolve_interaction_channel(config=None, override: dict | None = None,
                                use_override: bool = True) -> str:
    """生效的交互通道（测试注入默认渠道）。"""
    if override is None and use_override:
        override = load_override()
    if isinstance(override, dict) and override.get("interaction_channel"):
        return str(override["interaction_channel"])
    return config_defaults(config)[1]


def describe(config=None) -> dict:
    """控制台展示用状态：生效值 + Config 默认 + 来源 + 落盘位置。"""
    override = load_override(force=True)
    saved = isinstance(override, dict)
    prefixes = resolve_test_prefixes(config, override=override)
    channel = resolve_interaction_channel(config, override=override)
    default_prefixes, default_channel = config_defaults(config)
    return {
        "test_prefixes": prefixes,
        "interaction_channel": channel,
        # 空前缀 = 不拦截（会话 ID 不参与测试判定）
        "intercept_enabled": bool(prefixes),
        "defaults": {
            "test_prefixes": default_prefixes,
            "interaction_channel": default_channel,
        },
        "channels": list(INTERACTION_CHANNELS),
        "source": "runtime" if saved else "config",
        "updated_at": (override or {}).get("updated_at", "") if saved else "",
        "file": str(_override_path()),
    }
