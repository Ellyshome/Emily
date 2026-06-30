"""Tool 组装工厂。

M14 架构重构后，ToolRegistry 已移除。工具注册全部走 BusinessFlowToolRegistry，
由 emily_core/__init__.py 的 _init_phase_c_deps() 和 _register_plan_task_tools() 完成。

本文档仅作为历史参考保留，不再导出任何工厂函数。
"""
# File intentionally left minimal — tool registration is now done inline in EmilyCore.__init__.
