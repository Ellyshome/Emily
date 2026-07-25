"""rag_dry_run.py — RAG 基座 dry-run 测试脚本。

直接调用生产入口 handle_knowledge_search，保证"手动能跑通 ⟹ 调用能跑通"。
绕过 LLM 和 4 节点管道，直接打 pgvector 检索，输出 JSON 结构化的原始检索结果。

设计原则：测试入口 = 生产入口。
  手动 CLI 测试:  scripts/rag_dry_run.py → handle_knowledge_search(params, rag_provider)
  生产 SOP 调用:  RealExecutor        → handle_knowledge_search(params, rag_provider)
  两者调同一个函数，测试有效性最大化。

RAG 基座调用失败即反馈失败，不做本地回退兜底（与生产行为一致）。

用法：
    uv run python scripts/rag_dry_run.py "放线验收标准"
    uv run python scripts/rag_dry_run.py "施工工艺" --top-k 8
    uv run python scripts/rag_dry_run.py "钢筋间距" --stage 施工建设 --role 工程部经理
    uv run python scripts/rag_dry_run.py --probe
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
logger = logging.getLogger("rag_dry_run")


def _get_rag_provider():
    """获取 PgVector RAG provider（从 Config 默认值 + 环境变量构建）。"""
    from emily_core.bootstrap import _config_from_env
    from emily_core.config import Config

    # 加载 .env 文件中的环境变量（零依赖，CLI 脚本不自动读取 .env）
    _env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(_env_path):
        with open(_env_path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _key, _val = _line.split("=", 1)
                    _key = _key.strip()
                    _val = _val.strip()
                    if _key and _key not in os.environ:
                        os.environ[_key] = _val

    # Config 默认值 + 环境变量合并
    config = Config.from_dict(_config_from_env({}))

    if not config.kb_enabled:
        logger.warning("kb_enabled=false，无法创建 RAG provider（请设 EMILY_KB_ENABLED=true）")
        return None, config

    if not config.tei_url:
        logger.warning("tei_url 未配置（请设 EMILY_TEI_URL）")
        return None, config

    from emily_core.infrastructure.embedding.tei_client import TeiClient
    from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
    from emily_core.providers.rag.pgvector_provider import PgVectorRagProvider

    tei = TeiClient(config.tei_url)
    repo = KnowledgeChunkRepo()
    provider = PgVectorRagProvider(
        tei=tei, repo=repo,
        similarity=config.rag_similarity_threshold,
    )
    return provider, config


async def rag_dry_run(
    query: str = "",
    *,
    top_k: int | None = None,
    stage: str | None = None,
    role: str | None = None,
    probe: bool = False,
) -> dict:
    """RAG 基座 dry-run 检索。调用生产入口 handle_knowledge_search。

    Returns:
        dict: {"dry_run_meta": {...}, "handler_result": {...}}（probe 模式无 handler_result）
    """
    provider, config = _get_rag_provider()

    meta = {
        "kb_enabled": config.kb_enabled,
        "tei_url": config.tei_url,
        "rag_similarity_threshold": config.rag_similarity_threshold,
        "kb_top_k": config.kb_top_k,
    }

    if provider is None:
        meta["available"] = False
        meta["issues"] = ["RAG provider 未创建（检查 EMILY_KB_ENABLED / EMILY_TEI_URL）"]
        return {"dry_run_meta": meta}

    available = await provider.is_available()
    meta["available"] = available

    # ── probe 模式：仅诊断配置 + 连通性，不走 handler ──
    if probe:
        issues: list[str] = []
        if not available:
            issues.append("is_available()=false（TEI 服务不可达）")
        if config.kb_enabled is False:
            issues.append("kb_enabled=false（不影响 dry-run，但生产 bootstrap 不会创建 RAG provider）")
        meta["issues"] = issues
        return {"dry_run_meta": meta}

    # ── 检索模式：调生产入口 handle_knowledge_search ──
    from emily_core.tools.knowledge_search_tool import handle_knowledge_search

    params = {"query": query}
    if top_k is not None:
        params["top_k"] = top_k
    if stage:
        params["stage"] = stage
    if role:
        params["role"] = role

    # 与 RealExecutor 框架直调同一个函数
    handler_result = await handle_knowledge_search(params, provider)

    return {
        "dry_run_meta": meta,
        "handler_result": handler_result,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="RAG 基座 dry-run 测试（调生产入口 handle_knowledge_search，pgvector + TEI）",
    )
    parser.add_argument("query", nargs="?", default="", help="自然语言查询（--probe 模式可省略）")
    parser.add_argument("--top-k", type=int, default=None,
                        help="返回条数，默认 config.kb_top_k（5），handler 内限上限 10")
    parser.add_argument("--stage", default=None, help="按项目阶段过滤（施工建设/竣工验收等）")
    parser.add_argument("--role", default=None, help="按岗位过滤（工程部经理/设计部经理等）")
    parser.add_argument("--probe", action="store_true",
                        help="仅检查配置 + 连通性，不发检索请求")
    args = parser.parse_args()

    result = asyncio.run(rag_dry_run(
        query=args.query,
        top_k=args.top_k,
        stage=args.stage,
        role=args.role,
        probe=args.probe,
    ))

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
