"""GroupQueryService —— 群信息的**取数唯一出口** + 检索作用域收敛唯一实现。

三种读取（在追踪群列表 / 群消息列表 / 跨群检索）只有本模块一处实现，
对话侧工具（`tools/group_tool.py`）与控制台路由（`api/routes/console_groups.py`）
共同调用（AC-US-17.1 / §4.4-7）；作用域收敛也只有 `resolve_visible_group_ids`
一处实现（AC-US-08.6 / §4.4-9）。

作用域口径（PRD V3 AC-US-08.1，经拍板）：
  · 等级 ≥ 建设主管（L4）→ **观察档**，不受参与关系限制（全部在追踪群的群消息）；
  · 等级 < L4 → 该发起者在成员名册中**当前在册**（`is_active`）的群；
  · **fail-closed**：身份为空 / 在册集合为空 / 等级解析失败 → 空集，绝不放宽
    （§4.4-8 / AC-US-08.2）。

档位由**身份推定**，不由调用方任选：调用方无法自行声称自己是观察档
（AC-US-13.2——控制台与对话侧因此只能得出同一结论）。
"""

from __future__ import annotations

import logging

from ..infrastructure.database.models import Conversation, Message
from ..infrastructure.database.session import get_session
from ..repositories.chat_archive_repo import ChatArchiveRepository
from ..repositories.group_member_repo import GroupMemberRepository
from ..repositories.message_repo import MessageRepository

logger = logging.getLogger("emily.services.group_query")

#: 观察档门槛：建设主管（L4）及以上不受参与关系限制
OBSERVER_MIN_LEVEL = 4

#: 等级解析失败时的回退值（fail-closed：落入发起者档）
LEVEL_FALLBACK = 0

#: 默认排除的来源（测试 / 运维流量不应污染结论；R13 / AC-US-05.4）
DEFAULT_EXCLUDED_SOURCES: tuple[str, ...] = ("test", "ops")

#: 结果条数上限（调用方传入超限时钳制）
MAX_MESSAGE_LIMIT = 50
MAX_PAGE_SIZE = 200

#: 群标识歧义时返回的候选上限
MAX_CANDIDATES = 10


class GroupScope:
    """作用域结论。`allows()` 是各消息读取路径的**唯一放行判据**。"""

    __slots__ = ("all_groups", "group_ids", "reason", "actor_level")

    def __init__(self, *, all_groups: bool, group_ids, reason: str = "", actor_level: int = 0):
        self.all_groups = bool(all_groups)
        self.group_ids = frozenset(group_ids or ())
        self.reason = reason or ""
        self.actor_level = int(actor_level or 0)

    def allows(self, group_id: str) -> bool:
        if self.all_groups:
            return True
        return str(group_id or "") in self.group_ids

    def as_dict(self) -> dict:
        return {
            "all_groups": self.all_groups,
            "group_ids": sorted(self.group_ids),
            "reason": self.reason,
            "actor_level": self.actor_level,
        }


def resolve_group_ref(ref: str) -> dict:
    """群标识解析 —— 备注名 / 平台群名 / 群号 → 唯一群（**唯一实现**）。

    优先级：群号（唯一键）> 备注精确 > 群名精确 > 备注包含 > 群名包含。
    文本匹配命中多个时返回候选请调用方再问（AC-US-07.4）；无命中明确回报
    "未找到"，不以近似群代替（AC-US-10.1 / R16）。

    Returns:
        {"status": "ok"|"ambiguous"|"not_found", "group": dict, "candidates": list, "error": str}
    """
    text = str(ref or "").strip()
    if not text:
        return {"status": "not_found", "group": {}, "candidates": [],
                "error": "未提供群标识"}

    with get_session() as session:
        rows = (
            session.query(Conversation)
            .filter(Conversation.conversation_type == "group")
            .all()
        )
        convs = [_conv_to_dict(c) for c in rows]

    # 1) 群号（唯一键，命中即无歧义）
    for c in convs:
        if text == c["group_id"]:
            return {"status": "ok", "group": c, "candidates": [], "error": ""}

    # 2) 备注精确 / 群名精确
    for key in ("note", "title"):
        hits = [c for c in convs if text == (c.get(key) or "").strip()]
        if len(hits) == 1:
            return {"status": "ok", "group": hits[0], "candidates": [], "error": ""}
        if len(hits) > 1:
            return _ambiguous(hits)

    # 3) 备注包含 / 群名包含
    for key in ("note", "title"):
        hits = [c for c in convs if text and text in (c.get(key) or "")]
        if len(hits) == 1:
            return {"status": "ok", "group": hits[0], "candidates": [], "error": ""}
        if len(hits) > 1:
            return _ambiguous(hits)

    return {"status": "not_found", "group": {}, "candidates": [],
            "error": f"未找到与「{text}」匹配的群"}


def _ambiguous(hits: list[dict]) -> dict:
    candidates = [
        {"group_id": c["group_id"], "display_name": c["display_name"],
         "im_platform": c["im_platform"]}
        for c in hits[:MAX_CANDIDATES]
    ]
    return {"status": "ambiguous", "group": {}, "candidates": candidates,
            "error": f"「{hits[0].get('display_name') or ''}」等 {len(hits)} 个群匹配，请指明具体群"}


class GroupQueryService:
    """群信息取数出口（三种读取 + 作用域收敛 + 群标识解析）。"""

    def __init__(self, roster_service=None):
        """
        Args:
            roster_service: GroupRosterService（提供新鲜度判定与名册读取；
                本服务**不调用**其任何刷新入口，保证取数全链路只读）。
        """
        self._roster = roster_service

    # ── 作用域收敛（唯一实现）──

    def resolve_actor_level(self, actor_user_id: str,
                            session_level: int | None = None) -> int:
        """档位来源单一化：优先取会话快照的 level，否则查库；取不到一律 0。

        `LEVEL_FALLBACK=0` 使任何解析失败都落入发起者档（fail-closed），
        绝不会因为"读不到等级"而升级为观察档。
        """
        if session_level is not None:
            try:
                return int(session_level)
            except (TypeError, ValueError):
                return LEVEL_FALLBACK
        if not actor_user_id:
            return LEVEL_FALLBACK
        try:
            from ..repositories.permission_repo import PermissionRepository

            operator = PermissionRepository.get_user(actor_user_id)
            return int(getattr(operator, "level", 0) or 0)
        except Exception as e:  # noqa: BLE001 — 解析失败即 fail-closed
            logger.warning("resolve_actor_level failed user=%s: %s", actor_user_id, e)
            return LEVEL_FALLBACK

    def resolve_visible_group_ids(self, *, actor_user_id: str, level: int) -> GroupScope:
        """作用域收敛 —— **唯一实现**（AC-US-08.6 / §4.4-9）。

        观察档判定与参与关系收敛都在此一处完成；控制台与对话侧共同调用，
        不存在第二套过滤。
        """
        try:
            lv = int(level)
        except (TypeError, ValueError):
            lv = LEVEL_FALLBACK

        if lv >= OBSERVER_MIN_LEVEL:
            return GroupScope(all_groups=True, group_ids=(), actor_level=lv,
                              reason=f"观察档（等级 {lv} ≥ {OBSERVER_MIN_LEVEL}）：不受参与关系限制")

        if not actor_user_id:
            return GroupScope(all_groups=False, group_ids=(), actor_level=lv,
                              reason="身份为空 → fail-closed，不放行任何群")

        try:
            gids = GroupMemberRepository.list_active_group_ids(actor_user_id)
        except Exception as e:  # noqa: BLE001 — 名册读失败一律不放行
            logger.warning("resolve scope failed user=%s: %s", actor_user_id, e)
            gids = []

        if not gids:
            return GroupScope(all_groups=False, group_ids=(), actor_level=lv,
                              reason="该身份在名册中无在册记录的群 → fail-closed")
        return GroupScope(all_groups=False, group_ids=gids, actor_level=lv,
                          reason=f"发起者档：在册参与 {len(gids)} 个群")

    # ── 三种读取 ──

    def list_tracked_groups(self, *, keyword: str = "", page: int = 1,
                            page_size: int = 50, order_by: str = "last_active") -> dict:
        """在追踪群列表（含备注名 / 平台 / 成员数 / 已留存消息数 / 最近活跃 / 过期标识）。

        口径说明：**本方法不按身份收敛**——AC-US-06.1 与 G4 要求"能列出全部在追踪群"
        （否则用户无从得知可以就哪些群提问），且本列表不含群内消息内容。群消息的
        收敛由 `resolve_visible_group_ids` 承担。
        """
        page_size = max(1, min(int(page_size or 50), MAX_PAGE_SIZE))
        page = max(1, int(page or 1))
        rows = _all_group_convs()
        if keyword:
            needle = str(keyword).strip()
            rows = [r for r in rows
                    if needle in (r.get("note") or "") or needle in (r.get("title") or "")
                    or needle in r.get("group_id", "")]

        pairs = [(r["im_platform"], r["group_id"]) for r in rows]
        gids = [r["group_id"] for r in rows]
        counts = MessageRepository.count_messages_by_group(
            gids, exclude_sources=list(DEFAULT_EXCLUDED_SOURCES))
        last_active = MessageRepository.last_active_by_group(
            gids, exclude_sources=list(DEFAULT_EXCLUDED_SOURCES))
        roster_counts = GroupMemberRepository.count_active_by_groups(pairs)

        for r in rows:
            active = roster_counts.get(r["group_id"], 0)
            r["member_count"] = active if active else int(r.get("member_count") or 0)
            r["member_count_source"] = "roster" if active else "platform_snapshot"
            r["message_count"] = int(counts.get(r["group_id"], 0))
            r["last_active_at"] = last_active.get(r["group_id"], "") or r.get("updated_at", "")
            r["roster_stale"] = self._is_stale(r)

        if order_by == "name":
            rows.sort(key=lambda x: (x.get("display_name") or ""))
        elif order_by == "message_count":
            rows.sort(key=lambda x: x["message_count"], reverse=True)
        else:  # 默认最近活跃倒序
            rows.sort(key=lambda x: (x.get("last_active_at") or ""), reverse=True)

        total = len(rows)
        start = (page - 1) * page_size
        return {
            "rows": rows[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "stale_count": sum(1 for r in rows if r["roster_stale"]),
        }

    def list_group_messages(self, *, actor_user_id: str, level: int, group_ref: str,
                            limit: int = 50, before_id: str = "",
                            include_sources: bool = False) -> dict:
        """群消息列表（时间倒序，游标回溯）。"""
        scope = self.resolve_visible_group_ids(actor_user_id=actor_user_id, level=level)
        limit = max(1, min(int(limit or 50), MAX_MESSAGE_LIMIT))

        resolved = resolve_group_ref(group_ref)
        if resolved.get("status") != "ok":
            return {"ok": False, "rows": [], "total": 0, "has_more": False,
                    "next_before_id": "", "group": {}, "scope_denied": False,
                    "status": resolved.get("status"), "error": resolved.get("error") or ""}
        group = resolved["group"]

        if not scope.allows(group["group_id"]):
            # 越权：如实回报空，且不泄露该群消息内容（AC-US-08.3）
            logger.info("group messages denied group=%s actor=%s (%s)",
                        group["group_id"], actor_user_id or "-", scope.reason)
            return {"ok": True, "rows": [], "total": 0, "has_more": False,
                    "next_before_id": "", "group": _public_group(group),
                    "scope_denied": True, "error": ""}

        rows, total = MessageRepository.query_group_messages(
            group_ids=[group["group_id"]],
            before_id=before_id,
            limit=limit + 1,
            exclude_sources=None if include_sources else list(DEFAULT_EXCLUDED_SOURCES),
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        dto_rows = self._message_dtos(rows, group)
        return {
            "ok": True,
            "rows": dto_rows,
            "total": total,
            "has_more": has_more,
            "next_before_id": dto_rows[-1]["id"] if (has_more and dto_rows) else "",
            "group": _public_group(group),
            "scope_denied": False,
            "error": "",
        }

    def search_group_messages(self, *, actor_user_id: str, level: int, keyword: str,
                              group_ref: str = "", time_range: str = "all",
                              limit: int = 20, include_sources: bool = False) -> dict:
        """跨群 / 限群检索（与列表同一出口，仅 keyword 参与与否不同）。"""
        scope = self.resolve_visible_group_ids(actor_user_id=actor_user_id, level=level)
        limit = max(1, min(int(limit or 20), MAX_MESSAGE_LIMIT))

        target_group = None
        if str(group_ref or "").strip():
            resolved = resolve_group_ref(group_ref)
            if resolved.get("status") != "ok":
                return {"ok": False, "rows": [], "total": 0, "truncated": False,
                        "group": {}, "keyword": keyword, "scope_denied": False,
                        "status": resolved.get("status"), "error": resolved.get("error") or ""}
            target_group = resolved["group"]
            if not scope.allows(target_group["group_id"]):
                logger.info("group search denied group=%s actor=%s (%s)",
                            target_group["group_id"], actor_user_id or "-", scope.reason)
                return {"ok": True, "rows": [], "total": 0, "truncated": False,
                        "group": _public_group(target_group), "keyword": keyword,
                        "scope_denied": True, "error": ""}

        group_ids = None if scope.all_groups else sorted(scope.group_ids)
        if target_group is not None:
            group_ids = [target_group["group_id"]]

        rows, total = MessageRepository.query_group_messages(
            keyword=keyword,
            group_ids=group_ids,
            time_range=time_range,
            limit=limit,
            exclude_sources=None if include_sources else list(DEFAULT_EXCLUDED_SOURCES),
        )
        names = _group_display_map([r.group_id for r in rows if getattr(r, "group_id", None)])
        dto_rows = []
        for m in rows:
            gid = getattr(m, "group_id", "") or ""
            dto_rows.append({
                "id": m.id,
                "group_id": gid,
                "group_display": names.get(gid) or gid,
                "sender_name": m.sender_name or m.sender_im_id or "",
                "created_at": str(m.created_at or ""),
                "content": _snippet(m.content or "", keyword),
                "source": m.source or "",
            })
        return {
            "ok": True,
            "rows": dto_rows,
            "total": total,
            "truncated": total > len(dto_rows),
            "group": _public_group(target_group) if target_group else {},
            "keyword": keyword,
            "scope_denied": False,
            "error": "",
        }

    def list_group_roster(self, *, group_ref: str, include_inactive: bool = True,
                          limit: int = 200, offset: int = 0) -> dict:
        """名册读取（委托 M4；本服务不触发刷新，保持只读）。"""
        if self._roster is None:
            return {"ok": False, "group": {}, "rows": [], "total": 0,
                    "error": "名册服务未启用"}
        limit = max(1, min(int(limit or 200), MAX_PAGE_SIZE))
        return self._roster.list_roster(
            group_ref=group_ref, include_inactive=include_inactive,
            limit=limit, offset=offset)

    # ── 内部 ──

    def _is_stale(self, conv: dict) -> bool:
        if self._roster is None:
            return not (conv.get("roster_synced_at") or "")
        return bool(self._roster.is_stale(conv))

    def _message_dtos(self, rows: list[Message], group: dict) -> list[dict]:
        """消息行 → DTO（含批量附件元信息）。"""
        if not rows:
            return []
        atts = ChatArchiveRepository.get_attachments_for_messages([m.id for m in rows])
        display = group.get("display_name") or group.get("group_id") or ""
        out = []
        for m in rows:
            out.append({
                "id": m.id,
                "group_id": getattr(m, "group_id", "") or "",
                "group_display": display,
                "sender_name": m.sender_name or "",
                "sender_im_id": m.sender_im_id or "",
                "sender_user_id": m.sender_user_id or "",
                "created_at": str(m.created_at or ""),
                "content": m.content or "",
                "source": m.source or "",
                "attachments": [
                    {"id": a["id"], "name": _attachment_name(a),
                     "size": int(a.get("file_size") or 0),
                     "type": int(a.get("attachment_type") or 0),
                     "mime": a.get("mime_type") or ""}
                    for a in (atts.get(m.id) or [])
                ],
            })
        return out


# ── 模块级助手 ──

def _all_group_convs() -> list[dict]:
    """全部群台账行（在追踪 = 全部已发现群；R12 不设追踪开关）。"""
    with get_session() as session:
        rows = (
            session.query(Conversation)
            .filter(Conversation.conversation_type == "group")
            .all()
        )
        return [_conv_to_dict(c) for c in rows]


def _conv_to_dict(c: Conversation) -> dict:
    return {
        "group_id": c.conversation_id or c.group_id or "",
        "im_platform": c.im_platform or "",
        "note": c.note or "",
        "title": c.title or "",
        "display_name": (c.note or "").strip() or c.title or c.conversation_id or "",
        "member_count": int(c.member_count or 0),
        "roster_synced_at": c.roster_synced_at or "",
        "updated_at": str(c.updated_at or ""),
    }


def _group_display_map(group_ids: list[str]) -> dict[str, str]:
    """群号 → 展示名（备注优先，回退平台群名）批量映射。"""
    ids = {str(g) for g in (group_ids or []) if g}
    if not ids:
        return {}
    with get_session() as session:
        rows = (
            session.query(Conversation)
            .filter(Conversation.conversation_type == "group",
                    Conversation.conversation_id.in_(ids))
            .all()
        )
        return {c.conversation_id: ((c.note or "").strip() or c.title or c.conversation_id)
                for c in rows}


def _public_group(group: dict) -> dict:
    """对外的群标识（只暴露展示所需字段）。"""
    if not group:
        return {}
    return {
        "group_id": group.get("group_id", ""),
        "display_name": group.get("display_name", ""),
        "im_platform": group.get("im_platform", ""),
    }


def _snippet(content: str, keyword: str, radius: int = 60) -> str:
    """命中片段（关键词上下文），无关键词时截断。"""
    text = content or ""
    if keyword:
        idx = text.find(keyword)
        if idx >= 0:
            start = max(0, idx - radius)
            end = min(len(text), idx + len(keyword) + radius)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
            return f"{prefix}{text[start:end]}{suffix}"
    return text[:200] + ("…" if len(text) > 200 else "")


def _attachment_name(att: dict) -> str:
    from pathlib import PurePosixPath

    raw = att.get("local_path") or att.get("file_url") or ""
    return PurePosixPath(str(raw).replace("\\", "/")).name or "附件"
