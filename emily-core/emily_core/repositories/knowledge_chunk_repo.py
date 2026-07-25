"""KnowledgeChunkRepo —— knowledge_chunks 表 CRUD。

提供批量写入、密集检索、稀疏检索、按文档删除。
"""

from __future__ import annotations
import json
import logging
import uuid
from typing import Optional

from sqlalchemy import text as sa_text

from ..infrastructure.database.models import KnowledgeChunk

logger = logging.getLogger("emily.repo.knowledge_chunk")


class KnowledgeChunkRepo:
    """knowledge_chunks 表的数据访问层。"""

    def __init__(self, db_url: str | None = None):
        self._db_url = db_url

    def _get_session(self):
        """获取数据库 session。"""
        from ..infrastructure.database.session import get_session
        return get_session()

    def batch_insert(
        self, chunks: list[dict], embeddings: list[list[float]],
        doc_meta: dict | None = None,
    ) -> list[str]:
        """批量写入 chunks + embedding。

        Args:
            chunks: [{text, index, ...}]
            embeddings: 对应 chunk 的密集向量列表。
            doc_meta: 文档级元数据 {doc_id, doc_name, stage, role, ...}。

        Returns:
            写入的 chunk ID 列表。
        """
        doc_meta = doc_meta or {}
        doc_id = doc_meta.get("doc_id", str(uuid.uuid4()))
        doc_name = doc_meta.get("doc_name", "")
        metadata_json = json.dumps(doc_meta, ensure_ascii=False)

        ids = []
        with self._get_session() as session:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_id = str(uuid.uuid4())
                ids.append(chunk_id)
                record = KnowledgeChunk(
                    id=chunk_id,
                    doc_id=doc_id,
                    doc_name=doc_name,
                    chunk_index=chunk.get("index", i),
                    chunk_text=chunk.get("text", ""),
                    embedding=emb,
                    metadata_=metadata_json,
                )
                session.add(record)

        logger.info("batch_insert: %d chunks for doc '%s'", len(ids), doc_name)
        return ids

    def search_dense(
        self, embedding: list[float], top_k: int = 5,
        threshold: float = 0.3,
    ) -> list[dict]:
        """密集向量检索（cosine 相似度）。

        Args:
            embedding: 查询向量（1024 维）。
            top_k: 返回结果数。
            threshold: 相似度阈值。

        Returns:
            [{id, doc_id, doc_name, chunk_index, chunk_text, similarity, metadata}]
        """
        with self._get_session() as session:
            rows = session.query(
                KnowledgeChunk.id,
                KnowledgeChunk.doc_id,
                KnowledgeChunk.doc_name,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_text,
                KnowledgeChunk.metadata_.label("meta"),
                (1 - KnowledgeChunk.embedding.cosine_distance(embedding)).label("similarity"),
            ).filter(
                1 - KnowledgeChunk.embedding.cosine_distance(embedding) >= threshold,
            ).order_by(
                KnowledgeChunk.embedding.cosine_distance(embedding),
            ).limit(top_k).all()

        results = []
        for row in rows:
            try:
                meta = json.loads(row.meta) if row.meta else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            results.append({
                "id": row.id,
                "doc_id": row.doc_id,
                "doc_name": row.doc_name,
                "chunk_index": row.chunk_index,
                "chunk_text": row.chunk_text,
                "similarity": round(float(row.similarity), 4),
                "metadata": meta,
            })
        return results

    def search_sparse(
        self, sparse_vector: dict, top_k: int = 5,
    ) -> list[dict]:
        """稀疏向量检索（tsvector，后续实现）。"""
        # 稀疏检索暂不实现，留接口
        return []

    def delete_by_doc(self, doc_id: str) -> int:
        """删除指定文档的所有 chunks。"""
        sql = sa_text("DELETE FROM knowledge_chunks WHERE doc_id = :doc_id")
        with self._get_session() as session:
            result = session.execute(sql, {"doc_id": doc_id})
        logger.info("delete_by_doc: removed chunks for doc '%s'", doc_id)
        return result.rowcount if result else 0
