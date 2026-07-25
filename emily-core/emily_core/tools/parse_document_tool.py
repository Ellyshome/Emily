"""parse_document 工具 —— 文档结构化解析。

PDF: docling（版面分析 + 阅读顺序 + 表格识别）
Office（Word/Excel/PPT）: MarkItDown（统一转 Markdown）
输出: {sections[{level, title, content}], tables[], metadata}
"""

from __future__ import annotations
import logging
import os
import time

logger = logging.getLogger("emily.tool.parse")

_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "文档路径（pdf/docx/pptx/xlsx）"},
        "extract_tables": {"type": "boolean", "description": "是否提取表格，默认 true"},
    },
    "required": ["file_path"],
}

_PARSE_DESCRIPTION = (
    "解析 PDF/Word/PPT 文档，返回结构化内容（sections 层级 + 表格数组）。"
    "PDF 使用 docling 进行版面分析，Office 文档使用 MarkItDown 统一转 Markdown。"
)


async def handle_parse_document(params: dict) -> dict:
    """M14 handler：文档结构化解析。

    Args:
        params: {file_path, extract_tables?}
    Returns:
        {success, sections[{level, title, content}], tables[{headers[], rows[][]}],
         metadata{page_count, doc_type, parse_engine}}
    """
    started = time.monotonic()
    file_path = params.get("file_path", "")
    extract_tables = params.get("extract_tables", True)

    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": f"file not found: {file_path}"}

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            result = _parse_pdf(file_path, extract_tables)
        elif ext in (".docx", ".pptx", ".xlsx"):
            result = _parse_office(file_path, extract_tables)
        else:
            return {"success": False, "error": f"unsupported file type: {ext}"}

        result["doc_type"] = ext.lstrip(".")
        result["file_path"] = file_path
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return result
    except ImportError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.warning("parse_document failed: %s (%s)", file_path, e)
        return {"success": False, "error": str(e),
                "elapsed_ms": int((time.monotonic() - started) * 1000)}


def _parse_pdf(file_path: str, extract_tables: bool) -> dict:
    """Docling 解析 PDF。"""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise ImportError("docling not installed (pip install docling)")

    converter = DocumentConverter()
    result = converter.convert(file_path)
    doc = result.document

    # 提取 sections
    sections = []
    tables = []
    if doc.texts:
        prev_heading = None
        current_paragraphs = []
        for item in doc.texts:
            if item.label in ("title", "section_header", "section-header"):
                if current_paragraphs:
                    sections.append({
                        "level": 1,
                        "title": prev_heading or "",
                        "content": "\n".join(current_paragraphs),
                    })
                prev_heading = item.text
                current_paragraphs = []
            else:
                current_paragraphs.append(item.text)
        if current_paragraphs:
            sections.append({
                "level": 1,
                "title": prev_heading or "",
                "content": "\n".join(current_paragraphs),
            })

    if extract_tables and doc.tables:
        for tbl in doc.tables:
            table_data = tbl.export_to_dataframe()
            tables.append({
                "headers": [str(c) for c in table_data.columns.tolist()],
                "rows": table_data.values.tolist(),
            })

    return {
        "success": True,
        "sections": sections,
        "tables": tables,
        "metadata": {
            "page_count": len(doc.pages) if hasattr(doc, "pages") else 0,
            "parse_engine": "docling",
        },
    }


def _parse_office(file_path: str, extract_tables: bool) -> dict:
    """MarkItDown 解析 Office 文档。"""
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise ImportError("markitdown not installed (pip install markitdown)")

    md = MarkItDown()
    result = md.convert(file_path)
    text = result.text_content

    # 轻量按 # 标题分 section
    sections = []
    current_title = ""
    current_lines = []
    for line in text.split("\n"):
        if line.startswith("# "):
            if current_lines:
                sections.append({"level": 1, "title": current_title,
                                 "content": "\n".join(current_lines)})
            current_title = line.lstrip("# ").strip()
            current_lines = []
        elif line.startswith("## ") and not current_title:
            if current_lines:
                sections.append({"level": 1, "title": current_title,
                                 "content": "\n".join(current_lines)})
            current_title = line.lstrip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines or current_title:
        sections.append({"level": 1, "title": current_title,
                         "content": "\n".join(current_lines)})

    return {
        "success": True,
        "sections": sections,
        "tables": [],
        "metadata": {"parse_engine": "markitdown"},
    }
