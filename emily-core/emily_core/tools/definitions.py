"""工具定义数据类 —— 供 LLM function-calling 工具使用。

M14 重构后，BusinessFlowToolRegistry 是主路径（框架直调），
ToolDefinition 仅用于少数仍暴露为 OpenAI function-calling 的条件工具
（如 chat_archive / email / memory / pending_issue）。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


class WriteMode(str, Enum):
    """工具写语义类别（M2）。

    分级兜底据此判定：高级兜底仅放开 APPEND / TRANSITION；
    OVERWRITE / DELETE 一律不放开（走对应 SOP + 人工确认）。
    """

    READ = "read"              # 只读：查询/检索/列举
    APPEND = "append"          # 追加（create）：新建即留痕
    TRANSITION = "transition"  # 状态迁移：合法状态机迁移即留痕
    OVERWRITE = "overwrite"    # 覆盖/编辑已有内容
    DELETE = "delete"          # 删除/批量


@dataclass
class ToolDefinition:
    """Agent 可调用的工具定义。

    Attributes:
        name: 唯一工具标识（如 "record_event", "query_data"）
        description: 自然语言描述，注入 LLM system prompt
        parameters: JSON Schema 格式的参数定义
        execute: 异步执行函数，签名为 async fn(args: dict) -> dict
        require_admin: True 表示仅管理员可调用
        write_mode: 写语义类别，默认 READ
    """

    name: str
    description: str
    parameters: dict
    execute: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    require_admin: bool = False
    write_mode: WriteMode = WriteMode.READ
