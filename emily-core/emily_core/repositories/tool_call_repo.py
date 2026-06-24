"""ToolCallRepository —— 工具调用日志 CRUD。

M11: 每次 Agent 执行工具后写入一条，用于分析工具使用模式、常见失败路径。
"""

import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import ToolCallLog

logger = logging.getLogger("emily.repo.tool_call")


class ToolCallRepository:
    """工具调用日志 CRUD。"""

    @staticmethod
    def create(
        reasoning_log_id: str,
        llm_interaction_id: str,
        step_index: int,
        tool_name: str,
        tool_arguments: dict,
    ) -> str:
        """创建工具调用日志，返回 tool_call_log_id。"""
        from ..infrastructure.database.models import _new_uuid
        import json

        log_id = _new_uuid()
        try:
            args_json = json.dumps(tool_arguments, ensure_ascii=False)
        except Exception:
            args_json = str(tool_arguments)

        with get_session() as session:
            log = ToolCallLog(
                id=log_id,
                reasoning_log_id=reasoning_log_id,
                llm_interaction_id=llm_interaction_id,
                step_index=step_index,
                tool_name=tool_name,
                tool_arguments=args_json,
            )
            session.add(log)
            session.flush()
            logger.debug("ToolCallLog created: %s (%s)", log_id, tool_name)
            return log_id

    @staticmethod
    def update_result(
        tool_call_log_id: str,
        result_summary: str = "",
        is_success: bool = True,
        error_message: str = "",
        elapsed_ms: int = 0,
    ) -> None:
        """工具执行完成后更新结果。"""
        with get_session() as session:
            log = session.query(ToolCallLog).filter(
                ToolCallLog.id == tool_call_log_id
            ).first()
            if log is None:
                logger.warning("ToolCallLog not found: %s", tool_call_log_id)
                return

            log.tool_result_summary = (result_summary or "")[:500]
            log.is_success = is_success
            log.error_message = (error_message or "")[:500]
            log.elapsed_ms = elapsed_ms

    @staticmethod
    def get_by_reasoning_id(reasoning_log_id: str) -> list[dict]:
        """获取某次推理的所有工具调用日志。"""
        with get_session() as session:
            rows = (
                session.query(ToolCallLog)
                .filter(ToolCallLog.reasoning_log_id == reasoning_log_id)
                .order_by(ToolCallLog.step_index)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "llm_interaction_id": r.llm_interaction_id,
                    "step_index": r.step_index,
                    "tool_name": r.tool_name,
                    "tool_arguments": r.tool_arguments,
                    "tool_result_summary": r.tool_result_summary,
                    "is_success": r.is_success,
                    "error_message": r.error_message,
                    "elapsed_ms": r.elapsed_ms,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ]
