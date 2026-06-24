"""write_user_memory 工具 —— 写入用户长期记忆。

M8c: 当 MasterAgent 识别到用户表达了长期工作需求时，
调用此工具将需求写入用户的长期记忆文件。
"""

import logging
from typing import Optional

from ..agent.tool_registry import ToolDefinition

logger = logging.getLogger("emily.tool.memory")


def create_memory_tool(user_memory_service, user_name: str = "") -> ToolDefinition:
    """创建 write_user_memory 工具。

    Args:
        user_memory_service: UserMemoryService 实例
        user_name: 当前用户的显示名称

    Returns:
        ToolDefinition
    """
    service = user_memory_service
    name = user_name

    async def execute(args: dict) -> dict:
        """写入用户的长期工作记忆。

        Args:
            args: {"content": str, "title": Optional[str]}

        Returns:
            dict: 包含 success 和 message 的结果
        """
        content = args.get("content", "")
        title = args.get("title", "")

        if not service or not service.enabled:
            return {
                "success": False,
                "message": "长期记忆服务未启用",
            }

        if not name:
            return {
                "success": False,
                "message": "无法确定用户名，记忆未写入",
            }

        try:
            result_title = service.save_memory(
                user_name=name,
                content=content,
                title=title,
            )
            if result_title:
                logger.info(
                    "write_user_memory: user=%s, title=%s", name, result_title,
                )
                return {
                    "success": True,
                    "message": f"已记录长期工作要求：{result_title}",
                    "title": result_title,
                }
            else:
                return {
                    "success": False,
                    "message": "记忆写入失败",
                }
        except Exception as e:
            logger.error("write_user_memory failed: %s", e, exc_info=True)
            return {
                "success": False,
                "message": f"记忆写入异常：{e}",
            }

    return ToolDefinition(
        name="write_user_memory",
        description=(
            "将用户的长期工作要求写入记忆文件。"
            "当用户表达明显的长期或持续性需求时使用，例如："
            "'随时跟踪...'、'每天检查...'、'以后...'、'今后...'、"
            "'每周...'、'定期...'等。"
            "不用于记录一次性对话或临时查询。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "用户的长期工作要求描述，清晰完整地记录",
                },
                "title": {
                    "type": "string",
                    "description": "记忆标题，可选，为空时从内容截取前30字",
                },
            },
            "required": ["content"],
        },
        execute=execute,
    )
