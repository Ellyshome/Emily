"""RAG 提供者注册表。

导出:
  - RagProvider / SearchResult / RagSearchResponse: ABC 与数据模型
  - PgVectorRagProvider: pgvector + TEI 向量检索（替代 MaxKB）
  - LocalFileRagProvider: 本地 TF 关键词搜索（零依赖回退）
"""

from .base import RagProvider, SearchResult, RagSearchResponse
from .local_fallback import LocalFileRagProvider


def get_pgvector_provider():
    """懒加载 PgVectorRagProvider。"""
    from .pgvector_provider import PgVectorRagProvider
    return PgVectorRagProvider


__all__ = [
    "RagProvider", "SearchResult", "RagSearchResponse",
    "PgVectorRagProvider", "LocalFileRagProvider", "get_pgvector_provider",
]
