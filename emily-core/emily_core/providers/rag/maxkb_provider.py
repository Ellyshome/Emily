"""MaxKBRagProvider —— 通过 MaxKB Admin hit_test API 检索知识库。

MaxKB Admin REST API（非文档化，但稳定可用）:
  - POST /admin/api/user/login                         登录获取 token
  - GET  /admin/api/workspace/default/knowledge         列出所有知识库
  - POST /admin/api/workspace/default/knowledge/{id}/hit_test  纯向量检索

与 Chat API 不同，hit_test 不经过 LLM，直接返回向量检索命中的段落+相似度分数。

检索策略：自动列出工作区下所有知识库，逐库 hit_test，聚合后按相似度排序返回 top_k。
无需预设 knowledge_id。

用法:
    provider = MaxKBRagProvider(
        base_url="http://maxkb:8080",
        admin_password="xxx",
    )
    if await provider.is_available():
        resp = await provider.search("住宅层高标准", top_k=5)
"""

import logging
from typing import Optional

import aiohttp

from .base import RagProvider, SearchResult, RagSearchResponse

logger = logging.getLogger("emily.rag.maxkb")

# MaxKB 管理员账号固定为 admin
_ADMIN_USER = "admin"
# 工作区固定为 default
_WORKSPACE = "default"


class MaxKBRagProvider(RagProvider):
    """MaxKB hit_test API 知识库检索提供者。

    通过 MaxKB 管理后台的 hit_test 接口进行纯向量检索，
    返回知识库中命中的段落原文 + 相似度分数，不经过 LLM。

    检索时自动列出工作区下所有知识库，聚合结果后按相似度排序。
    认证流程：登录 → 缓存 token → 列出知识库 → 逐库 hit_test。
    Token 过期时（401）自动重新登录重试一次。

    依赖 aiohttp（emily_core 无 AstrBot 依赖）。
    """

    def __init__(
        self,
        base_url: str = "http://maxkb:8080",
        admin_password: str = "",
        search_mode: str = "embedding",
        similarity: float = 0.3,
        timeout: int = 60,
    ):
        self._base_url = base_url.rstrip("/")
        self._admin_password = admin_password
        self._search_mode = search_mode
        self._similarity = similarity
        self._timeout = timeout
        self._available: Optional[bool] = None
        self._auth_token: Optional[str] = None

    # ── 认证 ──────────────────────────────────────────

    async def _login(self) -> Optional[str]:
        """登录 MaxKB 管理后台，返回 auth token。

        Returns:
            token 字符串，失败返回 None。
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/admin/api/user/login",
                    json={"username": _ADMIN_USER, "password": self._admin_password},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("MaxKB admin login failed: HTTP %d", resp.status)
                        return None
                    data = await resp.json()
                    token = data.get("data", {}).get("token")
                    if token:
                        logger.info("MaxKB admin login successful")
                        return token
                    logger.warning("MaxKB admin login response missing token: %s",
                                   str(data)[:200])
                    return None
        except Exception as e:
            logger.warning("MaxKB admin login error: %s", e)
            return None

    # ── 知识库列表 ────────────────────────────────────

    async def _list_knowledge_ids(self, token: str) -> list[str]:
        """列出工作区下所有知识库 ID。

        Returns:
            知识库 ID 列表，失败返回空列表。
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base_url}/admin/api/workspace/{_WORKSPACE}/knowledge",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("MaxKB list knowledge failed: HTTP %d", resp.status)
                        return []
                    data = await resp.json()
                    kb_list = data.get("data", [])
                    ids = [kb["id"] for kb in kb_list if kb.get("id")]
                    logger.info("MaxKB knowledge bases found: %d", len(ids))
                    return ids
        except Exception as e:
            logger.warning("MaxKB list knowledge error: %s", e)
            return []

    # ── 可用性检查 ────────────────────────────────────

    async def is_available(self) -> bool:
        """通过尝试登录验证 MaxKB 连通性和凭据有效性。

        结果会缓存，避免重复登录。
        """
        if self._available is not None:
            return self._available

        if not self._admin_password:
            logger.debug("MaxKB: admin_password 未配置")
            self._available = False
            return False

        token = await self._login()
        self._available = token is not None
        if token:
            self._auth_token = token
        return self._available

    # ── 检索 ──────────────────────────────────────────

    async def search(
        self, query: str, top_k: int = 5,
        stage: str | None = None,
        role: str | None = None,
    ) -> RagSearchResponse:
        """调用 MaxKB hit_test 接口进行纯向量检索。

        自动列出所有知识库，逐库检索后按相似度聚合排序，返回 top_k。
        不经过 LLM。

        Args:
            query: 自然语言查询
            top_k: 返回的最大段落数
            stage: 可选，附加到查询中过滤阶段
            role: 可选，附加到查询中过滤角色

        Returns:
            RagSearchResponse，即使失败也返回合法对象（不抛异常）
        """
        if not await self.is_available():
            return RagSearchResponse(
                query=query, results=[], context_text="",
                total=0, provider_name="MaxKB (unavailable)",
            )

        # 构建查询（附加 stage/role 过滤条件）
        augmented_query = query
        filters = []
        if stage:
            filters.append(f"阶段={stage}")
        if role:
            filters.append(f"角色={role}")
        if filters:
            augmented_query = f"{query}（过滤条件：{', '.join(filters)}）"

        # 列出所有知识库
        kb_ids = await self._list_knowledge_ids(self._auth_token)

        # 401 → 重登录后重试
        if not kb_ids:
            # 空列表可能是 token 过期或真的无知识库，先重登录试试
            logger.info("MaxKB: re-logging in to fetch knowledge list...")
            self._available = None
            new_token = await self._login()
            if new_token:
                self._auth_token = new_token
                self._available = True
                kb_ids = await self._list_knowledge_ids(new_token)

        if not kb_ids:
            return RagSearchResponse(
                query=query, results=[], context_text="",
                total=0, provider_name="MaxKB (no knowledge bases)",
            )

        # 逐库检索，聚合所有结果
        all_results: list[SearchResult] = []
        for kb_id in kb_ids:
            results = await self._do_hit_test_for_kb(
                self._auth_token, kb_id, augmented_query, top_k,
            )
            # 401 → 重登录后重试该库
            if results is None:
                logger.info("MaxKB token expired during hit_test, re-logging in...")
                self._available = None
                new_token = await self._login()
                if new_token:
                    self._auth_token = new_token
                    self._available = True
                    results = await self._do_hit_test_for_kb(
                        new_token, kb_id, augmented_query, top_k,
                    )
                    if results is None:
                        results = []
            if results:
                all_results.extend(results)

        # 按相似度降序排序，取 top_k
        all_results.sort(key=lambda r: r.score, reverse=True)
        all_results = all_results[:top_k]

        # 构建 context_text
        contexts = []
        for i, r in enumerate(all_results):
            contexts.append(
                f"### [{i + 1}] {r.source_document} (相似度: {r.score:.2%})\n{r.content}"
            )
        context_text = "\n\n---\n\n".join(contexts)

        return RagSearchResponse(
            query=query,
            results=all_results,
            context_text=context_text,
            total=len(all_results),
            provider_name=f"MaxKB (hit_test, mode={self._search_mode}, kbs={len(kb_ids)})",
        )

    async def _do_hit_test_for_kb(
        self, token: str, kb_id: str, query: str, top_k: int,
    ) -> list[SearchResult] | None:
        """对指定知识库执行一次 hit_test 请求。

        Returns:
            results 列表；None 表示需要重登录（401）
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/admin/api/workspace/{_WORKSPACE}"
                    f"/knowledge/{kb_id}/hit_test",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query_text": query,
                        "top_number": top_k,
                        "similarity": self._similarity,
                        "search_mode": self._search_mode,
                    },
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    # 401 → 通知上层重登录
                    if resp.status == 401:
                        return None

                    if resp.status != 200:
                        text = await resp.text()
                        logger.error("MaxKB hit_test HTTP %d (kb=%s): %s",
                                     resp.status, kb_id[:8], text[:300])
                        return []

                    data = await resp.json()

            # 检查业务状态码
            if data.get("code") != 200:
                logger.error("MaxKB hit_test API error (kb=%s): %s",
                             kb_id[:8], str(data)[:300])
                return []

            hits = data.get("data", [])
            if not hits:
                return []

            results = []
            for i, hit in enumerate(hits):
                content = hit.get("content", "").strip()
                score = hit.get("similarity", 0.0)
                title = hit.get("title", "")
                doc_name = hit.get("document_name", "")
                source = doc_name or title or "未知来源"

                results.append(SearchResult(
                    content=content,
                    score=round(score, 4),
                    source_document=source,
                    source_kb=f"kb:{kb_id[:8]}",
                    metadata={
                        "kb_id": kb_id,
                        "title": title,
                        "document_name": doc_name,
                        "similarity": score,
                        "index": i,
                    },
                ))
            return results

        except aiohttp.ClientError as e:
            logger.error("MaxKB hit_test HTTP error (kb=%s): %s", kb_id[:8], e)
            return []
        except Exception as e:
            logger.error("MaxKB hit_test unknown error (kb=%s): %s", e, exc_info=True)
            return []
