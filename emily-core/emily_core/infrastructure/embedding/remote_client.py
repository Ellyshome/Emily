"""OpenAI 兼容 Embedding API 客户端 —— 替代本地 TEI 容器。

调用 SiliconFlow / OpenAI 等提供商的 /v1/embeddings 接口，
与 TeiClient 暴露相同的 embed() 接口，PgVectorRagProvider 无需修改。
"""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger("emily.embedding.remote")


class RemoteEmbeddingClient:
    """OpenAI 兼容 Embedding API 客户端。

    通过 HTTP API 调用远程模型生成 embedding 向量。
    接口格式与 TeiClient 一致，可无缝替换。
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
    ):
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成密集向量。

        Args:
            texts: 文本列表。

        Returns:
            向量列表，维度由模型决定（BGE-m3 为 1024 维）。
        """
        if not texts:
            return []

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": texts,
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.post(self._api_url, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(
                            f"Remote embedding API returned {resp.status}: {body[:500]}"
                        )
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Remote embedding API request failed: {e}") from e

        # OpenAI 兼容格式: {"data": [{"embedding": [...], "index": 0}, ...]}
        if isinstance(data, dict) and "data" in data:
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            return [item["embedding"] for item in items]
        # 宽松兼容：直接返回 list[list] 也接受
        if isinstance(data, list):
            return data

        raise RuntimeError(f"Unexpected remote embedding API response format: {type(data)}")

    async def is_available(self) -> bool:
        """健康检查。"""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                headers = {"Authorization": f"Bearer {self._api_key}"}
                # 尝试调用一个极简 embedding 来验证连通性
                async with session.post(
                    self._api_url,
                    json={"model": self._model, "input": "ping"},
                    headers=headers,
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
