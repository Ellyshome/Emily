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
import uuid
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
# 分块（复用 local_fallback 的标题分块）
# ══════════════════════════════════════════════════════════════════════════════

def chunk_file(file_path: Path, max_chars: int = 800) -> list[str]:
    """读取 .md/.txt 文件，按标题分块后，再对超长块做硬切分。"""
    content = file_path.read_text(encoding="utf-8")
    from emily_core.providers.rag.local_fallback import _split_by_headings
    sections = _split_by_headings(content)

    chunks: list[str] = []
    for title, body in sections:
        text = f"{title}\n{body}".strip() if title else body.strip()
        if not text:
            continue
        # 超长块硬切分（按 max_chars，尽量在句末断）
        while len(text) > max_chars:
            cut = text.rfind("\n", 0, max_chars)
            if cut < max_chars // 2:
                cut = max_chars
            chunks.append(text[:cut].strip())
            text = text[cut:].strip()
        if text:
            chunks.append(text)
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
) -> dict:
    from emily_core.infrastructure.database.session import init_db
    from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo

    if backend == "tei":
        from emily_core.infrastructure.embedding.tei_client import TeiClient
        embed_client = TeiClient(api_url)  # api_url 即 TEI base_url
    else:
        from emily_core.infrastructure.embedding.remote_client import RemoteEmbeddingClient
        embed_client = RemoteEmbeddingClient(api_url=api_url, api_key=api_key, model=model)

    if not scan_dir.exists():
        return {"error": f"目录不存在: {scan_dir}"}

    # 收集 .md/.txt 文件（pdf/docx 需解析，本地摄取暂不支持，跳过并提示）
    files = [
        p for p in sorted(scan_dir.rglob("*"))
        if p.suffix.lower() in (".md", ".txt", ".markdown")
    ]

    if not files:
        return {"error": f"{scan_dir} 下无 .md/.txt 文件（pdf/docx 需先转换）"}

    # 分块
    all_chunks: list[dict] = []  # {text, source, title}
    for fp in files:
        rel = f"{collection}/{fp.relative_to(scan_dir)}"
        for i, chunk in enumerate(chunk_file(fp)):
            all_chunks.append({"text": chunk, "source": rel, "index": i})

    logger.info("分块完成: %d 文件 → %d chunks (backend=%s)", len(files), len(all_chunks), backend)

    if dry_run:
        report = {
            "scan_dir": str(scan_dir),
            "collection": collection,
            "backend": backend,
            "files": [str(p) for p in files],
            "chunk_count": len(all_chunks),
            "preview": [c["text"][:80] for c in all_chunks[:5]],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    if not all_chunks:
        return {"error": "无可用分块"}

    # 初始化 DB
    init_db(db_url)

    repo = KnowledgeChunkRepo()

    # 逐文件入库（每个文件一个 doc_id）。embed 分小批（避免单次请求过大/超时）
    total = 0
    BATCH = 16
    for fp in files:
        rel = f"{collection}/{fp.relative_to(scan_dir)}"
        chunks = [c for c in all_chunks if c["source"] == rel]
        texts = [c["text"] for c in chunks]
        if not texts:
            continue

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
            "doc_id": str(uuid.uuid4()),
            "doc_name": fp.name,
            "collection": collection,
        }
        ids = repo.batch_insert(chunks, embeddings, doc_meta)
        total += len(ids)
        logger.info("入库 %s: %d chunks", fp.name, len(ids))

    return {"ok": True, "collection": collection, "chunks_indexed": total, "files": len(files)}


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
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
