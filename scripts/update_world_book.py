"""update_world_book.py — 根据偏差增量更新世界书。

用法：
    uv run python scripts/update_world_book.py --project-id <UUID>
    uv run python scripts/update_world_book.py --all --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("update_world_book")


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


async def update_world_book(project_id: str, *, db_url: str = "", dry_run: bool = False) -> dict:
    _init_db(db_url)
    from emily_core.services.world_book_service import ProjectWorldBookService
    service = ProjectWorldBookService()
    return await service.update_stale(project_id, dry_run=dry_run)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="增量更新世界书")
    parser.add_argument("--project-id", help="项目 ID")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.services.world_book_service import ProjectWorldBookService
        service = ProjectWorldBookService()
        results = asyncio.run(service.update_all(dry_run=args.dry_run))
        for r in results:
            print(json.dumps({k: v for k, v in r.items() if k not in ("content_json", "drift_details")}, ensure_ascii=False, indent=2, default=str))
    else:
        result = asyncio.run(update_world_book(args.project_id, db_url=args.db_url, dry_run=args.dry_run))
        print(json.dumps({k: v for k, v in result.items() if k not in ("content_json",)}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
