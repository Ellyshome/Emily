"""GroupMemberRepository —— 群成员名册的数据访问。

一行 = 同一平台 + 同一群 + 同一成员（唯一约束 `uq_gm_platform_group_member`）。
Repository 全 sync（C6），async Service 侧用 `asyncio.to_thread` 包裹。

`list_active_group_ids` 是**群消息检索作用域收敛的唯一数据来源**：由它得出
"该人当前参与哪些群"（AC-US-08.1）。
"""

from __future__ import annotations

import logging

from ..infrastructure.database.models import GroupMember
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.repositories.group_member")


class GroupMemberRepository:
    """群成员名册读写（全部 sync）。"""

    # ── 写入 ──

    @staticmethod
    def upsert_roster(im_platform: str, group_id: str, members: list[dict],
                      synced_at: str) -> dict:
        """全量覆盖式幂等 upsert：本批出现者置在册，本批缺失的既有行置已退群。

        Args:
            im_platform: 平台标识。
            group_id: 平台群号。
            members: [{"im_user_id", "display_name", "role", "joined_at", "user_id"}, ...]
                —— 已由 Service 归一化并完成绑定解析。
            synced_at: 本批同步时刻（同时写入 last_seen_at）。

        Returns:
            {"inserted", "updated", "reactivated", "deactivated", "active_total"}
        """
        stats = {"inserted": 0, "updated": 0, "reactivated": 0,
                 "deactivated": 0, "active_total": 0}
        seen: set[str] = set()

        with get_session() as session:
            existing = {
                row.im_user_id: row
                for row in session.query(GroupMember).filter(
                    GroupMember.im_platform == im_platform,
                    GroupMember.group_id == group_id,
                ).all()
            }

            for m in members:
                im_user_id = str(m.get("im_user_id") or "").strip()
                if not im_user_id:
                    continue
                seen.add(im_user_id)
                row = existing.get(im_user_id)
                if row is None:
                    session.add(GroupMember(
                        im_platform=im_platform,
                        group_id=group_id,
                        im_user_id=im_user_id,
                        display_name=m.get("display_name") or None,
                        role=m.get("role") or None,
                        joined_at=m.get("joined_at") or None,
                        user_id=m.get("user_id") or None,
                        is_active=True,
                        first_seen_at=synced_at,
                        last_seen_at=synced_at,
                    ))
                    stats["inserted"] += 1
                    continue

                # 已存在：更新展示信息；退群者重新入群即回真
                row.display_name = m.get("display_name") or row.display_name
                row.role = m.get("role") or row.role
                row.joined_at = m.get("joined_at") or row.joined_at
                if m.get("user_id"):
                    row.user_id = m["user_id"]
                row.last_seen_at = synced_at
                if not row.is_active:
                    row.is_active = True
                    stats["reactivated"] += 1
                else:
                    stats["updated"] += 1

            # 本批未出现 → 已退群（保留行，成员数只计在册）
            for im_user_id, row in existing.items():
                if im_user_id in seen or not row.is_active:
                    continue
                row.is_active = False
                stats["deactivated"] += 1

            session.flush()
            stats["active_total"] = session.query(GroupMember).filter(
                GroupMember.im_platform == im_platform,
                GroupMember.group_id == group_id,
                GroupMember.is_active.is_(True),
            ).count()
            session.commit()

        logger.info("roster upsert platform=%s group=%s %s", im_platform, group_id, stats)
        return stats

    # ── 读取 ──

    @staticmethod
    def list_active_group_ids(user_id: str) -> list[str]:
        """该用户**当前在册**的群号集合（检索作用域的起算依据）。

        `user_id` 为空或未在名册中出现 → 空列表（fail-closed：不授予任何群）。
        """
        if not user_id:
            return []
        with get_session() as session:
            rows = (
                session.query(GroupMember.group_id)
                .filter(
                    GroupMember.user_id == user_id,
                    GroupMember.is_active.is_(True),
                )
                .distinct()
                .all()
            )
        return [r[0] for r in rows if r[0]]

    @staticmethod
    def list_by_group(im_platform: str, group_id: str, *,
                      include_inactive: bool = True,
                      limit: int = 200, offset: int = 0) -> list[dict]:
        """按群列名册（控制台名册区）。"""
        with get_session() as session:
            q = session.query(GroupMember).filter(
                GroupMember.im_platform == im_platform,
                GroupMember.group_id == group_id,
            )
            if not include_inactive:
                q = q.filter(GroupMember.is_active.is_(True))
            rows = (
                q.order_by(GroupMember.is_active.desc(), GroupMember.display_name)
                .offset(max(0, int(offset))).limit(max(1, int(limit))).all()
            )
            return [GroupMemberRepository._to_dict(r) for r in rows]

    @staticmethod
    def count_by_group(im_platform: str, group_id: str, *,
                       include_inactive: bool = True) -> int:
        """该群名册行数（控制台分页用）。"""
        with get_session() as session:
            q = session.query(GroupMember).filter(
                GroupMember.im_platform == im_platform,
                GroupMember.group_id == group_id,
            )
            if not include_inactive:
                q = q.filter(GroupMember.is_active.is_(True))
            return int(q.count())

    @staticmethod
    def count_active(im_platform: str, group_id: str) -> int:
        """在册人数（**成员数的唯一口径**）。"""
        with get_session() as session:
            return int(session.query(GroupMember).filter(
                GroupMember.im_platform == im_platform,
                GroupMember.group_id == group_id,
                GroupMember.is_active.is_(True),
            ).count())

    @staticmethod
    def count_active_by_groups(pairs: list[tuple[str, str]]) -> dict[str, int]:
        """批量在册人数（群列表避免 N+1）。

        Args:
            pairs: [(im_platform, group_id), ...]

        Returns:
            {group_id: active_count}；未出现在名册中的群不出现在结果里。
        """
        if not pairs:
            return {}
        wanted = set(pairs)
        platforms = {p for p, _ in wanted}
        groups = {g for _, g in wanted}
        with get_session() as session:
            rows = (
                session.query(GroupMember.im_platform, GroupMember.group_id)
                .filter(
                    GroupMember.im_platform.in_(platforms),
                    GroupMember.group_id.in_(groups),
                    GroupMember.is_active.is_(True),
                )
                .all()
            )
        out: dict[str, int] = {}
        for platform, group_id in rows:
            if (platform, group_id) not in wanted:
                continue
            out[group_id] = out.get(group_id, 0) + 1
        return out

    @staticmethod
    def has_roster(im_platform: str, group_id: str) -> bool:
        """该群名册是否已拉取（群列表"名册未就绪"标识用）。"""
        with get_session() as session:
            return session.query(GroupMember).filter(
                GroupMember.im_platform == im_platform,
                GroupMember.group_id == group_id,
            ).first() is not None

    @staticmethod
    def _to_dict(row: GroupMember) -> dict:
        return {
            "id": row.id,
            "im_platform": row.im_platform,
            "group_id": row.group_id,
            "im_user_id": row.im_user_id,
            "display_name": row.display_name or "",
            "role": row.role or "",
            "joined_at": row.joined_at or "",
            "user_id": row.user_id or "",
            "is_active": bool(row.is_active),
            "first_seen_at": row.first_seen_at or "",
            "last_seen_at": row.last_seen_at or "",
        }
