"""CheckpointService —— SOP 状态快照持久化服务（M12b）。

管理确认流程的完整生命周期：
- 创建快照（进入 user_interaction 节点前）
- 检查确认/取消（用户回复匹配关键词后）
- 超时过期（定时清理 + 可恢复）
- 恢复执行（用户说"刚才的还有吗"）

借鉴 LangGraph checkpoint-per-superstep 模式：
- 每个 user_interaction 节点执行前自动写 checkpoint
- 容器重启后从 DB 恢复，不丢失待确认项
- 超时后保留快照（标记 expired），支持恢复

新旧并存策略：
- 短会话内（未超时）→ 优先查内存缓存，回退 DB
- 超时/重启后 → 从 DB 恢复
- 与 MessageApplication.pending_confirmations dict 并存，优先 checkpoint
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

logger = logging.getLogger("emily.svc.checkpoint")

# 默认确认/取消关键词（与 message_app.CONFIRM_KEYWORDS/CANCEL_KEYWORDS 对齐）
DEFAULT_CONFIRM_KEYWORDS = ["确认", "对", "是的", "ok", "OK", "好", "没问题", "没错", "可以"]
DEFAULT_CANCEL_KEYWORDS = ["取消", "不对", "错了", "不是", "取消录入", "放弃", "不要", "不行"]


class CheckpointService:
    """SOP 状态快照服务（M12b）。

    管理确认流程的完整生命周期：
    - 创建快照（进入 user_interaction 节点前）
    - 检查确认/取消（用户回复匹配关键词后）
    - 超时过期（定时清理 + 可恢复）
    - 恢复执行（用户说"刚才的还有吗"）

    线程安全：DB 写入通过 session_factory 获取独立 session；
    内存缓存仅用于加速短会话内查找。
    """

    def __init__(
        self,
        session_factory: Callable,
        ttl_seconds: int = 300,
        resume_window_seconds: int = 1800,
        max_per_user: int = 5,
        enabled: bool = True,
    ):
        """初始化检查点服务。

        Args:
            session_factory: 数据库 session 工厂（如 get_session）
            ttl_seconds: 检查点超时时间（秒），默认 5 分钟
            resume_window_seconds: 超时后可恢复的时间窗口（秒），默认 30 分钟
            max_per_user: 每用户最大活跃检查点数
            enabled: 是否启用检查点持久化
        """
        self._session_factory = session_factory
        self.ttl_seconds = ttl_seconds
        self.resume_window_seconds = resume_window_seconds
        self.max_per_user = max_per_user
        self.enabled = enabled

        # 内存缓存：加速短会话内的查找（重启后从 DB 重建）
        # { thread_id -> SOPCheckpoint }
        self._active_cache: dict[str, "SOPCheckpoint"] = {}

        if enabled:
            logger.info(
                "M12b CheckpointService initialized: ttl=%ds resume_window=%ds max_per_user=%d",
                ttl_seconds, resume_window_seconds, max_per_user,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # 创建
    # ══════════════════════════════════════════════════════════════════════════

    async def create(
        self,
        thread_id: str,
        sop_id: str,
        node_name: str,
        prompt_text: str,
        state_json: dict = None,
        resume_context: dict = None,
        confirm_keywords: list[str] = None,
        cancel_keywords: list[str] = None,
        pipeline_run_id: str = "",
        message_id: str = "",
        created_by: str = "",
    ) -> Optional["SOPCheckpoint"]:
        """在 user_interaction 节点执行前创建检查点。

        如果同 thread 已有活跃检查点，先过期旧检查点再创建新的。

        Returns:
            创建的 SOPCheckpoint 对象，失败时返回 None
        """
        if not self.enabled:
            return None

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.ttl_seconds)

        # 检查每用户活跃检查点数上限
        if created_by:
            existing_count = await self._count_active_by_user(created_by)
            if existing_count >= self.max_per_user:
                logger.warning(
                    "M12b checkpoint limit reached for user=%s (%d >= %d), expiring oldest",
                    created_by, existing_count, self.max_per_user,
                )
                await self._expire_oldest_for_user(created_by)

        # 先过期同 thread 的旧活跃检查点
        await self._expire_for_thread(thread_id)

        # 序列化
        state_str = json.dumps(state_json or {}, ensure_ascii=False)
        resume_str = json.dumps(resume_context or {}, ensure_ascii=False)
        confirm_str = json.dumps(
            confirm_keywords or DEFAULT_CONFIRM_KEYWORDS, ensure_ascii=False,
        )
        cancel_str = json.dumps(
            cancel_keywords or DEFAULT_CANCEL_KEYWORDS, ensure_ascii=False,
        )

        checkpoint = SOPCheckpoint(
            thread_id=thread_id,
            message_id=message_id,
            sop_id=sop_id,
            node_name=node_name,
            pipeline_run_id=pipeline_run_id,
            state_json=state_str,
            status="pending",
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            prompt_text=prompt_text,
            confirm_keywords=confirm_str,
            cancel_keywords=cancel_str,
            resume_context=resume_str,
            created_by=created_by,
        )

        try:
            with get_session() as session:
                session.add(checkpoint)
                session.commit()
                # 刷新以获取数据库生成的字段（如有）
                session.refresh(checkpoint)
        except Exception as e:
            logger.error("M12b checkpoint create failed: %s", e, exc_info=True)
            return None

        # 加入内存缓存
        self._active_cache[thread_id] = checkpoint

        logger.info(
            "M12b checkpoint created: id=%s thread=%s sop=%s node=%s expires=%s",
            checkpoint.id, thread_id, sop_id, node_name,
            expires_at.strftime("%H:%M:%S"),
        )
        return checkpoint

    # ══════════════════════════════════════════════════════════════════════════
    # 查询
    # ══════════════════════════════════════════════════════════════════════════

    async def get_active(self, thread_id: str) -> Optional["SOPCheckpoint"]:
        """获取指定会话的活跃待确认项（先查缓存，再查 DB）。

        Args:
            thread_id: conversation_id

        Returns:
            活跃的 SOPCheckpoint 或 None
        """
        if not self.enabled:
            return None

        # 1. 先查内存缓存
        cached = self._active_cache.get(thread_id)
        if cached is not None:
            # 检查是否已过期（内存中的可能未及时清理）
            now = datetime.now(timezone.utc)
            try:
                expires_at = datetime.fromisoformat(cached.expires_at)
                if expires_at > now:
                    return cached
                # 已过期，从缓存移除（不删 DB，留给 sweep）
                del self._active_cache[thread_id]
                logger.debug("M12b checkpoint cache expired: thread=%s", thread_id)
            except (ValueError, TypeError):
                del self._active_cache[thread_id]

        # 2. 回退到 DB 查询
        return await self._get_active_from_db(thread_id)

    async def get_by_id(self, checkpoint_id: str) -> Optional["SOPCheckpoint"]:
        """按 ID 获取检查点。"""
        if not self.enabled:
            return None

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        try:
            with get_session() as session:
                return (
                    session.query(SOPCheckpoint)
                    .filter(SOPCheckpoint.id == checkpoint_id)
                    .first()
                )
        except Exception as e:
            logger.error("M12b get_by_id failed: %s", e)
            return None

    async def list_expired(self) -> list["SOPCheckpoint"]:
        """列出所有已过期但未处理的检查点（status='expired' 或 pending 但已过 expires_at）。"""
        if not self.enabled:
            return []

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        try:
            with get_session() as session:
                return (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.status.in_(["pending", "resumed"]),
                        SOPCheckpoint.expires_at < now_str,
                    )
                    .order_by(SOPCheckpoint.expires_at)
                    .all()
                )
        except Exception as e:
            logger.error("M12b list_expired failed: %s", e)
            return []

    async def list_by_user(
        self, user_id: str, status: str = None
    ) -> list["SOPCheckpoint"]:
        """按用户查询检查点历史。"""
        if not self.enabled:
            return []

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        try:
            with get_session() as session:
                q = session.query(SOPCheckpoint).filter(
                    SOPCheckpoint.created_by == user_id
                )
                if status:
                    q = q.filter(SOPCheckpoint.status == status)
                return q.order_by(SOPCheckpoint.created_at.desc()).all()
        except Exception as e:
            logger.error("M12b list_by_user failed: %s", e)
            return []

    # ══════════════════════════════════════════════════════════════════════════
    # 状态变更
    # ══════════════════════════════════════════════════════════════════════════

    async def confirm(self, checkpoint_id: str) -> Optional["SOPCheckpoint"]:
        """用户确认 → status='confirmed'。"""
        return await self._transition(checkpoint_id, "confirmed", "confirmed_at")

    async def cancel(self, checkpoint_id: str) -> Optional["SOPCheckpoint"]:
        """用户取消 → status='cancelled'。"""
        return await self._transition(checkpoint_id, "cancelled", "cancelled_at")

    async def expire(self, checkpoint_id: str) -> Optional["SOPCheckpoint"]:
        """超时过期 → status='expired'（保留快照，不删除）。"""
        return await self._transition(checkpoint_id, "expired", None)

    async def mark_resumed(self, checkpoint_id: str) -> Optional["SOPCheckpoint"]:
        """从过期快照恢复 → status='resumed'。"""
        return await self._transition(checkpoint_id, "resumed", "resumed_at")

    # ══════════════════════════════════════════════════════════════════════════
    # 清理
    # ══════════════════════════════════════════════════════════════════════════

    async def sweep_expired(self) -> int:
        """扫描所有活跃检查点，将已过期的标记为 expired。返回处理数量。"""
        if not self.enabled:
            return 0

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()
        count = 0

        try:
            with get_session() as session:
                expired = (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.status.in_(["pending", "resumed"]),
                        SOPCheckpoint.expires_at < now_str,
                    )
                    .all()
                )

                for chk in expired:
                    chk.status = "expired"
                    count += 1
                    # 同步清理内存缓存
                    if chk.thread_id in self._active_cache:
                        cached = self._active_cache[chk.thread_id]
                        if cached.id == chk.id:
                            del self._active_cache[chk.thread_id]

                if count > 0:
                    session.commit()
                    logger.info("M12b sweep_expired: %d checkpoint(s) expired", count)

        except Exception as e:
            logger.error("M12b sweep_expired failed: %s", e)

        return count

    # ══════════════════════════════════════════════════════════════════════════
    # 恢复
    # ══════════════════════════════════════════════════════════════════════════

    async def find_resumable(
        self, user_id: str, thread_id: str = None
    ) -> list["SOPCheckpoint"]:
        """查找可恢复的检查点。

        条件：
        - status='expired' 或 status='pending'（已过期但未被 sweep）
        - 在可恢复时间窗口内（expires_at + resume_window_seconds > now）
        - 可选按 thread_id 过滤

        Args:
            user_id: 用户 ID
            thread_id: 会话 ID（可选，为空时查全部）

        Returns:
            可恢复的检查点列表（按过期时间降序）
        """
        if not self.enabled:
            return []

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        now = datetime.now(timezone.utc)
        # 可恢复窗口：expires_at 之后 resume_window_seconds 内
        cutoff = (now - timedelta(seconds=self.resume_window_seconds))
        # 放宽条件：任何 created_at 在 resume_window 内且 status 不是 confirmed/cancelled 的
        cutoff_str = cutoff.isoformat()

        try:
            with get_session() as session:
                q = (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.created_by == user_id,
                        SOPCheckpoint.status.in_(["expired", "pending", "resumed"]),
                        SOPCheckpoint.created_at >= cutoff_str,
                    )
                )
                if thread_id:
                    q = q.filter(SOPCheckpoint.thread_id == thread_id)
                return q.order_by(SOPCheckpoint.expires_at.desc()).all()
        except Exception as e:
            logger.error("M12b find_resumable failed: %s", e)
            return []

    @staticmethod
    async def restore_state(checkpoint: "SOPCheckpoint") -> dict:
        """从快照恢复 PipelineContext 状态（反序列化 state_json）。

        Args:
            checkpoint: SOPCheckpoint 对象

        Returns:
            dict: 恢复的状态字典，可注入到 context.baggage
        """
        try:
            state = json.loads(checkpoint.state_json or "{}")
            resume = json.loads(checkpoint.resume_context or "{}")
            # 合并：resume_context 优先级高于 state_json
            merged = {**state, **resume}
            logger.debug(
                "M12b state restored from checkpoint %s: %d keys",
                checkpoint.id, len(merged),
            )
            return merged
        except json.JSONDecodeError as e:
            logger.warning("M12b restore_state JSON parse failed: %s", e)
            return {}

    # ══════════════════════════════════════════════════════════════════════════
    # 内部辅助
    # ══════════════════════════════════════════════════════════════════════════

    async def _transition(
        self, checkpoint_id: str, new_status: str, timestamp_field: str | None
    ) -> Optional["SOPCheckpoint"]:
        """通用的状态转换方法。"""
        if not self.enabled:
            return None

        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        try:
            with get_session() as session:
                checkpoint = (
                    session.query(SOPCheckpoint)
                    .filter(SOPCheckpoint.id == checkpoint_id)
                    .first()
                )

                if checkpoint is None:
                    logger.warning("M12b checkpoint not found: %s", checkpoint_id)
                    return None

                # 状态机约束：只有特定状态能转换
                allowed_from = {
                    "confirmed": ["pending", "resumed"],
                    "cancelled": ["pending", "resumed"],
                    "expired": ["pending", "resumed"],
                    "resumed": ["expired", "pending"],
                }
                valid_from = allowed_from.get(new_status, [])
                if valid_from and checkpoint.status not in valid_from:
                    logger.warning(
                        "M12b invalid transition: %s %s→%s (expected from %s)",
                        checkpoint_id, checkpoint.status, new_status, valid_from,
                    )
                    return checkpoint  # 返回当前状态，不报错

                checkpoint.status = new_status
                if timestamp_field:
                    setattr(checkpoint, timestamp_field, now_str)

                session.commit()
                session.refresh(checkpoint)

                # 清理内存缓存（confirmed/cancelled 不再需要）
                if new_status in ("confirmed", "cancelled"):
                    if checkpoint.thread_id in self._active_cache:
                        cached = self._active_cache[checkpoint.thread_id]
                        if cached.id == checkpoint.id:
                            del self._active_cache[checkpoint.thread_id]

                logger.info(
                    "M12b checkpoint %s: id=%s thread=%s",
                    new_status, checkpoint_id, checkpoint.thread_id,
                )
                return checkpoint

        except Exception as e:
            logger.error("M12b _transition failed: %s", e, exc_info=True)
            return None

    async def _get_active_from_db(self, thread_id: str) -> Optional["SOPCheckpoint"]:
        """从数据库查询活跃检查点。"""
        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        try:
            with get_session() as session:
                checkpoint = (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.thread_id == thread_id,
                        SOPCheckpoint.status.in_(["pending", "resumed"]),
                        SOPCheckpoint.expires_at > now_str,
                    )
                    .order_by(SOPCheckpoint.created_at.desc())
                    .first()
                )

                if checkpoint is not None:
                    # 加入缓存
                    self._active_cache[thread_id] = checkpoint

                return checkpoint
        except Exception as e:
            logger.error("M12b _get_active_from_db failed: %s", e)
            return None

    async def _count_active_by_user(self, user_id: str) -> int:
        """统计某用户当前活跃检查点数。"""
        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        try:
            with get_session() as session:
                return (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.created_by == user_id,
                        SOPCheckpoint.status.in_(["pending", "resumed"]),
                    )
                    .count()
                )
        except Exception:
            return 0

    async def _expire_oldest_for_user(self, user_id: str) -> None:
        """过期某用户最老的活跃检查点。"""
        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        try:
            with get_session() as session:
                oldest = (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.created_by == user_id,
                        SOPCheckpoint.status.in_(["pending", "resumed"]),
                    )
                    .order_by(SOPCheckpoint.created_at)
                    .first()
                )
                if oldest:
                    oldest.status = "expired"
                    session.commit()
                    logger.debug("M12b expired oldest checkpoint for user=%s: %s", user_id, oldest.id)
        except Exception as e:
            logger.warning("M12b _expire_oldest_for_user failed: %s", e)

    async def _expire_for_thread(self, thread_id: str) -> None:
        """过期指定 thread 的所有活跃检查点。"""
        from ..infrastructure.database.models import SOPCheckpoint
        from ..infrastructure.database.session import get_session

        try:
            with get_session() as session:
                active = (
                    session.query(SOPCheckpoint)
                    .filter(
                        SOPCheckpoint.thread_id == thread_id,
                        SOPCheckpoint.status.in_(["pending", "resumed"]),
                    )
                    .all()
                )
                for chk in active:
                    chk.status = "expired"
                if active:
                    session.commit()
                    logger.debug(
                        "M12b expired %d checkpoint(s) for thread=%s",
                        len(active), thread_id,
                    )
            # 清理缓存
            self._active_cache.pop(thread_id, None)
        except Exception as e:
            logger.warning("M12b _expire_for_thread failed: %s", e)
