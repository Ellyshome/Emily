"""evolution_anomaly.py — 硬规则异常检测脚本。

基于快照（snapshot）数据做硬规则检测，纯逻辑无 LLM。
当前规则聚焦于快照中实际有数据的维度。

用法：
    uv run python scripts/evolution_anomaly.py --date 2026-07-26
    uv run python scripts/evolution_anomaly.py --snapshot-file snapshot.json
    uv run python scripts/evolution_anomaly.py --date 2026-07-26 --json
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
# 异常阈值配置
# ══════════════════════════════════════════════════════════════════════════════

THRESHOLDS = {
    "node_overdue": 1,            # 存在 >=1 个逾期节点
    "system_error_high": 20,      # 系统报错 > 20 条
    "system_error_warn": 5,       # 系统报错 > 5 条
    "session_anomaly_high": 10,   # Session 异常 > 10 处
    "session_anomaly_warn": 3,    # Session 异常 > 3 处
    "no_chat_activity": 0,        # 目标日期无任何聊天记录
}

# ══════════════════════════════════════════════════════════════════════════════
# 核心函数（可 import）
# ══════════════════════════════════════════════════════════════════════════════


def detect_anomalies(snapshot: dict, *, days: int = 1) -> list[dict]:
    """硬规则异常检测。输入 snapshot dict，输出异常列表。

    snapshot 结构：
        {
            "projects": { "aggregate": { "overdue_nodes": [...] } },
            "chat_samples": { "total_messages": ..., "conversations": [...] },
            "system_errors": { "error_count_dedup": ..., "errors": [...] },
            "session_anomalies": { "anomaly_count": ..., "anomalies": [...] }
        }

    每条异常：
    {
        "flag": "node_overdue",
        "severity": "high" | "medium",
        "message": "存在 13 个逾期节点",
        "detail": {...}
    }
    """
    anomalies = []

    projects = snapshot.get("projects", {})
    agg = projects.get("aggregate", {})
    chat = snapshot.get("chat_samples", {})
    sys_err = snapshot.get("system_errors", {})
    sess_anom = snapshot.get("session_anomalies", {})

    # 1. node_overdue: 存在逾期节点
    overdue = agg.get("overdue_nodes", [])
    if len(overdue) >= THRESHOLDS["node_overdue"]:
        anomalies.append({
            "flag": "node_overdue",
            "severity": "high",
            "message": f"存在 {len(overdue)} 个逾期节点",
            "detail": {
                "overdue_count": len(overdue),
                "nodes": [{"node_id": n["node_id"], "name": n["name"]} for n in overdue[:10]]
            },
        })

    # 2. system_error: 系统日志报错过多
    err_count = sys_err.get("error_count_dedup", 0)
    if err_count > THRESHOLDS["system_error_high"]:
        anomalies.append({
            "flag": "system_error_high",
            "severity": "high",
            "message": f"系统报错 {err_count} 条超过严重阈值 {THRESHOLDS['system_error_high']}",
            "detail": {
                "error_count": err_count,
                "sample_errors": [e[:150] for e in sys_err.get("errors", [])[:5]],
            },
        })
    elif err_count > THRESHOLDS["system_error_warn"]:
        anomalies.append({
            "flag": "system_error_warn",
            "severity": "medium",
            "message": f"系统报错 {err_count} 条超过警告阈值 {THRESHOLDS['system_error_warn']}",
            "detail": {
                "error_count": err_count,
                "sample_errors": [e[:150] for e in sys_err.get("errors", [])[:3]],
            },
        })

    # 3. session_anomaly: Session 归档中异常过多
    anom_count = sess_anom.get("anomaly_count", 0)
    if anom_count > THRESHOLDS["session_anomaly_high"]:
        anomalies.append({
            "flag": "session_anomaly_high",
            "severity": "high",
            "message": f"Session 异常 {anom_count} 处超过严重阈值 {THRESHOLDS['session_anomaly_high']}",
            "detail": {
                "anomaly_count": anom_count,
                "sample_anomalies": [a.get("excerpt", "")[:150] for a in sess_anom.get("anomalies", [])[:5]],
            },
        })
    elif anom_count > THRESHOLDS["session_anomaly_warn"]:
        anomalies.append({
            "flag": "session_anomaly_warn",
            "severity": "medium",
            "message": f"Session 异常 {anom_count} 处超过警告阈值 {THRESHOLDS['session_anomaly_warn']}",
            "detail": {
                "anomaly_count": anom_count,
                "sample_anomalies": [a.get("excerpt", "")[:150] for a in sess_anom.get("anomalies", [])[:3]],
            },
        })

    # 4. no_chat_activity: 目标日期无聊天
    total_msgs = chat.get("total_messages", 0)
    if total_msgs <= THRESHOLDS["no_chat_activity"]:
        anomalies.append({
            "flag": "no_chat_activity",
            "severity": "medium",
            "message": "目标日期无任何聊天记录",
            "detail": {"total_messages": 0},
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
        print("  [OK] 全部规则未触发异常")

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

    parser = argparse.ArgumentParser(description="硬规则异常检测脚本（基于快照）")
    parser.add_argument("--date", "-d", default="", help="日期 YYYY-MM-DD（自动采集快照）")
    parser.add_argument("--days", type=int, default=1, help="复盘天数（默认1）")
    parser.add_argument("--snapshot-file", "-f", default="", help="从 JSON 文件加载快照")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    if args.snapshot_file:
        with open(args.snapshot_file, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        date_label = snapshot.get("meta", {}).get("end_date", args.snapshot_file)
    elif args.date:
        from emily_core.snapshot import collect_snapshot
        snapshot = asyncio.run(collect_snapshot(args.date, days=args.days))
        date_label = args.date
    else:
        print("错误：必须指定 --date 或 --snapshot-file")
        sys.exit(1)

    anomalies = detect_anomalies(snapshot, days=args.days)

    if args.json:
        print(json.dumps(anomalies, ensure_ascii=False, indent=2, default=str))
    else:
        _print_anomalies(anomalies, date_label)


if __name__ == "__main__":
    main()
