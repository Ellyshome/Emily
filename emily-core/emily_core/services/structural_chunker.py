"""StructuralChunker — 结构断点分块（M5）。

优先按 Markdown 标题断点分块，超长块在段落/句子边界做硬切，
支持 overlap 重叠以保持上下文连贯。
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


class StructuralChunker:
    """结构断点分块器。"""

    def chunk(self, text: str, *, max_len: int = 512, overlap: int = 64) -> list[dict]:
        """将文本切分为结构化 chunk。

        Args:
            text: 源文本。
            max_len: 单 chunk 最大字符数（硬切阈值）。
            overlap: 硬切时的重叠字符数。

        Returns:
            [{text, index, heading}]
        """
        if not text or not text.strip():
            return []

        chunks: list[dict] = []
        index = 0
        for heading, body in self._split_by_headings(text):
            piece = f"{heading}\n{body}".strip() if heading else body.strip()
            if not piece:
                continue
            for seg in self._hard_split(piece, max_len, overlap):
                chunks.append({"text": seg, "index": index, "heading": heading})
                index += 1
        return chunks

    @staticmethod
    def _split_by_headings(content: str) -> list[tuple[str, str]]:
        """按 Markdown 标题切分，返回 [(heading, body), ...]。"""
        sections: list[tuple[str, str]] = []
        matches = list(_HEADING_RE.finditer(content))
        if not matches:
            return [("", content)]

        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            sections.append((m.group(2).strip(), content[start:end]))
        if matches and matches[0].start() > 0:
            sections.insert(0, ("", content[:matches[0].start()]))
        return sections

    @staticmethod
    def _hard_split(text: str, max_len: int, overlap: int) -> list[str]:
        """对超长文本做硬切（优先换行/句末边界，带 overlap）。"""
        if len(text) <= max_len:
            return [text]

        parts: list[str] = []
        cursor = 0
        while cursor < len(text):
            end = min(cursor + max_len, len(text))
            if end < len(text):
                # 尽量在换行或句末断
                cut = text.rfind("\n", cursor, end)
                if cut < cursor + max_len // 2:
                    cut = max(text.rfind("。", cursor, end), text.rfind(".", cursor, end))
                if cut < cursor + max_len // 2:
                    cut = end
                end = cut + 1 if cut < len(text) else end
            parts.append(text[cursor:end].strip())
            if end >= len(text):
                break
            cursor = max(cursor, end - overlap)
        return [p for p in parts if p]
