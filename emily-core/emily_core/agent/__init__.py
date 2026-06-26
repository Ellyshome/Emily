"""EmyAgent 模块 —— 工具注册表 + 发现式路由。

提供：
- ToolRegistry + ToolDefinition: 工具注册与发现
- SOPIntentRegistry: M9 SOP 意图注册表（纯加载机 + 目录格式化器）
- sop_parser: SOP Markdown 解析工具
"""

from .intent_registry import SOPIntentRegistry, SOPIntentSpec, RegistryStatus
from .sop_parser import parse_sop_markdown, extract_allowed_tools_from_sop
from .tool_registry import ToolRegistry, ToolDefinition

__all__ = [
    "SOPIntentRegistry",
    "SOPIntentSpec",
    "RegistryStatus",
    "ToolRegistry",
    "ToolDefinition",
    "parse_sop_markdown",
    "extract_allowed_tools_from_sop",
]
