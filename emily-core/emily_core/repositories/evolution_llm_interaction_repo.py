"""EvolutionLLMInteractionRepo —— 进化版 LLM 交互日志数据访问层。"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..infrastructure.database.models import EvolutionLLMInteractionLog
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.repo.evolution_llm_interaction")


class EvolutionLLMInteractionRepo:
    """进化版 LLM 交互日志 CRUD 操作。"""

    @staticmethod
    def list_by_pipeline_run_ids(
        run_ids: list[str],
        *,
        session: Optional[Session] = None,
    ) -> list[EvolutionLLMInteractionLog]:
        """按 pipeline_run_id 批量查询 LLM 交互日志。

        Args:
            run_ids: pipeline_run_id 列表。
            session: 可选的数据库会话。

        Returns:
            list[EvolutionLLMInteractionLog]: 按 call_sequence 排序的日志列表。
        """
        def _impl(sess: Session) -> list[EvolutionLLMInteractionLog]:
            if not run_ids:
                return []
            return (
                sess.query(EvolutionLLMInteractionLog)
                .filter(EvolutionLLMInteractionLog.pipeline_run_id.in_(run_ids))
                .order_by(EvolutionLLMInteractionLog.call_sequence)
                .all()
            )

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def list_by_conversation_id(
        conversation_id: str,
        *,
        since: str = "",
        session: Optional[Session] = None,
    ) -> list[EvolutionLLMInteractionLog]:
        """按 conversation_id 查询 LLM 交互日志（供归档收集意图识别阶段的日志）。

        Args:
            conversation_id: 会话 ID。
            since: 可选 ISO8601 时间戳，仅返回此时间之后创建的日志（用于过滤本轮）。
            session: 可选的数据库会话。

        Returns:
            list[EvolutionLLMInteractionLog]: 按 call_sequence 排序的日志列表。
        """
        def _impl(sess: Session) -> list[EvolutionLLMInteractionLog]:
            if not conversation_id:
                return []
            q = sess.query(EvolutionLLMInteractionLog).filter(
                EvolutionLLMInteractionLog.conversation_id == conversation_id
            )
            if since:
                q = q.filter(EvolutionLLMInteractionLog.created_at >= since)
            return q.order_by(EvolutionLLMInteractionLog.call_sequence).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
