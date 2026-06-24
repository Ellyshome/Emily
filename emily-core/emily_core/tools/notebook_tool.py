"""笔记本写入工具 —— GuardianAgent 专用。
一个应用工具写入的文件系统。
将调查发现持久化到 notebooks/守护Agent-笔记.md。

不依赖 LLMClient 或 QueryService —— 纯文件 I/O。
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from ..agent.tool_registry import ToolDefinition

logger = logging.getLogger("emily.tool.notebook")


def create_notebook_tool(notebook_dir: str = "") -> ToolDefinition:
    """为 GuardianAgent 创建 write_notebook 工具。

    Args:
        notebook_dir: 笔记本目录路径。为空时默认 emily_core/notebooks/
    """

    async def execute(args: dict) -> dict:
        content = args.get("content", "")
        filename = args.get("filename", "")  # 可选：自定义文件名（不含 .md）
        if not content or not content.strip():
            return {"success": False, "error": "content 参数不能为空"}

        try:
            # 笔记本目录：优先用传入路径，否则用默认
            if notebook_dir:
                nb_dir = Path(notebook_dir)
            else:
                nb_dir = Path(__file__).parent.parent / "notebooks"

            nb_dir.mkdir(parents=True, exist_ok=True)

            # 文件名：支持自定义，默认守护Agent-笔记
            safe_filename = (filename.strip() or "守护Agent-笔记")
            # 防止路径穿越
            safe_filename = safe_filename.replace("\\", "_").replace("/", "_")
            if not safe_filename.endswith(".md"):
                safe_filename += ".md"
            notebook_path = nb_dir / safe_filename

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            # 根据文件名推断标题
            title = safe_filename.replace(".md", "")
            entry = f"\n\n## [{timestamp}] {title}\n\n{content.strip()}\n"

            with open(notebook_path, "a", encoding="utf-8") as f:
                f.write(entry)

            logger.info("写入笔记: %s (%d 字符)", notebook_path, len(entry))

            return {
                "success": True,
                "reply": "已写入笔记",
                "path": str(notebook_path),
                "filename": safe_filename,
                "size": len(entry),
            }
        except Exception as e:
            logger.error("写入笔记失败: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    return ToolDefinition(
        name="write_notebook",
        description=(
            "将调查发现写入守护笔记文件备查。"
            "仅在发现问题（缺失项/异常项/矛盾点）时调用此工具。"
            "正常项不需要写入笔记。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "要写入笔记的内容。用自然语言描述发现的问题、"
                        "矛盾点、待确认事项、涉及的项目和人员。"
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "可选，自定义文件名（不含 .md 后缀）。"
                        "不传则默认写入「守护Agent-笔记.md」。"
                        "例如传入「未命中事件笔记」则写入 notebooks/未命中事件笔记.md。"
                    ),
                },
            },
            "required": ["content"],
        },
        execute=execute,
        require_admin=False,
    )
