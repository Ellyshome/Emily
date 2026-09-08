"""rag_visible_check.py — 手动校验某用户可见文件集与检索结果（M2/M4/M6/M8）。

打印某用户可见文件集统计 + 命中（可选带 query 的检索验证），
用于验收范围过滤（宁少答不泄露）与引用溯源。

用法：
    uv run python scripts/rag_visible_check.py --user-id <user_uuid>
    uv run python scripts/rag_visible_check.py --user-id <user_uuid> --query "施工规范"
    uv run python scripts/rag_visible_check.py --user-id <user_uuid> --query "施工规范" --rerank
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rag_visible_check")


def _load_env() -> None:
    env_path = _HERE.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


def _build_provider():
    """构建 PgVector RAG provider（远程 embedding 优先，其次本地 TEI）。"""
    from emily_core.bootstrap import _config_from_env
    from emily_core.config import Config

    config = Config.from_dict(_config_from_env({}))

    from emily_core.infrastructure.database.session import init_db
    init_db(config.database_url if config.database_url else None)

    from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
    repo = KnowledgeChunkRepo()

    tei = None
    if config.embedding_api_url and config.embedding_api_key and config.embedding_model:
        from emily_core.infrastructure.embedding.remote_client import RemoteEmbeddingClient
        tei = RemoteEmbeddingClient(
            api_url=config.embedding_api_url,
            api_key=config.embedding_api_key,
            model=config.embedding_model,
        )
    elif config.tei_url:
        from emily_core.infrastructure.embedding.tei_client import TeiClient
        tei = TeiClient(config.tei_url)

    if tei is None:
        return None, config

    from emily_core.providers.rag.pgvector_provider import PgVectorRagProvider
    provider = PgVectorRagProvider(
        tei=tei, repo=repo, similarity=config.rag_similarity_threshold,
    )
    return provider, config


def run(user_id: str, query: str = "", top_k: int = 5,
        rerank: bool = False, db_url: str | None = None) -> dict:
    """计算可见集并（可选）验证检索命中均在可见集内。

    Args:
        user_id: 用户 UUID。
        query: 可选检索词；为空只输出可见集统计。
        top_k: 检索条数。
        rerank: 是否触发重排。
        db_url: 可选 PG URL。

    Returns:
        {user_id, company_id, info_level, visible_file_count, visible_file_ids,
         query, search:{total, hits:[{doc_id, doc_name, score, in_visible_set}]}}
    """
    _load_env()
    from emily_core.bootstrap import _config_from_env
    from emily_core.config import Config
    from emily_core.infrastructure.database.session import init_db

    config = Config.from_dict(_config_from_env({}))
    effective_db_url = db_url or config.database_url
    init_db(effective_db_url)

    from emily_core.services.permission_service import PermissionService
    from emily_core.services.visible_file_set_resolver import VisibleFileSetResolver

    perm = PermissionService().build_permission_dict(user_id)
    company_id = perm.get("company_id", "")
    info_level = perm.get("info_level", "public")

    resolver = VisibleFileSetResolver()
    visible = resolver.resolve_visible_file_list(
        user_id, company_id=company_id, info_level=info_level,
    )

    result: dict = {
        "user_id": user_id,
        "company_id": company_id,
        "info_level": info_level,
        "visible_file_count": len(visible),
        "visible_file_ids": visible[:50],
        "query": query,
    }

    if query:
        provider, _ = _build_provider()
        if provider is None:
            result["search"] = {"error": "RAG provider 未创建（检查 embedding 配置）"}
            return result

        scoped = resolver.resolve_visible_file_ids(
            user_id, company_id=company_id, info_level=info_level,
        )

        async def _search():
            return await provider.search(
                query, top_k=top_k, scoped_doc_ids=scoped, rerank=rerank,
            )

        resp = asyncio.run(_search())
        visible_set = set(visible)
        hits = [
            {
                "doc_id": r.source_file_id,
                "doc_name": r.source_document,
                "score": r.score,
                "in_visible_set": r.source_file_id in visible_set,
            }
            for r in resp.results
        ]
        result["search"] = {
            "total": resp.total,
            "hits": hits,
            "all_in_visible_set": all(h["in_visible_set"] for h in hits),
        }

    return result


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    _load_env()

    parser = argparse.ArgumentParser(description="校验用户可见文件集与 RAG 检索结果")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--query", default="", help="检索词（为空只输出可见集）")
    parser.add_argument("--top-k", type=int, default=5, help="检索返回条数（默认 5）")
    parser.add_argument("--rerank", action="store_true", help="触发重排")
    parser.add_argument("--db-url", default=None, help="PostgreSQL URL（缺省走默认配置）")
    args = parser.parse_args()

    result = run(
        user_id=args.user_id,
        query=args.query,
        top_k=args.top_k,
        rerank=args.rerank,
        db_url=args.db_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
