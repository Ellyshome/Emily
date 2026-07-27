"""run_daily_file_parse.py — 每日文件解析盘点手动触发脚本。

从调度器 DailyFileParseHandler 抽取 CLI 入口，支持独立执行，
供开发者手动触发批量文件解析，或观察解析效果。

用法：
    uv run python scripts/run_daily_file_parse.py --dry-run       # 不写库，只输出将处理哪些文件
    uv run python scripts/run_daily_file_parse.py                 # 正式执行批量解析 + 写入摘要
    uv run python scripts/run_daily_file_parse.py --batch-limit 5 # 控制每次处理数量
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

# 加载 .env 文件（位于项目根目录）
_ENV_FILE = _HERE.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip().strip("'").strip('"')
                if _key and _key not in os.environ:
                    os.environ[_key] = _val

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_daily_file_parse")

# 抑制 emily 模块的详细日志，但保留 handler 自身的输出
for _mod in ["emily", "emily.scheduler.engine", "emily.file"]:
    logging.getLogger(_mod).setLevel(logging.WARNING)
# 恢复 handler 日志级别，确保能看到进度
logging.getLogger("emily.scheduler.jobs.daily_file_parse").setLevel(logging.INFO)


def _init_db(db_url: str = "") -> None:
    """初始化数据库连接（复用 startup_report.py 的模式）。"""
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
                    r = subprocess.run(
                        ["docker", "port", "emily-postgres", "5432/tcp"],
                        capture_output=True, text=True, timeout=5,
                    )
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(
                pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"),
                pg_port=pg_port,
                pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"),
            )


async def run_daily_file_parse(*, db_url: str = "", batch_limit: int = 50, dry_run: bool = False) -> str:
    """执行每日文件解析盘点。

    Returns:
        执行结果摘要字符串。
    """
    _init_db(db_url)

    from emily_core.services.file_service import FileService
    from emily_core.scheduler.jobs.daily_file_parse import DailyFileParseHandler

    handler = DailyFileParseHandler(file_service=FileService())
    result = await handler.execute({"batch_limit": batch_limit, "dry_run": dry_run})

    print("=" * 60)
    print(result.summary)
    print("=" * 60)

    return result.summary


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Emily 每日文件解析盘点手动触发")
    parser.add_argument("--db-url", default="", help="数据库连接串（可选，优先级高于 .env）")
    parser.add_argument("--batch-limit", type=int, default=50, help="每次处理文件数量上限（默认 50）")
    parser.add_argument("--dry-run", action="store_true", help="不写库，仅输出将处理哪些文件")
    args = parser.parse_args()

    summary = asyncio.run(run_daily_file_parse(
        db_url=args.db_url,
        batch_limit=args.batch_limit,
        dry_run=args.dry_run,
    ))

    if args.dry_run:
        print("\n[dry-run] 摘要未写入数据库。去掉 --dry-run 以正式执行。")


if __name__ == "__main__":
    main()
