"""list_stale_rosters.py —— 名册过期巡检（只读）。

列出「名册最后同步时间」超过新鲜度门槛、或从未同步成功的群，供运维巡检与手动刷新。
名册的缺失/过期状态**只在控制台与日志可见**，不给对话侧暴露（PRD 场景 19）。

用法：
    uv run python scripts/list_stale_rosters.py
    uv run python scripts/list_stale_rosters.py --limit 100 --json

退出码：0 = 无过期群；1 = 存在过期群或执行出错（便于外部调度判定）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = PROJECT_ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("list_stale_rosters")

DEFAULT_DB_URL = os.environ.get(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily",
)


def _init_db(db_url: str) -> None:
    from emily_core.infrastructure.database.session import init_db

    init_db(db_url)


def _declare_source() -> None:
    try:
        from emily_core.infrastructure.logging.audit import declare_script_source

        declare_script_source("list_stale_rosters")
    except Exception as ex:  # noqa: BLE001 — 非阻断
        logger.debug("declare_script_source failed: %s", ex)


def run(*, limit: int = 200, operator_id: str = "", db_url: str = "") -> dict:
    """核心函数（可 import）：返回名册过期的群清单。

    Returns:
        {"ok", "ttl_days", "rows": [{group_id, display_name, im_platform,
          roster_synced_at, stale_days}], "error"}
    """
    _init_db(db_url or DEFAULT_DB_URL)

    from emily_core.config import Config
    from emily_core.services.group_roster_service import GroupRosterService

    ttl_days = int(getattr(Config(), "group_roster_ttl_days", 7) or 7)
    svc = GroupRosterService(platform_action=None, ttl_days=ttl_days)
    rows = svc.scan_stale(operator_id=operator_id, limit=limit)
    return {"ok": True, "ttl_days": ttl_days, "rows": rows, "error": ""}


def main() -> int:
    parser = argparse.ArgumentParser(description="名册过期巡检（只读）")
    parser.add_argument("--limit", type=int, default=200, help="扫描上限（默认 200）")
    parser.add_argument("--operator-id", default="", help="操作人 UUID（巡检留痕用）")
    parser.add_argument("--db-url", default="", help="库地址（默认取 EMILY_DATABASE_URL）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（供机器读）")
    args = parser.parse_args()

    _declare_source()
    try:
        result = run(limit=args.limit, operator_id=args.operator_id, db_url=args.db_url)
    except Exception as e:  # noqa: BLE001
        logger.error("stale roster scan failed: %s", e)
        print(f"\n巡检失败：{e}")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["rows"] else 0

    rows = result["rows"]
    print("\n" + "=" * 60)
    print("名册过期巡检")
    print("=" * 60)
    print(f"  新鲜度门槛: {result['ttl_days']} 天")
    if not rows:
        print("  无过期群（全部名册均在门槛内）")
    else:
        print(f"  过期群 {len(rows)} 个：")
        for r in rows:
            days = r.get("stale_days")
            age = "从未同步" if days is None else f"距今 {days} 天"
            print(f"    · [{r.get('group_id')}] {r.get('display_name')}"
                  f"（{r.get('im_platform')}）最后同步 {r.get('roster_synced_at') or '-'} · {age}")
        print("\n  提示：可用 `uv run python scripts/refresh_group_roster.py --group <群号>`"
              " 或控制台「群追踪」逐个刷新。")
    print("=" * 60)

    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
