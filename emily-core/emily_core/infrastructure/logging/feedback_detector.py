# emily-core/emily_core/infrastructure/logging/feedback_detector.py
"""FeedbackSignalDetector —— 从用户消息中提取隐式反馈信号。"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.evolution.feedback_detector")


class FeedbackSignalDetector:
    """用户反馈信号检测器。

    在 SessionAgent.handle() 中调用 detect()，
    分析用户消息是否包含不满/满意/追问等隐式信号。
    """

    # 信号关键词映射
    _PATTERNS = {
        "explicit_correction": {
            "keywords": ["不对", "错了", "不是这个", "重新", "搞错了", "不是我要的", "搞反了"],
            "strength": 0.9,
        },
        "truncation_followup": {
            "keywords": ["继续", "然后呢", "还有吗", "后面呢", "接着说", "说完"],
            "strength": 0.6,
        },
        "positive": {
            "keywords": ["好的", "谢谢", "对", "收到", "可以", "没问题", "确认"],
            "strength": 0.5,
        },
        "repeat_request": {
            "keywords": [],  # 需要上下文比较，不能用关键词匹配
            "strength": 0.8,
        },
    }

    @staticmethod
    def detect(
        user_message: str,
        *,
        pipeline_run_id: str = "",
        conversation_id: str = "",
        user_id: str = "",
        assistant_reply: str = "",
    ) -> list[dict]:
        """检测用户消息中的反馈信号。

        Returns:
            检测到的信号列表 [{"signal_type": ..., "signal_strength": ..., "trigger_message": ...}]
        """
        if not user_message or len(user_message.strip()) < 2:
            return []

        text = user_message.strip().lower()
        signals = []

        for signal_type, config in FeedbackSignalDetector._PATTERNS.items():
            if signal_type == "repeat_request":
                continue  # repeat_request 需要历史上下文，暂不实现
            for kw in config["keywords"]:
                if kw in text:
                    signals.append({
                        "signal_type": signal_type,
                        "signal_strength": config["strength"],
                        "trigger_message": user_message[:200],
                        "context_summary": (assistant_reply or "")[:200],
                    })
                    break  # 每种信号只检测一次

        return signals

    @staticmethod
    async def log_signals(
        signals: list[dict],
        *,
        pipeline_run_id: str = "",
        conversation_id: str = "",
        user_id: str = "",
    ) -> None:
        """将检测到的信号非阻断写入 user_feedback_signals。"""
        if not signals:
            return

        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import UserFeedbackSignal

        for sig in signals:
            await EvolutionLogWriter.write(
                UserFeedbackSignal,
                pipeline_run_id=pipeline_run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                signal_type=sig.get("signal_type", ""),
                signal_strength=sig.get("signal_strength", 0.0),
                trigger_message=sig.get("trigger_message", ""),
                context_summary=sig.get("context_summary", ""),
            )
