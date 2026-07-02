"""UserMemoryService —— 用户长期记忆服务。

M8c: 为每个用户维护一个长期记忆 Markdown 文件。
当用户表达明显的长期工作要求时，Agent 调用 write_user_memory 工具写入。
每次新对话开始时，加载用户记忆作为 system prompt 上下文。

文件格式：
    # 用户名 - 长期工作记忆

    > 此文件由 Emy 自动维护，记录用户的长期工作要求。
    > 每次新对话时作为上下文加载。

    ## [2026-06-12 14:30] 标题
    内容描述...

使用方式：
    svc = UserMemoryService(memory_dir="memory/")
    svc.save_memory("张三", "每天检查科技城景观设计进度")
    content = svc.load_memory("张三")
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("emily.service.memory")


class UserMemoryService:
    """用户长期记忆服务。

    Args:
        memory_dir: 记忆文件存储目录（为空时默认 memory/）
        enabled: 是否启用
        max_entries: 每个用户最大记忆条目数
    """

    def __init__(
        self,
        memory_dir: str = "",
        enabled: bool = True,
        max_entries: int = 50,
    ):
        self.enabled = enabled
        self.max_entries = max_entries
        self.memory_dir = memory_dir or ""

        if not self.memory_dir:
            # 默认路径由 EmilyCore._init_m8c_services() 显式传入
            # （优先 /app/user_memory/，回退 emily-data/user_memory/）
            self.memory_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                ))),
                "memory",
            )

        if self.enabled:
            os.makedirs(self.memory_dir, exist_ok=True)

    def _sanitize_filename(self, user_name: str) -> str:
        """将用户名转为安全的文件名。

        移除路径分隔符和特殊字符，保留中文、字母、数字。
        """
        safe = re.sub(r'[\\/:*?"<>|]', '_', user_name)
        safe = safe.strip().replace(' ', '_')
        if not safe:
            safe = "unknown_user"
        return f"{safe}-长期记忆.md"

    def _get_filepath(self, user_name: str) -> str:
        """获取用户记忆文件路径。"""
        filename = self._sanitize_filename(user_name)
        return os.path.join(self.memory_dir, filename)

    def _ensure_file(self, filepath: str, user_name: str) -> None:
        """确保记忆文件存在，不存在时创建并写入头部模板。"""
        if not os.path.exists(filepath):
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(f"# {user_name} - 长期工作记忆\n\n")
                    f.write("> 此文件由 Emy 自动维护，记录用户的长期工作要求。\n")
                    f.write("> 每次新对话时作为上下文加载。\n\n")
                    f.write("---\n\n")
                logger.info("Created memory file for user: %s", user_name)
            except OSError as e:
                logger.error("Failed to create memory file %s: %s", filepath, e)

    def save_memory(
        self,
        user_name: str,
        content: str,
        title: str = "",
    ) -> Optional[str]:
        """写入一条长期记忆。

        Args:
            user_name: 用户名（如"张三"）
            content: 记忆内容
            title: 记忆标题（为空时从 content 截取前 30 字）

        Returns:
            写入的条目标题，或 None（写入失败）
        """
        if not self.enabled or not user_name or not content:
            return None

        filepath = self._get_filepath(user_name)
        self._ensure_file(filepath, user_name)

        # 生成日期和标题
        date_str = ""
        try:
            local = datetime.now(timezone.utc) + timedelta(hours=8)
            date_str = local.strftime("%Y-%m-%d %H:%M")
        except Exception:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        if not title:
            title = content[:30] + ("..." if len(content) > 30 else "")

        entry = f"## [{date_str}] {title}\n{content}\n\n"

        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(entry)

            # 检查条目数，超过限制时裁剪旧条目
            self._trim_if_needed(filepath)

            logger.info(
                "Memory saved for user %s: %s", user_name, title,
            )
            return title
        except OSError as e:
            logger.error("Failed to save memory for %s: %s", user_name, e)
            return None

    def load_memory(self, user_name: str) -> str:
        """加载用户的长期记忆全文。

        Args:
            user_name: 用户名

        Returns:
            记忆文件的完整文本内容（含 Markdown 格式）。
            如果文件不存在或服务禁用，返回空字符串。
        """
        if not self.enabled or not user_name:
            return ""

        filepath = self._get_filepath(user_name)
        if not os.path.exists(filepath):
            return ""

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            logger.info(
                "Memory loaded for user %s: %d chars",
                user_name, len(content),
            )
            return content
        except OSError as e:
            logger.error("Failed to load memory for %s: %s", user_name, e)
            return ""

    def load_memory_context(self, user_name: str) -> str:
        """加载用户长期记忆的压缩上下文（用于注入 system prompt）。

        只返回条目内容，不含文件头模板。

        Args:
            user_name: 用户名

        Returns:
            记忆条目的摘要文本，适合注入到 LLM 上下文。
            如果无记忆返回空字符串。
        """
        full = self.load_memory(user_name)
        if not full:
            return ""

        # 提取所有 ## 条目标题行，生成简洁上下文
        lines = full.split("\n")
        entries = []
        current_title = ""
        current_content = []

        for line in lines:
            if line.startswith("## "):
                if current_title:
                    content_text = " ".join(current_content).strip()
                    entries.append(f"- {current_title}: {content_text}")
                current_title = line[3:].strip()
                current_content = []
            elif current_title and line.strip():
                current_content.append(line.strip())

        # 最后一个条目
        if current_title:
            content_text = " ".join(current_content).strip()
            entries.append(f"- {current_title}: {content_text}")

        if not entries:
            return ""

        return "用户长期工作要求：\n" + "\n".join(entries)

    def _trim_if_needed(self, filepath: str) -> None:
        """如果条目数超过 max_entries，裁剪最旧的条目。"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # 统计 ## 条目数
            entries = re.findall(r'^## ', content, re.MULTILINE)
            if len(entries) <= self.max_entries:
                return

            # 按 ## 分割，保留头部 + 最近的 max_entries 条
            parts = re.split(r'(?=^## )', content, flags=re.MULTILINE)
            header = ""
            entry_parts = []

            for part in parts:
                if part.startswith("## "):
                    entry_parts.append(part)
                else:
                    header = part

            # 保留最近的条目
            kept = entry_parts[-self.max_entries:]
            new_content = header + "".join(kept)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

            logger.info(
                "Memory trimmed for %s: %d -> %d entries",
                filepath, len(entries), len(kept),
            )
        except OSError as e:
            logger.warning("Failed to trim memory file %s: %s", filepath, e)

    def list_users(self) -> list[str]:
        """列出所有有记忆文件的用户。

        Returns:
            用户名列表
        """
        if not self.enabled:
            return []

        try:
            users = []
            for filename in os.listdir(self.memory_dir):
                if filename.endswith("-长期记忆.md"):
                    # 去掉后缀和前缀
                    name = filename[:-len("-长期记忆.md")]
                    name = name.replace("_", " ")
                    users.append(name)
            return users
        except OSError as e:
            logger.error("Failed to list memory users: %s", e)
            return []
