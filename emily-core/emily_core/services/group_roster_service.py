"""GroupRosterService —— 群成员名册的采集、惰性保鲜与过期巡检。

职责边界：
  · 名册的唯一写入口（平台成员清单 → 名册表的幂等 upsert）；
  · 新鲜度门槛判定（`is_stale`，唯一实现）；
  · 惰性保鲜（`ensure_fresh`）——**只被"人在场"路径调用**（控制台 / 维护脚本），
    机器人检索路径**不触发任何名册写入**（AC-US-04.4 / 约束 §4.4-10）；
  · 过期群巡检（`scan_stale`，带留痕）。

保鲜机制不依赖任何调度：新鲜度门槛 + 惰性刷新（PRD D7 机制三）。刷新失败时
**不写 `roster_synced_at`** —— 不把失败当成新鲜。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..infrastructure.database.models import Conversation
from ..infrastructure.database.session import get_session
from ..infrastructure.logging.audit import audited
from ..repositories.group_member_repo import GroupMemberRepository
from ..repositories.user_repo import UserRepository

logger = logging.getLogger("emily.services.group_roster")

#: 默认新鲜度门槛（天）。PRD 风险 1 的拍板值；可由 config.group_roster_ttl_days 覆盖。
DEFAULT_TTL_DAYS = 7

#: OneBot 动作名（与插件侧白名单一致）
ACTION_GET_MEMBERS = "get_group_member_list"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GroupRosterService:
    """群成员名册服务（写侧 + 保鲜 + 巡检）。"""

    def __init__(self, platform_action=None, ttl_days: int = DEFAULT_TTL_DAYS):
        """
        Args:
            platform_action: PlatformActionService（按需拉取平台成员清单）。
            ttl_days: 新鲜度门槛（天）。
        """
        self._platform_action = platform_action
        self.ttl_days = int(ttl_days or DEFAULT_TTL_DAYS)

    # ── 新鲜度判定（唯一实现）──

    def is_stale(self, conv: dict) -> bool:
        """该群名册是否已过期。

        口径：`roster_synced_at` 为空（从未同步成功）→ 过期；早于 `now - ttl` → 过期。
        不解析时间戳时按过期处理（fail-safe：宁可多刷一次，不把脏数据当准数）。
        """
        raw = (conv or {}).get("roster_synced_at") or ""
        if not raw:
            return True
        try:
            ts = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts < datetime.now(timezone.utc) - timedelta(days=self.ttl_days)

    def stale_days(self, conv: dict) -> int | None:
        """距上次同步的天数；从未同步返回 None（巡检展示用）。"""
        raw = (conv or {}).get("roster_synced_at") or ""
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - ts).days)

    # ── 刷新 ──

    async def refresh(self, *, group_ref: str, operator_id: str = "",
                      timeout: float = 8.0) -> dict:
        """拉取某群成员名册并落库（幂等）。

        Returns:
            {"ok", "group", "refreshed", "counts", "error", "error_code"}
            —— 失败只回报原因，不抛异常；失败时**不写** `roster_synced_at`。
        """
        from .group_query_service import resolve_group_ref

        resolved = resolve_group_ref(group_ref)
        if resolved.get("status") != "ok":
            return self._fail(resolved.get("error") or "群未找到",
                              error_code=resolved.get("status") or "not_found")
        group = resolved["group"]

        if self._platform_action is None:
            return self._fail("平台动作服务未启用", group=group)
        if not group.get("group_id"):
            return self._fail("群台账缺少平台群号，无法拉取名册", group=group)

        result = await self._platform_action.request(
            ACTION_GET_MEMBERS,
            {"group_id": group["group_id"]},
            platform=group.get("im_platform") or "",
            timeout=timeout,
        )
        if not result.get("ok"):
            logger.warning("roster refresh failed group=%s: %s",
                           group.get("group_id"), result.get("error"))
            return self._fail(result.get("error") or "拉取成员名册失败", group=group)

        members = self._normalize_members(result.get("data"), group.get("im_platform") or "")
        if members is None:
            return self._fail("平台返回的成员名册格式无法解析", group=group)

        synced_at = _now_iso()
        counts = await asyncio.to_thread(
            GroupMemberRepository.upsert_roster,
            group.get("im_platform") or "", group.get("group_id") or "",
            members, synced_at,
        )
        await asyncio.to_thread(
            self._mark_synced, group.get("im_platform") or "",
            group.get("group_id") or "", synced_at,
        )
        self._record_refresh(group, counts, operator_id)
        logger.info("roster refreshed group=%s counts=%s", group.get("group_id"), counts)
        return {"ok": True, "group": group, "refreshed": True, "counts": counts,
                "error": "", "error_code": ""}

    async def ensure_fresh(self, *, group_ref: str, operator_id: str = "",
                           timeout: float = 8.0) -> dict:
        """惰性保鲜（**人在场路径专用**）：已新鲜则跳过，过期才刷新。

        机器人检索路径不得调用本方法——否则该路径不再是全链路只读（AC-US-04.4）。
        """
        from .group_query_service import resolve_group_ref

        resolved = resolve_group_ref(group_ref)
        if resolved.get("status") != "ok":
            return {"ok": False, "refreshed": False, "stale": False, "group": {},
                    "error": resolved.get("error") or "群未找到",
                    "error_code": resolved.get("status") or "not_found"}
        group = resolved["group"]
        if not self.is_stale(group):
            return {"ok": True, "refreshed": False, "stale": False, "group": group,
                    "error": "", "error_code": ""}

        out = await self.refresh(group_ref=group_ref, operator_id=operator_id, timeout=timeout)
        out["stale"] = self.is_stale(_reload_group(group) or group)
        return out

    def _mark_synced(self, im_platform: str, group_id: str, synced_at: str) -> None:
        """写入名册最后同步时间（仅刷新成功后调用）。"""
        with get_session() as session:
            row = session.query(Conversation).filter(
                Conversation.im_platform == im_platform,
                Conversation.conversation_id == group_id,
            ).first()
            if row is not None:
                row.roster_synced_at = synced_at
                session.commit()

    # ── 过期巡检 ──

    @audited(
        category="group",
        action="stale_scanned",
        target_type="group_roster",
        actor_arg="operator_id",
        summary="{summary}",
    )
    def scan_stale(self, *, operator_id: str = "", limit: int = 200) -> list[dict]:
        """列出名册已过期（或从未同步）的群，供运维巡检与手动刷新。

        带留痕（AC-US-20.4）；`self.summary` 由本方法写入，供 `@audited` 取用。
        """
        rows = self._all_groups(limit=limit)
        stale = [r for r in rows if self.is_stale(r)]
        for r in stale:
            r["stale_days"] = self.stale_days(r)
            r["ttl_days"] = self.ttl_days
        self.summary = f"名册过期巡检：共 {len(rows)} 群，其中 {len(stale)} 群过期"
        logger.info("stale roster scan: %d/%d groups stale", len(stale), len(rows))
        return stale

    @staticmethod
    def _all_groups(limit: int = 200) -> list[dict]:
        with get_session() as session:
            rows = (
                session.query(Conversation)
                .filter(Conversation.conversation_type == "group")
                .order_by(Conversation.roster_synced_at.asc().nullsfirst())
                .limit(max(1, int(limit)))
                .all()
            )
            return [_conv_to_dict(c) for c in rows]

    # ── 名册读取（控制台名册区）──

    def list_roster(self, *, group_ref: str, include_inactive: bool = True,
                    limit: int = 200, offset: int = 0) -> dict:
        """读取某群名册（含在册 / 已退群标记与绑定状态）。"""
        from .group_query_service import resolve_group_ref

        resolved = resolve_group_ref(group_ref)
        if resolved.get("status") != "ok":
            return {"ok": False, "group": {}, "rows": [], "total": 0,
                    "error": resolved.get("error") or "群未找到"}
        group = resolved["group"]
        platform = group.get("im_platform") or ""
        gid = group.get("group_id") or ""
        rows = GroupMemberRepository.list_by_group(
            platform, gid, include_inactive=include_inactive,
            limit=limit, offset=offset)
        total = GroupMemberRepository.count_by_group(
            platform, gid, include_inactive=include_inactive)
        return {"ok": True, "group": group, "rows": rows, "total": total, "error": ""}

    # ── 内部 ──

    def _record_refresh(self, group: dict, counts: dict, operator_id: str) -> None:
        """刷新留痕（AC-US-20.4）。"""
        try:
            from ..infrastructure.logging.audit import record_action

            record_action(
                category="group",
                action="roster_refreshed",
                target_type="group_roster",
                target_no=str(group.get("group_id") or ""),
                summary=(f"名册刷新：{group.get('display_name') or group.get('group_id')}"
                         f" 在册 {counts.get('active_total', 0)} 人"
                         f"（新增 {counts.get('inserted', 0)} / 退群 {counts.get('deactivated', 0)}）"),
                actor_id=str(operator_id or ""),
            )
        except Exception as e:  # noqa: BLE001 — 留痕非阻断
            logger.warning("roster refresh audit failed: %s", e)

    @staticmethod
    def _normalize_members(data, im_platform: str) -> list[dict] | None:
        """平台成员清单 → 名册入参（含绑定解析）。

        Returns:
            归一化后的成员列表；`data` 不是列表时返回 None（调用方据此如实回报）。
        """
        if not isinstance(data, list):
            return None
        raw: list[dict] = []
        for m in data:
            if not isinstance(m, dict):
                continue
            uid = str(m.get("user_id", "") or "").strip()
            if not uid:
                continue
            raw.append({
                "im_user_id": uid,
                "display_name": (m.get("card") or m.get("nickname") or "").strip() or None,
                "role": _norm_role(m.get("role")),
                "joined_at": _norm_epoch(m.get("join_time")),
            })
        # 批量绑定解析（未绑定者保留平台账号，user_id 置空）
        bindings = UserRepository.get_bindings_by_im_ids(
            im_platform, [r["im_user_id"] for r in raw])
        for r in raw:
            r["user_id"] = bindings.get(r["im_user_id"]) or None
        return raw

    @staticmethod
    def _fail(error: str, *, group: dict | None = None, error_code: str = "") -> dict:
        return {"ok": False, "group": group or {}, "refreshed": False, "counts": {},
                "error": error, "error_code": error_code}


def _norm_role(role) -> str | None:
    """平台角色 → owner / admin / member；不可得或未知一律 None（不报错）。"""
    value = str(role or "").strip().lower()
    return value if value in {"owner", "admin", "member"} else None


def _norm_epoch(value) -> str | None:
    """epoch 秒 → ISO 字符串；缺失或非法返回 None。"""
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _conv_to_dict(c: Conversation) -> dict:
    return {
        "group_id": c.conversation_id or c.group_id or "",
        "im_platform": c.im_platform or "",
        "note": c.note or "",
        "title": c.title or "",
        "display_name": (c.note or "").strip() or c.title or c.conversation_id or "",
        "member_count": int(c.member_count or 0),
        "roster_synced_at": c.roster_synced_at or "",
    }


def _reload_group(group: dict) -> dict | None:
    """刷新后重读群台账（取回新的 roster_synced_at）。"""
    try:
        with get_session() as session:
            row = session.query(Conversation).filter(
                Conversation.im_platform == group.get("im_platform") or "",
                Conversation.conversation_id == group.get("group_id") or "",
            ).first()
            return _conv_to_dict(row) if row is not None else None
    except Exception as e:  # noqa: BLE001
        logger.debug("reload group failed: %s", e)
        return None
