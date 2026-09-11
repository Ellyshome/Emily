"""模型元数据表 —— 上下文窗口 / 输出上限 / 能力声明。

设计参照 Pi Agent 的 `models.generated.ts` 思路：把"模型能力差异"集中为
一份声明式元数据，替代散落各处的 `if "reasoner" in model` 分支。

当前只覆盖 Emily 实际使用的模型（垂直应用，3~5 家主流 + 可切换即可），
未知模型回退到保守默认值，并允许 config 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelMeta:
    context_window: int          # 上下文窗口（输入 + 输出）
    max_output: int              # 单次最大输出 token
    supports_temperature: bool   # 是否接受 temperature 等采样参数
    supports_cache: bool         # 是否支持 prompt cache 计量
    display_name: str = ""


# 保守默认：窗口 64K、输出 8K、支持采样参数
DEFAULT_META = ModelMeta(
    context_window=65536,
    max_output=8192,
    supports_temperature=True,
    supports_cache=False,
    display_name="(unknown)",
)

# 已知模型（键为小写精确名；未命中的同前缀模型走 _prefix_fallback）
MODEL_META: dict[str, ModelMeta] = {
    # DeepSeek
    "deepseek-chat": ModelMeta(65536, 8192, True, True, "DeepSeek Chat"),
    "deepseek-reasoner": ModelMeta(65536, 65536, False, True, "DeepSeek Reasoner"),
    "deepseek-v4-flash": ModelMeta(65536, 8192, False, True, "DeepSeek V4 Flash"),
    "deepseek-v4-pro": ModelMeta(131072, 8192, False, True, "DeepSeek V4 Pro"),
    # OpenAI 兼容
    "gpt-4o-mini": ModelMeta(131072, 16384, True, True, "GPT-4o mini"),
    "gpt-4o": ModelMeta(131072, 16384, True, True, "GPT-4o"),
}

# 前缀兜底：命中前缀且精确名未登记时，沿用其能力（窗口可被 config 覆盖）
_PREFIX_FALLBACK: list[tuple[str, ModelMeta]] = [
    ("deepseek-reasoner", MODEL_META["deepseek-reasoner"]),
    ("deepseek-v4-pro", MODEL_META["deepseek-v4-pro"]),
    ("deepseek-v4", MODEL_META["deepseek-v4-flash"]),
    ("deepseek", MODEL_META["deepseek-chat"]),
    ("gpt-4o", MODEL_META["gpt-4o"]),
]


def get_model_meta(model: str | None, *, window_override: int = 0) -> ModelMeta:
    """查询模型元数据。

    Args:
        model: 模型名（大小写不敏感）。
        window_override: config 显式指定的上下文窗口（>0 时覆盖表内值）。

    Returns:
        ModelMeta：未知模型回退 DEFAULT_META；window_override 优先。
    """
    name = (model or "").strip().lower()
    meta = MODEL_META.get(name)
    if meta is None:
        for prefix, prefix_meta in _PREFIX_FALLBACK:
            if name.startswith(prefix):
                meta = prefix_meta
                break
    if meta is None:
        meta = DEFAULT_META
    if window_override and window_override > 0:
        meta = ModelMeta(
            context_window=window_override,
            max_output=meta.max_output,
            supports_temperature=meta.supports_temperature,
            supports_cache=meta.supports_cache,
            display_name=meta.display_name,
        )
    return meta


def get_context_window(model: str | None, *, window_override: int = 0) -> int:
    """便捷方法：只取上下文窗口。"""
    return get_model_meta(model, window_override=window_override).context_window
