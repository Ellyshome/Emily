"""embed_and_index 工具 —— 文本批量 embedding + 入 pgvector。

薄壳：实现与留痕都在 `services/knowledge_service.KnowledgeService`（方案 A 同源同痕），
本模块只负责把 LLM 工具入参转成 service 调用，签名与返回契约保持不变。

输入 chunks[]，调当前 embedding 后端（本地 TEI / 远程 API，选型见
infrastructure/embedding/factory.py）生成向量，写入 knowledge_chunks 表。
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.embedding.base import EmbeddingClient
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
    tei: "EmbeddingClient",
    repo: "KnowledgeChunkRepo",
) -> dict:
    """M14 handler：embedding + 入 pgvector（委托 KnowledgeService）。

    Args:
        params: {chunks[{text, index?}], doc_metadata?}
        tei: EmbeddingClient 实例（本地 TEI / 远程 API / auto 组合）。
        repo: KnowledgeChunkRepo 实例。
    Returns:
        {success, indexed_ids[], count, doc_id, elapsed_ms}
    """
    from ..services.knowledge_service import KnowledgeService

    return await KnowledgeService(tei=tei, repo=repo).index_chunks(
        params.get("chunks", []) or [],
        params.get("doc_metadata", {}) or {},
    )
