"""check_initialization.py — 检查项目初始化层级和缺失项。

用法：
    uv run python scripts/check_initialization.py --project-id <UUID> --dry-run
    uv run python scripts/check_initialization.py --all
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
logger = logging.getLogger("check_initialization")


def _detect_docker_pg_port() -> int | None:
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().rsplit(":", 1)[-1])
    except Exception:
        pass
    return None


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else _detect_docker_pg_port() or 5432
            init_db(pg_host=pg_host, pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def check_initialization(project_id: str, *, db_url: str = "") -> dict:
    """检查项目初始化（脚本入口）。"""
    _init_db(db_url)
    from emily_core.services.initialization_checker import InitializationChecker
    checker = InitializationChecker()
    return checker.check(project_id)


def _format_report(result: dict) -> str:
    """格式化为自检邮件风格的文本报告。"""
    lines = []
    lines.append("Emily 项目初始化检查报告")
    lines.append("=" * 40)
    lines.append(f"初始化层级：{result['tier_label']}（{result['total_done']}/{result['total_items']} 必备项）")
    lines.append("=" * 40)

    for tier_key in ["T1", "T2", "T3", "T4"]:
        summary = result["summary_by_tier"].get(tier_key, {})
        done = summary.get("done", 0)
        total = summary.get("total", 0)
        icon = "PASS" if done >= total else "FAIL" if done == 0 else "WARN"
        lines.append(f"\n[{icon}] {tier_key}（{done}/{total}）")

        tier_items = {k: v for k, v in result["items"].items() if k.startswith(tier_key + "_")}
        for k, v in tier_items.items():
            lines.append(f"  {'[x]' if v else '[ ]'} {k}")

    if result["missing"]:
        lines.append(f"\n下一步：补充缺失项以提升初始化层级")

    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="检查项目初始化层级")
    parser.add_argument("--project-id", help="项目 ID（UUID）")
    parser.add_argument("--all", action="store_true", help="检查所有 active 项目")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--dry-run", action="store_true", help="仅预览（本项目无副作用，始终可安全运行）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.infrastructure.database.models import Project
        from emily_core.infrastructure.database.session import get_session
        with get_session() as session:
            projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()
        for p in projects:
            result = check_initialization(p.id, db_url=args.db_url)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(_format_report(result))
                print()
    else:
        result = check_initialization(args.project_id, db_url=args.db_url)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(_format_report(result))


if __name__ == "__main__":
    main()
