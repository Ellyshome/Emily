"""backfill_doc_id.py — 回填/清理历史断链 chunk。

扫描 knowledge_chunks 中 doc_id 无对应 files 记录的孤儿 chunk，
输出 orphan_count 与 sample_doc_ids，供治理或清理使用。

用法：
    uv run python scripts/backfill_doc_id.py --dry-run
    uv run python scripts/backfill_doc_id.py --dry-run --mark-orphan
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


def run(dry_run: bool = True, mark_orphan: bool = False,
        db_url: str | None = None, sample_limit: int = 10) -> dict:
    """识别历史断链 chunk（doc_id 无对应 files 记录）。

    Args:
        dry_run: True 仅识别不修改；False 与 True 行为一致（本版本不做破坏性清理）。
        mark_orphan: True 时返回待处理样例（本版本仅报告，不落库标记）。
        db_url: 可选 PG URL，缺省走 session.init_db() 默认。

    Returns:
        {orphan_count, orphan_chunk_count, sample_doc_ids}
    """
    from emily_core.infrastructure.database.session import init_db, get_session
    from emily_core.infrastructure.database.models import KnowledgeChunk, File

    init_db(db_url)

    with get_session() as session:
        files_sub = session.query(File.id).subquery()
        orphan_rows = session.query(KnowledgeChunk.doc_id).filter(
            ~KnowledgeChunk.doc_id.in_(files_sub),
        ).all()

    orphan_doc_ids = sorted({r.doc_id for r in orphan_rows if r.doc_id})
    return {
        "orphan_count": len(orphan_doc_ids),
        "orphan_chunk_count": len(orphan_rows),
        "sample_doc_ids": orphan_doc_ids[:sample_limit],
        "mark_orphan": bool(mark_orphan),
        "dry_run": bool(dry_run),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="识别历史断链 chunk（doc_id 无对应 files 记录）")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="仅识别不修改（默认开启）")
    parser.add_argument("--mark-orphan", action="store_true",
                        help="返回待处理孤儿样例（本版本仅报告，不做破坏性清理）")
    parser.add_argument("--db-url", default=None, help="PostgreSQL URL（缺省走默认配置）")
    parser.add_argument("--sample-limit", type=int, default=10, help="样例 doc_id 数量上限")
    args = parser.parse_args()

    result = run(
        dry_run=args.dry_run,
        mark_orphan=args.mark_orphan,
        db_url=args.db_url,
        sample_limit=args.sample_limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
