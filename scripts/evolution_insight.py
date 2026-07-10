"""evolution_insight.py — 日洞察生成脚本。

调用 InsightGenerator 完成完整洞察生成流水线。
支持可变复盘周期（默认1天，支持N天）。
可独立运行，也可 import generate_insight()。

用法：
    uv run python scripts/evolution_insight.py --date 2026-07-09
    uv run python scripts/evolution_insight.py --date 2026-07-09 --days 7
    uv run python scripts/evolution_insight.py --date 2026-07-09 --preview
    uv run python scripts/evolution_insight.py --date 2026-07-09 --show
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
logger = logging.getLogger("evolution_insight")


# ══════════════════════════════════════════════════════════════════════════════
# 核心函数（可 import）
# ══════════════════════════════════════════════════════════════════════════════

async def generate_insight(end_date: str, *, days: int = 1, db_url: str = "", dry_run: bool = False) -> dict:
    """洞察生成（脚本入口），支持可变复盘周期。

    Args:
        end_date: 复盘结束日期 YYYY-MM-DD
        days: 复盘天数（默认 1，最小 1）
        db_url: PostgreSQL 连接 URL
        dry_run: 预览模式，不调 LLM 不写 DB

    Returns:
        dict with status, metrics, anomalies, insight
    """
    from emily_core.infrastructure.database.session import get_session
    from emily_core.services.evolution.insight_generator import InsightGenerator
    from evolution_metrics import _init_db

    _init_db(db_url)

    # 尝试初始化 LLM client
    llm_client = None
    if not dry_run:
        try:
            from emily_core.infrastructure.llm.client import LLMClient
            api_key = os.environ.get("EMILY_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
            base_url = os.environ.get("EMILY_LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
            model = os.environ.get("EMILY_LLM_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o"))
            if api_key and base_url:
                llm_client = LLMClient(
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                )
                logger.info("LLM client initialized: model=%s", model)
            else:
                logger.warning("LLM credentials not configured, running in preview mode")
                dry_run = True
        except Exception as e:
            logger.warning("Failed to init LLM client: %s, running in preview mode", e)
            dry_run = True

    generator = InsightGenerator(llm_client=llm_client)
    result = await generator.generate(end_date, days=days, dry_run=dry_run)
    return result


async def show_insight(date: str) -> dict | None:
    """查看已有洞察。"""
    from emily_core.repositories.evolution_repo import EvolutionRepo
    from evolution_metrics import _init_db

    _init_db()
    insight = EvolutionRepo.get_insight_by_date(date)
    if insight is None:
        print(f"未找到日期 {date} 的洞察记录")
        return None
    return {
        "insight_date": insight.insight_date,
        "analysis_days": insight.analysis_days,
        "sop_hit_rate": insight.sop_hit_rate,
        "fallback_rate": insight.fallback_rate,
        "health_score": insight.health_score,
        "anomaly_flags": json.loads(insight.anomaly_flags) if insight.anomaly_flags else [],
        "insight": json.loads(insight.insight_text) if insight.insight_text else {},
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="日洞察生成脚本（支持可变周期）")
    parser.add_argument("--date", "-d", required=True, help="复盘结束日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="复盘天数（默认1，最小1）")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--preview", action="store_true", help="预览模式（不调 LLM、不写 DB）")
    parser.add_argument("--show", action="store_true", help="仅显示已有洞察（不生成）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    if args.show:
        result = asyncio.run(show_insight(args.date))
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    result = asyncio.run(generate_insight(
        args.date,
        days=args.days,
        db_url=args.db_url,
        dry_run=args.preview,
    ))

    if args.json:
        # 去除 metrics 减少输出
        output = {k: v for k, v in result.items() if k != "metrics"}
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    else:
        status = result.get("status", "unknown")
        print(f"\n洞察生成完成: status={status}")
        if status == "preview":
            print("预览模式：metrics 聚合已完成，LLM 未调用")
        elif status == "generated":
            insight = result.get("insight", {})
            if insight:
                print(f"健康评分: {insight.get('health_score', 'N/A')}/100")
                print(f"摘要: {insight.get('summary', 'N/A')}")
                findings = insight.get("key_findings", [])
                if findings:
                    print(f"关键发现: {len(findings)} 条")
                    for f in findings[:5]:
                        print(f"  - [{f.get('category', '')}] {f.get('finding', '')}")


if __name__ == "__main__":
    main()
