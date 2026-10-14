"""群台账相关端点。

  · `POST /api/v1/groups/sync`           —— 插件推送群列表（既有）
  · `POST /api/v1/groups/pull`           —— 内核主动拉取群清单（群信息保存与复用 M3）
  · `POST /api/v1/groups/roster-refresh` —— 名册刷新（群信息保存与复用 M4）

后两者必须落在 **HTTP 进程内**：平台动作经 SSE 出站总线请求薄插件执行，而 SSE 订阅者
（AstrBot 插件）连的是本进程——脚本子进程内没有订阅者，无法自行拉取平台数据。
故维护脚本按"薄 CLI → 调本端点"的方式接线（脚本与控制台同源同能力）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ..server import get_core

logger = logging.getLogger("emily.api.groups")

router = APIRouter()


class GroupItem(BaseModel):
    group_id: str
    group_name: str = ""
    member_count: int = 0
    platform: str = ""


class GroupsSyncIn(BaseModel):
    groups: list[GroupItem]


class GroupPullIn(BaseModel):
    platform: str = "napcat"
    timeout: float = 8.0
    dry_run: bool = False


class RosterRefreshIn(BaseModel):
    group: str = ""
    operator_id: str = ""
    timeout: float = 8.0
    dry_run: bool = False
    limit: int = 0


@router.post("/groups/sync")
async def sync_groups(payload: GroupsSyncIn):
    """同步 bot 加入的群列表到 core。"""
    core = get_core()
    count = core._group_registry_service.upsert_groups(
        [{"group_id": g.group_id, "group_name": g.group_name,
          "member_count": g.member_count, "platform": g.platform}
         for g in payload.groups]
    )
    return {"synced": count}


@router.post("/groups/pull")
async def pull_groups(payload: GroupPullIn | None = None):
    """内核主动拉取 bot 所在全部群并写入群台账（幂等）。

    手动触发入口（AC-US-02.1 / 02.5）：控制台脚本面板、CLI 脚本、外部调度均调用本端点。
    `dry_run=true` 时只拉取并返回清单，不写库（预览）。
    """
    core = get_core()
    svc = getattr(core, "_group_sync_service", None)
    if svc is None:
        return {"ok": False, "pulled": 0, "upserted": 0, "groups": [],
                "error": "群同步服务未启用"}

    req = payload or GroupPullIn()
    if req.dry_run:
        out = await _pull_preview(core, req.platform, req.timeout)
        return {"ok": out["ok"], "pulled": out["pulled"], "upserted": 0,
                "groups": out["groups"], "error": out["error"],
                "dry_run": True}

    out = await svc.sync_from_platform(platform=req.platform, timeout=req.timeout)
    return {"ok": out["ok"], "pulled": out["pulled"], "upserted": out["upserted"],
            "groups": [], "error": out["error"], "dry_run": False}


async def _pull_preview(core, platform: str, timeout: float) -> dict:
    """预览：只拉取平台群清单（只读），不入库。"""
    svc = getattr(core, "_platform_action_service", None)
    if svc is None:
        return {"ok": False, "pulled": 0, "groups": [], "error": "平台动作服务未启用"}
    result = await svc.request("get_group_list", {}, platform=platform, timeout=timeout)
    if not result.get("ok"):
        return {"ok": False, "pulled": 0, "groups": [],
                "error": result.get("error") or "拉取群清单失败"}
    rows = result.get("data") if isinstance(result.get("data"), list) else []
    groups = [
        {"group_id": str(g.get("group_id", "")),
         "group_name": g.get("group_name", "") or "",
         "member_count": int(g.get("member_count") or 0)}
        for g in rows if isinstance(g, dict) and str(g.get("group_id", ""))
    ]
    return {"ok": True, "pulled": len(groups), "groups": groups, "error": ""}


@router.post("/groups/roster-refresh")
async def refresh_group_roster(payload: RosterRefreshIn):
    """刷新某群（或前 N 个过期群）的成员名册。

    人在场路径的入口之一（AC-US-04.2 / US-18.1）：`group` 留空时按 `limit` 扫描过期群
    逐个刷新。刷新失败时如实回报且**不写**名册同步时间。
    """
    core = get_core()
    svc = getattr(core, "_group_roster_service", None)
    if svc is None:
        return {"ok": False, "results": [], "ok_count": 0, "fail_count": 0,
                "error": "名册服务未启用"}

    if payload.dry_run:
        return await _roster_preview(core, svc, payload)

    if payload.group.strip():
        out = await svc.refresh(group_ref=payload.group, operator_id=payload.operator_id,
                                timeout=payload.timeout)
        return {"ok": out["ok"], "results": [out],
                "ok_count": 1 if out["ok"] else 0,
                "fail_count": 0 if out["ok"] else 1,
                "error": out.get("error") or ""}

    # 未指定群：按过期清单逐群刷新（供外部调度/巡检使用）
    stale = svc.scan_stale(operator_id=payload.operator_id,
                           limit=int(payload.limit or 50))
    results = []
    for row in stale:
        out = await svc.refresh(group_ref=row["group_id"],
                                operator_id=payload.operator_id,
                                timeout=payload.timeout)
        results.append(out)
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_count == len(results), "results": results,
            "ok_count": ok_count, "fail_count": len(results) - ok_count,
            "error": ""}


async def _roster_preview(core, svc, payload: RosterRefreshIn) -> dict:
    """预览：只拉取成员名册（只读），不入库。"""
    from emily_core.services.group_query_service import resolve_group_ref

    pa = getattr(core, "_platform_action_service", None)
    if pa is None:
        return {"ok": False, "results": [], "ok_count": 0, "fail_count": 0,
                "error": "平台动作服务未启用", "dry_run": True}

    targets: list[dict] = []
    if payload.group.strip():
        resolved = resolve_group_ref(payload.group)
        if resolved.get("status") != "ok":
            return {"ok": False, "results": [], "ok_count": 0, "fail_count": 0,
                    "error": resolved.get("error") or "群未找到", "dry_run": True}
        targets = [resolved["group"]]
    else:
        targets = svc.scan_stale(operator_id=payload.operator_id,
                                 limit=int(payload.limit or 50))

    results = []
    for g in targets:
        if not g.get("group_id"):
            continue
        result = await pa.request("get_group_member_list", {"group_id": g["group_id"]},
                                  platform=g.get("im_platform") or "",
                                  timeout=payload.timeout)
        members = result.get("data") if isinstance(result.get("data"), list) else []
        results.append({
            "ok": bool(result.get("ok")),
            "group": g,
            "refreshed": False,
            "counts": {"active_total": len(members)} if result.get("ok") else {},
            "error": result.get("error") or "",
            "error_code": "",
        })
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": ok_count == len(results) and bool(results), "results": results,
            "ok_count": ok_count, "fail_count": len(results) - ok_count,
            "error": "", "dry_run": True}
