"""脚本聚合层 —— 与 tools/ 平级，管理开发者/维护脚本。

ScriptManager 管 scripts/*.py（subprocess CLI），ToolManager 管 LLM 运行时工具（BusinessFlowTool.handler）。
"""

from .script_entry import ScriptEntry
from .manager import ScriptManager
from .registry import ScriptRegistry, load_registry

__all__ = ["ScriptEntry", "ScriptManager", "ScriptRegistry", "load_registry"]
