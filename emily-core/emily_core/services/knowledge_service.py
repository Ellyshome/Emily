"""KnowledgeService —— 知识库动作服务（知识入库 / 检索 / 移除的唯一实现）。

方案 A（见 `issues/操作留痕治理/操作留痕治理_计划_V1.md` §七）：
本 service 是知识入库/检索/移除的**唯一实现**，`tools/embed_tool.py` 退化为薄壳，
console 路由改为调用本 service —— 工具路径与后台路径同源同痕。

动作边界（FR-15）：**公开方法各挂一次 `@audited`**；内部共用私有 `_embed_and_insert`，
避免一次动作记两条留痕。

依赖注入：`tei`（EmbeddingClient）与 `repo`（KnowledgeChunkRepo）由调用方注入
（console 按请求选后端、工具经注册期 partial 注入），本层不自行选型；
`storage` 可选，缺省用 `FileStorageService()`（console 需传入 core 的实例，
因为容器实际挂载 `/app/attachments` 与无参默认值不一致）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..infrastructure.logging.audit import audited

if TYPE_CHECKING:
    from ..infrastructure.embedding.base import EmbeddingClient
    from ..repositories.knowledge_chunk_repo import KnowledgeChunkRepo

logger = logging.getLogger("emily.service.knowledge")


class KnowledgeService:
    """知识库动作服务。"""

    def __init__(
        self,
        tei: "Optional[EmbeddingClient]" = None,
        repo: "Optional[KnowledgeChunkRepo]" = None,
        storage=None,
        similarity: float = 0.3,
    ):
        self._tei = tei
        self._repo = repo
        self._storage = storage
        self._similarity = similarity

    # ── 依赖 ──

    def _chunk_repo(self) -> "KnowledgeChunkRepo":
        if self._repo is None:
            from ..repositories.knowledge_chunk_repo import KnowledgeChunkRepo

            self._repo = KnowledgeChunkRepo()
        return self._repo

    def _storage_service(self):
        if self._storage is not None:
            return self._storage
        from .file_storage_service import FileStorageService

        return FileStorageService()

    # ── 入库（公开动作入口 1：chunk 级，工具路径）──

    @audited(
        category="knowledge",
        action="indexed",
        target_type="document",
        target_arg="doc_metadata.doc_id",
        summary="知识入库：{target}",
    )
    async def index_chunks(self, chunks: list[dict], doc_metadata: dict) -> dict:
        """文本 chunks → embedding → 入 pgvector。

        Args:
            chunks: [{text, index?}]
            doc_metadata: {doc_id（必填，锚定 files.id）, doc_name?, stage?, role?, ...}
        Returns:
            {success, indexed_ids[], count, doc_id, elapsed_ms}
        """
        return await self._embed_and_insert(chunks, doc_metadata or {})

    # ── 入库（公开动作入口 2：文件级，console / 单动作入口）──

    @audited(
        category="knowledge",
        action="indexed_file",
        target_type="document",
        target_arg="file_id",
        actor_arg="operator_id",
        summary="文件知识入库：{target}",
    )
    async def index_file(self, file_id: str, operator_id: str = "") -> dict:
        """文件 → 解析 → 结构分块 → 入 pgvector。

        Returns:
            {success, file_id, file_no?, filename?, doc_id?, count?, elapsed_ms?} 或 {success:False, error}
        """
        prep = await asyncio.to_thread(self._prepare_file_chunks, file_id)
        if not prep.get("success"):
            return prep

        res = await self._embed_and_insert(prep["chunks"], {
            "doc_id": file_id,
            "doc_name": prep.get("file_no") or file_id,
            "stage": "file_index",
            "role": operator_id or "",
        })
        if not res.get("success"):
            return res

        return {
            "success": True,
            "file_id": file_id,
            "file_no": prep.get("file_no", ""),
            "filename": prep.get("filename", ""),
            "doc_id": res.get("doc_id", file_id),
            "count": res.get("count", 0),
            "elapsed_ms": res.get("elapsed_ms", 0),
        }

    # ── 移除 ──

    @audited(
        category="knowledge",
        action="removed",
        target_type="document",
        target_arg="doc_id",
        actor_arg="operator_id",
        summary="知识移除：{target}",
    )
    async def remove_document(self, doc_id: str, operator_id: str = "") -> dict:
        """删除库内指定文档的全部向量分块（doc_id 锚定 files.id）。"""
        if not doc_id:
            return {"success": False, "error": "doc_id is required"}
        try:
            deleted = await asyncio.to_thread(self._chunk_repo().delete_by_doc, doc_id)
        except Exception as e:
            logger.warning("knowledge.remove_document failed doc_id=%s: %s", doc_id, e)
            return {"success": False, "error": f"删除失败：{e}"}
        return {"success": True, "doc_id": doc_id, "deleted": int(deleted or 0)}

    # ── 检索（高风险读，按 FR-2 记录）──

    @audited(
        category="knowledge",
        action="searched",
        target_type="document",
        summary_arg="query",
        actor_arg="operator_id",
        summary="知识查库：{summary}",
    )
    async def search(
        self,
        query: str,
        top_k: int = 5,
        scoped_doc_ids: Optional[list[str]] = None,
        operator_id: str = "",
    ) -> dict:
        """向量检索（pgvector）。失败与"无结果"均可区分。"""
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "query is required"}
        if self._tei is None:
            return {"success": False, "error": "embedding 后端未就绪"}

        from ..providers.rag.pgvector_provider import PgVectorRagProvider

        provider = PgVectorRagProvider(
            tei=self._tei, repo=self._chunk_repo(), similarity=self._similarity,
        )
        try:
            resp = await provider.search(query, top_k=top_k, scoped_doc_ids=scoped_doc_ids)
        except Exception as e:
            logger.warning("knowledge.search failed: %s", e)
            return {"success": False, "error": f"查库失败：{e}"}

        return {
            "success": True,
            "query": query,
            "total": resp.total,
            "results": [
                {
                    "content": r.content,
                    "score": r.score,
                    "source_document": r.source_document,
                    "source_file_id": r.source_file_id,
                    "source_title": r.source_title,
                }
                for r in resp.results
            ],
        }

    # ── 内部共享实现（不挂留痕：调用方公开方法已记一次）──

    async def _embed_and_insert(self, chunks: list[dict], doc_meta: dict) -> dict:
        """embedding + 入 pgvector（工具与 console 共用同一实现）。"""
        started = time.monotonic()

        if not chunks:
            return {"success": False, "error": "chunks is empty"}

        # doc_id 必填校验（锚定 files.id，禁止回退 uuid4）
        if not doc_meta.get("doc_id"):
            return {"success": False, "error": "doc_metadata.doc_id is required (files.id)"}

        texts = [c.get("text", "") for c in chunks]
        if not any(texts):
            return {"success": False, "error": "all chunks have empty text"}

        if self._tei is None:
            return {"success": False, "error": "embedding 后端未就绪"}

        try:
            embeddings = await self._tei.embed(texts)
        except Exception as e:
            logger.warning("knowledge.index: embed failed: %s", e)
            return {"success": False, "error": f"embedding failed: {e}"}

        if len(embeddings) != len(chunks):
            return {"success": False,
                    "error": f"embedding count mismatch: got {len(embeddings)}, expected {len(chunks)}"}

        try:
            ids = self._chunk_repo().batch_insert(chunks, embeddings, doc_meta)
        except Exception as e:
            logger.warning("knowledge.index: batch_insert failed: %s", e)
            return {"success": False, "error": f"DB insert failed: {e}"}

        return {
            "success": True,
            "indexed_ids": ids,
            "count": len(ids),
            "doc_id": doc_meta.get("doc_id", ids[0] if ids else ""),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    def _prepare_file_chunks(self, file_id: str) -> dict:
        """读取文件实体 → 解析文本 → 结构分块（同步，由调用方用 to_thread 包裹）。"""
        from ..repositories.file_repo import FileRepository
        from .document_parser import DocumentParser
        from .structural_chunker import StructuralChunker

        f = FileRepository.get_by_id(file_id)
        if f is None:
            return {"success": False, "error": "文件不存在"}
        if not f.storage_path:
            return {"success": False, "error": "文件缺少本地存储路径"}

        file_no = f.file_no or file_id
        filename = f.filename or file_no
        storage_root = str(self._storage_service()._storage_root)
        local_path = Path(storage_root) / f.storage_path
        if not local_path.exists():
            return {"success": False, "error": f"文件实体不存在：{local_path.name}"}

        text = DocumentParser().parse(local_path)
        if not text.strip():
            return {"success": False, "error": "文件解析后无文本内容（可能是不支持的二进制格式）"}

        chunks = StructuralChunker().chunk(text)
        if not chunks:
            return {"success": False, "error": "分块结果为空"}

        return {
            "success": True,
            "chunks": chunks,
            "file_no": file_no,
            "filename": filename,
        }
