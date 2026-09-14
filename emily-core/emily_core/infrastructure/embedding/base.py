"""Embedding 客户端统一接口。

两种实现都满足本接口，调用方只依赖 ``EmbeddingClient``，不感知具体后端：

- ``TeiClient``             —— 本地 TEI 容器（BGE-m3），见 tei_client.py
- ``RemoteEmbeddingClient`` —— 远程 OpenAI 兼容 /v1/embeddings，见 remote_client.py

后端选择规则（本地优先、远程兜底）见 ``factory.py``。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingClient(Protocol):
    """文本嵌入客户端统一接口。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成密集向量，返回值与入参等长、顺序一一对应。"""
        ...

    async def is_available(self) -> bool:
        """后端连通性检查（不可用返回 False，不抛异常）。"""
        ...
