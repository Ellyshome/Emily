"""EventJournal —— 项目事件流水日志服务。

M8c: 以追加模式记录项目事件到 Markdown 日志文件。
格式：[YYYY-MM-DD] 姓名 操作摘要

使用方式：
    journal = EventJournal(path="emily-data/journal/项目日志.md")
    journal.append(name="彭工", summary="确认录入事件：铺装完成（EVT-20260612-0001）")
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("emily.service.journal")


class EventJournal:
    """项目事件流水日志服务。

    每次系统事件（确认录入、任务/会议/文件创建、守护发现异常、
    启动体检、待解决问题处理）成功后，追加一行简要记录到日志文件。

    Args:
        path: 日志文件路径。如果文件不存在自动创建。
        enabled: 是否启用日志记录。
    """

    def __init__(self, path: str = "", enabled: bool = True):
        self.enabled = enabled
        self.path = path or ""
        if not self.path:
            # 默认路径：项目根目录下的 tem_log/项目日志.md
            # event_journal.py → services/ → emily_core/ → emily-core/ → Emily/
            self.path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ))),
                "tem_log", "项目日志.md",
            )
        if self.enabled:
            self._ensure_file()

    def _ensure_file(self) -> None:
        """确保日志文件存在，不存在时创建并写入头部模板。"""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("# 项目事件日志\n\n")
                    f.write("> 此文件由 Emy 自动维护，记录所有系统事件（不含用户对话）。\n")
                    f.write("> 格式：[日期] 姓名 操作摘要\n\n")
                    f.write("---\n\n")
                logger.info("Created journal file: %s", self.path)
            except OSError as e:
                logger.error("Failed to create journal file %s: %s", self.path, e)
                self.enabled = False

    def append(self, name: str, summary: str) -> bool:
        """追加一条事件日志。

        Args:
            name: 操作人姓名（如"彭工"、"守护Agent"、"张总"）
            summary: 操作摘要（不含日期和姓名前缀）

        Returns:
            bool: 是否成功写入
        """
        if not self.enabled:
            return False

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 使用 UTC+8 北京时间
        try:
            from datetime import timedelta
            local = datetime.now(timezone.utc) + timedelta(hours=8)
            date_str = local.strftime("%Y-%m-%d")
        except Exception as e:
            logger.debug("journal date compute failed: %s", e, exc_info=True)

        line = f"[{date_str}] {name} {summary}\n"

        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            logger.info("Journal appended: %s", line.strip())
            return True
        except OSError as e:
            logger.error("Failed to append journal: %s", e)
            return False

    def read(self, limit: int = 100) -> list[str]:
        """读取最近 N 条日志。

        Args:
            limit: 最大返回行数

        Returns:
            日志行列表（不含文件头）
        """
        if not self.enabled or not os.path.exists(self.path):
            return []

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # 过滤掉模板头行（以 # 和 > 和 --- 开头的行）
            entries = [
                line.strip() for line in lines
                if line.strip() and not line.startswith("#")
                and not line.startswith(">") and not line.startswith("---")
            ]
            return entries[-limit:] if len(entries) > limit else entries
        except OSError as e:
            logger.error("Failed to read journal: %s", e)
            return []

    def search(self, keyword: str = "", limit: int = 50) -> list[str]:
        """按关键词搜索日志。

        Args:
            keyword: 搜索关键词（为空返回所有）
            limit: 最大返回行数

        Returns:
            匹配的日志行列表
        """
        entries = self.read(limit=10000)
        if not keyword:
            return entries[-limit:]
        filtered = [e for e in entries if keyword in e]
        return filtered[-limit:]
