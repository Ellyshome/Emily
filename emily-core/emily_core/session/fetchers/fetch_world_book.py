"""fetch_world_book —— 世界书裁剪纯函数（按授权节点裁剪）。

被两处消费：
  1) SessionContext.get_prompt_variables() → render_brief()  生成 {project_brief} 常驻摘要
  2) tools/meta_cognition_tool.py          → render_full()   生成 book="world" 全文

设计约束：
  - 纯函数，无 IO（世界书原文由 SessionContext 缓存 / tool 层读取后传入）
  - fail-open：解析失败或异常一律返回空串，不阻断 prompt 装配
  - 权限 fail-closed：authorized_node_ids 为空 → 不返回任何节点信息

参照模式：emily_core/session/fetchers/fetch_system_description.py（纯函数 + CLI 入口）
"""

from __future__ import annotations

import json
import logging
import argparse

logger = logging.getLogger("emily.session.fetchers.fetch_world_book")

_STATUS_CN = {
    "COMPLETED": "已完成",
    "IN_PROGRESS": "进行中",
    "CONDITIONS_NOT_MET": "条件未满足",
}


def _load(content_json: str) -> dict:
    """解析世界书 content_json；失败返回空 dict（fail-open）。"""
    try:
        data = json.loads(content_json or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning("world book content_json parse failed: %s", e)
        return {}


def _truncate(text: str, max_chars: int) -> str:
    """按行边界截断到 max_chars。"""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    nl = cut.rfind("\n")
    return cut[:nl] if nl > 0 else cut


def _visible(entry: dict, auth: set[str]) -> bool:
    """节点级条目可见性判定（统一出口）。

    带 node_id 的条目：按授权节点集判定；
    无 node_id 的条目：一律不可见（fail-closed，宁少勿多）。
    """
    nid = str((entry or {}).get("node_id", "") or "")
    return bool(nid) and nid in auth


def render_brief(content_json: str, authorized_node_ids: list[str], max_chars: int = 200,
                 include_events: bool = False) -> str:
    """生成常驻摘要（≤max_chars）。

    结构（缺数据则整行省略）：
      📋 {项目名}（{编号}） ｜ 阶段：{阶段}
      📊 {N}节点：{完成}完成 / {进行中}进行中 / {逾期}逾期 ｜ {整体进度}
      🔖 我可见节点（{k}）：{节点名}[状态·未签认] / ...
      🔴 逾期：{节点名} / ...
    """
    data = _load(content_json)
    if not data:
        return ""
    auth = set(authorized_node_ids or [])
    lines: list[str] = []

    o = data.get("ontology") or {}
    if o.get("name"):
        code = f"（{o['code']}）" if o.get("code") else ""
        stage = o.get("lifecycle_stage_label") or ""
        lines.append(f"📋 {o['name']}{code}" + (f" ｜ 阶段：{stage}" if stage else ""))

    s = data.get("structure") or {}
    if int(s.get("total_nodes", 0) or 0) > 0:
        lines.append(
            f"📊 {s.get('total_nodes', 0)}节点：{s.get('completed', 0)}完成 / "
            f"{s.get('in_progress', 0)}进行中 / {s.get('overdue', 0)}逾期 ｜ "
            f"{s.get('overall_progress', '')}"
        )
        segs = s.get("node_segments") or {}
        if segs and auth:
            visible = [(nid, seg) for nid, seg in segs.items() if nid in auth]
            if visible:
                names = [
                    f"{seg.get('name', '')}"
                    f"[{_STATUS_CN.get(seg.get('status', ''), seg.get('status', ''))}"
                    f"{'·未签认' if not seg.get('acknowledged', False) else ''}]"
                    for _, seg in visible[:3]
                ]
                lines.append(f"🔖 我可见节点（{len(visible)}）：" + " / ".join(names))

    # 逾期：仅展示"可见节点"内的条目（无 node_id 的历史数据一律不展示）
    oi = [i for i in ((data.get("temporal") or {}).get("overdue_items") or []) if _visible(i, auth)]
    if oi:
        lines.append("🔴 逾期：" + " / ".join(str(i.get("name", "")) for i in oi[:3]))

    return _truncate("\n".join(lines), max_chars)


def render_full(content_json: str, authorized_node_ids: list[str],
                include_events: bool = False) -> str:
    """生成裁剪后全文（供 meta_cognition_read）。节点级段落一律按可见节点裁剪。

    include_events=True 时才输出【近期事件】——事件表无节点关联，无法安全裁剪，
    故仅对管理单位开放（非管理单位不展示该段）。
    """
    data = _load(content_json)
    if not data:
        return ""
    auth = set(authorized_node_ids or [])
    lines: list[str] = []

    brief = render_brief(content_json, authorized_node_ids, max_chars=100000,
                         include_events=include_events)
    if brief:
        lines.append(brief)

    segs = ((data.get("structure") or {}).get("node_segments")) or {}
    if segs and auth:
        rows = []
        for nid, seg in segs.items():
            if nid not in auth:
                continue
            rows.append(
                f"{nid} {seg.get('name', '')}"
                f"[{_STATUS_CN.get(seg.get('status', ''), seg.get('status', ''))}]"
                f" 进度{seg.get('progress', 0)}%"
                + ("（里程碑）" if seg.get("milestone") else "")
                + ("（未签认）" if not seg.get("acknowledged", False) else "")
            )
        if rows:
            lines.append("")
            lines.append("【节点明细】")
            lines.extend(rows)

    t = data.get("temporal") or {}
    for label, key in (("近期事件", "recent_events"), ("7天内到期", "upcoming_deadlines")):
        items = t.get(key) or []
        if key == "recent_events":
            if not include_events:
                continue
            picked = items[:10]
        else:
            picked = [i for i in items if _visible(i, auth)][:10]
        if picked:
            lines.append("")
            lines.append(f"【{label}】")
            for it in picked:
                lines.append(f"· {it.get('date', it.get('deadline', ''))} {it.get('name', it.get('summary', ''))}")

    r = data.get("relation") or {}
    rows: list[str] = []
    for b in (r.get("blocked_nodes") or []):
        # 下游节点必须可见；上游节点名仅在"上游也可见"时展示，否则脱敏为"前置节点未完成"
        if not _visible(b, auth):
            continue
        up_id = str(b.get("blocked_by_node_id", "") or "")
        up_visible = bool(up_id) and up_id in auth
        shown = str(b.get("blocked_by", "") or "") if up_visible else "前置节点未完成"
        rows.append(f"· {shown} → {b.get('node', '')}等待")
        if len(rows) >= 10:
            break
    if rows:
        lines.append("")
        lines.append("【阻塞】")
        lines.extend(rows)

    return "\n".join(lines)


def main() -> None:
    """独立运行入口：python -m emily_core.session.fetchers.fetch_world_book --project-id <UUID> --node-ids SG-001,SG-002"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="世界书裁剪预览")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--node-ids", default="", help="逗号分隔的授权节点编号")
    parser.add_argument("--full", action="store_true", help="输出全文而非摘要")
    parser.add_argument("--db-url", default="")
    args = parser.parse_args()

    from emily_core.infrastructure.database import init_db
    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo
    init_db(db_url=args.db_url) if args.db_url else init_db()

    wb = ProjectWorldBookRepo.get_by_project(args.project_id)
    if wb is None:
        print("（无世界书记录）")
        return
    node_ids = [x.strip() for x in args.node_ids.split(",") if x.strip()]
    out = render_full(wb.content_json, node_ids) if args.full else render_brief(wb.content_json, node_ids)
    print(out)


if __name__ == "__main__":
    main()
