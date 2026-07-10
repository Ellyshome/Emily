"""evolution_rules.py — 规则归纳脚本。

从近 N 天洞察中归纳进化规则。
可独立运行，也可 import induct_rules()。

用法：
    uv run python scripts/evolution_rules.py --end-date 2026-07-09
    uv run python scripts/evolution_rules.py --end-date 2026-07-09 --days 14
    uv run python scripts/evolution_rules.py --end-date 2026-07-09 --preview
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evolution_rules")


async def induct_rules(end_date: str, *, days: int = 7, db_url: str = "", dry_run: bool = False) -> list[dict]:
    """规则归纳（脚本入口）。

    Args:
        end_date: 分析截止日期
        days: 回顾天数（默认 7）
        db_url: PostgreSQL 连接 URL
        dry_run: 预览模式

    Returns:
        归纳出的规则列表
    """
    from emily_core.services.evolution.rule_inductor import RuleInductor
    from evolution_metrics import _init_db

    _init_db(db_url)

    llm_client = None
    if not dry_run:
        try:
            from emily_core.infrastructure.llm.client import LLMClient
            api_key = os.environ.get("EMILY_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
            base_url = os.environ.get("EMILY_LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
            model = os.environ.get("EMILY_LLM_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o"))
            if api_key and base_url:
                llm_client = LLMClient(api_key=api_key, base_url=base_url, model=model)
            else:
                logger.warning("LLM not configured, running in preview mode")
                dry_run = True
        except Exception as e:
            logger.warning("Failed to init LLM: %s", e)
            dry_run = True

    inductor = RuleInductor(llm_client=llm_client)
    rules = await inductor.induct(end_date, days=days, dry_run=dry_run)
    return rules


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="进化规则归纳脚本")
    parser.add_argument("--end-date", "-d", required=True, help="分析截止日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=7, help="回顾天数（默认 7）")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--preview", action="store_true", help="预览模式（不调 LLM）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    rules = asyncio.run(induct_rules(
        args.end_date,
        days=args.days,
        db_url=args.db_url,
        dry_run=args.preview,
    ))

    if args.json:
        print(json.dumps(rules, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n规则归纳完成: {len(rules)} 条规则")
        for r in rules:
            status = r.get("status", "")
            if status == "preview":
                print(f"  预览模式: 趋势={r.get('trend_data', {})}, 重复异常={r.get('recurring_anomalies', [])}")
            elif status == "generated" or "rule_no" in r:
                print(f"  {r.get('rule_no', '?')}: {r.get('title', '?')} (confidence={r.get('confidence', 0)})")
            else:
                print(f"  {r}")


if __name__ == "__main__":
    main()
