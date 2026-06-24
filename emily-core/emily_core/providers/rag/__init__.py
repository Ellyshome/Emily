"""RAG 提供者注册表。

导出:
  - RagProvider / SearchResult / RagSearchResponse: ABC 与数据模型
  - MaxKBRagProvider: MaxKB HTTP API 检索（懒加载，需 aiohttp）
  - LocalFileRagProvider: 本地 TF 关键词搜索（零依赖回退）
"""

from .base import RagProvider, SearchResult, RagSearchResponse
from .local_fallback import LocalFileRagProvider


def get_maxkb_provider():
    """懒加载 MaxKBRagProvider（避免本地环境缺 aiohttp 时导入失败）。"""
    from .maxkb_provider import MaxKBRagProvider
    return MaxKBRagProvider


__all__ = [
    "RagProvider", "SearchResult", "RagSearchResponse",
    "MaxKBRagProvider", "LocalFileRagProvider", "get_maxkb_provider",
]
