"""EmyAgent 模块 —— 发现式路由。

提供：
- SOPIntentRegistry: M9 SOP 意图注册表（纯加载机 + 目录格式化器）
- sop_parser: SOP Markdown 解析工具

M14 架构重构后，ToolRegistry 已移除——M14 主路径走 BusinessFlowToolRegistry 直调。
"""

from .intent_registry import SOPIntentRegistry, SOPIntentSpec, RegistryStatus
from .sop_parser import parse_sop_markdown, extract_allowed_tools_from_sop

__all__ = [
    "SOPIntentRegistry",
    "SOPIntentSpec",
    "RegistryStatus",
    "parse_sop_markdown",
    "extract_allowed_tools_from_sop",
]
