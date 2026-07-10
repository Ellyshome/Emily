"""build_world_book.py — 构建/重建单个项目的世界书。

参照模式：scripts/evolution_metrics.py（sys.path + _init_db + async 核心函数 + CLI）。

用法：
    uv run python scripts/build_world_book.py --project-id <UUID> --dry-run
    uv run python scripts/build_world_book.py --project-id <UUID>
    uv run python scripts/build_world_book.py --all --dry-run
"""

from __future__ import annotations

import argparse
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("build_world_book")


def _detect_docker_pg_port() -> int | None:
    """参照 collect_session_data.py 的 Docker PG 端口检测。"""
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = result.stdout.strip().rsplit(":", 1)[-1]
            return int(port_str)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _init_db(db_url: str = "") -> None:
    """初始化数据库连接。"""
    from emily_core.infrastructure.database.session import init_db

    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
            pg_port_env = os.environ.get("EMILY_PG_PORT")
            if pg_port_env:
                pg_port = int(pg_port_env)
            else:
                pg_port = _detect_docker_pg_port() or 5432
            pg_db = os.environ.get("EMILY_PG_DB", "emily")
            pg_user = os.environ.get("EMILY_PG_USER", "emily")
            pg_password = os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026")
            init_db(pg_host=pg_host, pg_port=pg_port, pg_db=pg_db, pg_user=pg_user, pg_password=pg_password)


def build_world_book(project_id: str, *, generated_by: str = "manual", db_url: str = "", dry_run: bool = False) -> dict:
    """构建项目世界书（脚本入口）。"""
    _init_db(db_url)

    from emily_core.services.world_book_builder import ProjectWorldBookBuilder
    builder = ProjectWorldBookBuilder()
    return builder.build(project_id, generated_by=generated_by, dry_run=dry_run)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="构建/重建项目世界书")
    parser.add_argument("--project-id", help="项目 ID（UUID）")
    parser.add_argument("--all", action="store_true", help="构建所有 active 项目")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--dry-run", action="store_true", help="预览模式（不写 DB）")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.infrastructure.database.models import Project
        from emily_core.infrastructure.database.session import get_session

        with get_session() as session:
            projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()

        print(f"找到 {len(projects)} 个 active 项目")
        for p in projects:
            print(f"\n=== 项目：{p.name}（{p.id}）===")
            result = build_world_book(p.id, generated_by="manual", db_url=args.db_url, dry_run=args.dry_run)
            print(f"状态: {result.get('status')}")
            print(f"初始化层级: T{result.get('initialization_tier', 0)}")
            print(f"Token 数: {result.get('token_count', 0)}")
            if args.dry_run:
                print(f"\n--- content_text 预览 ---")
                print(result.get("content_text", ""))
    else:
        result = build_world_book(args.project_id, generated_by="manual", db_url=args.db_url, dry_run=args.dry_run)
        print(json.dumps(
            {k: v for k, v in result.items() if k not in ("content_json",)},
            ensure_ascii=False, indent=2, default=str,
        ))
        if args.dry_run:
            print(f"\n--- content_text 预览 ---")
            print(result.get("content_text", ""))


if __name__ == "__main__":
    main()
