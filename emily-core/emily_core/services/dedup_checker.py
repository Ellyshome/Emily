"""DedupChecker — content 哈希去重（M5）。

以 chunk 原文 sha256 为指纹，判断 knowledge_chunks 是否已存在相同内容，
避免重复入库。
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger("emily.service.dedup_checker")


class DedupChecker:
    """按 content sha256 判断重复。"""

    @staticmethod
    def content_hash(text: str) -> str:
        """计算 chunk 原文的 sha256 指纹。"""
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def is_duplicate(self, content_hash: str) -> bool:
        """判断指定 content_hash 是否已存在于 knowledge_chunks。"""
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import KnowledgeChunk

        with get_session() as session:
            existing = session.query(KnowledgeChunk.id).filter(
                KnowledgeChunk.content_hash == content_hash,
            ).first()
        return existing is not None
