"""TEI (Text Embeddings Inference) 客户端 —— BGE-m3 embedding 服务。

TEI 容器提供 /embed 和 /embed-sparse 接口。
BGE-m3 返回密集向量（1024 维），可选稀疏向量。
"""

from __future__ import annotations
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("emily.tei")


class TeiClient:
    """TEI embedding 服务客户端。

    通过 HTTP API 调用 TEI 容器的 /embed 接口。
    """

    def __init__(self, base_url: str, timeout: int = 60):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成密集向量。

        Args:
            texts: 文本列表。

        Returns:
            向量列表，每个向量为 1024 维 float 列表。
        """
        if not texts:
            return []

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._base_url}/embed",
                    json={"inputs": texts},
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(
                            f"TEI /embed returned {resp.status}: {body[:500]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"TEI request failed: {e}") from e

        # TEI 返回格式: [[vec1], [vec2], ...]
        if isinstance(data, list):
            return data
        # 某些版本包了一层
        if isinstance(data, dict) and "embeddings" in data:
            return data["embeddings"]
        raise RuntimeError(f"Unexpected TEI response format: {type(data)}")

    async def embed_sparse(self, texts: list[str]) -> list[dict]:
        """批量生成稀疏向量（可选，BGE-m3 支持 /embed-sparse）。

        Args:
            texts: 文本列表。

        Returns:
            稀疏向量列表 [{index: value, ...}, ...]
        """
        if not texts:
            return []

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._base_url}/embed-sparse",
                    json={"inputs": texts},
                ) as resp:
                    if resp.status == 404:
                        logger.warning("TEI /embed-sparse not available (BGE-m3 sparse not configured)")
                        return [{} for _ in texts]
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(
                            f"TEI /embed-sparse returned {resp.status}: {body[:500]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"TEI sparse request failed: {e}") from e

        if isinstance(data, list):
            return data
        return [{} for _ in texts]

    async def is_available(self) -> bool:
        """TEI 健康检查（GET /health）。"""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                async with session.get(f"{self._base_url}/health") as resp:
                    return resp.status == 200
        except Exception:
            return False
