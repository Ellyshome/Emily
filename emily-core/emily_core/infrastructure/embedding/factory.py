"""Embedding 客户端工厂 —— 本地优先，远程 API 兜底。

后端选择规则（``Config.embedding_mode`` / 环境变量 ``EMILY_EMBEDDING_MODE``）：

=========  ==========================================================
模式       行为
=========  ==========================================================
``auto``   默认。先探测本地 TEI：可用 → 用本地；不可用 → 用远程 API。
           运行期本地调用失败时，当次自动转远程 API 兜底；下次仍先试
           本地，本地恢复后自动切回。
``local``  仅用本地 TEI，**不兜底**；本地失败即抛错（便于暴露本地链路问题）。
``remote`` 仅用远程 API，**不兜底**。
=========  ==========================================================

本地与远程都不可用时返回 ``None``，由调用方决定降级
（bootstrap 会退到 LocalFileRagProvider 关键词检索）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import EmbeddingClient

if TYPE_CHECKING:
    from ...config import Config

logger = logging.getLogger("emily.embedding.factory")

MODE_AUTO = "auto"
MODE_LOCAL = "local"
MODE_REMOTE = "remote"

#: 允许的模式取值（含前端历史别名 api == remote）
_VALID_MODES = {MODE_AUTO, MODE_LOCAL, MODE_REMOTE}


def normalize_mode(mode: str | None) -> str:
    """归一化模式字符串（``api`` 为 ``remote`` 的历史别名；未知值回落 auto）。"""
    m = (mode or "").strip().lower()
    if m == "api":
        return MODE_REMOTE
    if m in _VALID_MODES:
        return m
    if m:
        logger.warning("未知 embedding_mode=%r，按 auto 处理", mode)
    return MODE_AUTO


class AutoEmbeddingClient:
    """本地优先 + 远程兜底的组合客户端（``auto`` 模式专用）。

    - 首次 ``embed()``（或显式 ``resolve()``）时探测本地可用性并确定 primary，
      避免本地从未部署时每次都白试一趟。
    - primary 抛异常时当次转 fallback；**不永久降级**，本地恢复后自动切回。
    """

    def __init__(
        self,
        local: "EmbeddingClient | None",
        remote: "EmbeddingClient | None",
    ):
        self._local = local
        self._remote = remote
        self._resolved = False
        self._primary: "EmbeddingClient | None" = None
        self._fallback: "EmbeddingClient | None" = None

    @property
    def backend(self) -> str:
        """当前生效后端（未解析时返回 ``unresolved``）。"""
        if not self._resolved:
            return "unresolved"
        if self._primary is None:
            return "none"
        return "remote" if self._primary is self._remote else "local"

    async def resolve(self) -> str:
        """探测本地并确定 primary / fallback（幂等），返回生效后端名。"""
        if self._resolved:
            return self.backend
        self._resolved = True

        if self._local is not None and await self._local.is_available():
            self._primary, self._fallback = self._local, self._remote
            logger.info(
                "Embedding auto-select: 本地 TEI 可用，使用本地"
                "%s",
                "（远程 API 为兜底）" if self._remote is not None else "（未配置远程 API，无兜底）",
            )
        elif self._remote is not None:
            self._primary, self._fallback = self._remote, None
            logger.info("Embedding auto-select: 本地 TEI 不可用，改用远程 API")
        else:
            self._primary, self._fallback = None, None
            logger.warning("Embedding auto-select: 本地与远程 API 均不可用")
        return self.backend

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        await self.resolve()
        if self._primary is None:
            raise RuntimeError(
                "no embedding backend available（本地 TEI 不可用且未配置远程 Embedding API）"
            )
        try:
            return await self._primary.embed(texts)
        except Exception as e:
            if self._fallback is None:
                raise
            logger.warning("本地 embedding 调用失败，本次转远程 API 兜底: %s", e)
            return await self._fallback.embed(texts)

    async def is_available(self) -> bool:
        if self._local is not None and await self._local.is_available():
            return True
        if self._remote is not None:
            return await self._remote.is_available()
        return False


def build_local_client(config: "Config") -> "EmbeddingClient | None":
    """构造本地 TEI 客户端（未配置地址则返回 None）。"""
    tei_url = (getattr(config, "tei_url", "") or "").strip()
    if not tei_url:
        return None
    from .tei_client import TeiClient

    return TeiClient(tei_url)


def build_remote_client(config: "Config") -> "EmbeddingClient | None":
    """构造远程 OpenAI 兼容 Embedding 客户端（url/key/model 缺一即返回 None）。"""
    url = (getattr(config, "embedding_api_url", "") or "").strip()
    key = (getattr(config, "embedding_api_key", "") or "").strip()
    model = (getattr(config, "embedding_model", "") or "").strip()
    if not (url and key and model):
        return None
    from .remote_client import RemoteEmbeddingClient

    return RemoteEmbeddingClient(api_url=url, api_key=key, model=model)


def create_embedding_client(
    config: "Config",
    mode: str | None = None,
) -> "EmbeddingClient | None":
    """选择 embedding 后端；无可用后端时返回 None。

    Args:
        config: 运行时配置。
        mode: 可选，显式覆盖 ``config.embedding_mode``（供 API 层按请求选择后端）。

    ``local`` / ``remote`` 为显式指定，不做兜底；``auto``（默认）为本地优先、远程兜底。
    """
    mode = normalize_mode(mode or getattr(config, "embedding_mode", None))
    local = build_local_client(config)
    remote = build_remote_client(config)

    if mode == MODE_LOCAL:
        if local is None:
            logger.warning("embedding_mode=local 但未配置 EMILY_TEI_URL，无可用 embedding 后端")
        return local

    if mode == MODE_REMOTE:
        if remote is None:
            logger.warning(
                "embedding_mode=remote 但 EMILY_EMBEDDING_API_URL/KEY/MODEL 不完整，"
                "无可用 embedding 后端"
            )
        return remote

    # auto：本地优先，远程兜底
    if local is None:
        return remote
    if remote is None:
        return local
    return AutoEmbeddingClient(local, remote)


def create_embedding_client_from_env() -> "EmbeddingClient | None":
    """从环境变量构造（复用与 bootstrap 完全相同的选型规则）。

    供 ``scripts/`` 下未走 bootstrap 的调用方使用，避免再出现「硬编码本地实现」。
    环境变量：``EMILY_EMBEDDING_MODE`` / ``EMILY_TEI_URL`` /
    ``EMILY_EMBEDDING_API_URL`` / ``EMILY_EMBEDDING_API_KEY`` / ``EMILY_EMBEDDING_MODEL``。
    """
    from ...bootstrap import _config_from_env
    from ...config import Config

    return create_embedding_client(Config.from_dict(_config_from_env(None)))


def describe_mode(config: "Config") -> str:
    """选型结果的一句话描述（启动日志用）。"""
    mode = normalize_mode(getattr(config, "embedding_mode", None))
    local = build_local_client(config)
    remote = build_remote_client(config)
    if mode == MODE_LOCAL:
        return f"mode=local（仅本地，{'已配置' if local else '未配置'}，不兜底）"
    if mode == MODE_REMOTE:
        return f"mode=remote（仅远程 API，{'已配置' if remote else '未配置'}，不兜底）"
    if local is not None and remote is not None:
        return "mode=auto（本地优先，远程 API 兜底）"
    if local is not None:
        return "mode=auto（仅本地可用，远程 API 未配置）"
    if remote is not None:
        return "mode=auto（本地未配置，使用远程 API）"
    return "mode=auto（本地与远程 API 均未配置）"
