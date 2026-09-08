"""KnowledgeChunkRepo —— knowledge_chunks 表 CRUD。

提供批量写入、密集检索、稀疏检索、按文档删除。
"""

from __future__ import annotations
import json
import logging
import uuid
from typing import Optional

from sqlalchemy import func, literal, text as sa_text

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
        doc_id = doc_meta.get("doc_id")
        if not doc_id:
            raise ValueError(
                "doc_id is required in doc_meta (anchor to files.id); "
                "refusing to fallback to uuid4"
            )
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
                    content_hash=chunk.get("content_hash", ""),
                    ingest_status="indexed",
                )
                session.add(record)

        logger.info("batch_insert: %d chunks for doc '%s'", len(ids), doc_name)
        return ids

    def search_dense(
        self, embedding: list[float], top_k: int = 5,
        threshold: float = 0.3,
        doc_ids=None,
    ) -> list[dict]:
        """密集向量检索（cosine 相似度），支持 doc_id allowlist（M3 范围过滤）。

        Args:
            embedding: 查询向量（1024 维）。
            top_k: 返回结果数。
            threshold: 相似度阈值。
            doc_ids: 可选可见文件 id 集合（Select 或 list[str]），排序前过滤。

        Returns:
            [{id, doc_id, doc_name, chunk_index, chunk_text, similarity, metadata}]
        """
        with self._get_session() as session:
            q = session.query(
                KnowledgeChunk.id,
                KnowledgeChunk.doc_id,
                KnowledgeChunk.doc_name,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.chunk_text,
                KnowledgeChunk.metadata_.label("meta"),
                (1 - KnowledgeChunk.embedding.cosine_distance(embedding)).label("similarity"),
            ).filter(
                1 - KnowledgeChunk.embedding.cosine_distance(embedding) >= threshold,
            )
            if doc_ids is not None:
                q = q.filter(KnowledgeChunk.doc_id.in_(doc_ids))
            rows = q.order_by(
                KnowledgeChunk.embedding.cosine_distance(embedding),
            ).limit(top_k).all()

        return _rows_to_dicts(rows)

    def search_hybrid(
        self, query: str, embedding: list[float], top_k: int = 5,
        doc_ids=None,
    ) -> list[dict]:
        """混合检索：向量 + 关键词（pg_trgm）RRF 融合（M6）。

        内部复用 search_dense 与 _search_keyword，两条分支均接受 doc_ids allowlist，
        融合前不越界，融合后取 top_k。
        """
        candidate_n = max(top_k * 3, 10)
        dense = self.search_dense(embedding, top_k=candidate_n, threshold=0.0, doc_ids=doc_ids)
        sparse = self._search_keyword(query, top_k=candidate_n, doc_ids=doc_ids)
        return _rrf_fuse(dense, sparse, top_k)

    def _search_keyword(self, query: str, top_k: int = 10, doc_ids=None) -> list[dict]:
        """关键词召回（pg_trgm similarity），失败降级到 ILIKE 子串匹配。"""
        q = (query or "").strip()
        if not q:
            return []

        with self._get_session() as session:
            try:
                session.execute(sa_text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                rows = session.query(
                    KnowledgeChunk.id,
                    KnowledgeChunk.doc_id,
                    KnowledgeChunk.doc_name,
                    KnowledgeChunk.chunk_index,
                    KnowledgeChunk.chunk_text,
                    KnowledgeChunk.metadata_.label("meta"),
                    func.similarity(KnowledgeChunk.chunk_text, q).label("similarity"),
                ).filter(
                    func.similarity(KnowledgeChunk.chunk_text, q) > 0.05,
                )
                if doc_ids is not None:
                    rows = rows.filter(KnowledgeChunk.doc_id.in_(doc_ids))
                rows = rows.order_by(
                    func.similarity(KnowledgeChunk.chunk_text, q).desc(),
                ).limit(top_k).all()
            except Exception:
                logger.warning("pg_trgm keyword search failed, fallback to ILIKE")
                rows = session.query(
                    KnowledgeChunk.id,
                    KnowledgeChunk.doc_id,
                    KnowledgeChunk.doc_name,
                    KnowledgeChunk.chunk_index,
                    KnowledgeChunk.chunk_text,
                    KnowledgeChunk.metadata_.label("meta"),
                    literal(1.0).label("similarity"),
                ).filter(
                    KnowledgeChunk.chunk_text.ilike(f"%{q}%"),
                )
                if doc_ids is not None:
                    rows = rows.filter(KnowledgeChunk.doc_id.in_(doc_ids))
                rows = rows.order_by(KnowledgeChunk.chunk_text).limit(top_k).all()

        return _rows_to_dicts(rows)

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

    # ── M7: 入库状态机 ──

    def recover_failed(self) -> int:
        """failed → pending（崩溃恢复/手动重试）。"""
        with self._get_session() as session:
            result = session.query(KnowledgeChunk).filter(
                KnowledgeChunk.ingest_status == "failed",
            ).update({KnowledgeChunk.ingest_status: "pending"})
        logger.info("recover_failed: %d chunks → pending", result)
        return result

    def list_by_status(self, statuses: tuple[str, ...], limit: int = 100) -> list[dict]:
        """列出指定 ingest_status 的 chunk（M7 状态机领取）。"""
        with self._get_session() as session:
            rows = session.query(KnowledgeChunk).filter(
                KnowledgeChunk.ingest_status.in_(statuses),
            ).limit(limit).all()
        return [
            {"id": r.id, "doc_id": r.doc_id, "chunk_text": r.chunk_text}
            for r in rows
        ]

    def update_ingest_status(self, chunk_id: str, status: str) -> int:
        """更新单个 chunk 的 ingest_status（M7 状态机流转）。"""
        with self._get_session() as session:
            result = session.query(KnowledgeChunk).filter(
                KnowledgeChunk.id == chunk_id,
            ).update({KnowledgeChunk.ingest_status: status})
        return result

    def set_embedding(self, chunk_id: str, embedding: list[float]) -> int:
        """写入 embedding 并将状态置为 indexed（M7 终态）。"""
        with self._get_session() as session:
            result = session.query(KnowledgeChunk).filter(
                KnowledgeChunk.id == chunk_id,
            ).update({
                KnowledgeChunk.embedding: embedding,
                KnowledgeChunk.ingest_status: "indexed",
            })
        return result


def _rows_to_dicts(rows) -> list[dict]:
    """将查询行统一转为 dict（search_dense / _search_keyword 共用）。"""
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
            "similarity": round(float(row.similarity or 0.0), 4),
            "metadata": meta,
        })
    return results


def _rrf_fuse(dense: list[dict], sparse: list[dict], top_k: int) -> list[dict]:
    """RRF（Reciprocal Rank Fusion）融合两个排序列表，按 id 去重。

    score = Σ 1/(k + rank + 1)，k=60。融合后按 RRF 分降序取 top_k。
    """
    k = 60
    scores: dict[str, float] = {}
    by_id: dict[str, dict] = {}
    for ranked in (dense, sparse):
        for rank, item in enumerate(ranked):
            key = item.get("id") or item.get("doc_id") or ""
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in by_id:
                by_id[key] = item

    merged = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    out: list[dict] = []
    for key, _score in merged:
        item = dict(by_id[key])
        item["similarity"] = round(scores[key], 4)
        out.append(item)
    return out
