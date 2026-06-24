"""LLMInteractionRepository —— LLM 交互日志 CRUD。

M11: 每次 LLM API 调用后写入一条，用于统计 token 消耗、延迟、模型行为分析。
"""

import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import LLMInteractionLog

logger = logging.getLogger("emily.repo.llm_interaction")


class LLMInteractionRepository:
    """LLM 交互日志 CRUD。"""

    @staticmethod
    def create(
        reasoning_log_id: str,
        call_sequence: int,
        call_type: str,
        model: str,
        user_message_count: int = 0,
        tool_count: int = 0,
    ) -> str:
        """创建 LLM 交互日志，返回 llm_interaction_id。"""
        from ..infrastructure.database.models import _new_uuid

        log_id = _new_uuid()
        with get_session() as session:
            log = LLMInteractionLog(
                id=log_id,
                reasoning_log_id=reasoning_log_id,
                call_sequence=call_sequence,
                call_type=call_type,
                model=model,
                user_message_count=user_message_count,
                tool_count=tool_count,
            )
            session.add(log)
            session.flush()
            logger.debug("LLMInteractionLog created: %s (seq=%d)", log_id, call_sequence)
            return log_id

    @staticmethod
    def update_response(
        llm_interaction_id: str,
        response_type: str,
        response_summary: str = "",
        finish_reason: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        """LLM 调用完成后更新响应信息。"""
        with get_session() as session:
            log = session.query(LLMInteractionLog).filter(
                LLMInteractionLog.id == llm_interaction_id
            ).first()
            if log is None:
                logger.warning("LLMInteractionLog not found: %s", llm_interaction_id)
                return

            log.response_type = response_type
            log.response_summary = (response_summary or "")[:500]
            log.finish_reason = finish_reason[:50] if finish_reason else ""
            log.prompt_tokens = prompt_tokens
            log.completion_tokens = completion_tokens
            log.total_tokens = total_tokens
            log.latency_ms = latency_ms

    @staticmethod
    def get_by_reasoning_id(reasoning_log_id: str) -> list[dict]:
        """获取某次推理的所有 LLM 交互日志。"""
        with get_session() as session:
            rows = (
                session.query(LLMInteractionLog)
                .filter(LLMInteractionLog.reasoning_log_id == reasoning_log_id)
                .order_by(LLMInteractionLog.call_sequence)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "call_sequence": r.call_sequence,
                    "call_type": r.call_type,
                    "model": r.model,
                    "response_type": r.response_type,
                    "response_summary": r.response_summary,
                    "finish_reason": r.finish_reason,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "latency_ms": r.latency_ms,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ]

    @staticmethod
    def get_usage_stats(time_range: str = "7d") -> dict:
        """统计 LLM 用量。

        Args:
            time_range: today / week / month / all

        Returns:
            dict: {total_calls, total_tokens, avg_latency_ms, by_model: [...], by_type: {...}}
        """
        from datetime import datetime, timezone, timedelta

        with get_session() as session:
            now = datetime.now(timezone.utc)
            if time_range == "today":
                cutoff = now.strftime("%Y-%m-%d")
            elif time_range == "week":
                cutoff = (now - timedelta(days=7)).isoformat()
            elif time_range == "month":
                cutoff = (now - timedelta(days=30)).isoformat()
            else:
                cutoff = None

            q = session.query(LLMInteractionLog)
            if cutoff:
                q = q.filter(LLMInteractionLog.created_at >= cutoff)

            rows = q.all()

            total_calls = len(rows)
            total_tokens = sum(r.total_tokens or 0 for r in rows)
            avg_latency = sum(r.latency_ms or 0 for r in rows) / max(total_calls, 1)

            # 按模型分组
            by_model: dict[str, dict] = {}
            for r in rows:
                model = r.model or "unknown"
                if model not in by_model:
                    by_model[model] = {"calls": 0, "total_tokens": 0}
                by_model[model]["calls"] += 1
                by_model[model]["total_tokens"] += (r.total_tokens or 0)

            # 按类型分组
            by_type: dict[str, int] = {}
            for r in rows:
                t = r.call_type or "unknown"
                by_type[t] = by_type.get(t, 0) + 1

            return {
                "total_calls": total_calls,
                "total_tokens": total_tokens,
                "avg_latency_ms": round(avg_latency, 1),
                "by_model": by_model,
                "by_type": by_type,
            }
