"""LLM 层异常类型。

ContextOverflowError 用于区分"上下文超窗"与普通调用失败：前者可由上层
触发压缩后重试一次（Pi 的 overflow → compact → retry 语义），后者直接失败。
"""

from __future__ import annotations


class ContextOverflowError(Exception):
    """上下文超出模型窗口（provider 拒绝本次请求）。

    由 LLMClient 在识别到 provider 溢出报错特征时抛出；携带原始模型名与
    报错摘要，供上层记录与决策（压缩后重试一次）。
    """

    def __init__(self, message: str = "", *, model: str = "", provider_message: str = ""):
        super().__init__(message or "context overflow")
        self.model = model
        self.provider_message = provider_message


# provider 溢出报错特征串（大小写不敏感匹配）。
# 覆盖 OpenAI / DeepSeek / Azure / Anthropic / Google 等常见措辞。
OVERFLOW_SIGNATURES: tuple[str, ...] = (
    "maximum context length",
    "context length exceeded",
    "context_length_exceeded",
    "context window",
    "reduce the length of the messages",
    "too many tokens",
    "input is too long",
    "prompt is too long",
    "exceeds the maximum",
    "token limit exceeded",
)


def is_overflow_error(err: BaseException) -> bool:
    """判断异常是否为上下文溢出特征。"""
    text = str(err).lower()
    return any(sig in text for sig in OVERFLOW_SIGNATURES)
