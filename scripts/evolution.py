"""evolution.py — 进化闭环薄聚合脚本。

按调度顺序串联各独立脚本的核心函数。
不含业务逻辑，仅做编排。

用法：
    uv run python scripts/evolution.py daily --date 2026-07-09
    uv run python scripts/evolution.py daily --date 2026-07-09 --days 7
    uv run python scripts/evolution.py weekly --end-date 2026-07-09
    uv run python scripts/evolution.py morning --date 2026-07-10
    uv run python scripts/evolution.py validate
    uv run python scripts/evolution.py full --date 2026-07-09
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from evolution_anomaly import detect_anomalies

# 此时不 import collect_metrics，daily pipeline 改用快照
# 兼容旧调用：collect_metrics 可作为 import 使用，但不参与新流水线
try:
    from evolution_metrics import collect_metrics  # noqa: F401 — 保留给外部 import
except ImportError:
    pass


async def run_daily_pipeline(date: str, *, days: int = 1, dry_run: bool = False) -> dict:
    """每日 22:00 调度入口：snapshot → anomaly → problem report"""
    print(f"=== 问题分析流水线 — {date}（{days}天） ===")

    from emily_core.snapshot import collect_snapshot

    snapshot = await collect_snapshot(date, days=days)
    anomalies = detect_anomalies(snapshot, days=days)
    print(f"  快照采集完成: {len(anomalies)} 条异常")

    try:
        from evolution_insight import generate_insight
        insight = await generate_insight(date, days=days, dry_run=dry_run)
        print(f"  问题分析报告: status={insight.get('status')}")
    except Exception as e:
        insight = {"status": "error", "error": str(e)}
        print(f"  报告生成失败: {e}")

    return {"snapshot_collected": True, "anomalies": anomalies, "report": insight}


async def run_weekly_pipeline(end_date: str, *, days: int = 7, dry_run: bool = False) -> dict:
    """每周日 22:30 调度入口：rule_induction → patch_generation"""
    print(f"=== 每周流水线 — {end_date} (近 {days} 天) ===")

    try:
        from evolution_rules import induct_rules
        rules = await induct_rules(end_date, days=days, dry_run=dry_run)
        print(f"  规则归纳: {len(rules)} 条规则")
    except Exception as e:
        rules = [{"status": "error", "error": str(e)}]
        print(f"  规则归纳失败: {e}")

    try:
        from evolution_patch import generate_patches
        patches = await generate_patches(dry_run=dry_run)
        print(f"  补丁生成: {len(patches)} 个补丁")
    except Exception as e:
        patches = [{"status": "error", "error": str(e)}]
        print(f"  补丁生成失败: {e}")

    return {"rules": rules, "patches": patches}


async def run_morning(date: str, *, dry_run: bool = False) -> dict:
    """每日 07:30 调度入口：晨报"""
    print(f"=== 晨报流水线 — {date} ===")
    try:
        from evolution_morning import generate_morning_report
        reports = await generate_morning_report(date, dry_run=dry_run)
        return {"reports": reports}
    except Exception as e:
        print(f"  晨报生成失败: {e}")
        return {"reports": [], "error": str(e)}


async def run_validation(*, dry_run: bool = False) -> dict:
    """每日 08:00 调度入口：补丁验证"""
    print(f"=== 补丁验证流水线 ===")
    try:
        from evolution_validate import validate_patches
        results = await validate_patches(dry_run=dry_run)
        return {"validation_results": results}
    except Exception as e:
        print(f"  验证失败: {e}")
        return {"validation_results": [], "error": str(e)}


async def run_full_pipeline(date: str, *, dry_run: bool = False) -> dict:
    """全流程：daily + morning"""
    daily = await run_daily_pipeline(date, dry_run=dry_run)
    morning = await run_morning(date, dry_run=dry_run)
    return {"daily": daily, "morning": morning}


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="进化闭环薄聚合脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("daily", help="洞察流水线: metrics -> anomaly -> insight")
    p.add_argument("--date", "-d", required=True)
    p.add_argument("--days", type=int, default=1, help="复盘天数（默认1）")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("weekly", help="每周流水线: rules -> patches")
    p.add_argument("--end-date", "-d", required=True)
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("morning", help="晨报")
    p.add_argument("--date", "-d", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("validate", help="补丁验证")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("full", help="全流程: daily + morning")
    p.add_argument("--date", "-d", required=True)
    p.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    dry_run = getattr(args, 'dry_run', False)

    if args.command == "daily":
        asyncio.run(run_daily_pipeline(args.date, days=args.days, dry_run=dry_run))
    elif args.command == "weekly":
        asyncio.run(run_weekly_pipeline(args.end_date, days=args.days, dry_run=dry_run))
    elif args.command == "morning":
        asyncio.run(run_morning(args.date, dry_run=dry_run))
    elif args.command == "validate":
        asyncio.run(run_validation(dry_run=dry_run))
    elif args.command == "full":
        asyncio.run(run_full_pipeline(args.date, dry_run=dry_run))


if __name__ == "__main__":
    main()
