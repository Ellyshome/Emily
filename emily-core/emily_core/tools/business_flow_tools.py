"""业务流工具注册表 — 框架直接执行，不暴露为 LLM function calling 工具。

M14 架构重构：将 record_event / record_task / record_meeting / record_file / query_data
5 个核心业务工具从 LLM ToolRegistry 迁移至此。BusinessFlowAgent 使用结构化输出模式
（LLM 提取参数 → 框架直接调用 handler），不再走 ReAct + tool calling。

设计原则：
  - BusinessFlowTool 与 ToolDefinition 等价，但不转换为 OpenAI function calling 格式
  - handler 签名统一：async fn(params: dict, user_id, message_id, ...) -> dict
  - 工具白名单由 Skill YAML 的 tools 字段声明，SkillExecutor 执行时校验（不再依赖 SOP §3.2）
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Any

logger = logging.getLogger("emily.tools.business_flow")


@dataclass
class BusinessFlowTool:
    """业务流工具定义 —— 框架直接执行，不暴露给 LLM function calling。

    与 ToolDefinition 的区别：
      - ToolDefinition.execute: LLM 通过 function calling 调用
      - BusinessFlowTool.handler: 框架在 LLM 结构化输出后直接调用
    """
    name: str                           # 工具名（与 Skill YAML tools 声明一致）
    description: str                    # 工具描述（注入 LLM prompt 帮助参数提取）
    parameters: dict                    # JSON Schema 参数定义
    handler: Callable                   # async fn(params: dict) -> dict
    category: str = "base"              # base / business / project
    permission_flag: str = "all"        # all / admin / write


class BusinessFlowToolRegistry:
    """业务流工具注册表。

    类似 ToolRegistry，但不生成 OpenAI function calling 格式。
    供 SkillExecutor 在执行 Skill steps 时按 tool_name 查找并调用 handler。
    """

    def __init__(self):
        self._tools: dict[str, BusinessFlowTool] = {}

    def register(self, tool: BusinessFlowTool) -> None:
        """注册一个业务流工具。"""
        if tool.name in self._tools:
            raise ValueError(f"BusinessFlowTool '{tool.name}' 已注册")
        self._tools[tool.name] = tool
        logger.debug("BusinessFlowTool registered: %s", tool.name)

    def get(self, name: str) -> BusinessFlowTool | None:
        """按名称获取工具。"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    def list_names(self) -> list[str]:
        """列出所有已注册工具名。"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
