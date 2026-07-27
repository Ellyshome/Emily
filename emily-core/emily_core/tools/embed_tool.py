"""embed_and_index 工具 —— 文本批量 embedding + 入 pgvector。

输入 chunks[]，调 TEI 生成向量，写入 knowledge_chunks 表。
"""

from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.embedding.tei_client import TeiClient
    from ..repositories.knowledge_chunk_repo import KnowledgeChunkRepo

logger = logging.getLogger("emily.tool.embed")

_EMBED_SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {
            "type": "array",
            "items": {"type": "object"},
            "description": "待入库的 chunk 列表，每个含 text（必填）和 index（可选）",
        },
        "doc_metadata": {
            "type": "object",
            "description": "文档级元数据 {doc_id?, doc_name?, stage?, role?, doc_type?, ...}",
        },
    },
    "required": ["chunks"],
}

_EMBED_DESCRIPTION = (
    "对文本 chunks 做 BGE-m3 embedding 并写入 pgvector 知识库。"
    "供 document → chunk_text → embed_and_index 流水线的最后一环使用。"
)


async def handle_embed_and_index(
    params: dict,
    tei: "TeiClient",
    repo: "KnowledgeChunkRepo",
) -> dict:
    """M14 handler：embedding + 入 pgvector。

    Args:
        params: {chunks[{text, index?}], doc_metadata?}
        tei: TeiClient 实例。
        repo: KnowledgeChunkRepo 实例。
    Returns:
        {success, indexed_ids[], count, doc_id, elapsed_ms}
    """
    started = time.monotonic()
    chunks = params.get("chunks", [])
    doc_meta = params.get("doc_metadata", {})

    if not chunks:
        return {"success": False, "error": "chunks is empty"}

    # 1. 提取文本
    texts = [c.get("text", "") for c in chunks]
    if not any(texts):
        return {"success": False, "error": "all chunks have empty text"}

    # 2. 批量 embedding
    try:
        embeddings = await tei.embed(texts)
    except Exception as e:
        logger.warning("embed_and_index: TEI embed failed: %s", e)
        return {"success": False, "error": f"embedding failed: {e}"}

    if len(embeddings) != len(chunks):
        return {"success": False,
                "error": f"embedding count mismatch: got {len(embeddings)}, expected {len(chunks)}"}

    # 3. 入 pgvector
    try:
        ids = repo.batch_insert(chunks, embeddings, doc_meta)
    except Exception as e:
        logger.warning("embed_and_index: batch_insert failed: %s", e)
        return {"success": False, "error": f"DB insert failed: {e}"}

    return {
        "success": True,
        "indexed_ids": ids,
        "count": len(ids),
        "doc_id": doc_meta.get("doc_id", ids[0] if ids else ""),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
