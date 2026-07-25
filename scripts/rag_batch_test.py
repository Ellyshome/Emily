"""rag_batch_test.py — RAG 批量入库 + 回归查询测试脚本。

对 emily-data/baseknowledge/项目资料/ 下的 5 个文档执行：
  1. 文档解析（PDF → docling / Office → MarkItDown / MD → 直接读取）
  2. 文本分块（markdown 策略按标题分块，保留章节语义）
  3. 批量 embedding + 入 pgvector
  4. 预设查询测试，观察检索准确性

用法：
    uv run python scripts/rag_batch_test.py              # 全流程
    uv run python scripts/rag_batch_test.py --probe      # 仅连通性检查
    uv run python scripts/rag_batch_test.py --query-only # 仅查询测试（跳过入库）
    uv run python scripts/rag_batch_test.py --clean      # 清理入库数据后重新入库
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_CORE_DIR = _PROJECT_ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

_DATA_DIR = _PROJECT_ROOT / "emily-data" / "baseknowledge" / "项目资料"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rag_batch_test")

# ══════════════════════════════════════════════════════════════════════════════
# 预设查询测试用例（根据 5 个文件内容设计）
# ══════════════════════════════════════════════════════════════════════════════
QUERY_TESTS = [
    # ── 针对 生命周期.md ──
    {"query": "地产项目的生命周期包含哪些阶段",              "expected_doc": "生命周期"},
    {"query": "投资决策阶段主要做什么",                      "expected_doc": "生命周期"},
    # ── 针对 组织管理体系.md ──
    {"query": "地产项目的组织管理体系如何构成",              "expected_doc": "组织管理体系"},
    {"query": "项目经理的职责是什么",                        "expected_doc": "组织管理体系"},
    # ── 针对 消防验收指南.md ──
    {"query": "消防验收需要准备哪些材料",                    "expected_doc": "消防验收指南"},
    {"query": "消防验收的流程是什么",                        "expected_doc": "消防验收指南"},
    # ── 针对 城市绿化工程竣工验收服务指南（2024版）.docx ──
    {"query": "城市绿化工程竣工验收的标准是什么",            "expected_doc": "城市绿化"},
    {"query": "绿化工程竣工验收需要提交哪些文件",            "expected_doc": "城市绿化"},
    # ── 针对 TCSES 77—2022《城市景观水体水质提升技术指南》.pdf ──
    {"query": "城市景观水体水质提升有哪些技术方法",          "expected_doc": "城市景观水体"},
    {"query": "水质提升技术指南中的水生态修复措施",          "expected_doc": "城市景观水体"},
    # ── 交叉查询 ──
    {"query": "竣工验收阶段有哪些验收项目",                  "expected_doc": None},
    {"query": "项目建设中的质量管理要求",                    "expected_doc": None},
]


def _load_env():
    """加载 .env 环境变量（零依赖，CLI 脚本不自动读取）。"""
    env_path = _PROJECT_ROOT / ".env"
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    if key and key not in os.environ:
                        os.environ[key] = val


def _build_provider():
    """构建 RAG provider（复用 rag_dry_run 的模式）。"""
    from emily_core.bootstrap import _config_from_env
    from emily_core.config import Config

    config = Config.from_dict(_config_from_env({}))

    if not config.kb_enabled:
        raise RuntimeError("EMILY_KB_ENABLED=false，请设为 true")
    if not config.tei_url:
        raise RuntimeError("EMILY_TEI_URL 未配置")

    from emily_core.infrastructure.embedding.tei_client import TeiClient
    from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
    from emily_core.providers.rag.pgvector_provider import PgVectorRagProvider

    tei = TeiClient(config.tei_url, timeout=300)
    repo = KnowledgeChunkRepo()
    provider = PgVectorRagProvider(
        tei=tei, repo=repo,
        similarity=config.rag_similarity_threshold,
    )
    return provider, config


# ══════════════════════════════════════════════════════════════════════════════
# 步骤1: 连通性检查
# ══════════════════════════════════════════════════════════════════════════════
async def probe(provider, config) -> dict:
    """检查 TEI + DB 连通性。"""
    tei_ok = await provider._tei.is_available()
    db_ok = False
    db_error = ""
    try:
        from emily_core.infrastructure.database.session import init_db
        db_url = config.database_url if config.database_url else None
        init_db(db_url)
        db_ok = True
    except Exception as e:
        db_error = str(e)

    result = {"tei_available": tei_ok, "db_available": db_ok, "db_error": db_error}
    if tei_ok and db_ok:
        logger.info("连通性检查: TEI ✓  DB ✓")
    else:
        if not tei_ok:
            logger.error("连通性检查: TEI ✗（请确认 TEI 服务已启动）")
        if not db_ok:
            logger.error("连通性检查: DB ✗ (%s)", db_error)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 步骤2: 文档解析
# ══════════════════════════════════════════════════════════════════════════════
async def parse_file(file_path: Path) -> dict:
    """解析单个文件，返回 {success, text, sections, tables, doc_type, ...}。"""
    ext = file_path.suffix.lower()
    logger.info("  解析: %s", file_path.name)

    if ext == ".md":
        # Markdown 直接读取，不需要 docling/MarkItDown
        text = file_path.read_text(encoding="utf-8")
        return {"success": True, "text": text, "doc_type": "md", "file_name": file_path.name}

    # PDF / Office → 走 parse_document handler
    from emily_core.tools.parse_document_tool import handle_parse_document
    result = await handle_parse_document({"file_path": str(file_path), "extract_tables": True})

    if not result.get("success"):
        logger.warning("  解析失败: %s", result.get("error"))
        return result

    # 将 sections 拼接为 text
    sections = result.get("sections", [])
    text = "\n\n".join(
        s.get("title", "") + "\n" + s.get("content", "") for s in sections
    )
    return {
        "success": True,
        "text": text,
        "sections": len(sections),
        "tables": len(result.get("tables", [])),
        "doc_type": result.get("doc_type", ext.lstrip(".")),
        "file_name": file_path.name,
        "metadata": result.get("metadata", {}),
        "elapsed_ms": result.get("elapsed_ms", 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 步骤3: 文本分块
# ══════════════════════════════════════════════════════════════════════════════
async def chunk_text(text: str, doc_type: str) -> dict:
    """对文本分块，统一用 recursive 策略确保 chunk 尺寸可控（BGE-m3 有 8192 token 限制）。"""
    from emily_core.tools.chunk_tool import handle_chunk_text

    result = await handle_chunk_text({
        "text": text,
        "strategy": "recursive",
        "chunk_size": 500,
        "chunk_overlap": 50,
    })
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 步骤4: 批量 embedding + 入库
# ══════════════════════════════════════════════════════════════════════════════
async def embed_and_index(chunks: list, doc_name: str, provider) -> dict:
    """将 chunks embedding 后写入 pgvector。"""
    from emily_core.tools.embed_tool import handle_embed_and_index

    doc_id = str(uuid.uuid4())
    result = await handle_embed_and_index(
        params={
            "chunks": chunks,
            "doc_metadata": {"doc_id": doc_id, "doc_name": doc_name},
        },
        tei=provider._tei,
        repo=provider._repo,
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 单个文档的完整入库流水线
# ══════════════════════════════════════════════════════════════════════════════
async def ingest_one(file_path: Path, provider) -> dict:
    """单文档: parse → chunk → embed → index 流水线。"""
    name = file_path.name
    logger.info("── 开始入库: %s ──", name)
    t_start = time.monotonic()

    # 1. 解析
    parse_result = await parse_file(file_path)
    if not parse_result.get("success"):
        return {"file": name, "success": False, "error": parse_result.get("error"), "stage": "parse"}

    text = parse_result.get("text", "")
    if not text.strip():
        return {"file": name, "success": False, "error": "解析后文本为空", "stage": "parse"}

    # 2. 分块
    chunk_result = await chunk_text(text, parse_result.get("doc_type", ""))
    if not chunk_result.get("success"):
        return {"file": name, "success": False, "error": chunk_result.get("error"), "stage": "chunk"}

    chunks = chunk_result.get("chunks", [])
    if not chunks:
        return {"file": name, "success": False, "error": "分块结果为空", "stage": "chunk"}

    # 3. embedding + 入库
    index_result = await embed_and_index(chunks, name, provider)
    if not index_result.get("success"):
        return {"file": name, "success": False, "error": index_result.get("error"), "stage": "embed"}

    elapsed = int((time.monotonic() - t_start) * 1000)
    logger.info("  完成: %d chunks 已入库 (%dms)", len(chunks), elapsed)

    return {
        "file": name,
        "success": True,
        "doc_type": parse_result.get("doc_type"),
        "sections": parse_result.get("sections", 0),
        "chunk_count": len(chunks),
        "chunk_strategy": chunk_result.get("strategy"),
        "indexed_ids": index_result.get("indexed_ids", []),
        "elapsed_ms": elapsed,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 单条查询测试
# ══════════════════════════════════════════════════════════════════════════════
async def search_one(query: str, top_k: int, provider) -> dict:
    """执行单条查询并返回结果。"""
    response = await provider.search(query, top_k=top_k)
    return {
        "query": query,
        "total": response.total,
        "results": [
            {
                "rank": i + 1,
                "doc_name": r.source_document,
                "score": round(r.score, 4),
                "content_preview": r.content[:150] + ("..." if len(r.content) > 150 else ""),
            }
            for i, r in enumerate(response.results)
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════
async def main_async(args):
    _load_env()
    provider, config = _build_provider()
    logger.info("配置: kb_enabled=%s, tei=%s, threshold=%.2f, top_k=%d",
                config.kb_enabled, config.tei_url,
                config.rag_similarity_threshold, config.kb_top_k)

    # ── 连通性检查 ──
    p = await probe(provider, config)
    if args.probe:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return

    if not p["tei_available"]:
        logger.error("TEI 服务不可用，终止")
        return
    if not p["db_available"]:
        logger.error("数据库不可用，终止")
        return

    # ── 入库阶段 ──
    if not args.query_only:
        logger.info("=" * 60)
        logger.info("阶段1: 批量文档入库")
        logger.info("=" * 60)

        # 可选：清理已有数据
        if args.clean:
            logger.info("清理已有知识库数据...")
            try:
                from emily_core.infrastructure.database.session import get_session
                from sqlalchemy import text as sa_text
                with get_session() as session:
                    result = session.execute(sa_text("DELETE FROM knowledge_chunks"))
                logger.info("  已删除 %d 条记录", result.rowcount)
            except Exception as e:
                logger.warning("  清理失败: %s", e)

        files = sorted(_DATA_DIR.glob("*"))
        if not files:
            logger.error("未找到文件: %s", _DATA_DIR)
            return

        logger.info("找到 %d 个文件:", len(files))
        for f in files:
            logger.info("  - %s", f.name)

        ingest_results = []
        for i, fp in enumerate(files, 1):
            logger.info("[%d/%d]", i, len(files))
            result = await ingest_one(fp, provider)
            ingest_results.append(result)

        # 入库汇总
        success_count = sum(1 for r in ingest_results if r.get("success"))
        fail_count = len(ingest_results) - success_count
        total_chunks = sum(r.get("chunk_count", 0) for r in ingest_results if r.get("success"))
        logger.info("入库汇总: %d 成功, %d 失败, 共 %d chunks", success_count, fail_count, total_chunks)

        print("\n" + json.dumps(ingest_results, ensure_ascii=False, indent=2))

        if fail_count > 0:
            logger.warning("部分文件入库失败（%d 个），继续查询测试", fail_count)

    # ── 查询测试阶段 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("阶段2: 查询回归测试")
    logger.info("=" * 60)

    top_k = args.top_k or config.kb_top_k
    search_results = []
    correct = 0
    total = 0

    for i, tc in enumerate(QUERY_TESTS):
        query = tc["query"]
        expected = tc["expected_doc"]
        logger.info("[%d/%d] 查询: %s", i + 1, len(QUERY_TESTS), query)

        result = await search_one(query, top_k, provider)
        search_results.append(result)

        # 判断准确性: 期望文档名是否出现在 top_k 结果中
        if expected:
            hits = [r for r in result["results"] if expected in r.get("doc_name", "")]
            match = len(hits) > 0
            top1 = result["results"][0]["doc_name"] if result["results"] else "(无结果)"
            top1_score = result["results"][0]["score"] if result["results"] else 0
            status = "✓" if match else "✗"
            logger.info("  %s 期望=%s  Top1=%s (%.4f) 命中=%d", status, expected, top1, top1_score, len(hits))
            total += 1
            if match:
                correct += 1
        else:
            # 交叉查询, 有结果就可以
            has_results = result["total"] > 0
            status = "✓" if has_results else "?"
            top_scores = [r["score"] for r in result["results"][:3]]
            logger.info("  %s 返回 %d 条, Top3 scores=%s", status, result["total"], top_scores)

    # ── 准确性汇总 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("回归准确性汇总")
    logger.info("=" * 60)
    if total > 0:
        accuracy = correct / total * 100
        logger.info("定向查询准确率: %d/%d = %.1f%%", correct, total, accuracy)
    logger.info("详细结果见下方 JSON 输出")

    # 输出完整 JSON
    output = {
        "ingest_results": [] if args.query_only else ingest_results,
        "search_results": search_results,
        "accuracy": {
            "correct": correct,
            "total": total,
            "accuracy_pct": round(correct / total * 100, 1) if total > 0 else None,
        },
    }
    print("\n" + json.dumps(output, ensure_ascii=False, indent=2))


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="RAG 批量入库 + 回归查询测试",
    )
    parser.add_argument("--probe", action="store_true", help="仅检查连通性")
    parser.add_argument("--query-only", action="store_true", help="仅查询测试，跳过入库")
    parser.add_argument("--clean", action="store_true", help="入库前清理旧数据")
    parser.add_argument("--top-k", type=int, default=None, help="检索返回条数 (默认5)")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
