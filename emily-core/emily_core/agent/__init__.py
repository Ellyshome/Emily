"""Agent 工具模块。

提供：
- sop_parser: SOP Markdown 解析工具（供 SkillRegistry 使用）

SOPIntentRegistry 和 ToolRegistry 已废弃并移除：
- SOPIntentRegistry → 由 SkillRegistry 替代
- ToolRegistry → 由 BusinessFlowToolRegistry 替代
"""

from .sop_parser import parse_sop_markdown, extract_allowed_tools_from_sop

__all__ = [
    "parse_sop_markdown",
    "extract_allowed_tools_from_sop",
]
