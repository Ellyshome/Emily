"""ingest_knowledge.py — 知识库文档向量化入库脚本。

扫描指定目录下的 .md/.txt 文件，按 Markdown 标题分块，调用远程 Embedding API
（SiliconFlow BGE-m3，与生产 PgVectorRagProvider 同一 embedding 通道）生成向量，
批量写入 knowledge_chunks 表（pgvector）。

设计原则：摄取通道 = 检索通道。
  - 分块：复用 providers/rag/local_fallback.py 的 _split_by_headings（与本地兜底同源）
  - embedding：复用 RemoteEmbeddingClient（与生产 bootstrap 同一客户端）
  - 入库：复用 KnowledgeChunkRepo.batch_insert（与 embed_tool 同一 repo）

用法：
    # 预览（不写库，仅输出分块报告）
    uv run python scripts/ingest_knowledge.py --dir emily-data/company_policies --dry-run

    # 实际入库（公司制度）
    uv run python scripts/ingest_knowledge.py --dir emily-data/company_policies --collection company_policies

    # 实际入库（项目资料）
    uv run python scripts/ingest_knowledge.py --dir emily-data/baseknowledge/项目资料 --collection project_docs

    # 指定 DB 与 embedding（默认从 .env / 环境变量读取）
    uv run python scripts/ingest_knowledge.py --dir X --db-url "postgresql://..." --api-key "sk-..."
"""

from __future__ import annotations

import argparse
import asyncio
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
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest_knowledge")


# ══════════════════════════════════════════════════════════════════════════════
# 环境变量加载（零依赖，CLI 不自动读 .env）
# ══════════════════════════════════════════════════════════════════════════════

def _load_env(env_path: Path | None = None) -> None:
    env_path = env_path or (_HERE.parent / ".env")
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


# ══════════════════════════════════════════════════════════════════════════════
# 解析 + 分块（M5: DocumentParser + StructuralChunker + DedupChecker）
# ══════════════════════════════════════════════════════════════════════════════

_SUPPORTED_EXTS = (".md", ".txt", ".markdown", ".text", ".pdf", ".docx", ".doc")


def parse_and_chunk(file_path: Path) -> list[dict]:
    """解析文件并结构分块，填充 content_hash。

    Returns:
        [{text, index, heading, content_hash}]
    """
    from emily_core.services.document_parser import DocumentParser
    from emily_core.services.structural_chunker import StructuralChunker
    from emily_core.services.dedup_checker import DedupChecker

    text = DocumentParser().parse(file_path)
    if not text or not text.strip():
        return []

    chunks: list[dict] = []
    for c in StructuralChunker().chunk(text):
        chunks.append({
            "text": c["text"],
            "index": c["index"],
            "heading": c.get("heading", ""),
            "content_hash": DedupChecker.content_hash(c["text"]),
        })
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# 核心
# ══════════════════════════════════════════════════════════════════════════════

async def ingest(
    scan_dir: Path,
    *,
    collection: str,
    db_url: str,
    api_url: str,
    api_key: str,
    model: str,
    dry_run: bool,
    backend: str = "remote",
    uploaded_by: str | None = None,
    confidentiality: int = 1,
) -> dict:
    from emily_core.infrastructure.database.session import init_db
    from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
    from emily_core.repositories.file_repo import FileRepository
    from emily_core.services.dedup_checker import DedupChecker

    if backend == "tei":
        from emily_core.infrastructure.embedding.tei_client import TeiClient
        embed_client = TeiClient(api_url)  # api_url 即 TEI base_url
    else:
        from emily_core.infrastructure.embedding.remote_client import RemoteEmbeddingClient
        embed_client = RemoteEmbeddingClient(api_url=api_url, api_key=api_key, model=model)

    if not scan_dir.exists():
        return {"error": f"目录不存在: {scan_dir}"}

    # 收集支持格式文件（M5 扩展 pdf/docx/doc）
    files = [
        p for p in sorted(scan_dir.rglob("*"))
        if p.suffix.lower() in _SUPPORTED_EXTS
    ]

    if not files:
        return {"error": f"{scan_dir} 下无支持的文档文件（md/txt/pdf/docx/doc）"}

    # M5: 解析 + 结构分块 + 批内 content_hash 去重
    dedup = DedupChecker()
    seen_hashes: set[str] = set()
    all_chunks: list[dict] = []  # {text, source, index, heading, content_hash}
    file_reports: list[dict] = []
    intra_dup = 0
    for fp in files:
        rel = f"{collection}/{fp.relative_to(scan_dir)}"
        parsed = parse_and_chunk(fp)
        file_chunks: list[dict] = []
        for c in parsed:
            h = c["content_hash"]
            if h in seen_hashes:
                intra_dup += 1
                continue
            seen_hashes.add(h)
            file_chunks.append({
                "text": c["text"],
                "source": rel,
                "index": c["index"],
                "heading": c.get("heading", ""),
                "content_hash": h,
            })
        all_chunks.extend(file_chunks)
        file_reports.append({
            "file": str(fp),
            "chunks": len(file_chunks),
            "skipped_duplicate": len(parsed) - len(file_chunks),
        })

    logger.info("分块完成: %d 文件 → %d chunks (批内去重 %d, backend=%s)",
                len(files), len(all_chunks), intra_dup, backend)

    if dry_run:
        report = {
            "scan_dir": str(scan_dir),
            "collection": collection,
            "backend": backend,
            "uploaded_by": uploaded_by,
            "confidentiality": confidentiality,
            "files": [str(p) for p in files],
            "doc_id_anchor": "files.id (via ensure_file_record)",
            "chunk_count": len(all_chunks),
            "duplicate_chunks_skipped": intra_dup,
            "file_reports": file_reports,
            "preview": [c["text"][:80] for c in all_chunks[:5]],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    if not all_chunks:
        return {"error": "无可用分块"}

    # 初始化 DB
    init_db(db_url)

    repo = KnowledgeChunkRepo()

    # 跨批次去重：跳过 DB 中已存在的相同 content_hash（M5）
    new_chunks: list[dict] = []
    db_dup = 0
    for c in all_chunks:
        if dedup.is_duplicate(c["content_hash"]):
            db_dup += 1
            continue
        new_chunks.append(c)
    logger.info("DB 去重: 跳过 %d 个已存在 chunk", db_dup)

    # 逐文件入库（每个文件一个 files.id 作为 doc_id）。embed 分小批（避免单次请求过大/超时）
    from collections import defaultdict
    by_source: dict[str, list[dict]] = defaultdict(list)
    for c in new_chunks:
        by_source[c["source"]].append(c)

    total = 0
    BATCH = 16
    for fp in files:
        rel = f"{collection}/{fp.relative_to(scan_dir)}"
        chunks = by_source.get(rel, [])
        texts = [c["text"] for c in chunks]
        if not texts:
            continue

        # M1: 目录扫描入库前建/查 files 记录，用 files.id 作 doc_id 锚点
        file_record = FileRepository.ensure_file_record(
            fp.name,
            uploaded_by=uploaded_by,
            confidentiality=confidentiality,
        )

        # 分小批 embed，合并向量
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            vecs = await embed_client.embed(batch)
            embeddings.extend(vecs)
            logger.info("embed %s: %d/%d", fp.name, min(i + BATCH, len(texts)), len(texts))

        if len(embeddings) != len(texts):
            logger.error("embedding 数量不匹配 %s: got %d, expect %d", fp.name, len(embeddings), len(texts))
            continue

        doc_meta = {
            "doc_id": file_record.id,
            "doc_name": fp.name,
            "file_no": file_record.file_no,
            "collection": collection,
        }
        ids = repo.batch_insert(chunks, embeddings, doc_meta)
        total += len(ids)
        logger.info("入库 %s: %d chunks (doc_id=%s)", fp.name, len(ids), file_record.id)

    return {
        "ok": True,
        "collection": collection,
        "chunks_indexed": total,
        "files": len(files),
        "duplicate_chunks_skipped": intra_dup + db_dup,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    _load_env()

    parser = argparse.ArgumentParser(description="知识库文档向量化入库")
    parser.add_argument("--dir", required=True, help="扫描目录")
    parser.add_argument("--collection", default="company_policies", help="知识库集合名（写入 metadata.collection）")
    parser.add_argument("--backend", choices=["remote", "tei"], default="remote",
                        help="embedding 后端：remote=SiliconFlow API，tei=本地 TEI 容器")
    parser.add_argument("--db-url", default=os.environ.get(
        "EMILY_DATABASE_URL", "postgresql://emily:emily_secret_2026@127.0.0.1:25432/emily"))
    parser.add_argument("--api-url", default=os.environ.get(
        "EMILY_EMBEDDING_API_URL", "https://api.siliconflow.cn/v1/embeddings"))
    parser.add_argument("--api-key", default=os.environ.get("SILICONFLOW_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("EMILY_EMBEDDING_MODEL", "BAAI/bge-m3"))
    parser.add_argument("--uploaded-by", default="", help="上传者 user_id（files.uploaded_by，M1 doc_id 锚定）")
    parser.add_argument("--confidentiality", type=int, default=1,
                        help="密级 0=公开 1=内部 2=机密 3=绝密（默认 1）")
    parser.add_argument("--dry-run", action="store_true", help="预览分块，不写库")
    args = parser.parse_args()

    if args.backend == "remote" and not args.api_key:
        print("❌ 缺少 SILICONFLOW_API_KEY（remote 后端需在 .env 或 --api-key 提供；或改用 --backend tei）")
        sys.exit(1)

    result = asyncio.run(ingest(
        scan_dir=Path(args.dir),
        collection=args.collection,
        db_url=args.db_url,
        api_url=args.api_url,
        api_key=args.api_key,
        model=args.model,
        dry_run=args.dry_run,
        backend=args.backend,
        uploaded_by=args.uploaded_by or None,
        confidentiality=args.confidentiality,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
