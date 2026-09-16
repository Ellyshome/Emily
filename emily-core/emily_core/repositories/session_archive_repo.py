"""Session 归档读写抽象层 —— 会话**建立即建档**，每轮实时刷新（BUG-004）。

生命周期：
  1. 会话建立 → `upsert_live()` 写索引（status=active，archived_at 空）
  2. 每轮收口 → `touch()` 刷新轮次 + 最后活跃时间
  3. 会话截断 → `truncate()` 标记 status=truncated + 截断时间/原因
超时截断不再是归档触发点：索引在会话建立时就已可查，避免进程重启等
未走截断路径的会话在归档列表中完全不可见。
"""

import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import SessionArchive, _utc_now

logger = logging.getLogger("emily.repo.session_archive")


class SessionArchiveRepo:
    """Session 归档表 CRUD 操作。"""

    @staticmethod
    def upsert_live(
        *,
        conversation_id: str,
        user_id: Optional[str] = None,
        user_name: str = "",
        platform: str = "",
        im_user_id: str = "",
        is_guest: bool = False,
        md_file_path: str = "",
        started_at: Optional[str] = None,
    ) -> str:
        """实时建档（幂等）：复用该会话进行中的索引行，无则新建。

        user_id 为 None 表示访客（users 表无此人），按 NULL 落库以避开外键约束。

        Returns:
            str: 索引行 id；失败返回空字符串。
        """
        now = _utc_now()
        try:
            with get_session() as session:
                row = (
                    session.query(SessionArchive)
                    .filter(SessionArchive.conversation_id == conversation_id)
                    .filter(SessionArchive.status == "active")
                    .order_by(SessionArchive.last_active_at.desc())
                    .first()
                )
                if row is None:
                    row = SessionArchive(
                        conversation_id=conversation_id,
                        status="active",
                        archived_at="",
                        archive_reason="",
                        started_at=started_at,
                        turn_count=0,
                    )
                    session.add(row)
                # 身份/通道信息按最新一次解析结果矫正（访客补登记后自动升级为已登记用户）
                row.user_id = user_id
                row.user_name = user_name
                row.platform = platform
                row.im_user_id = im_user_id
                row.is_guest = is_guest
                row.md_file_path = md_file_path
                row.last_active_at = now
                session.flush()
                logger.info(
                    "SessionArchive live index: conv=%s user=%s guest=%s platform=%s im_user=%s",
                    conversation_id, user_id or "(none)", is_guest, platform or "-", im_user_id or "-",
                )
                return row.id
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive upsert_live failed: %s — %s", conversation_id, e)
            return ""

    @staticmethod
    def touch(*, conversation_id: str, turn_count: int) -> bool:
        """每轮收口刷新索引（轮次 + 最后活跃时间）。"""
        now = _utc_now()
        try:
            with get_session() as session:
                updated = (
                    session.query(SessionArchive)
                    .filter(SessionArchive.conversation_id == conversation_id)
                    .filter(SessionArchive.status == "active")
                    .update(
                        {SessionArchive.turn_count: turn_count,
                         SessionArchive.last_active_at: now},
                        synchronize_session=False,
                    )
                )
            if not updated:
                logger.debug("SessionArchive touch: 无进行中的索引行 conv=%s", conversation_id)
            return bool(updated)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive touch failed: %s — %s", conversation_id, e)
            return False

    @staticmethod
    def truncate(
        *,
        conversation_id: str,
        archive_reason: str = "expired",
        turn_count: Optional[int] = None,
    ) -> bool:
        """会话截断：标记进行中的索引行为已截断（TTL 超时 / 手动终止）。"""
        now = _utc_now()
        values = {
            SessionArchive.status: "truncated",
            SessionArchive.archived_at: now,
            SessionArchive.archive_reason: archive_reason,
        }
        if turn_count is not None:
            values[SessionArchive.turn_count] = turn_count
        try:
            with get_session() as session:
                updated = (
                    session.query(SessionArchive)
                    .filter(SessionArchive.conversation_id == conversation_id)
                    .filter(SessionArchive.status == "active")
                    .update(values, synchronize_session=False)
                )
            logger.info(
                "SessionArchive truncated: conv=%s reason=%s turns=%s rows=%d",
                conversation_id, archive_reason, turn_count, updated,
            )
            return bool(updated)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive truncate failed: %s — %s", conversation_id, e)
            return False

    @staticmethod
    def truncate_all_active(*, archive_reason: str = "restart") -> int:
        """启动自愈：把所有「进行中」索引按截断收口。

        会话池是内存态，Core 重启后池内会话全部丢失、不会再有截断时机；
        启动时统一收口，避免归档列表出现「幽灵进行中」。
        不改 last_active_at（保留真实最后活跃时间，仅供展示与排序）。
        """
        now = _utc_now()
        try:
            with get_session() as session:
                updated = (
                    session.query(SessionArchive)
                    .filter(SessionArchive.status == "active")
                    .update(
                        {SessionArchive.status: "truncated",
                         SessionArchive.archived_at: now,
                         SessionArchive.archive_reason: archive_reason},
                        synchronize_session=False,
                    )
                )
            if updated:
                logger.info("SessionArchive startup recovery: %d active → truncated", updated)
            return int(updated or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive truncate_all_active failed: %s", e)
            return 0

    @staticmethod
    def get_by_conversation_id(conversation_id: str) -> Optional[SessionArchive]:
        """按 conversation_id 查找最新归档。"""
        with get_session() as session:
            return (
                session.query(SessionArchive)
                .filter(SessionArchive.conversation_id == conversation_id)
                .order_by(SessionArchive.last_active_at.desc())
                .first()
            )

    # ── 只读观测（供 emy-console 消费；返回 dict，避免 detached-instance 访问）──

    @staticmethod
    def list_grouped_by_conversation(limit: int = 200) -> list[dict]:
        """按会话合并的只读视图：**一个 conversation 一行**（供 emy-console「会话日志」）。

        为什么需要合并：索引是"会话建立即建档、截断即收口"（见模块 docstring），
        同一 conversation 在 TTL 截断 / 重启收口后再来消息就会新开一段，
        故原始表里一个 conversation 会有多行（新段 active + 历史段 truncated）。
        观测窗口应按会话聚合展示，而不是按"段"罗列。

        合并口径：
          - 状态：任一段进行中 → `active`；否则 `truncated`
          - 时间：`last_active_at` / `archived_at` 取最新值，`started_at` 取最早值
          - 身份与 `md_file_path`：以**最新一段**为准（首行即最新，查询已按时间倒序）
          - `segments`：各段明细，供调用方按**去重后的文件**聚合轮次与正文
            （同一 conversation 同天重启会复用同一 md 文件，按行累加会重复计数）

        Args:
            limit: 参与合并的原始行数上限（不是合并后的会话数）。

        Returns:
            list[dict]: 每个 conversation 一条记录，含 `segments` / `segment_count`。
        """
        with get_session() as session:
            rows = (
                session.query(SessionArchive)
                .order_by(SessionArchive.last_active_at.desc(), SessionArchive.archived_at.desc())
                .limit(limit)
                .all()
            )
        grouped: dict[str, dict] = {}
        for row in rows:
            r = _to_dict(row)
            g = grouped.get(r["conversation_id"])
            if g is None:
                g = dict(r)
                g["segments"] = []
                g["segment_count"] = 0
                grouped[r["conversation_id"]] = g
            g["segments"].append({
                "id": r["id"],
                "status": r["status"],
                "archive_reason": r["archive_reason"],
                "md_file_path": r["md_file_path"],
                "turn_count": r["turn_count"],
                "started_at": r["started_at"],
                "last_active_at": r["last_active_at"],
                "archived_at": r["archived_at"],
            })
            g["segment_count"] += 1
            if r["status"] == "active":
                # 任一段进行中即视为进行中，且不显示截断原因/截断时间
                g["status"] = "active"
                g["archive_reason"] = ""
                g["archived_at"] = ""
            if r["started_at"] and (not g["started_at"] or r["started_at"] < g["started_at"]):
                g["started_at"] = r["started_at"]
            if r["last_active_at"] and r["last_active_at"] > (g["last_active_at"] or ""):
                g["last_active_at"] = r["last_active_at"]
        return list(grouped.values())

    @staticmethod
    def list_by_conversation(conversation_id: str) -> list[dict]:
        """列出该会话的全部归档段（按最后活跃升序，只读）——供全文合并读取。"""
        with get_session() as session:
            rows = (
                session.query(SessionArchive)
                .filter(SessionArchive.conversation_id == conversation_id)
                .order_by(SessionArchive.last_active_at.asc(), SessionArchive.archived_at.asc())
                .all()
            )
            return [_to_dict(r) for r in rows]

    @staticmethod
    def get_by_id(archive_id: str) -> Optional[dict]:
        """按归档记录主键 id 查单条（只读）。"""
        with get_session() as session:
            row = (
                session.query(SessionArchive)
                .filter(SessionArchive.id == archive_id)
                .first()
            )
            return _to_dict(row) if row else None

    @staticmethod
    def list_by_user(user_id: str, limit: int = 20) -> list[SessionArchive]:
        """按用户查询归档历史。"""
        with get_session() as session:
            return (
                session.query(SessionArchive)
                .filter(SessionArchive.user_id == user_id)
                .order_by(SessionArchive.last_active_at.desc())
                .limit(limit)
                .all()
            )


def _to_dict(row: SessionArchive) -> dict:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id or "",
        "user_id": row.user_id or "",
        "user_name": row.user_name or "",
        "platform": row.platform or "",
        "im_user_id": row.im_user_id or "",
        "is_guest": bool(row.is_guest),
        "turn_count": row.turn_count or 0,
        "started_at": row.started_at or "",
        "last_active_at": row.last_active_at or "",
        "archived_at": row.archived_at or "",
        "archive_reason": row.archive_reason or "",
        "status": row.status or "truncated",
        "md_file_path": row.md_file_path or "",
    }
