"""group_info_maintenance.py —— 群信息保鲜聚合薄壳（外部调度入口）。

串联三步、不含业务逻辑：
    1) 群清单同步（sync_groups）      —— 拉取 bot 所在全部群并写入群台账
    2) 名册刷新（refresh_group_roster）—— 刷新已过期的群成员名册
    3) 过期巡检（list_stale_rosters）  —— 输出仍过期的群，供运维判定是否需要二次执行

系统内**不存在周期性触发机制**：需要周期执行时，由宿主机 cron / 容器编排调度调用
本脚本或其中的单个脚本即可（US-18 / PRD §1.3-4）。

用法：
    uv run python scripts/group_info_maintenance.py
    uv run python scripts/group_info_maintenance.py --limit 20 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = PROJECT_ROOT / "emily-core"
_HERE = Path(__file__).resolve().parent
for _p in (str(_CORE_DIR), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("group_info_maintenance")


def _declare_source() -> None:
    try:
        from emily_core.infrastructure.logging.audit import declare_script_source

        declare_script_source("group_info_maintenance")
    except Exception as ex:  # noqa: BLE001 — 非阻断
        logger.debug("declare_script_source failed: %s", ex)


def run(*, limit: int = 50, dry_run: bool = False,
        base_url: str = "", db_url: str = "") -> dict:
    """核心函数（可 import）：按序执行三步并汇总。

    Returns:
        {"ok", "dry_run", "steps": {"sync": {...}, "roster": {...}, "stale": {...}}}
    """
    import refresh_group_roster
    import list_stale_rosters
    import sync_groups

    steps: dict = {}

    print("\n[1/3] 群清单同步 …")
    steps["sync"] = sync_groups.run(dry_run=dry_run, base_url=base_url)
    print(f"      拉取 {steps['sync']['pulled']} 群，写入 {steps['sync']['upserted']} 群"
          + (f"（{steps['sync']['error']}）" if steps["sync"]["error"] else ""))

    print("[2/3] 名册刷新（过期群）…")
    steps["roster"] = refresh_group_roster.run(
        group="", limit=limit, dry_run=dry_run, base_url=base_url)
    print(f"      成功 {steps['roster']['ok_count']} · 失败 {steps['roster']['fail_count']}")

    print("[3/3] 过期巡检 …")
    steps["stale"] = list_stale_rosters.run(limit=max(limit, 200), db_url=db_url)
    print(f"      仍过期 {len(steps['stale']['rows'])} 群")

    ok = bool(steps["sync"]["ok"]) and bool(steps["stale"]["ok"])
    return {"ok": ok, "dry_run": dry_run, "steps": steps}


def main() -> int:
    parser = argparse.ArgumentParser(description="群信息保鲜聚合薄壳（同步 → 刷新 → 巡检）")
    parser.add_argument("--limit", type=int, default=50, help="单轮刷新的群数上限（默认 50）")
    parser.add_argument("--dry-run", action="store_true", help="全链路预览，不写库")
    parser.add_argument("--base-url", default="", help="内核地址（默认 127.0.0.1:18080）")
    parser.add_argument("--db-url", default="", help="库地址（默认取 EMILY_DATABASE_URL）")
    args = parser.parse_args()

    _declare_source()
    result = run(limit=args.limit, dry_run=args.dry_run,
                 base_url=args.base_url, db_url=args.db_url)

    print("\n" + "=" * 60)
    print(f"群信息保鲜汇总{' (DRY-RUN)' if args.dry_run else ''}")
    print("=" * 60)
    s = result["steps"]
    print(f"  群清单  : 拉取 {s['sync']['pulled']} · 写入 {s['sync']['upserted']}"
          + (f" · {s['sync']['error']}" if s["sync"]["error"] else ""))
    print(f"  名册刷新: 成功 {s['roster']['ok_count']} · 失败 {s['roster']['fail_count']}")
    print(f"  过期巡检: 仍过期 {len(s['stale']['rows'])} 群"
          f"（门槛 {s['stale']['ttl_days']} 天）")
    print("=" * 60)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
