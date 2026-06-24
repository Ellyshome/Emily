"""AgentTraceService —— Agent 追踪业务服务。

M11: 统一管理 Agent 推理日志、LLM 交互日志、工具调用日志的写入和查询。

设计原则：所有 DB 写入在 try/except 中执行，失败只记 logger.warning，不抛异常、不阻塞 Agent 主流程。
"""

import logging

logger = logging.getLogger("emily.service.agent_trace")


class AgentTraceService:
    """Agent 追踪业务服务。

    构造时无依赖（纯协调层），所有方法直接委托给对应 Repository。
    """

    def __init__(self):
        pass

    # ── 推理日志 ──

    def create_reasoning_log(
        self,
        message_id: str,
        user_id: str = "",
        conversation_id: str = "",
        matched_sop_id: str | None = None,
        match_confidence: str = "none",
        is_compound: bool = False,
        fallback: bool = False,
    ) -> str | None:
        """创建推理日志，返回 reasoning_log_id；失败返回 None。"""
        try:
            from ..repositories.agent_reasoning_repo import AgentReasoningRepository

            return AgentReasoningRepository.create(
                message_id=message_id,
                user_id=user_id,
                conversation_id=conversation_id,
                matched_sop_id=matched_sop_id,
                match_confidence=match_confidence,
                is_compound=is_compound,
                fallback=fallback,
            )
        except Exception as e:
            logger.warning("Failed to create reasoning log: %s", e)
            return None

    def finalize_reasoning_log(
        self,
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
        try:
            from ..repositories.agent_reasoning_repo import AgentReasoningRepository

            AgentReasoningRepository.finalize(
                reasoning_log_id=reasoning_log_id,
                iteration_count=iteration_count,
                elapsed_ms=elapsed_ms,
                matched_sop_id=matched_sop_id,
                match_confidence=match_confidence,
                is_compound=is_compound,
                fallback=fallback,
                execution_result=execution_result,
                reply_preview=reply_preview,
                steps=steps,
                error_message=error_message,
                max_iterations_reached=max_iterations_reached,
            )
        except Exception as e:
            logger.warning("Failed to finalize reasoning log %s: %s", reasoning_log_id, e)

    # ── LLM 交互日志 ──

    def create_llm_interaction_log(
        self,
        reasoning_log_id: str,
        call_sequence: int,
        call_type: str,
        model: str,
        user_message_count: int = 0,
        tool_count: int = 0,
    ) -> str | None:
        """创建 LLM 交互日志，返回 llm_interaction_id；失败返回 None。"""
        try:
            from ..repositories.llm_interaction_repo import LLMInteractionRepository

            return LLMInteractionRepository.create(
                reasoning_log_id=reasoning_log_id,
                call_sequence=call_sequence,
                call_type=call_type,
                model=model,
                user_message_count=user_message_count,
                tool_count=tool_count,
            )
        except Exception as e:
            logger.warning("Failed to create LLM interaction log: %s", e)
            return None

    def update_llm_response(
        self,
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
        try:
            from ..repositories.llm_interaction_repo import LLMInteractionRepository

            LLMInteractionRepository.update_response(
                llm_interaction_id=llm_interaction_id,
                response_type=response_type,
                response_summary=response_summary,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.warning("Failed to update LLM response %s: %s", llm_interaction_id, e)

    # ── 工具调用日志 ──

    def create_tool_call_log(
        self,
        reasoning_log_id: str,
        llm_interaction_id: str,
        step_index: int,
        tool_name: str,
        tool_arguments: dict,
    ) -> str | None:
        """创建工具调用日志，返回 tool_call_log_id；失败返回 None。"""
        try:
            from ..repositories.tool_call_repo import ToolCallRepository

            return ToolCallRepository.create(
                reasoning_log_id=reasoning_log_id,
                llm_interaction_id=llm_interaction_id,
                step_index=step_index,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
            )
        except Exception as e:
            logger.warning("Failed to create tool call log: %s", e)
            return None

    def update_tool_result(
        self,
        tool_call_log_id: str,
        result_summary: str = "",
        is_success: bool = True,
        error_message: str = "",
        elapsed_ms: int = 0,
    ) -> None:
        """工具执行完成后更新结果。"""
        try:
            from ..repositories.tool_call_repo import ToolCallRepository

            ToolCallRepository.update_result(
                tool_call_log_id=tool_call_log_id,
                result_summary=result_summary,
                is_success=is_success,
                error_message=error_message,
                elapsed_ms=elapsed_ms,
            )
        except Exception as e:
            logger.warning("Failed to update tool result %s: %s", tool_call_log_id, e)

    # ── 查询 ──

    def get_complete_agent_trace(self, message_id: str) -> dict:
        """获取一条消息的完整 Agent 执行追踪（推理+LLM调用+工具调用）。"""
        try:
            from ..repositories.agent_reasoning_repo import AgentReasoningRepository

            return AgentReasoningRepository.get_complete_trace(message_id)
        except Exception as e:
            logger.warning("Failed to get agent trace for %s: %s", message_id, e)
            return {"found": False, "error": str(e)}

    def query_llm_usage_stats(self, time_range: str = "7d") -> dict:
        """统计 LLM 用量：总token、平均延迟、调用次数、按模型分组。"""
        try:
            from ..repositories.llm_interaction_repo import LLMInteractionRepository

            return LLMInteractionRepository.get_usage_stats(time_range)
        except Exception as e:
            logger.warning("Failed to query LLM usage stats: %s", e)
            return {}
