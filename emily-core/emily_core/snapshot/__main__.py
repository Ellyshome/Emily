"""snapshot CLI 入口 — python -m emily_core.snapshot

需在 emily-core 目录下执行，或使用根目录 scripts/snapshot.py 包装脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from datetime import datetime

from .collector import collect_snapshot


def _print_snapshot(snapshot: dict) -> None:
    """人类可读输出。"""
    meta = snapshot["meta"]
    projects = snapshot["projects"]
    chat_samples = snapshot["chat_samples"]
    system_errors = snapshot["system_errors"]
    session_anomalies = snapshot["session_anomalies"]

    period_label = f"{meta['end_date']}（{meta['analysis_days']}天）" if meta["analysis_days"] > 1 else meta["end_date"]

    print(f"\n{'=' * 60}")
    print(f"  进化快照 — {period_label}")
    print(f"  采集时间: {meta['collected_at']}")
    print(f"{'=' * 60}")

    # ── 项目节点 ──
    print(f"\n── 项目节点 ──")
    agg = projects.get("aggregate", {})
    status_dist = agg.get("status_distribution", {})
    overdue = agg.get("overdue_nodes", [])
    print(f"状态分布: {', '.join(f'{k}={v}' for k, v in status_dist.items()) if status_dist else '无'}")
    print(f"逾期节点: {len(overdue)}个")
    by_proj = projects.get("by_project", {})
    for pid, pinfo in by_proj.items():
        print(f"  {pinfo['project_name']} ({pid[:8]}..): 节点{pinfo['total_nodes']} 逾期{pinfo['overdue_count']}")
        od_nodes = pinfo.get("overdue_nodes", [])[:5]
        for on in od_nodes:
            print(f"    ⚠ {on['name']} 截止{on['deadline']}")

    # ── 出入站聊天记录 ──
    print(f"\n── 出入站聊天记录 ──")
    cs = chat_samples
    print(f"{cs['start_date']} ~ {cs['end_date']}: {cs['total_messages']}条消息, {cs['total_conversations']}个会话")
    convs = cs.get("conversations", [])
    if convs:
        max_show = 5
        for ci, conv in enumerate(convs[:max_show]):
            turns = conv["turns"]
            print(f"\n  [{ci+1}] 用户 {conv['user']} — {len(turns)}轮")
            for turn in turns[:10]:
                role = "👤" if turn["direction"] == "user_to_agent" else "🤖"
                content = turn["content"][:100]
                print(f"    {turn['time']} {role} {content}")
        if len(convs) > max_show:
            print(f"  ... 共{len(convs)}个会话，仅展示前{max_show}个")
    else:
        print(f"  (无对话记录)")

    # ── 系统日志报错 ──
    print(f"\n── 系统日志报错 ──")
    se = system_errors
    if not se.get("exists"):
        print(f"日志文件不存在: {se.get('log_file', '?')}")
    else:
        print(f"{se['log_file']}: {se['total_lines']}行, ERROR原始{se['error_count_raw']}条, 去重后{se['error_count_dedup']}条")
        errs = se.get("errors", [])[:8]
        if errs:
            print(f"  报错摘要 (前{len(errs)}):")
            for ei, e in enumerate(errs, 1):
                first = e.split("\n")[0][:120]
                print(f"    [{ei}] {first}")
        else:
            print(f"  ✅ 无报错")

    # ── Session 异常 ──
    print(f"\n── Session 归档异常 ──")
    sa = session_anomalies
    if not sa.get("exists"):
        print(f"路径不存在: {sa.get('path', '?')}")
    else:
        print(f"扫描 {sa.get('target_files_scanned', 0)} 个归档, 发现 {sa.get('anomaly_count', 0)} 处异常")
        anoms = sa.get("anomalies", [])
        by_type: dict[str, int] = {}
        for a in anoms:
            t = a.get("type", "?")
            by_type[t] = by_type.get(t, 0) + 1
        if by_type:
            print(f"  类型: {', '.join(f'{k}={v}' for k,v in by_type.items())}")
        top_anoms = anoms[:5]
        if top_anoms:
            print(f"  异常详情 (前{len(top_anoms)}):")
            for a in top_anoms:
                print(f"    [{a['type']}] {a['user']} — {a['excerpt'][:100]}")
        else:
            print(f"  ✅ 无异常")

    print(f"\n{'=' * 60}")


def main():
    """CLI 入口。"""
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace') if hasattr(sys.stdout, 'buffer') else sys.stdout

    parser = argparse.ArgumentParser(description="进化快照采集器")
    parser.add_argument("--date", "-d", default="", help="截止日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=1, help="复盘天数（默认 1）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    date = args.date or datetime.utcnow().strftime("%Y-%m-%d")

    snapshot = asyncio.run(collect_snapshot(
        date,
        days=args.days,
    ))

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    else:
        _print_snapshot(snapshot)


if __name__ == "__main__":
    main()
