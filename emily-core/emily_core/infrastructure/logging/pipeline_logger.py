# emily-core/emily_core/infrastructure/logging/pipeline_logger.py
"""PipelineExecutionLogger —— WorkItem 完成后写入 pipeline_execution_logs。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...workitem.pipeline.context import BusContext

logger = logging.getLogger("emily.evolution.pipeline_logger")


class PipelineExecutionLogger:
    """Pipeline 执行日志采集器。

    在 PipelineBUS.run() 末尾调用 log()，将 BusContext + WorkItem
    的完整执行过程非阻断写入 pipeline_execution_logs。
    """

    @staticmethod
    async def log(context: "BusContext", started_at: str, node_timings: dict) -> None:
        """写入一条 Pipeline 执行日志。

        Args:
            context: BusContext（包含 WorkItem + 用户信息）
            started_at: Pipeline 执行开始时间 ISO8601
            node_timings: {"wi_node1": ms, "wi_node2": ms, ...} 各节点耗时
        """
        from .log_writer import EvolutionLogWriter
        from ...infrastructure.database.models import PipelineExecutionLog

        wi = context.work_item
        if wi is None:
            return

        # 从 WorkItem 采集意图识别信息
        intent = context.intent
        sop_id = getattr(intent, "sop_id", None) or wi.sop_id or ""
        confidence = getattr(intent, "confidence", "none") if intent else "none"
        is_compound = getattr(intent, "is_compound", False) if intent else False
        is_fallback = getattr(intent, "fallback", False) if intent else (wi.sop_id is None)

        # 从 WorkItem 采集执行结果
        from ...workitem.workitem_state import WorkItemState
        final_status = "DONE"
        if context.should_abort:
            final_status = "ABORTED"
        elif wi.state == WorkItemState.FAILED:
            final_status = "FAILED"

        # 序列化 tool_calls
        tool_calls_data = []
        step_results_data = []
        for sr in (wi.step_results or []):
            sr_dict = {
                "step_id": sr.step_id,
                "success": sr.success,
                "output": (sr.output or "")[:200],
            }
            step_results_data.append(sr_dict)
            for tc in (sr.tool_calls or []):
                tool_calls_data.append({
                    "tool_name": tc.tool_name,
                    "success": tc.success,
                    "elapsed_ms": tc.elapsed_ms,
                })

        # 序列化 Hook 决策
        hook_decisions = []
        for w in (context.warnings or []):
            hook_decisions.append({"decision": "WARN", "message": w[:200]})

        # 用户信息
        session_ctx = context.get_session_context()
        user_name = getattr(session_ctx, "user_name", "") if session_ctx else ""
        user_level = getattr(session_ctx, "level", 1) if session_ctx else 1

        completed_at = datetime.now(timezone.utc).isoformat()

        # 计算总耗时
        elapsed_ms = sum(node_timings.values())

        await EvolutionLogWriter.write(
            PipelineExecutionLog,
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
            user_id=context.user_id or "",
            user_name=user_name,
            user_level=user_level,
            matched_sop_id=sop_id,
            match_confidence=confidence,
            is_compound=is_compound,
            is_fallback=is_fallback,
            intent_reasoning=getattr(intent, "reasoning", "")[:500] if intent else "",
            final_status=final_status,
            abort_reason=(context.abort_reason or "")[:500],
            result_text=(wi.result_text or "")[:1000],
            tool_calls_json=json.dumps(tool_calls_data, ensure_ascii=False),
            step_results_json=json.dumps(step_results_data, ensure_ascii=False),
            hook_decisions_json=json.dumps(hook_decisions, ensure_ascii=False),
            was_blocked=context.should_abort,
            block_hook_name="",
            started_at=started_at,
            completed_at=completed_at,
            elapsed_ms=elapsed_ms,
            node1_ms=node_timings.get("wi_node1", 0),
            node2_ms=node_timings.get("wi_node2", 0),
            node3_ms=node_timings.get("wi_node3", 0),
            node4_ms=node_timings.get("wi_node4", 0),
        )
