"""detect_cognition_drift.py — 检测项目世界书与实际数据的偏差。

用法：
    uv run python scripts/detect_cognition_drift.py --project-id <UUID>
    uv run python scripts/detect_cognition_drift.py --all
    uv run python scripts/detect_cognition_drift.py --project-id <UUID> --dry-run
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("detect_cognition_drift")


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


def detect_cognition_drift(project_id: str, *, db_url: str = "") -> dict:
    """检测认知偏差（脚本入口）。"""
    _init_db(db_url)
    from emily_core.services.cognition_drift_detector import CognitionDriftDetector
    detector = CognitionDriftDetector()
    return detector.detect(project_id)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="检测认知偏差")
    parser.add_argument("--project-id", help="项目 ID（UUID）")
    parser.add_argument("--all", action="store_true", help="检测所有项目")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.infrastructure.database.models import Project
        from emily_core.infrastructure.database.session import get_session
        with get_session() as session:
            projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()

        print(f"检测 {len(projects)} 个 active 项目")
        for p in projects:
            result = detect_cognition_drift(p.id, db_url=args.db_url)
            print(f"\n--- {p.name}（{p.id}）---")
            if not result.get("has_world_book"):
                print("  [INFO] 项目无世界书，需首次生成")
            elif result.get("has_drift"):
                print(f"  [DRIFT] 过时层: {result.get('stale_layers', [])}")
                for layer_name, layer_data in result.get("drift", {}).items():
                    if layer_data.get("stale"):
                        print(f"    {layer_name}: {layer_data.get('signals', [])}")
            else:
                print("  [OK] 无偏差")
    else:
        result = detect_cognition_drift(args.project_id, db_url=args.db_url)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
