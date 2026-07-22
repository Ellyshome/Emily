# emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py
"""LegacyLogBridge —— 将 Pipeline 执行数据同步写入 M11 时代的 4 张旧日志表。

解决 Bug #6：sop_routing_logs / agent_reasoning_logs / llm_interaction_logs /
tool_call_logs 在新 Pipeline 架构下无数据。

写入时机：PipelineBUS.run() 完成时（与 PipelineExecutionLogger 并行调用）。
写入原则：非阻断——写入失败只 warning，不影响主流程。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...workitem.pipeline.context import BusContext

logger = logging.getLogger("emily.legacy_log_bridge")

BEIJING_TZ = timezone(timedelta(hours=8))


async def write_legacy_logs(context: "BusContext", started_at: str) -> None:
    """Pipeline 完成后，向 4 张 M11 旧表补写日志。

    Args:
        context: BusContext
        started_at: Pipeline 执行开始时间 ISO8601
    """
    wi = context.work_item
    if wi is None:
        return

    from .log_writer import EvolutionLogWriter
    from ...infrastructure.database.models import (
        SOPRoutingLog, AgentReasoningLog, LLMInteractionLog, ToolCallLog,
    )

    intent = context.intent
    sop_id = getattr(intent, "sop_id", None) or wi.sop_id or ""
    confidence = getattr(intent, "confidence", "none") if intent else "none"
    is_hit = bool(sop_id) and sop_id != "__FALLBACK_SOP__"

    now_beijing = datetime.now(BEIJING_TZ)
    message_content = wi.user_input or ""

    # ── 1. sop_routing_logs ──
    try:
        await EvolutionLogWriter.write(
            SOPRoutingLog,
            log_date=now_beijing.strftime("%Y-%m-%d"),
            log_time=now_beijing.strftime("%H:%M:%S"),
            user_id=context.user_id or None,
            conversation_id=context.message.conversation_id if context.message else None,
            message_id=None,
            message_content=message_content[:500],
            matched_sop_id=sop_id or None,
            is_hit=is_hit,
            match_confidence=confidence,
            fallback_action="" if is_hit else "fallback",
            llm_reasoning=getattr(intent, "reasoning", "")[:200] if intent else "",
            execution_result="",
        )
    except Exception as e:
        logger.warning("Legacy sop_routing_logs write failed: %s", e)

    # ── 2. agent_reasoning_logs ──
    try:
        # 从 DB message 获取 message_id（M3 防御加固）
        db_msg_id = context.db_message_id if hasattr(context, "db_message_id") else None
        db_msg_id = db_msg_id or ""  # 归一化 None → ""

        elapsed_ms = 0
        for sr in (wi.step_results or []):
            for tc in (sr.tool_calls or []):
                elapsed_ms += tc.elapsed_ms

        steps_json = []
        for sr in (wi.step_results or []):
            steps_json.append({
                "step_id": sr.step_id,
                "success": sr.success,
                "output": (sr.output or "")[:200],
            })

        reply_preview = (wi.result_text or "")[:500]
        error_msg = wi.error_message or ""

        if not db_msg_id:
            # message_id 是 nullable=False，空值会触发 FK 违规 → 跳过
            logger.warning(
                "Legacy agent_reasoning_logs skipped: db_message_id empty (wi=%s)",
                wi.id,
            )
        else:
            await EvolutionLogWriter.write(
                AgentReasoningLog,
                message_id=db_msg_id,
                user_id=context.user_id or None,
                conversation_id=None,
                iteration_count=wi.llm_call_count,
                elapsed_ms=elapsed_ms,
                max_iterations_reached=False,
                matched_sop_id=sop_id,
                match_confidence=confidence,
                is_compound=getattr(intent, "is_compound", False) if intent else False,
                fallback=not is_hit,
                execution_result="success" if not error_msg else "failed",
                reply_preview=reply_preview,
                error_message=error_msg[:500],
                steps_json=json.dumps(steps_json, ensure_ascii=False),
            )
    except Exception as e:
        logger.warning("Legacy agent_reasoning_logs write failed: %s", e)

    # ── 3. llm_interaction_logs + 4. tool_call_logs ──
    # 从 WorkItem 的 step_results 中提取 tool_calls 和估算 LLM 交互
    try:
        for sr in (wi.step_results or []):
            for tc in (sr.tool_calls or []):
                # tool_call_logs
                try:
                    await EvolutionLogWriter.write(
                        ToolCallLog,
                        reasoning_log_id=None,
                        llm_interaction_id=None,
                        step_index=0,
                        tool_name=tc.tool_name,
                        tool_arguments=json.dumps(tc.tool_input, ensure_ascii=False, default=str)[:5000],
                        tool_result_summary=str(tc.tool_output)[:500] if tc.tool_output else "",
                        is_success=tc.success,
                        error_message="",
                        elapsed_ms=tc.elapsed_ms,
                    )
                except Exception as e:
                    logger.warning("Legacy tool_call_logs write failed: %s", e)

            # 每个有 tool_call 的步骤视为一次 LLM 交互（规划/执行）
            if sr.tool_calls:
                try:
                    await EvolutionLogWriter.write(
                        LLMInteractionLog,
                        reasoning_log_id=None,
                        message_id=db_msg_id or None,
                        call_sequence=0,
                        call_type="chat_with_tools",
                        model="",
                        prompt_summary=sr.output[:500] if sr.output else "",
                        user_message_count=1,
                        tool_count=len(sr.tool_calls),
                        response_type="tool_call",
                        response_summary=sr.output[:500] if sr.output else "",
                        finish_reason="stop",
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                        latency_ms=sum(tc.elapsed_ms for tc in sr.tool_calls),
                    )
                except Exception as e:
                    logger.warning("Legacy llm_interaction_logs write failed: %s", e)
    except Exception as e:
        logger.warning("Legacy log bridge tool/llm loop failed: %s", e)
