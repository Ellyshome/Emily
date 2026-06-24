"""MaxKBRagProvider —— 通过 MaxKB Admin hit_test API 检索知识库。

MaxKB Admin REST API（非文档化，但稳定可用）:
  - POST /admin/api/user/login                         登录获取 token
  - POST /admin/api/workspace/default/knowledge/{id}/hit_test  纯向量检索

与 Chat API 不同，hit_test 不经过 LLM，直接返回向量检索命中的段落+相似度分数。

用法:
    provider = MaxKBRagProvider(
        base_url="http://maxkb:8080",
        admin_password="xxx",
        knowledge_id="019ee4f2-...",
        search_mode="embedding",
        similarity=0.3,
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

    认证流程：登录 → 缓存 token → 调用 hit_test。
    Token 过期时（401）自动重新登录重试一次。

    依赖 aiohttp（emily_core 无 AstrBot 依赖）。
    """

    def __init__(
        self,
        base_url: str = "http://maxkb:8080",
        admin_password: str = "",
        knowledge_id: str = "",
        search_mode: str = "embedding",
        similarity: float = 0.3,
        timeout: int = 60,
    ):
        self._base_url = base_url.rstrip("/")
        self._admin_password = admin_password
        self._knowledge_id = knowledge_id
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

    # ── 可用性检查 ────────────────────────────────────

    async def is_available(self) -> bool:
        """通过尝试登录验证 MaxKB 连通性和凭据有效性。

        结果会缓存，避免重复登录。
        """
        if self._available is not None:
            return self._available

        if not self._admin_password or not self._knowledge_id:
            logger.debug("MaxKB: admin_password 或 knowledge_id 未配置")
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

        不经过 LLM，直接返回知识库中命中的段落 + 相似度分数。

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

        # 执行 hit_test
        results, context_text = await self._do_hit_test(
            self._auth_token, augmented_query, top_k,
        )

        # Token 过期 → 重登录后重试一次
        if results is None:
            logger.info("MaxKB token expired, re-logging in...")
            self._available = None
            new_token = await self._login()
            if new_token:
                self._auth_token = new_token
                self._available = True
                results, context_text = await self._do_hit_test(
                    new_token, augmented_query, top_k,
                )
                if results is None:
                    results, context_text = [], ""

        if results is None:
            results, context_text = [], ""

        return RagSearchResponse(
            query=query,
            results=results,
            context_text=context_text,
            total=len(results) if results else 0,
            provider_name=f"MaxKB (hit_test, mode={self._search_mode})",
        )

    async def _do_hit_test(
        self, token: str, query: str, top_k: int,
    ) -> tuple[list[SearchResult] | None, str]:
        """执行一次 hit_test 请求。

        Returns:
            (results, context_text): results 为 None 表示需要重登录（401）
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/admin/api/workspace/{_WORKSPACE}"
                    f"/knowledge/{self._knowledge_id}/hit_test",
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
                        return None, ""

                    if resp.status != 200:
                        text = await resp.text()
                        logger.error("MaxKB hit_test HTTP %d: %s",
                                     resp.status, text[:300])
                        return [], ""

                    data = await resp.json()

            # 检查业务状态码
            if data.get("code") != 200:
                logger.error("MaxKB hit_test API error: %s",
                             str(data)[:300])
                return [], ""

            hits = data.get("data", [])
            if not hits:
                return [], ""

            results = []
            contexts = []
            for i, hit in enumerate(hits):
                content = hit.get("content", "").strip()
                similarity = hit.get("similarity", 0.0)
                title = hit.get("title", "")
                doc_name = hit.get("document_name", "")
                source = doc_name or title or "未知来源"

                results.append(SearchResult(
                    content=content,
                    score=round(similarity, 4),
                    source_document=source,
                    source_kb="EmyBot-地产知识库",
                    metadata={
                        "title": title,
                        "document_name": doc_name,
                        "similarity": similarity,
                        "index": i,
                    },
                ))
                contexts.append(
                    f"### [{i + 1}] {source} (相似度: {similarity:.2%})\n{content}"
                )

            context_text = "\n\n---\n\n".join(contexts)
            return results, context_text

        except aiohttp.ClientError as e:
            logger.error("MaxKB hit_test HTTP error: %s", e)
            return [], ""
        except Exception as e:
            logger.error("MaxKB hit_test unknown error: %s", e, exc_info=True)
            return [], ""
