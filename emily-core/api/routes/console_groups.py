"""emy-console 群追踪视图 —— 只读取数路由（群信息保存与复用 M7）。

定位：**观察窗口**。全部取数复用能力层（`GroupQueryService` / `GroupRosterService`），
本文件只做参数校验 → 调服务 → 包装统一响应；**不含**任何业务查询构造、检索条件拼装
或绕过服务层的库直查（宪法 C13 / PRD AC-US-13.3）。

写能力：本文件**没有任何写路由**。
  · 群备注维护 → 既有脚本通道 `POST /api/v1/scripts/run/set_group_note`（前端直接调用）
  · 名册刷新   → 复用 M4 同一服务方法（与 `refresh_group_roster.py` 同源同能力）

作用域：档位由**所选操作人的等级**推定（≥L4 为观察档，可见全部群消息）；与对话侧
共用同一实现，不存在第二套过滤（AC-US-8.1 / AC-US-13.2）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger("emily.api.console_groups")

router = APIRouter(prefix="/console", tags=["console-groups"])


def _err(message: str, code: int = 1) -> dict:
    return {"code": code, "message": message, "data": None}


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


def _svc():
    """取能力层服务（未就绪返回 None，由调用方如实回报）。"""
    from api.server import get_core

    core = get_core()
    return getattr(core, "_group_query_service", None), getattr(core, "_group_roster_service", None)


def _level_of(svc, operator_id: str) -> int:
    """档位来源：所选操作人的等级（取不到一律 0 → 落入发起者档，fail-closed）。"""
    if svc is None:
        return 0
    return svc.resolve_actor_level(operator_id)


# ── 上栏：在追踪群列表 ──

@router.get("/group-track/groups")
async def group_track_groups(
    operator_id: str = Query("", description="操作人 UUID（决定可见档位）"),
    keyword: str = Query("", description="按备注名 / 群名 / 群号筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    order_by: str = Query("last_active", description="last_active / name / message_count"),
    refresh_stale: bool = Query(True, description="是否对过期群先刷新再呈现（人在场路径）"),
):
    """在追踪群列表（含备注名 / 平台 / 成员数 / 已留存消息数 / 最近活跃 / 过期标识）。"""
    import asyncio

    svc, roster = _svc()
    if svc is None:
        return _err("群信息服务未启用")

    refresh_errors: list[dict] = []
    if refresh_stale and roster is not None:
        # 人在场路径：打开视图时先刷新过期群，再呈现（AC-US-04.2）；失败不阻断呈现
        try:
            stale = await asyncio.to_thread(roster.scan_stale, operator_id=operator_id, limit=20)
        except Exception as ex:  # noqa: BLE001
            logger.warning("scan_stale failed: %s", ex)
            stale = []
        for row in stale[:5]:  # 单次打开最多刷 5 个，避免长时间等待
            try:
                out = await roster.refresh(group_ref=row["group_id"], operator_id=operator_id)
                if not out.get("ok"):
                    refresh_errors.append({"group_id": row["group_id"],
                                           "error": out.get("error") or "刷新失败"})
            except Exception as ex:  # noqa: BLE001
                refresh_errors.append({"group_id": row["group_id"], "error": str(ex)})

    try:
        data = await asyncio.to_thread(
            svc.list_tracked_groups, keyword=keyword, page=page,
            page_size=page_size, order_by=order_by)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.groups failed")
        return _err(f"群列表读取失败：{ex}")

    data["refresh_errors"] = refresh_errors
    data["actor_level"] = _level_of(svc, operator_id)
    return _ok(data)


# ── 下栏：选定群的通讯内容 ──

@router.get("/group-track/messages")
async def group_track_messages(
    group: str = Query(..., description="群：备注名 / 群名 / 群号 任一表达"),
    operator_id: str = Query("", description="操作人 UUID（决定可见档位）"),
    limit: int = Query(50, ge=1, le=50),
    before_id: str = Query("", description="回溯游标（上一页最早一条的 id）"),
    include_sources: bool = Query(False, description="是否包含测试 / 运维来源"),
):
    """选定群的通讯内容（分页回溯，时间倒序；前端按时间正序展示）。"""
    import asyncio

    svc, _ = _svc()
    if svc is None:
        return _err("群信息服务未启用")
    try:
        data = await asyncio.to_thread(
            svc.list_group_messages,
            actor_user_id=operator_id, level=_level_of(svc, operator_id),
            group_ref=group, limit=limit, before_id=before_id,
            include_sources=include_sources)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.messages failed group=%s", group)
        return _err(f"群消息读取失败：{ex}")
    if not data.get("ok"):
        return _err(data.get("error") or "群未找到")
    return _ok(data)


@router.get("/group-track/search")
async def group_track_search(
    keyword: str = Query(..., description="检索关键词（子串匹配）"),
    group: str = Query("", description="留空 = 跨全部在追踪群；否则限该群"),
    operator_id: str = Query("", description="操作人 UUID（决定可见档位）"),
    time_range: str = Query("all", description="today / week / month / all"),
    limit: int = Query(20, ge=1, le=50),
    include_sources: bool = Query(False, description="是否包含测试 / 运维来源"),
):
    """跨群 / 群内检索（两入口共用同一取数出口，口径一致）。"""
    import asyncio

    svc, _ = _svc()
    if svc is None:
        return _err("群信息服务未启用")
    if not keyword.strip():
        return _err("请提供检索关键词")
    try:
        data = await asyncio.to_thread(
            svc.search_group_messages,
            actor_user_id=operator_id, level=_level_of(svc, operator_id),
            keyword=keyword, group_ref=group, time_range=time_range,
            limit=limit, include_sources=include_sources)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.search failed")
        return _err(f"群消息检索失败：{ex}")
    if not data.get("ok"):
        return _err(data.get("error") or "群未找到")
    return _ok(data)


# ── 名册区 ──

@router.get("/group-track/roster")
async def group_track_roster(
    group: str = Query(..., description="群：备注名 / 群名 / 群号 任一表达"),
    operator_id: str = Query("", description="操作人 UUID（留痕）"),
    include_inactive: bool = Query(True, description="是否包含已退群成员"),
    limit: int = Query(200, ge=1, le=200),
    offset: int = Query(0, ge=0),
    refresh_stale: bool = Query(True, description="名册过期时先刷新再呈现（人在场路径）"),
):
    """名册区：按需先刷新鲜呈现；刷新失败如实提示并显示现有数据（不白屏）。"""
    import asyncio

    svc, roster = _svc()
    if svc is None:
        return _err("群信息服务未启用")

    refreshed = False
    stale = False
    refresh_error = ""
    if refresh_stale and roster is not None:
        try:
            out = await roster.ensure_fresh(group_ref=group, operator_id=operator_id)
            refreshed = bool(out.get("refreshed"))
            stale = bool(out.get("stale"))
            if not out.get("ok"):
                refresh_error = out.get("error") or "名册刷新失败"
        except Exception as ex:  # noqa: BLE001
            refresh_error = f"名册刷新异常：{ex}"

    try:
        data = await asyncio.to_thread(
            svc.list_group_roster, group_ref=group,
            include_inactive=include_inactive, limit=limit, offset=offset)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.roster failed group=%s", group)
        return _err(f"名册读取失败：{ex}")
    if not data.get("ok"):
        return _err(data.get("error") or "群未找到")

    data["refreshed"] = refreshed
    data["stale"] = stale
    data["refresh_error"] = refresh_error
    return _ok(data)


class RosterRefreshRequest(BaseModel):
    group: str
    operator_id: str = ""


@router.post("/group-track/roster-refresh")
async def group_track_roster_refresh(req: RosterRefreshRequest):
    """运维手动刷新某群名册（AC-US-04.5 / 15.5）。

    复用 M4 的同一服务方法（与 `refresh_group_roster.py` 同源），不在路由层写库。
    """
    _svc_q, roster = _svc()
    if roster is None:
        return _err("名册服务未启用")
    if not req.operator_id:
        return _err("请先选择操作人")
    try:
        out = await roster.refresh(group_ref=req.group, operator_id=req.operator_id)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.roster_refresh failed")
        return _err(f"名册刷新失败：{ex}")
    if not out.get("ok"):
        return _err(out.get("error") or "名册刷新失败")
    return _ok(out)


@router.get("/group-track/stale-groups")
async def group_track_stale_groups(
    operator_id: str = Query("", description="操作人 UUID（巡检留痕）"),
    limit: int = Query(200, ge=1, le=2000),
):
    """名册过期群清单（运维巡检）。"""
    import asyncio

    _svc_q, roster = _svc()
    if roster is None:
        return _err("名册服务未启用")
    try:
        rows = await asyncio.to_thread(roster.scan_stale, operator_id=operator_id, limit=limit)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.stale_groups failed")
        return _err(f"过期巡检失败：{ex}")
    return _ok({"rows": rows, "total": len(rows), "ttl_days": roster.ttl_days})


# ── 附件打开（复用既有文件能力，不新建附件服务）──

@router.get("/group-track/attachment/{attachment_id}")
async def group_track_attachment(attachment_id: str):
    """打开群消息附件。

    三种情形（见设计 M7）：① 已锚定文件台账 → 复用 `FileManager`；
    ② 仅有本地相对路径 → 在存储根下解析并做目录穿越防护；③ 两者皆无 → 404。
    """
    from fastapi.responses import FileResponse

    from emily_core.repositories.chat_archive_repo import ChatArchiveRepository

    try:
        att = ChatArchiveRepository.get_attachment_by_id(attachment_id)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.group_track.attachment lookup failed")
        return _err(f"附件读取失败：{ex}")

    if att is None:
        return _err("附件不存在")

    # ① 锚定文件台账 → 复用既有文件能力（FileRepository 定位 + FileManager 解析路径）
    if att.get("file_id"):
        try:
            from api.server import get_core

            from emily_core.repositories.file_repo import FileRepository

            fm = getattr(get_core(), "_file_manager", None)
            record = FileRepository.get_by_id(att["file_id"])
            if fm is not None and record is not None and not getattr(record, "is_deleted", False):
                local = fm.resolve_local_path(record.file_no)
                if local and Path(local).is_file():
                    return FileResponse(local, filename=record.filename or record.file_no)
        except Exception as ex:  # noqa: BLE001
            logger.warning("attachment via file ledger failed: %s", ex)

    # ② 仅有本地相对路径 → 存储根下解析 + 目录穿越防护
    local_rel = str(att.get("local_path") or "").strip()
    if local_rel:
        resolved = _resolve_under_storage(local_rel)
        if resolved is not None:
            return FileResponse(str(resolved), filename=resolved.name)

    return _err("附件文件不存在或不可读取")


def _resolve_under_storage(relative: str) -> Path | None:
    """在存储根下解析相对路径；越界或不存在返回 None（目录穿越防护）。"""
    try:
        from api.server import get_core

        fm = getattr(get_core(), "_file_manager", None)
        root = getattr(getattr(fm, "storage", None), "_storage_root", None)
        if root is None:
            from emily_core.services.file_storage_service import FileStorageService

            root = FileStorageService()._storage_root
        base = Path(str(root)).resolve()
        candidate = (base / relative.lstrip("/\\")).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            return None
        return candidate
    except Exception as ex:  # noqa: BLE001
        logger.debug("resolve attachment failed: %s", ex)
        return None
