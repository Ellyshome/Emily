# emily-core/emily_core/infrastructure/logging/log_writer.py
"""EvolutionLogWriter —— 统一日志写入工具。

所有进化日志表的写入入口。保证三原则：
1. 非阻断——写入失败只 warning，不影响业务主流程
2. 截断——Text ≤ 5000 字，String ≤ 500 字
3. 异步——asyncio.to_thread 包裹同步 DB 写入
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("emily.evolution_log_writer")


class EvolutionLogWriter:
    """统一日志写入工具——所有进化日志的写入入口。"""

    # 截断上限
    TEXT_LIMIT = 5000
    STRING_LIMIT = 500

    # 需要长截断的字段名后缀/名称
    _LONG_FIELD_SUFFIXES = ("_json", "_summary", "_detail", "_text")
    _LONG_FIELD_NAMES = ("detail_json", "results_summary", "tool_calls_json",
                         "step_results_json", "hook_decisions_json", "error_detail",
                         "params_json", "context_summary")

    @staticmethod
    async def write(model_class, **kwargs):
        """非阻断写入一条日志。

        Args:
            model_class: ORM 模型类（如 PipelineExecutionLog）
            **kwargs: 模型字段键值对
        """
        try:
            kwargs = EvolutionLogWriter._truncate(kwargs)
            kwargs = EvolutionLogWriter._fill_defaults(kwargs)
            await asyncio.to_thread(EvolutionLogWriter._sync_write, model_class, kwargs)
        except Exception as e:
            logger.warning("EvolutionLogWriter.write failed for %s: %s",
                           getattr(model_class, "__tablename__", "?"), e)

    @staticmethod
    def write_sync(model_class, **kwargs):
        """同步非阻断写入（供 sync 上下文使用，如 Hook 内）。

        Args:
            model_class: ORM 模型类
            **kwargs: 模型字段键值对
        """
        try:
            kwargs = EvolutionLogWriter._truncate(kwargs)
            kwargs = EvolutionLogWriter._fill_defaults(kwargs)
            EvolutionLogWriter._sync_write(model_class, kwargs)
        except Exception as e:
            logger.warning("EvolutionLogWriter.write_sync failed for %s: %s",
                           getattr(model_class, "__tablename__", "?"), e)

    @staticmethod
    def _sync_write(model_class, kwargs: dict):
        from ..database.session import get_session
        with get_session() as session:
            entry = model_class(**kwargs)
            session.add(entry)
            session.commit()

    @staticmethod
    def _truncate(kwargs: dict) -> dict:
        truncated = {}
        for k, v in kwargs.items():
            if isinstance(v, str):
                limit = EvolutionLogWriter.TEXT_LIMIT if (
                    any(k.endswith(s) for s in EvolutionLogWriter._LONG_FIELD_SUFFIXES)
                    or k in EvolutionLogWriter._LONG_FIELD_NAMES
                ) else EvolutionLogWriter.STRING_LIMIT
                truncated[k] = v[:limit]
            else:
                truncated[k] = v
        return truncated

    @staticmethod
    def _fill_defaults(kwargs: dict) -> dict:
        """填充 created_at 默认值（如果未提供）。"""
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(timezone.utc).isoformat()
        return kwargs
