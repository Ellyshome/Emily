"""ToolRegistry —— 工具注册与发现。

提供 ToolDefinition 数据类和 ToolRegistry 注册表，
支持注册、获取、列表以及生成 OpenAI function calling 格式的工具 schema。
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("emily.agent.tool")


@dataclass
class ToolDefinition:
    """Agent 可调用的工具定义。

    Attributes:
        name: 唯一工具标识（如 "record_event", "query_data"）
        description: 自然语言描述，注入 LLM system prompt
        parameters: JSON Schema 格式的参数定义
        execute: 异步执行函数，签名为 async fn(args: dict) -> dict
        require_admin: True 表示仅管理员可调用
    """

    name: str
    description: str
    parameters: dict
    execute: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    require_admin: bool = False


class ToolRegistry:
    """工具注册表。

    管理所有可用工具，支持按名称获取、列表、导出 OpenAI 格式。

    Methods:
        register(tool): 注册一个工具
        get(name): 按名称获取工具
        list_all(): 获取全部工具列表
        list_public(): 获取非管理员工具列表
        get_openai_tools(admin): 导出 OpenAI function calling 格式
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """注册一个工具。

        Args:
            tool: 工具定义

        Raises:
            ValueError: 工具名已存在
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        logger.debug("Tool registered: %s", tool.name)

    def get(self, name: str) -> ToolDefinition | None:
        """按名称获取工具。

        Args:
            name: 工具名

        Returns:
            ToolDefinition 或 None
        """
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        """获取全部已注册工具。"""
        return list(self._tools.values())

    def list_public(self) -> list[ToolDefinition]:
        """获取非管理员工具列表。"""
        return [t for t in self._tools.values() if not t.require_admin]

    def get_openai_tools(self, admin: bool = False) -> list[dict]:
        """导出 OpenAI function calling 格式的工具列表。

        Args:
            admin: 是否包含管理员专有工具

        Returns:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]
        """
        tools = self._tools.values() if admin else self.list_public()
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return result

    @property
    def tool_names(self) -> list[str]:
        """所有已注册工具的名称列表。"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
