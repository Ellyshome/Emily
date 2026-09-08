"""IngestJobHandler —— RAG 入库作业状态机（M7）。

处理 knowledge_chunks.ingest_status 的 5 态流转：
    pending → parsing → embedding → indexed / failed，failed → pending 可恢复。

接入 JobHandlerRegistry + scheduler_config.json（action_type = "rag_ingest"），
由调度器按声明式配置触发；崩溃后可重跑恢复 failed 记录。
"""

from __future__ import annotations

import asyncio
import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.ingest")


class IngestJobHandler(SchedulerJobHandler):
    """RAG 入库状态机处理器。"""

    action_type = "rag_ingest"
    description = "RAG 入库状态机：failed→pending 恢复 + pending chunk 向量化写入"

    def __init__(self, repo=None, embedding_client=None):
        self._repo = repo
        self._embedding_client = embedding_client

    async def execute(self, params: dict) -> JobResult:
        dry_run = params.get("dry_run", False)
        limit = int(params.get("limit", 100))
        retry_failed = params.get("retry_failed", True)

        from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo

        repo = self._repo or KnowledgeChunkRepo()

        # 状态机：failed → pending（崩溃恢复/手动重试）
        recovered = 0
        if retry_failed and not dry_run:
            recovered = await asyncio.to_thread(repo.recover_failed)

        # 领取 pending
        pending = await asyncio.to_thread(repo.list_by_status, ("pending",), limit)

        indexed = 0
        failed = 0
        for chunk in pending:
            cid = chunk["id"]
            text = chunk["chunk_text"]
            try:
                await asyncio.to_thread(repo.update_ingest_status, cid, "parsing")
                if not text or not text.strip():
                    raise ValueError("empty chunk_text")
                await asyncio.to_thread(repo.update_ingest_status, cid, "embedding")
                if dry_run:
                    await asyncio.to_thread(repo.update_ingest_status, cid, "indexed")
                else:
                    if self._embedding_client is None:
                        raise RuntimeError("no embedding client configured")
                    vecs = await self._embedding_client.embed([text])
                    if not vecs:
                        raise RuntimeError("embedding returned empty")
                    await asyncio.to_thread(repo.set_embedding, cid, vecs[0])
                indexed += 1
            except Exception as e:
                logger.warning("rag_ingest chunk %s failed: %s", cid, e)
                if not dry_run:
                    await asyncio.to_thread(repo.update_ingest_status, cid, "failed")
                failed += 1

        summary = (
            f"入库状态机：pending={len(pending)}, indexed={indexed}, "
            f"failed={failed}, recovered={recovered}"
        )
        logger.info(summary)
        return JobResult(
            success=True,
            summary=summary,
            data={
                "status": "done",
                "pending": len(pending),
                "indexed": indexed,
                "failed": failed,
                "recovered": recovered,
                "next_state": "indexed" if failed == 0 else "failed",
            },
        )
