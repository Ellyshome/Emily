"""migrate_world_book_segments —— 为存量世界书补 structure.node_segments。

M1a 兼容迁移：旧世界书 content_json 无 node_segments，会话侧摘要会降级为
"仅统计"版本。重跑 ProjectWorldBookBuilder 即可补齐（幂等：version +1）。

用法：
    uv run python scripts/migrate_world_book_segments.py --all --dry-run
    uv run python scripts/migrate_world_book_segments.py --project-id <UUID>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if Path("/app/emily_core").exists() and "/app" not in sys.path:
    sys.path.insert(0, "/app")


def run(*, project_ids: list[str] | None = None, dry_run: bool = False, db_url: str = "") -> dict:
    """核心通道：为指定/全部世界书重建 content_json（含 node_segments）。

    Returns:
        {"dry_run": bool, "total": int, "items": [{"project_id", "node_segments", "has_segments"}]}
    """
    from emily_core.infrastructure.database import init_db
    init_db(db_url=db_url) if db_url else init_db()

    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo
    from emily_core.services.world_book_builder import ProjectWorldBookBuilder

    books = ProjectWorldBookRepo.list_all()
    targets = [
        b.project_id for b in books
        if (not project_ids or b.project_id in set(project_ids))
    ]

    builder = ProjectWorldBookBuilder()
    items: list[dict] = []
    for pid in targets:
        result = builder.build(pid, generated_by="manual", dry_run=dry_run)
        try:
            structure = (json.loads(result.get("content_json") or "{}").get("structure") or {})
        except (json.JSONDecodeError, TypeError):
            structure = {}
        segs = structure.get("node_segments") or {}
        items.append({
            "project_id": pid,
            "node_segments": len(segs),
            "has_segments": bool(segs),
        })
    return {"dry_run": dry_run, "total": len(targets), "items": items}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="世界书 node_segments 兼容迁移")
    p.add_argument("--project-id", action="append", default=[], help="指定项目（可重复）")
    p.add_argument("--all", action="store_true", help="处理全部已有世界书")
    p.add_argument("--dry-run", action="store_true", help="仅预览，不写库")
    p.add_argument("--db-url", default="")
    args = p.parse_args()

    if not args.all and not args.project_id:
        print("请指定 --all 或 --project-id <UUID>")
        raise SystemExit(2)

    report = run(project_ids=args.project_id or None,
                 dry_run=args.dry_run, db_url=args.db_url)
    print(f"[dry_run={report['dry_run']}] 目标项目数={report['total']}")
    for it in report["items"]:
        flag = "OK" if it["has_segments"] else "EMPTY"
        print(f"  · {it['project_id']}  node_segments={it['node_segments']}  [{flag}]")


if __name__ == "__main__":
    main()
