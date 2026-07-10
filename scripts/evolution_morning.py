"""evolution_morning.py — 晨报生成脚本。

生成个性化晨报并推送。

用法：
    uv run python scripts/evolution_morning.py --date 2026-07-10 --preview
    uv run python scripts/evolution_morning.py --date 2026-07-10 --user-id <UUID>
    uv run python scripts/evolution_morning.py --date 2026-07-10 --push
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from evolution_metrics import _init_db


async def generate_morning_report(date: str, *, user_id: str = "", push: bool = False, db_url: str = "", dry_run: bool = False) -> dict | list[dict]:
    """晨报生成（脚本入口）。

    Args:
        date: 晨报日期
        user_id: 指定用户 ID，为空则生成全量
        push: 是否推送
        dry_run: 预览模式

    Returns:
        晨报列表或单用户晨报
    """
    from emily_core.services.evolution.morning_report_builder import MorningReportBuilder
    from emily_core.repositories.evolution_repo import EvolutionRepo

    _init_db(db_url)

    builder = MorningReportBuilder()

    if user_id:
        user = EvolutionRepo.get_active_users()  # sync call ok
        target_user = None
        for u in user:
            if u.id == user_id:
                target_user = u
                break
        if not target_user:
            return {"error": f"User {user_id} not found or not active"}

        report = await builder.build_for_user(target_user, date)
        result = [report]
    else:
        result = await builder.build_for_date(date)

    return result


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="晨报生成脚本")
    parser.add_argument("--date", "-d", required=True, help="晨报日期 YYYY-MM-DD")
    parser.add_argument("--user-id", default="", help="指定用户 ID（为空则全量）")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--push", action="store_true", help="生成并推送")
    parser.add_argument("--preview", action="store_true", help="预览模式")

    args = parser.parse_args()

    reports = asyncio.run(generate_morning_report(
        args.date,
        user_id=args.user_id,
        push=args.push,
        db_url=args.db_url,
        dry_run=args.preview,
    ))

    result_list = reports if isinstance(reports, list) else [reports]
    for r in result_list:
        if "error" in r:
            print(f"错误: {r['error']}")
        else:
            print(f"\n{'=' * 50}")
            print(f"晨报 — {r.get('user_name', '?')}")
            print(f"{'=' * 50}")
            print(r.get("report_text", ""))
            print()


if __name__ == "__main__":
    main()
