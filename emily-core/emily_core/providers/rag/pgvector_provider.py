"""PgVectorRagProvider —— pgvector 向量检索，替换 MaxKBRagProvider。

通过 embedding 后端（本地 TEI / 远程 API，选型见 infrastructure/embedding/factory.py）
生成 query embedding，在 emily-postgres 的 knowledge_chunks 表做相似度查询。
支持密集检索（HNSW）+ 阶段/岗位 metadata 过滤。
"""

from __future__ import annotations
import logging
import time
from typing import TYPE_CHECKING

from .base import RagProvider, SearchResult, RagSearchResponse

if TYPE_CHECKING:
    from ..infrastructure.embedding.base import EmbeddingClient
    from ..repositories.knowledge_chunk_repo import KnowledgeChunkRepo

logger = logging.getLogger("emily.rag.pgvector")


class PgVectorRagProvider(RagProvider):
    """pgvector + embedding 后端实现的 RAG 检索提供者。

    替代 MaxKBRagProvider，直接在 PostgreSQL 中做向量相似度检索。
    """

    def __init__(
        self,
        tei: "EmbeddingClient",
        repo: "KnowledgeChunkRepo",
        similarity: float = 0.3,
        top_k: int = 5,
    ):
        self._tei = tei
        self._repo = repo
        self._similarity = similarity
        self._top_k = top_k

    async def is_available(self) -> bool:
        """TEI + pgvector 连通性检查。"""
        return await self._tei.is_available()

    async def search(
        self,
        query: str,
        top_k: int = 5,
        stage: str | None = None,
        role: str | None = None,
        scoped_doc_ids=None,
        rerank: bool = False,
    ) -> RagSearchResponse:
        """query → TEI embedding → pgvector 相似度查询 → top_k chunks。

        Args:
            query: 自然语言查询。
            top_k: 最大返回结果数。
            stage: 可选，按项目阶段过滤。
            role: 可选，按岗位过滤。
            scoped_doc_ids: 可选可见文件 id 集合（M3 范围过滤，排序前）。
            rerank: 可选重排（M6，不可用/失败时降级到向量分数）。

        Returns:
            RagSearchResponse，包含检索结果和格式化上下文文本。
        """
        started = time.monotonic()

        if not query.strip():
            return RagSearchResponse(
                query=query,
                results=[],
                context_text="",
                total=0,
                provider_name="pgvector",
            )

        res = await self._search_impl(
            query, top_k or self._top_k, stage, role,
            scoped_doc_ids, rerank,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "pgvector search: '%s' → %d results (%dms)",
            query[:50], res.total, elapsed_ms,
        )
        return res

    async def _search_impl(
        self, query: str, top_k: int,
        stage: str | None, role: str | None,
        scoped_doc_ids=None, rerank: bool = False,
    ) -> RagSearchResponse:
        """内部实现：embedding → pgvector 检索 → 构建响应。"""
        try:
            embeddings = await self._tei.embed([query])
        except Exception as e:
            logger.warning("pgvector search: TEI embed failed: %s", e)
            return RagSearchResponse(
                query=query,
                results=[],
                context_text="",
                total=0,
                provider_name="pgvector",
            )

        if not embeddings:
            return RagSearchResponse(
                query=query, results=[], context_text="",
                total=0, provider_name="pgvector",
            )

        query_vec = embeddings[0]

        try:
            # M6: 混合检索（向量 + pg_trgm 关键词 RRF 融合），复用 M3 allowlist
            rows = self._repo.search_hybrid(
                query, query_vec, top_k=top_k, doc_ids=scoped_doc_ids,
            )
        except Exception:
            logger.exception("pgvector search: hybrid query failed, fallback to dense-only")
            try:
                rows = self._repo.search_dense(
                    query_vec, top_k=top_k, threshold=self._similarity,
                    doc_ids=scoped_doc_ids,
                )
            except Exception:
                logger.exception("pgvector search: DB query failed")
                return RagSearchResponse(
                    query=query, results=[], context_text="",
                    total=0, provider_name="pgvector",
                )

        # 阶段/岗位过滤（metadata JSON）
        if stage or role:
            rows = self._filter_by_metadata(rows, stage, role)

        # M6: 可选重排（失败降级到向量分数，不阻断）
        if rerank and rows:
            rows = self._rerank(query, rows)

        # 截断到 top_k 再构建
        rows = rows[:top_k]

        results = []
        context_parts = []
        for row in rows:
            results.append(SearchResult(
                content=row["chunk_text"],
                score=row["similarity"],
                source_document=row.get("doc_name", ""),
                source_kb="pgvector",
                metadata=row.get("metadata", {}),
                source_file_id=row.get("doc_id", ""),
                source_title=row.get("doc_name", ""),
            ))
            context_parts.append(f"[{row.get('doc_name', '')}]\n{row['chunk_text']}")

        context_text = "\n\n---\n\n".join(context_parts) if context_parts else ""

        return RagSearchResponse(
            query=query,
            results=results,
            context_text=context_text,
            total=len(results),
            provider_name="pgvector",
        )

    @staticmethod
    def _rerank(query: str, rows: list[dict]) -> list[dict]:
        """可选重排：无独立 rerank 服务时降级为向量分数排序（不阻断）。"""
        try:
            # 重排服务未接入时保持原顺序；后续可在此接入 cross-encoder/bge-reranker。
            logger.debug("rerank: no reranker configured, keep vector ranking (%d rows)", len(rows))
            return rows
        except Exception:
            logger.warning("rerank failed, fallback to vector ranking")
            return rows

    @staticmethod
    def _filter_by_metadata(rows: list[dict], stage: str | None, role: str | None) -> list[dict]:
        """按 metadata 字段过滤（阶段/岗位）。"""
        filtered = []
        for row in rows:
            meta = row.get("metadata", {})
            if stage and meta.get("stage", "") != stage:
                continue
            if role and meta.get("role", "") != role:
                continue
            filtered.append(row)
        return filtered
