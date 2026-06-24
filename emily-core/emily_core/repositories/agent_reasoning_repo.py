"""AgentReasoningRepository —— Agent 推理日志 CRUD。

M11: 每次 MasterAgent.run() 完成后写入一条推理记录，含执行摘要、路由决策、步骤明细。
"""

import json
import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import AgentReasoningLog

logger = logging.getLogger("emily.repo.agent_reasoning")


class AgentReasoningRepository:
    """Agent 推理日志 CRUD。"""

    @staticmethod
    def create(
        message_id: str,
        user_id: str = "",
        conversation_id: str = "",
        matched_sop_id: str | None = None,
        match_confidence: str = "none",
        is_compound: bool = False,
        fallback: bool = False,
    ) -> str:
        """创建推理日志，返回 reasoning_log_id。"""
        from ..infrastructure.database.models import _new_uuid

        reason_id = _new_uuid()
        with get_session() as session:
            # 解析 business conversation_id → conversations.id (UUID)
            conv_uuid = None
            if conversation_id:
                conv_uuid = AgentReasoningRepository._resolve_conversation_id(
                    session, conversation_id
                )

            log = AgentReasoningLog(
                id=reason_id,
                message_id=message_id,
                user_id=user_id or None,
                conversation_id=conv_uuid,
                matched_sop_id=matched_sop_id,
                match_confidence=match_confidence,
                is_compound=is_compound,
                fallback=fallback,
            )
            session.add(log)
            session.flush()
            logger.debug("ReasoningLog created: %s", reason_id)
            return reason_id

    @staticmethod
    def _resolve_conversation_id(session, business_conv_id: str) -> str | None:
        """将业务 conversation_id 解析为 conversations.id (UUID)。

        如果对应 Conversation 不存在，自动创建一个。
        """
        from ..infrastructure.database.models import Conversation

        conv = (
            session.query(Conversation)
            .filter(Conversation.conversation_id == business_conv_id)
            .first()
        )
        if conv:
            return conv.id

        conv = Conversation(
            im_platform="",
            conversation_type="private",
            conversation_id=business_conv_id,
            takeover_mode="collaborate",
        )
        session.add(conv)
        session.flush()
        return conv.id

    @staticmethod
    def finalize(
        reasoning_log_id: str,
        *,
        iteration_count: int = 0,
        elapsed_ms: int = 0,
        matched_sop_id: str | None = None,
        match_confidence: str = "none",
        is_compound: bool = False,
        fallback: bool = False,
        execution_result: str = "",
        reply_preview: str = "",
        steps: list | None = None,
        error_message: str = "",
        max_iterations_reached: bool = False,
    ) -> None:
        """Agent 执行完成后更新推理日志。"""
        with get_session() as session:
            log = session.query(AgentReasoningLog).filter(
                AgentReasoningLog.id == reasoning_log_id
            ).first()
            if log is None:
                logger.warning("ReasoningLog not found: %s", reasoning_log_id)
                return

            log.iteration_count = iteration_count
            log.elapsed_ms = elapsed_ms
            log.max_iterations_reached = max_iterations_reached
            log.matched_sop_id = matched_sop_id
            log.match_confidence = match_confidence
            log.is_compound = is_compound
            log.fallback = fallback
            log.execution_result = execution_result
            log.reply_preview = (reply_preview or "")[:500]
            log.error_message = (error_message or "")[:500]

            if steps:
                try:
                    log.steps_json = json.dumps(
                        [s.to_dict() if hasattr(s, 'to_dict') else s for s in steps],
                        ensure_ascii=False,
                        default=str,
                    )
                except Exception:
                    log.steps_json = json.dumps(steps, ensure_ascii=False, default=str)

    @staticmethod
    def get_by_message_id(message_id: str) -> Optional[AgentReasoningLog]:
        """按 message_id 查询推理日志。"""
        with get_session() as session:
            return (
                session.query(AgentReasoningLog)
                .filter(AgentReasoningLog.message_id == message_id)
                .first()
            )

    @staticmethod
    def get_complete_trace(message_id: str) -> dict:
        """获取一条消息的完整 Agent 执行追踪（推理+LLM调用+工具调用）。"""
        from .llm_interaction_repo import LLMInteractionRepository
        from .tool_call_repo import ToolCallRepository

        reasoning = AgentReasoningRepository.get_by_message_id(message_id)
        if reasoning is None:
            return {"found": False}

        llm_logs = LLMInteractionRepository.get_by_reasoning_id(reasoning.id)
        tool_logs = ToolCallRepository.get_by_reasoning_id(reasoning.id)

        return {
            "found": True,
            "reasoning": {
                "id": reasoning.id,
                "message_id": reasoning.message_id,
                "iteration_count": reasoning.iteration_count,
                "elapsed_ms": reasoning.elapsed_ms,
                "matched_sop_id": reasoning.matched_sop_id,
                "match_confidence": reasoning.match_confidence,
                "is_compound": reasoning.is_compound,
                "fallback": reasoning.fallback,
                "execution_result": reasoning.execution_result,
                "reply_preview": reasoning.reply_preview,
                "error_message": reasoning.error_message,
                "steps_json": reasoning.steps_json,
                "created_at": str(reasoning.created_at) if reasoning.created_at else None,
            },
            "llm_interactions": llm_logs,
            "tool_calls": tool_logs,
        }
