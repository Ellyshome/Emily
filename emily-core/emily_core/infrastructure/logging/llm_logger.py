# emily-core/emily_core/infrastructure/logging/llm_logger.py
"""LLMInteractionLogger —— 接入 LLMClient._trace_callback 写入 evolution_llm_interaction_logs。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.evolution.llm_logger")


class LLMInteractionLogger:
    """LLM 交互日志采集器。

    通过 LLMClient.set_trace_callback() 接入，在每次 LLM 调用结束时
    非阻断写入 evolution_llm_interaction_logs。

    使用方式：
        llm_client.set_trace_callback(LLMInteractionLogger.make_callback())
    """

    # 当前活跃的 pipeline_run_id / conversation_id / user_id
    # 由 PipelineBUS.run() 在执行前设置
    _current_context: dict[str, str] = {}

    @classmethod
    def set_context(cls, pipeline_run_id: str = "",
                    conversation_id: str = "", user_id: str = "",
                    call_category: str = "") -> None:
        """设置当前请求上下文（由调用方在 LLM 调用前设置）。"""
        cls._current_context = {
            "pipeline_run_id": pipeline_run_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "call_category": call_category,
        }

    @classmethod
    def clear_context(cls) -> None:
        """清除当前请求上下文。"""
        cls._current_context = {}

    @classmethod
    def set_stage(cls, stage: str) -> None:
        """更新当前 pipeline 节点名称（不重置 pipeline_run_id 等其他字段）。

        供 PipelineBUS.run() 在节点循环中调用，使 LLM 调用日志的
        call_category 可按节点阶段 fallback 推断。
        """
        cls._current_context["current_stage"] = stage

    @classmethod
    def set_category(cls, category: str) -> None:
        """临时 overlay call_category（供 Guardian 等独立调用方使用）。

        与 set_context() 不同，此方法只更新 call_category 字段，不重置
        pipeline_run_id / conversation_id / user_id / current_stage。
        """
        cls._current_context["call_category"] = category

    @classmethod
    def make_callback(cls):
        """创建 trace callback 闭包。"""
        def callback(data: dict) -> None:
            if data.get("phase") != "end":
                return
            cls._on_llm_call_end(data)
        return callback

    @classmethod
    def _on_llm_call_end(cls, data: dict) -> None:
        """LLM 调用结束时非阻断写入日志。"""
        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import EvolutionLLMInteractionLog

        ctx = cls._current_context
        call_category = ctx.get("call_category", "")
        # 未显式设置 call_category 时，优先按 pipeline 节点推断，再回退 call_type
        if not call_category:
            stage = ctx.get("current_stage", "")
            if stage == "wi_node1":
                call_category = "intent"
            elif stage == "wi_node2":
                call_category = "planning"
            elif stage in ("wi_node3", "wi_node4"):
                call_category = "execution"
            else:
                call_type = data.get("call_type", "")
                if "intent" in call_type or "json" in call_type:
                    call_category = "intent"
                elif "plan" in call_type:
                    call_category = "planning"
                else:
                    call_category = "execution"

        is_error = data.get("finish_reason") == "error" or data.get("response_type") == "error"
        error_summary = ""
        if is_error:
            error_summary = str(data.get("error", ""))[:500]

        EvolutionLogWriter.write_sync(
            EvolutionLLMInteractionLog,
            pipeline_run_id=ctx.get("pipeline_run_id", ""),
            conversation_id=ctx.get("conversation_id", ""),
            user_id=ctx.get("user_id", ""),
            call_category=call_category,
            call_sequence=data.get("call_sequence", 0),
            model=data.get("model", ""),
            message_count=data.get("message_count", 0),
            tool_count=data.get("tool_count", 0),
            json_mode=data.get("json_mode", False),
            response_type=data.get("response_type", ""),
            response_summary=(data.get("response_summary", "") or "")[:500],
            response_full=(data.get("response_full", "") or ""),
            reasoning_content=(data.get("reasoning_content", "") or ""),
            finish_reason=data.get("finish_reason", ""),
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            latency_ms=data.get("latency_ms", 0),
            is_error=is_error,
            error_summary=error_summary,
        )
