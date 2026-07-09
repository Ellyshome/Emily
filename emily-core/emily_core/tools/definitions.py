"""工具定义数据类 —— 供 LLM function-calling 工具使用。

M14 重构后，BusinessFlowToolRegistry 是主路径（框架直调），
ToolDefinition 仅用于少数仍暴露为 OpenAI function-calling 的条件工具
（如 chat_archive / email / memory / pending_issue）。
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


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
