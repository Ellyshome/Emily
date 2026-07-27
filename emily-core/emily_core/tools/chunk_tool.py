"""chunk_text 工具 —— 文本分块。

Markdown: langchain_text_splitters.MarkdownHeaderTextSplitter（按标题层级）
通用: RecursiveCharacterTextSplitter（递归字符）
输出: {chunks[{index, text, metadata{section}}]}
"""

from __future__ import annotations
import logging
import time

logger = logging.getLogger("emily.tool.chunk")

_CHUNK_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "待分块的文本"},
        "strategy": {"type": "string", "enum": ["markdown", "recursive"],
                     "description": "分块策略，默认 recursive"},
        "chunk_size": {"type": "integer", "description": "每块最大字符数，默认 500"},
        "chunk_overlap": {"type": "integer", "description": "块间重叠字符数，默认 50"},
        "headers_to_split_on": {"type": "array",
                                "description": "Markdown 标题层级（仅 strategy=markdown），"
                                               "默认 [('#', 'Header 1'), ('##', 'Header 2'), ('###', 'Header 3')]"},
    },
    "required": ["text"],
}

_CHUNK_DESCRIPTION = (
    "将长文本按指定策略分块。markdown 策略按标题层级分块（保留章节语义），"
    "recursive 策略按字符递归分割。供 embed_and_index 流水的上游使用。"
)


async def handle_chunk_text(params: dict) -> dict:
    """M14 handler：文本分块。

    Args:
        params: {text, strategy?, chunk_size?, chunk_overlap?, headers_to_split_on?}
    Returns:
        {success, chunks[{index, text, metadata}], strategy}
    """
    started = time.monotonic()
    text = params.get("text", "")
    strategy = params.get("strategy", "recursive")
    chunk_size = int(params.get("chunk_size", 500))
    chunk_overlap = int(params.get("chunk_overlap", 50))

    if not text:
        return {"success": False, "error": "text is empty"}

    try:
        if strategy == "markdown":
            chunks = _chunk_markdown(text, params.get("headers_to_split_on"))
        else:
            chunks = _chunk_recursive(text, chunk_size, chunk_overlap)
    except ImportError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.warning("chunk_text failed: %s", e)
        return {"success": False, "error": str(e)}

    result = {
        "success": True,
        "chunks": [{"index": i, "text": c[0], "metadata": c[1]}
                   for i, c in enumerate(chunks)],
        "strategy": strategy,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    return result


def _chunk_markdown(text: str, headers: list | None = None) -> list[tuple[str, dict]]:
    """按 Markdown 标题层级分块。"""
    try:
        from langchain_text_splitters import MarkdownHeaderTextSplitter
    except ImportError:
        raise ImportError(
            "langchain-text-splitters not installed "
            "(pip install langchain-text-splitters)"
        )

    if headers is None:
        headers = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
        ]

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers,
        strip_headers=False,
    )
    docs = splitter.split_text(text)
    return [(doc.page_content, doc.metadata) for doc in docs]


def _chunk_recursive(text: str, chunk_size: int, chunk_overlap: int) -> list[tuple[str, dict]]:
    """递归字符分块（通用策略）。"""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        raise ImportError(
            "langchain-text-splitters not installed "
            "(pip install langchain-text-splitters)"
        )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", ".", " ", ""],
    )
    docs = splitter.create_documents([text])
    return [(doc.page_content, doc.metadata) for doc in docs]
