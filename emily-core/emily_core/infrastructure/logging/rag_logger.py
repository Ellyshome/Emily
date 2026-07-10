# emily-core/emily_core/infrastructure/logging/rag_logger.py
"""RAGRetrievalLogger —— RAG 检索后写入 rag_retrieval_logs。"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("emily.evolution.rag_logger")


class RAGRetrievalLogger:
    """RAG 检索日志采集器。"""

    @staticmethod
    async def log(
        *,
        query_text: str,
        provider: str,
        hit_count: int = 0,
        top_score: float = 0.0,
        avg_score: float = 0.0,
        results_summary: str = "",
        was_used_by_llm: bool = True,
        latency_ms: int = 0,
        error_summary: str = "",
        pipeline_run_id: str = "",
        conversation_id: str = "",
        user_id: str = "",
    ) -> None:
        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import RAGRetrievalLog

        await EvolutionLogWriter.write(
            RAGRetrievalLog,
            pipeline_run_id=pipeline_run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            query_text=query_text,
            provider=provider,
            hit_count=hit_count,
            top_score=top_score,
            avg_score=avg_score,
            results_summary=results_summary,
            was_used_by_llm=was_used_by_llm,
            latency_ms=latency_ms,
            error_summary=error_summary,
        )
