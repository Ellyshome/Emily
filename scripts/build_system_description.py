"""build_system_description.py — 构建/重建/检测系统自我描述。

参照模式：scripts/build_world_book.py（sys.path + _init_db + 核心函数 + CLI）。

用法：
    uv run python scripts/build_system_description.py --dry-run       # 预览构建结果
    uv run python scripts/build_system_description.py                 # 实际构建
    uv run python scripts/build_system_description.py --check-only    # 仅检测偏差
    uv run python scripts/build_system_description.py --force         # 强制重建
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
logger = logging.getLogger("build_system_description")


def _detect_docker_pg_port() -> int | None:
    """参照 build_world_book.py 的 Docker PG 端口检测。"""
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


def build_system_description(*, generated_by: str = "manual", db_url: str = "", dry_run: bool = False) -> dict:
    """构建系统自我描述（脚本入口）。"""
    _init_db(db_url)

    from emily_core.services.system_description_builder import SystemDescriptionBuilder
    builder = SystemDescriptionBuilder()
    return builder.build(generated_by=generated_by, dry_run=dry_run)


def check_drift(*, db_url: str = "") -> dict:
    """检测系统描述偏差（脚本入口）。"""
    _init_db(db_url)

    from emily_core.services.schema_drift_detector import SchemaDriftDetector
    detector = SchemaDriftDetector()
    return detector.detect()


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="构建/重建/检测系统自我描述")
    parser.add_argument("--dry-run", action="store_true", help="预览模式（不写 DB）")
    parser.add_argument("--check-only", action="store_true", help="仅检测偏差（不构建）")
    parser.add_argument("--force", action="store_true", help="强制重建（无视偏差检测）")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    if args.check_only:
        result = check_drift(db_url=args.db_url)
        print(f"has_description: {result.get('has_description')}")
        print(f"has_drift: {result.get('has_drift')}")
        stale = result.get("stale_domains", [])
        if stale:
            print(f"stale_domains: {stale}")
            drift = result.get("drift", {})
            for domain, info in drift.items():
                if info.get("stale"):
                    signals = info.get("signals", [])
                    print(f"  {domain}: {signals}")
        else:
            print("系统描述与当前代码结构一致，无需更新")
        return

    if args.force:
        # 强制重建：直接调用 builder
        result = build_system_description(generated_by="manual_force", db_url=args.db_url, dry_run=args.dry_run)
    else:
        # 常规构建
        result = build_system_description(generated_by="manual", db_url=args.db_url, dry_run=args.dry_run)

    # 输出结果
    summary = {k: v for k, v in result.items() if k not in ("content_json", "content_text")}
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    if args.dry_run:
        print(f"\n--- content_text 预览 ---")
        print(result.get("content_text", ""))


if __name__ == "__main__":
    main()
