"""rag_governance.py — 零可见治理（M2 反查）。

扫描 files 中「无任何可见来源」的文件（非公开、无上传者、未节点绑定、未显式授权），
生成「零可见治理清单」供人工清理/授权，避免知识库中滞留无人可检索的死文件。

用法：
    uv run python scripts/rag_governance.py --dry-run
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


def run(dry_run: bool = True, db_url: str | None = None, limit: int = 100) -> dict:
    """识别零可见文件（无 ①上传者 ∪ ②公开 ∪ ③节点 ∪ ④显式授权 任一来源）。

    Args:
        dry_run: 保留参数（本脚本为只读治理清单，不写库）。
        db_url: 可选 PG URL。
        limit: 返回样例上限。

    Returns:
        {zero_visible_count, files: [{id, file_no, filename, confidentiality, uploaded_by}]}
    """
    from emily_core.infrastructure.database.session import init_db, get_session
    from emily_core.infrastructure.database.models import (
        File, NodeAccessibleFile, SessionAccessibleFile,
    )
    from sqlalchemy import or_

    init_db(db_url)

    with get_session() as session:
        node_linked = session.query(NodeAccessibleFile.file_id).distinct().subquery()
        explicit = session.query(SessionAccessibleFile.file_id).filter(
            SessionAccessibleFile.access_type == "explicit",
        ).distinct().subquery()

        rows = session.query(File).filter(
            File.is_deleted == False,
            File.confidentiality != 0,
            or_(File.uploaded_by.is_(None), File.uploaded_by == ""),
            ~File.id.in_(node_linked),
            ~File.id.in_(explicit),
        ).limit(limit).all()

    files = [
        {
            "id": f.id,
            "file_no": f.file_no,
            "filename": f.filename,
            "confidentiality": f.confidentiality,
            "uploaded_by": f.uploaded_by or "",
        }
        for f in rows
    ]

    return {
        "zero_visible_count": len(files),
        "files": files,
        "dry_run": bool(dry_run),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="零可见治理：识别无任何可见来源的文件")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="只读输出治理清单（默认开启）")
    parser.add_argument("--db-url", default=None, help="PostgreSQL URL（缺省走默认配置）")
    parser.add_argument("--limit", type=int, default=100, help="返回样例上限")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, db_url=args.db_url, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
