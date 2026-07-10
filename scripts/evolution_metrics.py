"""evolution_metrics.py — 指标聚合脚本。

聚合 10 个数据源指标，输出人类可读报告。
支持可变复盘周期（默认1天，支持N天）。
参照 scripts/manage_nodes.py 的 CLI 模式。

用法：
    uv run python scripts/evolution_metrics.py --date 2026-07-09
    uv run python scripts/evolution_metrics.py --date 2026-07-09 --days 7
    uv run python scripts/evolution_metrics.py --date 2026-07-09 --preview
    uv run python scripts/evolution_metrics.py --date 2026-07-09 --source pipeline
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
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
logger = logging.getLogger("evolution_metrics")


def _detect_docker_pg_port() -> int | None:
    """参照 collect_session_data.py 的 Docker PG 端口检测。"""
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


# ══════════════════════════════════════════════════════════════════════════════
# 核心函数（可 import）
# ══════════════════════════════════════════════════════════════════════════════

async def collect_metrics(end_date: str, *, days: int = 1, db_url: str = "") -> dict:
    """聚合 10 个数据源指标，返回完整 metrics dict。days 为复盘天数，默认 1，最小 1。"""
    _init_db(db_url)

    from emily_core.repositories.evolution_repo import EvolutionRepo
    from emily_core.infrastructure.database.session import get_session

    # 计算起止日期
    start_dt = datetime.fromisoformat(end_date) - timedelta(days=days - 1)
    start_date = start_dt.strftime("%Y-%m-%d")

    with get_session() as sess:
        metrics = {
            "start_date": start_date,
            "end_date": end_date,
            "analysis_days": days,
            "pipeline": EvolutionRepo.aggregate_pipeline_logs(start_date, end_date, session=sess),
            "sop_routing": EvolutionRepo.aggregate_sop_routing_logs(start_date, end_date, session=sess),
            "feedback": EvolutionRepo.aggregate_feedback_signals(start_date, end_date, session=sess),
            "rag": EvolutionRepo.aggregate_rag_logs(start_date, end_date, session=sess),
            "business_events": EvolutionRepo.aggregate_business_events(start_date, end_date, session=sess),
            "session_lifecycle": EvolutionRepo.aggregate_session_lifecycle(start_date, end_date, session=sess),
            "agent_reasoning": EvolutionRepo.aggregate_agent_reasoning(start_date, end_date, session=sess),
            "tool_calls": EvolutionRepo.aggregate_tool_calls(start_date, end_date, session=sess),
            "project_nodes": EvolutionRepo.aggregate_project_nodes(end_date, session=sess),
            "cognition_drift": EvolutionRepo.aggregate_cognition_drift(end_date, session=sess),
        }

    return metrics


async def collect_single_source(source: str, end_date: str, *, days: int = 1, db_url: str = "") -> dict:
    """聚合单个数据源指标（用于排错）。"""
    _init_db(db_url)

    from emily_core.repositories.evolution_repo import EvolutionRepo
    from emily_core.infrastructure.database.session import get_session

    start_dt = datetime.fromisoformat(end_date) - timedelta(days=days - 1)
    start_date = start_dt.strftime("%Y-%m-%d")

    source_map = {
        "pipeline": EvolutionRepo.aggregate_pipeline_logs,
        "sop_routing": EvolutionRepo.aggregate_sop_routing_logs,
        "feedback": EvolutionRepo.aggregate_feedback_signals,
        "rag": EvolutionRepo.aggregate_rag_logs,
        "business_events": EvolutionRepo.aggregate_business_events,
        "session_lifecycle": EvolutionRepo.aggregate_session_lifecycle,
        "agent_reasoning": EvolutionRepo.aggregate_agent_reasoning,
        "tool_calls": EvolutionRepo.aggregate_tool_calls,
        "project_nodes": EvolutionRepo.aggregate_project_nodes,
        "cognition_drift": EvolutionRepo.aggregate_cognition_drift,
    }

    if source not in source_map:
        raise ValueError(f"未知数据源: {source}，可选: {list(source_map.keys())}")

    with get_session() as sess:
        if source in ("project_nodes", "cognition_drift"):
            return source_map[source](end_date, session=sess)
        return source_map[source](start_date, end_date, session=sess)


# ══════════════════════════════════════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════════════════════════════════════

def _print_metrics(metrics: dict) -> None:
    """输出人类可读的指标报告。"""
    end_date = metrics["end_date"]
    days = metrics.get("analysis_days", 1)

    period_label = f"{end_date}（{days}天）" if days > 1 else end_date
    print(f"\n{'=' * 60}")
    print(f"  指标聚合报告 — {period_label}")
    print(f"{'=' * 60}")

    # A. Pipeline
    p = metrics["pipeline"]
    print(f"\n[A] Pipeline 执行日志")
    print(f"  总执行: {p['total']} | SOP 命中: {p['hit']} ({p['sop_hit_rate']:.1%}) | Fallback: {p['fallback']} ({p['fallback_rate']:.1%})")
    if p["status_distribution"]:
        print(f"  状态: {', '.join(f'{k}={v}' for k, v in p['status_distribution'].items())}")
    print(f"  平均耗时: {p['avg_elapsed_ms']}ms | 最大: {p['max_elapsed_ms']}ms | 被阻断: {p['blocked']}")

    # B. SOP Routing
    sr = metrics["sop_routing"]
    print(f"\n[B] SOP 路由日志")
    print(f"  未命中: {sr['not_hit_count']} 次")
    if sr["miss_samples"]:
        print(f"  未命中样本:")
        for s in sr["miss_samples"][:5]:
            print(f"    - \"{s['message'][:50]}\" (confidence={s['confidence']})")

    # C. Feedback
    fb = metrics["feedback"]
    print(f"\n[C] 用户反馈信号")
    if fb["type_distribution"]:
        for t in fb["type_distribution"]:
            print(f"  {t['type']}: {t['count']} 次 (avg_strength={t['avg_strength']:.2f})")

    # D. RAG
    rag = metrics["rag"]
    print(f"\n[D] RAG 检索日志")
    print(f"  总查询: {rag['total']} | 命中: {rag['hit']} | 零命中: {rag['zero_hit_count']} ({rag['zero_hit_rate']:.1%})")
    print(f"  平均最高分: {rag['avg_top_score']:.4f} | 平均延迟: {rag['avg_latency_ms']}ms")

    # E. Business Events
    be = metrics["business_events"]
    print(f"\n[E] 业务事件日志")
    if be["category_distribution"]:
        print(f"  类别: {', '.join(f'{k}={v}' for k, v in be['category_distribution'].items())}")

    # F. Session Lifecycle
    sl = metrics["session_lifecycle"]
    print(f"\n[F] Session 生命周期")
    print(f"  新建: {sl['sessions_created']} | 归档: {sl['sessions_archived']} | 平均时长: {sl['avg_duration_ms']}ms")

    # G. Agent Reasoning
    ar = metrics["agent_reasoning"]
    print(f"\n[G] Agent 推理日志")
    print(f"  Fallback: {ar['fallback_count']} | 最大迭代触达: {ar['max_iterations_reached']} | 平均迭代: {ar['avg_iterations']}")

    # H. Tool Calls
    tc = metrics["tool_calls"]
    print(f"\n[H] 工具调用日志")
    if tc["tool_distribution"]:
        top3 = list(tc["tool_distribution"].items())[:3]
        print(f"  Top 工具: {', '.join(f'{k}={v}' for k, v in top3)}")

    # I. Project Nodes
    pn = metrics["project_nodes"]
    print(f"\n[I] 项目节点")
    if pn["status_distribution"]:
        print(f"  状态: {', '.join(f'{k}={v}' for k, v in pn['status_distribution'].items())}")
    if pn["overdue_nodes"]:
        print(f"  逾期节点: {len(pn['overdue_nodes'])} 个")
        for n in pn["overdue_nodes"][:3]:
            print(f"    - {n['node_id']} {n['name']} (截止 {n['deadline']})")

    # J. Cognition Drift
    cd = metrics["cognition_drift"]
    print(f"\n[J] 认知偏差")
    print(f"  总项目: {cd['total_projects']} | 有世界书: {cd['projects_with_world_book']} | 无世界书: {cd['projects_without_world_book']}")
    print(f"  世界书覆盖率: {cd['world_book_coverage']:.1%}")
    print(f"  有偏差项目: {cd['projects_with_drift']} ({cd['drift_rate']:.1%}) | 总过时层数: {cd['total_stale_layers']}")
    if cd["stale_layer_distribution"]:
        layer_summary = ", ".join(f"{k}={v}" for k, v in cd["stale_layer_distribution"].items() if v > 0)
        if layer_summary:
            print(f"  过时层分布: {layer_summary}")
    if cd.get("project_drifts"):
        drifted = [p for p in cd["project_drifts"] if p["has_drift"]]
        for p in drifted[:3]:
            print(f"  - {p['project_name']} ({p['project_id'][:8]}...): {', '.join(p['stale_layers'])}")

    print(f"\n{'=' * 60}")


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Windows GBK 修复
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="日指标聚合脚本")
    parser.add_argument("--date", "-d", required=True, help="复盘结束日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="复盘天数（默认1，最小1，支持7/30等）")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--source", "-s", default="", help="只查单个数据源 (pipeline/sop_routing/feedback/rag/business_events/session_lifecycle/agent_reasoning/tool_calls/project_nodes/cognition_drift)")
    parser.add_argument("--preview", action="store_true", help="预览模式（只读不写）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    if args.source:
        result = asyncio.run(collect_single_source(args.source, args.date, days=args.days, db_url=args.db_url))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        metrics = asyncio.run(collect_metrics(args.date, days=args.days, db_url=args.db_url))
        if args.json:
            print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
        else:
            _print_metrics(metrics)


if __name__ == "__main__":
    main()
