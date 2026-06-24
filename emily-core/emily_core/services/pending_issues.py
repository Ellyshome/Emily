"""待解决问题清单服务 —— M8a。

维护 Markdown 格式的待解决问题清单文件（tem_log/待解决问题.md）。
提供编号生成、写入、标记已处理、列表查询等功能。
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("emily.service.pending_issues")

# 默认路径：项目根目录 tem_log/待解决问题.md
DEFAULT_ISSUES_PATH = "tem_log/待解决问题.md"


class PendingIssuesService:
    """待解决问题清单的读写服务。

    文件格式为 Markdown，分为「待处理」和「已处理」两个二级标题区域。
    每条待解决问题以 ### PND-YYYYMMDD-NNNN 三级标题开头。
    """

    def __init__(self, issues_path: str = ""):
        self._path = Path(issues_path) if issues_path else None
        self._resolved_path = False

    @property
    def path(self) -> Path:
        """获取清单文件路径（延迟解析）。"""
        if self._resolved_path:
            return self._path
        self._resolved_path = True
        if self._path is not None:
            return self._path
        # 默认路径：从当前文件位置推导项目根目录
        # pending_issues.py → services/ → emily_core/ → team_brain_agent/ → EmyBot/
        # pending_issues.py → services/ → emily_core/ → team_brain_agent/ → plugins/ → data/ → EmyBot/
        project_root = Path(__file__).parent.parent.parent.parent.parent
        self._path = project_root / DEFAULT_ISSUES_PATH
        return self._path

    def _ensure_file(self) -> None:
        """确保清单文件存在，不存在则创建模板。"""
        p = self.path
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "# 待解决问题清单\n\n"
                "> 此文件由 Emy 守护Agent 自动维护，项目总经理处理并给出决策。\n"
                "> 处理后移出本文，决策以事件形式入库并关联原事件。\n\n"
                "## 待处理\n\n"
                "## 已处理\n\n",
                encoding="utf-8",
            )
            logger.info("Created pending issues file: %s", p)

    def _read(self) -> str:
        """读取清单文件的全部内容。"""
        self._ensure_file()
        return self.path.read_text(encoding="utf-8")

    def _write(self, content: str) -> None:
        """写入清单文件。"""
        self._ensure_file()
        self.path.write_text(content, encoding="utf-8")
        logger.debug("Pending issues file updated: %s", self.path)

    def _generate_id(self, content: str) -> str:
        """生成下一个 PND 编号。

        从现有条目中提取最大序号，+1 生成新编号。
        格式：PND-YYYYMMDD-NNNN
        """
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        # 从文件中提取所有 PND-YYYYMMDD-NNNN 编号
        import re
        pattern = rf"PND-{today}-(\d+)"
        matches = re.findall(pattern, content)
        if matches:
            max_seq = max(int(m) for m in matches)
            seq = max_seq + 1
        else:
            seq = 1
        return f"PND-{today}-{seq:04d}"

    def add(
        self,
        raised_by: str,
        source: str,
        description: str,
        suggestion: str = "",
        related_events: list[str] | None = None,
    ) -> str:
        """添加一条新的待解决问题。

        Args:
            raised_by: 提出人（如 "彭工"、"守护Agent"）
            source: 来源描述（如 "录入事件铺装完成 99999 平米"）
            description: 问题详细描述
            suggestion: 建议处理方式
            related_events: 关联事件编号列表

        Returns:
            新生成的 PND 编号
        """
        content = self._read()
        issue_id = self._generate_id(content)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        related = ""
        if related_events:
            related = "、".join(related_events)

        entry = (
            f"### {issue_id}\n"
            f"- **提出时间**：{now}\n"
            f"- **提出人**：{raised_by}\n"
            f"- **来源**：{source}\n"
        )
        if related:
            entry += f"- **关联事件**：{related}\n"
        entry += (
            f"- **问题描述**：{description}\n"
            f"- **建议**：{suggestion}\n\n"
        )

        # 插入到「## 待处理」区域（第一个待处理标题之后）
        pending_marker = "## 待处理"
        if pending_marker in content:
            pos = content.index(pending_marker) + len(pending_marker)
            # 跳到下一行
            newline_pos = content.index("\n", pos)
            content = content[:newline_pos + 1] + "\n" + entry + content[newline_pos + 1:]
        else:
            # 不应该出现，但兜底
            content += "\n## 待处理\n\n" + entry

        self._write(content)
        logger.info("Pending issue added: %s — %s", issue_id, description[:50])
        return issue_id

    def resolve(
        self,
        issue_id: str,
        handler: str,
        decision: str,
        decision_event_id: str = "",
    ) -> bool:
        """将一条待解决问题标记为已处理。

        Args:
            issue_id: PND 编号
            handler: 处理人
            decision: 决策描述
            decision_event_id: 决策事件编号（可选）

        Returns:
            True 如果成功找到并处理
        """
        content = self._read()

        # 查找该问题的三级标题
        marker = f"### {issue_id}"
        if marker not in content:
            logger.warning("Pending issue not found: %s", issue_id)
            return False

        # 提取该条目到下一个 ### 或 ## 之间的内容
        start = content.index(marker)
        rest = content[start + len(marker):]

        # 找到下一个 ### 或 ## 标题
        import re
        next_section = re.search(r"\n(###|##) ", rest)
        if next_section:
            entry_end = start + len(marker) + next_section.start()
        else:
            entry_end = len(content)

        # 提取原条目内容
        entry_content = content[start:entry_end]

        # 构建已处理条目
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        resolved_entry = marker + "  ✅ 已处理\n"
        # 在原有字段后插入处理信息
        lines = entry_content[len(marker):].strip().split("\n")
        resolved_entry += "\n".join(lines) + "\n"
        resolved_entry += f"- **处理时间**：{now}\n"
        resolved_entry += f"- **处理人**：{handler}\n"
        if decision_event_id:
            resolved_entry += f"- **决策事件**：{decision_event_id}\n"
        resolved_entry += f"- **决策**：{decision}\n\n"

        # 从待处理区移除，追加到已处理区
        # 删除原条目
        content = content[:start] + content[entry_end:]

        # 追加到已处理区
        resolved_marker = "## 已处理"
        if resolved_marker in content:
            pos = content.index(resolved_marker) + len(resolved_marker)
            newline_pos = content.index("\n", pos)
            content = content[:newline_pos + 1] + "\n" + resolved_entry + content[newline_pos + 1:]
        else:
            content += "\n## 已处理\n\n" + resolved_entry

        self._write(content)
        logger.info("Pending issue resolved: %s by %s", issue_id, handler)
        return True

    def list_pending(self) -> list[dict]:
        """列出所有待处理的问题。

        Returns:
            [{"id": "PND-...", "raised_by": "...", ...}, ...]
        """
        content = self._read()

        # 提取「## 待处理」到下一个「## 」之间的内容
        pending_marker = "## 待处理"
        if pending_marker not in content:
            return []

        start = content.index(pending_marker) + len(pending_marker)
        rest = content[start:]

        import re
        next_section = re.search(r"\n## ", rest)
        if next_section:
            section = rest[:next_section.start()]
        else:
            section = rest

        # 解析每个 ### PND-... 条目
        return self._parse_entries(section)

    def list_resolved(self) -> list[dict]:
        """列出所有已处理的问题。"""
        content = self._read()

        resolved_marker = "## 已处理"
        if resolved_marker not in content:
            return []

        start = content.index(resolved_marker) + len(resolved_marker)
        section = content[start:]
        # 去掉后面可能存在的其他 ## 区域（一般不会有）

        return self._parse_entries(section)

    def _parse_entries(self, section: str) -> list[dict]:
        """从 Markdown 区域解析条目列表。"""
        import re
        entries = []

        # 按 ### 分割条目
        parts = re.split(r"\n### ", section)
        for part in parts:
            if not part.strip():
                continue
            entry = {"id": "", "is_resolved": False}
            lines = part.strip().split("\n")

            # 第一行是标题
            title = lines[0].strip()
            if "✅ 已处理" in title:
                entry["is_resolved"] = True
                title = title.replace("✅ 已处理", "").strip()
            entry["id"] = title

            # 解析字段
            for line in lines[1:]:
                match = re.match(r"- \*\*(.+?)\*\*：(.*)", line)
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    entry[key] = value

            if entry.get("id"):
                entries.append(entry)

        return entries
