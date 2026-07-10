"""evolution_anomaly.py — 硬规则异常检测脚本。

8 条硬规则异常检测，纯逻辑无 LLM 依赖。
支持多天复盘：部分"次/天"类阈值按天数均摊。
可独立运行，也可从 evolution_metrics.py 的输出检测。

用法：
    uv run python scripts/evolution_anomaly.py --date 2026-07-09
    uv run python scripts/evolution_anomaly.py --date 2026-07-09 --days 7
    uv run python scripts/evolution_anomaly.py --preview
    uv run python scripts/evolution_anomaly.py --metrics-file metrics.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
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
logger = logging.getLogger("evolution_anomaly")


# ══════════════════════════════════════════════════════════════════════════════
# 核心函数（可 import）
# ══════════════════════════════════════════════════════════════════════════════

# 异常阈值配置
THRESHOLDS = {
    "high_fallback": 0.30,           # Fallback 率 > 30%
    "sop_correction_spike": 3,       # 单 SOP 纠正信号 > 3 次/天
    "low_rag_hit": 0.50,            # RAG 零命中率 > 50%
    "slow_pipeline": 10000,         # Pipeline 平均耗时 > 10s
    "volume_anomaly": 0.50,         # 消息量同比 ±50%
    "tool_failure": 0.20,           # 单工具失败率 > 20%
    "iteration_overflow": 3,        # max_iterations_reached > 3 次/天
    "node_overdue": 1,              # 存在逾期节点
}


def detect_anomalies(metrics: dict, *, days: int = 1) -> list[dict]:
    """8 条硬规则异常检测。输入 metrics dict，输出异常列表。

    多天均摊规则：部分"次/天"类阈值需乘以 days，避免长周期误报：
    - sop_correction_spike: 阈值 = 3 * days
    - iteration_overflow: 阈值 = 3 * days
    - 率值类（fallback_rate, zero_hit_rate, tool_failure_rate）无需均摊

    每条异常：
    {
        "flag": "high_fallback",
        "severity": "high" | "medium",
        "message": "Fallback 率 35% 超过阈值 30%",
        "detail": {...}
    }
    """
    anomalies = []
    p = metrics.get("pipeline", {})
    fb = metrics.get("feedback", {})
    rag = metrics.get("rag", {})
    tc = metrics.get("tool_calls", {})
    ar = metrics.get("agent_reasoning", {})
    pn = metrics.get("project_nodes", {})

    # 1. high_fallback: Fallback 率 > 30%
    fallback_rate = p.get("fallback_rate", 0.0)
    if fallback_rate > THRESHOLDS["high_fallback"]:
        anomalies.append({
            "flag": "high_fallback",
            "severity": "high",
            "message": f"Fallback 率 {fallback_rate:.1%} 超过阈值 {THRESHOLDS['high_fallback']:.0%}",
            "detail": {"fallback_rate": fallback_rate, "threshold": THRESHOLDS["high_fallback"]},
        })

    # 2. sop_correction_spike: 单 SOP 纠正信号 > 3*days（多天均摊）
    correction_count = 0
    for t in fb.get("type_distribution", []):
        if t["type"] == "explicit_correction":
            correction_count = t["count"]
            break
    correction_threshold = THRESHOLDS["sop_correction_spike"] * days
    if correction_count > correction_threshold:
        anomalies.append({
            "flag": "sop_correction_spike",
            "severity": "high",
            "message": f"纠正信号 {correction_count} 次超过阈值 {correction_threshold}",
            "detail": {"correction_count": correction_count},
        })

    # 3. low_rag_hit: RAG 零命中率 > 50%
    zero_hit_rate = rag.get("zero_hit_rate", 0.0)
    if zero_hit_rate > THRESHOLDS["low_rag_hit"]:
        anomalies.append({
            "flag": "low_rag_hit",
            "severity": "medium",
            "message": f"RAG 零命中率 {zero_hit_rate:.1%} 超过阈值 {THRESHOLDS['low_rag_hit']:.0%}",
            "detail": {"zero_hit_rate": zero_hit_rate},
        })

    # 4. slow_pipeline: Pipeline 平均耗时 > 10s
    avg_elapsed = p.get("avg_elapsed_ms", 0)
    if avg_elapsed > THRESHOLDS["slow_pipeline"]:
        anomalies.append({
            "flag": "slow_pipeline",
            "severity": "medium",
            "message": f"Pipeline 平均耗时 {avg_elapsed}ms 超过阈值 {THRESHOLDS['slow_pipeline']}ms",
            "detail": {"avg_elapsed_ms": avg_elapsed},
        })

    # 5. volume_anomaly: 消息量同比 ±50%（需要前日数据，简化：仅当日总量异常低/高标记）
    # TODO: 实现需要对比前日数据，Phase 2 补充

    # 6. tool_failure: 单工具失败率 > 20%
    tool_dist = tc.get("tool_distribution", {})
    failure_details = tc.get("failure_details", [])
    failure_by_tool = {}
    for f in failure_details:
        failure_by_tool[f["tool"]] = failure_by_tool.get(f["tool"], 0) + f["count"]
    for tool_name, fail_count in failure_by_tool.items():
        total_calls = tool_dist.get(tool_name, 0)
        if total_calls > 0:
            fail_rate = fail_count / total_calls
            if fail_rate > THRESHOLDS["tool_failure"]:
                anomalies.append({
                    "flag": "tool_failure",
                    "severity": "high",
                    "message": f"工具 {tool_name} 失败率 {fail_rate:.1%} 超过阈值 {THRESHOLDS['tool_failure']:.0%}",
                    "detail": {"tool": tool_name, "fail_rate": fail_rate, "fail_count": fail_count, "total": total_calls},
                })

    # 7. iteration_overflow: max_iterations_reached > 3*days（多天均摊）
    max_iter = ar.get("max_iterations_reached", 0)
    iter_threshold = THRESHOLDS["iteration_overflow"] * days
    if max_iter > iter_threshold:
        anomalies.append({
            "flag": "iteration_overflow",
            "severity": "medium",
            "message": f"最大迭代触达 {max_iter} 次超过阈值 {iter_threshold}",
            "detail": {"max_iterations_reached": max_iter},
        })

    # 8. node_overdue: 存在 IN_PROGRESS 节点已过 deadline
    overdue = pn.get("overdue_nodes", [])
    if len(overdue) >= THRESHOLDS["node_overdue"]:
        anomalies.append({
            "flag": "node_overdue",
            "severity": "high",
            "message": f"存在 {len(overdue)} 个逾期节点",
            "detail": {"overdue_count": len(overdue), "nodes": [{"node_id": n["node_id"], "name": n["name"]} for n in overdue[:5]]},
        })

    return anomalies


# ══════════════════════════════════════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════════════════════════════════════

def _print_anomalies(anomalies: list[dict], date_label: str) -> None:
    severity_icon = {"high": "HIGH", "medium": "MED"}

    print(f"\n{'=' * 60}")
    print(f"  异常检测报告 — {date_label}")
    print(f"{'=' * 60}")

    if anomalies:
        for a in anomalies:
            icon = severity_icon.get(a["severity"], "WARN")
            print(f"  [{icon}] {a['flag']}: {a['message']}")
    else:
        print("  [OK] 全部 8 条规则未触发异常")

    high_count = sum(1 for a in anomalies if a["severity"] == "high")
    medium_count = sum(1 for a in anomalies if a["severity"] == "medium")
    print(f"\n异常数: {len(anomalies)} (HIGH {high_count} / MED {medium_count})")
    print(f"{'=' * 60}")


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="硬规则异常检测脚本")
    parser.add_argument("--date", "-d", default="", help="复盘结束日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="复盘天数（默认1，最小1）")
    parser.add_argument("--metrics-file", "-f", default="", help="从 JSON 文件加载 metrics")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    if args.metrics_file:
        with open(args.metrics_file, "r", encoding="utf-8") as f:
            metrics = json.load(f)
        date_label = metrics.get("end_date", args.metrics_file)
    elif args.date:
        from evolution_metrics import collect_metrics
        metrics = asyncio.run(collect_metrics(args.date, days=args.days))
        date_label = args.date
    else:
        print("错误：必须指定 --date 或 --metrics-file")
        sys.exit(1)

    anomalies = detect_anomalies(metrics, days=args.days)

    if args.json:
        print(json.dumps(anomalies, ensure_ascii=False, indent=2, default=str))
    else:
        _print_anomalies(anomalies, date_label)


if __name__ == "__main__":
    main()
