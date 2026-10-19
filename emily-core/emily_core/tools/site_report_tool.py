"""site_report_tool —— 一线完工上报（记录事件 + 节点归属 + 临时收容）。

演示场景：
  张师傅：“铺装今天干了10平米” + 2 张照片
  → 归属判定：命中已有任务（档 A）→ 记入该节点；
     未命中（档 B/C）→ 记入里程碑或“待归类事件”收容节点。
照片已由消息附件链路落盘；本工具只负责事件归属与记录，不重复下载。

权限：L2+（上报）；临时收容容器由系统内部创建，不要求上报名用户具备节点创建权限。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("emily.tool.site_report")

_MIN_LEVEL = 2

_SITE_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "项目 UUID（可选，优先用 project_name 解析）"},
        "project_name": {"type": "string", "description": "项目名称（可选，模糊匹配）"},
        "location": {"type": "string", "description": "施工部位/工序，例如“2#、4#楼间道路花岗岩铺装”"},
        "quantity": {"type": "number", "description": "完成工程量"},
        "unit": {"type": "string", "description": "单位，例如 ㎡ / m / 株 / 处"},
        "report_text": {"type": "string", "description": "用户上报原文"},
        "photo_file_nos": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选，已落盘照片的 file_no 列表（用于写入事件描述）",
        },
        "dry_run": {
            "type": "boolean",
            "description": "true=只返回归属建议，不写事件；false=实际记录（默认 false）",
        },
    },
    "required": ["location", "report_text"],
}

_SITE_REPORT_DESCRIPTION = (
    "一线完工上报：解析部位/工程量/照片，按全景节点归属判定（档 A/B/C）记录事件；"
    "命中已有任务则记入该节点，未命中则记入里程碑或“待归类事件”收容节点。"
    "先可用 dry_run=true 返回归属建议，用户确认后再 dry_run=false 落库。"
)


async def handle_site_report(
    params: dict,
    event_app=None,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """记录一线完工上报并完成节点归属。"""
    from .file_tool import check_min_level

    denied = check_min_level(user_id, _MIN_LEVEL, "完工上报")
    if denied is not None:
        return denied
    if event_app is None:
        return {"success": False, "error": "事件服务未就绪", "error_code": "service_unavailable"}

    params = params or {}
    location = str(params.get("location") or "").strip()
    report_text = str(params.get("report_text") or "").strip()
    if not location or not report_text:
        return {"success": False, "error": "缺少施工部位或上报内容",
                "error_code": "missing_fields"}

    project_id = str(params.get("project_id") or "").strip()
    project_name = str(params.get("project_name") or "").strip()
    if not project_id and project_name:
        project_id = await asyncio.to_thread(_resolve_project_id, project_name)
    if not project_id:
        return {"success": False, "error": "无法确定项目，请提供 project_id 或 project_name",
                "error_code": "missing_project"}

    dry_run = bool(params.get("dry_run", False))
    quantity = params.get("quantity")
    unit = str(params.get("unit") or "").strip()
    raw_photos = params.get("photo_file_nos") or []
    if isinstance(raw_photos, str):
        raw_photos = [raw_photos]
    photo_nos = [str(x).strip() for x in raw_photos if str(x).strip()]

    # ── 归属判定（只读）──
    attribution = await asyncio.to_thread(_judge, project_id, location, report_text, user_id)
    tier = attribution.tier
    target_node_id = attribution.target_node_id
    target_node_name = attribution.target_node_name
    milestone_id = attribution.milestone_id
    milestone_name = attribution.milestone_name
    deliverable = None

    if tier == "A" and target_node_id:
        deliverable = await asyncio.to_thread(
            _match_deliverable, project_id, target_node_id, f"{location} {report_text}",
        )
    elif tier == "B" and milestone_id:
        target_node_id = milestone_id
        target_node_name = milestone_name
    else:
        # 档 C / 无归属：用“待归类事件”收容节点；dry_run 时不创建容器
        target_node_id = "" if dry_run else await asyncio.to_thread(
            _ensure_sink, project_id, user_id,
        )
        target_node_name = "待归类事件（收容节点）"

    tier_labels = {"A": "档A 命中已有任务", "B": "档B 有里程碑级归属",
                   "C": "档C 无里程碑级归属"}
    base = {
        "success": True,
        "tier": tier,
        "tier_label": tier_labels.get(tier, tier),
        "target_node_id": target_node_id,
        "target_node_name": target_node_name,
        "deliverable_id": (deliverable or {}).get("deliverable_id", ""),
        "deliverable_name": (deliverable or {}).get("deliverable_name", ""),
        "reason": attribution.reason,
        "dry_run": dry_run,
    }

    if dry_run:
        base["reply"] = _proposal_reply(base, location, quantity, unit)
        return base

    # ── 记录事件 ──
    description = report_text
    if quantity not in (None, ""):
        description += f"\n工程量：{quantity}{unit}"
    if photo_nos:
        description += f"\n照片：{', '.join(photo_nos)}"

    event_params = {
        "project_id": project_id,
        "data": {
            "title": location[:10] or "完工上报",
            "event_type": "construction_progress",
            "event_date": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
            "description": description,
            "node_id": target_node_id,
        },
    }
    from .event_tool import handle_record_event

    result = await handle_record_event(
        event_params, event_app=event_app, user_id=user_id, message_id=message_id,
    )
    if not result.get("success"):
        return {
            **base,
            "success": False,
            "error": result.get("reply") or "事件记录失败",
            "event_result": result,
        }

    # 上报走的是“先提议、后确认”的交互；dry_run=false 说明用户已确认，
    # 这里直接确认 pending 事件，避免再走一次 SYS-confirm。
    event_id = result.get("object_id", "")
    if result.get("pending_confirmation") and event_id:
        try:
            conf = await asyncio.to_thread(
                event_app.handle_confirmation, event_id, "confirm", user_id,
            )
            if not getattr(conf, "success", False):
                return {
                    **base,
                    "success": False,
                    "error": getattr(conf, "reply", "") or "事件确认失败",
                    "event_id": event_id,
                }
        except Exception as e:  # noqa: BLE001
            return {**base, "success": False, "error": f"事件确认失败：{e}",
                    "event_id": event_id}

    base.update({
        "event_id": event_id,
        "event_reply": result.get("reply", ""),
        "reply": _recorded_reply(base, location, quantity, unit, photo_nos),
    })
    return base


def _resolve_project_id(project_name: str) -> str:
    from ..repositories.project_repo import ProjectRepository

    rows = ProjectRepository.find_by_name_fuzzy(project_name, limit=5)
    if not rows:
        return ""
    if len(rows) > 1:
        exact = [r for r in rows if (r.name or "") == project_name]
        if exact:
            return exact[0].id
    return rows[0].id


def _judge(project_id: str, location: str, report_text: str, user_id: str):
    from ..services.node_attribution_service import NodeAttributionService

    svc = NodeAttributionService()
    return svc.judge(
        project_id=project_id,
        payload={"title": location, "content_summary": report_text},
        actor_id=user_id,
    )


def _match_deliverable(project_id: str, node_id: str, text: str):
    from ..repositories.node_repo import NodeDeliverableRepo
    from ..services.node_attribution_service import _name_variants, _squash

    haystack = _squash(text)
    pool = NodeDeliverableRepo.find_by_project(project_id)
    best = None
    best_len = 0
    for item in pool:
        if item.get("node_id") != node_id:
            continue
        name = _squash(item.get("deliverable_name", ""))
        if len(name) < 4:
            continue
        if not any(v in haystack for v in _name_variants(name)):
            continue
        if len(name) > best_len:
            best, best_len = item, len(name)
    return best


def _ensure_sink(project_id: str, user_id: str) -> str:
    from ..services.node_container_service import NodeContainerService
    from ..services.node_state_machine import NODE_ROLE_SINK

    svc = NodeContainerService()
    return svc.ensure_container_sync(project_id, NODE_ROLE_SINK, operator_id=user_id)


def _proposal_reply(base: dict, location: str, quantity, unit: str) -> str:
    qty = f"{quantity}{unit}" if quantity not in (None, "") else "工程量未填"
    if base["tier"] == "A":
        return (f"将记入已有任务「{base['target_node_name']}」（{base['target_node_id']}）："
                f"{location}，{qty}；成果：{base['deliverable_name'] or '未匹配到具体成果'}。"
                f"请确认后我再提交。")
    if base["tier"] == "B":
        return (f"未命中已有任务，将记入里程碑「{base['target_node_name']}」："
                f"{location}，{qty}。请确认后我再提交。")
    return (f"未命中已有任务，将暂存到「待归类事件（收容节点）」：{location}，{qty}。"
            f"请确认后我再提交。")


def _recorded_reply(base: dict, location: str, quantity, unit: str, photos: list[str]) -> str:
    qty = f"{quantity}{unit}" if quantity not in (None, "") else "工程量未填"
    photo_text = f"照片 {len(photos)} 张" if photos else "未附照片"
    if base["tier"] == "A":
        target = f"「{base['target_node_name']}」"
    elif base["tier"] == "B":
        target = f"里程碑「{base['target_node_name']}」"
    else:
        target = "「待归类事件（收容节点）」"
    return (f"已登记：{location}，{qty}，{photo_text}；已归档到{target}。"
            f"请及时联系相关负责人验收。")
