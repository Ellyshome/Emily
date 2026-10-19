"""node_query_tool —— 按项目/条线/状态查询全景节点（只读，L5+）。

供 HTML 演示场景「哪些节点已启动、哪些已超时」使用。
与 `query_node` 的区别：`query_node` 只接受单个 node_id；本工具支持项目/单位/状态/关键词/超时筛选。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("emily.tool.node_query")

_MIN_LEVEL = 5

_STATUS_LABELS = {
    "CONDITIONS_NOT_MET": "未启动",
    "IN_PROGRESS": "进行中",
    "COMPLETED": "已完成",
    "DISABLED": "已停用",
}
_STATUS_ALIASES = {
    "未启动": "CONDITIONS_NOT_MET",
    "进行中": "IN_PROGRESS",
    "已启动": "IN_PROGRESS",
    "已完成": "COMPLETED",
    "已停用": "DISABLED",
}

_QUERY_NODES_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "项目 UUID（可选）"},
        "project_name": {"type": "string", "description": "项目名称（可选，模糊匹配）"},
        "company_id": {"type": "string", "description": "主责单位 UUID（可选）"},
        "company_name": {"type": "string", "description": "主责单位名称（可选，模糊匹配）"},
        "status": {
            "type": "string",
            "description": "节点状态：未启动/进行中/已完成/已停用（也接受 CONDITIONS_NOT_MET/IN_PROGRESS/COMPLETED/DISABLED）",
        },
        "keyword": {"type": "string", "description": "节点名称/编号关键词，如“景观”"},
        "overdue_only": {"type": "boolean", "description": "只看已超时节点（默认 false）"},
        "include_completed": {"type": "boolean", "description": "是否包含已完成节点（默认 false）"},
        "limit": {"type": "integer", "description": "返回上限，默认 100，最大 200"},
    },
    "required": [],
}

_QUERY_NODES_DESCRIPTION = (
    "按项目/主责单位/状态/关键词查询全景节点，并计算是否超时、超时天数；"
    "返回节点状态、截止时间、主责单位、成果齐备情况。"
    "用于回答“某项目/某条线哪些节点已启动、哪些已超时”。"
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _status(value: str | None) -> str:
    v = (value or "").strip()
    return _STATUS_ALIASES.get(v, v)


async def handle_query_nodes(params: dict, user_id: str = "", **kwargs) -> dict:
    """查询节点状态（只读）。"""
    from .file_tool import check_min_level

    denied = check_min_level(user_id, _MIN_LEVEL, "节点状态查询")
    if denied is not None:
        return denied

    from ..repositories.company_repo import CompanyRepository
    from ..repositories.node_repo import NodeDeliverableRepo, ProjectNodeRepo

    params = params or {}
    now = datetime.now(timezone.utc)
    overdue_only = bool(params.get("overdue_only", False))
    include_completed = bool(params.get("include_completed", False))
    limit = int(params.get("limit", 100) or 100)

    try:
        nodes = await asyncio.to_thread(
            ProjectNodeRepo.query_nodes,
            project_id=(params.get("project_id") or "").strip() or None,
            project_name=(params.get("project_name") or "").strip() or None,
            company_id=(params.get("company_id") or "").strip() or None,
            company_name=(params.get("company_name") or "").strip() or None,
            status=_status(params.get("status")) or None,
            keyword=(params.get("keyword") or "").strip() or None,
            deadline_before=now.isoformat() if overdue_only else None,
            include_completed=include_completed,
            limit=limit,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("query_nodes failed: %s", e, exc_info=True)
        return {"success": False, "error": f"节点查询失败：{e}", "error_code": "query_failed"}

    if not nodes:
        return {"success": True, "count": 0, "nodes": [],
                "reply": "没有找到符合条件的节点。"}

    node_ids = [n.node_id for n in nodes]
    deliverable_map = await asyncio.to_thread(NodeDeliverableRepo.find_by_nodes, node_ids)

    company_ids = list({n.related_company_id for n in nodes if n.related_company_id})
    company_map: dict[str, str] = {}
    if company_ids:
        try:
            rows = await asyncio.to_thread(CompanyRepository.find_for_matching, company_ids)
            company_map = {r["company_id"]: r["company_name"] for r in rows}
        except Exception as e:  # noqa: BLE001
            logger.warning("query_nodes company lookup failed: %s", e)

    result: list[dict] = []
    overdue_count = 0
    for n in nodes:
        deadline_dt = _parse_dt(n.deadline)
        is_overdue = bool(
            deadline_dt and n.status != "COMPLETED" and now > deadline_dt
        )
        overdue_days = 0
        if is_overdue:
            overdue_days = max(0, (now.date() - deadline_dt.date()).days)
        if overdue_only and not is_overdue:
            continue
        if is_overdue:
            overdue_count += 1

        deliverables = []
        for d in deliverable_map.get(n.node_id, []):
            deliverables.append({
                "deliverable_id": d.deliverable_id,
                "name": d.deliverable_name,
                "target_amount": d.target_amount,
                "current_amount": d.current_amount,
                "unit": d.unit,
                "is_required": bool(d.is_required),
                "submission_status": d.submission_status,
            })

        result.append({
            "node_id": n.node_id,
            "node_name": n.node_name,
            "project_id": n.project_id,
            "status": n.status,
            "status_label": _STATUS_LABELS.get(n.status, n.status),
            "deadline": n.deadline,
            "overdue": is_overdue,
            "overdue_days": overdue_days,
            "responsible_user_id": n.responsible_user_id,
            "company_id": n.related_company_id,
            "company_name": company_map.get(n.related_company_id, "") or "—",
            "node_type": n.node_type,
            "deliverables": deliverables,
        })

    # 超时优先，其次按截止时间
    result.sort(key=lambda x: (not x["overdue"], x.get("deadline") or ""))
    result = result[:max(1, min(limit, 200))]

    lines = [f"共 {len(result)} 个节点，其中 {overdue_count} 个已超时。"]
    for x in result[:20]:
        flag = f"已超时 {x['overdue_days']} 天" if x["overdue"] else x["status_label"]
        lines.append(f"- {x['node_id']}｜{x['node_name']}｜{flag}｜截止 {x['deadline'] or '—'}")
    if len(result) > 20:
        lines.append(f"……另有 {len(result) - 20} 个节点未列出。")

    return {
        "success": True,
        "count": len(result),
        "overdue_count": overdue_count,
        "nodes": result,
        "reply": "\n".join(lines),
    }
