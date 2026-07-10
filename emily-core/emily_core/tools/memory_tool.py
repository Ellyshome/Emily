"""write_user_memory 工具 —— 写入用户长期记忆。

M8c: 当 Agent 识别到用户表达了长期工作需求时，
调用此工具将需求写入用户的长期记忆文件。

用户名解析策略（运行时）：
  1. 如果 args 中有 user_name，直接使用（LLM 可能传入）
  2. 否则从 args["_user_id"]（UUID）查 User 表获取 real_name / username
  3. fallback = "用户"
"""

import logging
from typing import Optional

from .definitions import ToolDefinition

logger = logging.getLogger("emily.tool.memory")


def create_memory_tool(user_memory_service) -> ToolDefinition:
    """创建 write_user_memory 工具。

    Args:
        user_memory_service: UserMemoryService 实例

    Returns:
        ToolDefinition
    """
    service = user_memory_service

    async def execute(args: dict) -> dict:
        """写入用户的长期工作记忆。

        Args:
            args: {"content": str, "title": Optional[str],
                   "user_name": Optional[str], "_user_id": Optional[str]}

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

        # ═══ TC-M01: 运行时解析用户名 ═══
        user_name = ""
        # 1. LLM 可能在 params 中传了 user_name
        user_name = args.get("user_name", "")

        # 2. 否则从 _user_id（UUID）查 User 表获取 username
        if not user_name:
            user_id = args.get("_user_id", "")
            if user_id:
                try:
                    from ..repositories.user_repo import UserRepository
                    u = UserRepository.get(user_id)
                    if u:
                        user_name = u.username or ""
                except Exception:
                    pass

        # 3. fallback
        if not user_name:
            logger.warning("write_user_memory: cannot resolve user_name, _user_id=%s", args.get("_user_id", "?"))
            return {
                "success": False,
                "message": "无法确定用户名，记忆未写入",
            }

        try:
            result_title = service.save_memory(
                user_name=user_name,
                content=content,
                title=title,
            )
            if result_title:
                logger.info(
                    "write_user_memory: user=%s, title=%s", user_name, result_title,
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
