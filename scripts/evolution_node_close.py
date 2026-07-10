"""evolution_node_close.py — 节点闭合总结脚本。

节点状态从 IN_PROGRESS 转为 COMPLETED 时自动触发。
输出闭合总结 Markdown。

用法：
    uv run python scripts/evolution_node_close.py --node-id SG-JG-01
    uv run python scripts/evolution_node_close.py --node-id SG-JG-01 --preview
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from evolution_metrics import _init_db


async def generate_node_closure(node_id: str, *, db_url: str = "", dry_run: bool = False) -> dict:
    """节点闭合总结生成。

    Returns:
        {node_id, node_name, created_at, completed_at, duration, events_count, deliverables_count, report}
    """
    _init_db(db_url)

    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import ProjectNode, NodeDeliverable, BusinessEventLog

    with get_session() as sess:
        node = sess.query(ProjectNode).filter(
            ProjectNode.node_id == node_id,
            ProjectNode.is_discarded == False,
        ).first()

        if node is None:
            return {"error": f"Node {node_id} not found"}

        # 成果清单
        deliverables = sess.query(NodeDeliverable).filter(
            NodeDeliverable.node_id == node_id,
        ).all()

        # 关联事件
        events = sess.query(BusinessEventLog).filter(
            BusinessEventLog.target_id == node_id,
        ).order_by(BusinessEventLog.created_at).all()

        # 统计
        created_at = node.created_at
        completed_at = node.completed_at or datetime.now().isoformat()

        duration_str = ""
        try:
            start_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration = end_dt - start_dt
            days = duration.days
            hours = duration.seconds // 3600
            duration_str = f"{days} 天 {hours} 小时"
        except Exception:
            duration_str = "无法计算"

    # 构建报告
    report_lines = []
    report_lines.append(f"# 节点闭合总结 — {node.node_name}")
    report_lines.append("")
    report_lines.append(f"- 节点编号：{node.node_id}")
    report_lines.append(f"- 开启时间：{created_at}")
    report_lines.append(f"- 关闭时间：{completed_at}")
    report_lines.append(f"- 总耗时：{duration_str}")
    report_lines.append(f"- 最终进度：{node.progress}%")
    report_lines.append(f"- 关联事件数：{len(events)}")
    report_lines.append(f"- 成果交付数：{len(deliverables)}")
    report_lines.append("")

    if deliverables:
        report_lines.append("## 成果清单")
        report_lines.append("")
        for d in deliverables:
            report_lines.append(f"- {d.deliverable_id}: {d.name if hasattr(d, 'name') else d.deliverable_id}")
        report_lines.append("")

    if events:
        report_lines.append("## 关联事件")
        report_lines.append("")
        for e in events:
            report_lines.append(f"- [{e.event_category}/{e.event_action}] {e.summary}")
        report_lines.append("")

    report_text = "\n".join(report_lines)

    if dry_run:
        return {
            "node_id": node.node_id,
            "node_name": node.node_name,
            "created_at": created_at,
            "completed_at": completed_at,
            "duration": duration_str,
            "events_count": len(events),
            "deliverables_count": len(deliverables),
            "report": report_text,
            "status": "preview",
        }

    return {
        "node_id": node.node_id,
        "node_name": node.node_name,
        "created_at": created_at,
        "completed_at": completed_at,
        "duration": duration_str,
        "events_count": len(events),
        "deliverables_count": len(deliverables),
        "report": report_text,
        "status": "generated",
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="节点闭合总结脚本")
    parser.add_argument("--node-id", "-n", required=True, help="节点编号")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--preview", action="store_true", help="预览模式")

    args = parser.parse_args()

    result = asyncio.run(generate_node_closure(
        args.node_id,
        db_url=args.db_url,
        dry_run=args.preview,
    ))

    if "error" in result:
        print(f"错误: {result['error']}")
    else:
        print(result["report"])


if __name__ == "__main__":
    main()
