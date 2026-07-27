"""脚本聚合层 —— 与 tools/ 平级，管理开发者/维护脚本。

ScriptManager 管 scripts/*.py（subprocess CLI），ToolManager 管 LLM 运行时工具（BusinessFlowTool.handler）。
同时承载可注册为 API 的业务工具脚本（如 search_files）和独立运维脚本（如 build_world_book）。
"""

from .script_entry import ScriptEntry
from .manager import ScriptManager
from .registry import ScriptRegistry, load_registry
from .search_files import (
    SEARCH_FILES_SCHEMA,
    SEARCH_FILES_DISPLAY_NAME,
    handle_search_files,
    register as register_search_files,
)

__all__ = [
    "ScriptEntry", "ScriptManager", "ScriptRegistry", "load_registry",
    "SEARCH_FILES_SCHEMA", "SEARCH_FILES_DISPLAY_NAME",
    "handle_search_files", "register_search_files",
]
