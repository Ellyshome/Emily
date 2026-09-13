# emily-core/emily_core/retrieval/__init__.py
"""检索通道治理（P0）：通道元数据（M1）、策略表（M2）、出处标注（M5）。

定位：治理层，不介入内核图结构（PRD 约束 11）。
"""
from .channel_registry import load_channels, describe_for_prompt, format_provenance  # noqa: F401
from .strategy import load_strategy, dispatch_table_text, strategy_enabled  # noqa: F401
