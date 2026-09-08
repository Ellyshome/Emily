"""DocumentParser — 多格式解析路由（M5）。

按扩展名路由：md/txt 直读；pdf 走 pymupdf；docx 走 python-docx。
任何格式解析失败时降级为 UTF-8 直读（errors=ignore），不抛异常中断入库。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("emily.service.document_parser")


class DocumentParser:
    """文档解析路由。"""

    def parse(self, local_path: Path) -> str:
        """解析本地文件为纯文本。

        Args:
            local_path: 本地文件路径。

        Returns:
            纯文本内容（解析失败降级为空串或直读文本）。
        """
        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(str(path))

        ext = path.suffix.lower()
        try:
            if ext in (".md", ".txt", ".markdown", ".text"):
                return path.read_text(encoding="utf-8")
            if ext == ".pdf":
                return self._parse_pdf(path)
            if ext in (".docx", ".doc"):
                return self._parse_docx(path)
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.warning("DocumentParser: parse %s failed, fallback: %s", path.name, e)
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return ""

    @staticmethod
    def _parse_pdf(path: Path) -> str:
        import fitz  # pymupdf

        parts: list[str] = []
        with fitz.open(str(path)) as doc:
            for page in doc:
                parts.append(page.get_text())
        return "\n".join(parts)

    @staticmethod
    def _parse_docx(path: Path) -> str:
        import docx

        document = docx.Document(str(path))
        return "\n".join(p.text for p in document.paragraphs)
