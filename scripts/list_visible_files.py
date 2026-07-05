"""list_visible_files.py — 列出指定用户的可见文件。

用法：
  uv run python scripts/list_visible_files.py <user_id> [--sync] [--search <关键词>] [--top-k 5] [--json]
"""

from __future__ import annotations

import argparse
import json
import io
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def _detect_docker_pg_port() -> int | None:
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().rsplit(":", 1)[-1])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _init_db():
    from emily_core.infrastructure.database import init_db

    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if db_url:
        init_db(db_url=db_url)
    else:
        pg_host = os.environ.get("EMILY_PG_HOST", os.environ.get("PG_HOST", "127.0.0.1"))
        pg_port_env = os.environ.get("EMILY_PG_PORT", os.environ.get("PG_PORT"))
        if pg_port_env:
            pg_port = int(pg_port_env)
        else:
            pg_port = _detect_docker_pg_port() or 5432
        pg_db = os.environ.get("EMILY_PG_DB", "emily")
        pg_user = os.environ.get("EMILY_PG_USER", "emily")
        pg_password = os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026")
        init_db(pg_host=pg_host, pg_port=pg_port, pg_db=pg_db, pg_user=pg_user, pg_password=pg_password)


def main():
    parser = argparse.ArgumentParser(description="列出指定用户的可见文件")
    parser.add_argument("user_id", help="用户 ID")
    parser.add_argument("--sync", action="store_true", help="先同步可见文件再查询")
    parser.add_argument("--search", help="关键词搜索（可选）")
    parser.add_argument("--top-k", type=int, default=5, help="搜索结果数上限")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    _init_db()

    from emily_core.repositories.session_accessible_file_repo import SessionAccessibleFileRepo

    # ── 同步 ──
    if args.sync:
        from emily_core.repositories.user_repo import UserRepository
        user = UserRepository.get_by_id(args.user_id)
        project_ids = []
        if user:
            project_ids = [user.project_id] if getattr(user, "project_id", None) else []
        n = SessionAccessibleFileRepo.sync_for_user(
            user_id=args.user_id,
            project_ids=project_ids,
            info_level="internal",
        )
        print(f"[sync] {n} files synced for user {args.user_id}\n")

    # ── 汇总 ──
    summary = SessionAccessibleFileRepo.get_file_summary(args.user_id)

    if args.search:
        results = SessionAccessibleFileRepo.search(args.user_id, args.search, top_k=args.top_k)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for f in results:
                print(f['filename'])
    else:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            for f in summary.get("files", []):
                print(f['filename'])


if __name__ == "__main__":
    main()
