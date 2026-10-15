"""knowledge_library —— 资料库（数字资产管理）服务层包。

模块构成（与概要设计 M1/M2 对应）：
  - library_fs.py     M1 资料库文件仓储（sync 文件系统原语 + 越界防护）
  - ingest_service.py M2 入库引擎（一次性遍历、逐级生成索引、幂等、dry-run、留痕）

约束：本包与既有 RAG 检索通道并存且互相隔离 —— 资料库目录不被注册为检索源。
"""

from .library_fs import (
    BINARY_EXTENSIONS,
    DEFAULT_MAX_READ_BYTES,
    INDEX_FILENAME,
    TEXT_EXTENSIONS,
    KnowledgeLibraryError,
    KnowledgeLibraryFs,
    KnowledgeLibraryNotFoundError,
    KnowledgeLibraryPathError,
    KnowledgeLibraryTypeError,
    resolve_root,
)

__all__ = [
    "BINARY_EXTENSIONS",
    "DEFAULT_MAX_READ_BYTES",
    "INDEX_FILENAME",
    "TEXT_EXTENSIONS",
    "KnowledgeLibraryError",
    "KnowledgeLibraryFs",
    "KnowledgeLibraryNotFoundError",
    "KnowledgeLibraryPathError",
    "KnowledgeLibraryTypeError",
    "resolve_root",
]
