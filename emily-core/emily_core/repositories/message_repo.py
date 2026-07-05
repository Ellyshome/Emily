"""消息读写抽象层 —— 封装 SQL 细节。

Service 层只调 repo 方法，不碰 SQL。
M11: 增强入站消息填充 + 出站消息写入 + 全文搜索。
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import Message, Conversation
from ..adapters.standard.message import StandardMessage
from ..adapters.standard.route_decision import RouteDecision

logger = logging.getLogger("emily.repo.message")


class MessageRepository:
    """消息 CRUD 操作。"""

    @staticmethod
    def create_from_standard(
        event_id: str,
        msg: StandardMessage,
        decision: RouteDecision,
        status: str = "received",
    ) -> Message:
        """从 StandardMessage 创建消息记录。

        自动查找或创建对应的 Conversation 记录。

        Args:
            event_id: 消息指纹（来自 main.py 去重逻辑）
            msg: 标准化消息对象
            decision: 接管决策
            status: 消息状态
        """
        with get_session() as session:
            # 查找或创建 Conversation
            conv = session.query(Conversation).filter(
                Conversation.im_platform == msg.platform,
                Conversation.conversation_id == msg.conversation_id,
            ).first()

            if conv is None:
                conv = Conversation(
                    im_platform=msg.platform,
                    conversation_type=msg.conversation_type,
                    conversation_id=msg.conversation_id,
                    group_id=msg.group_id,
                    title=msg.group_name or msg.group_id or msg.sender_name,
                    takeover_mode=decision.mode,
                )
                session.add(conv)
                session.flush()  # 获取 conv.id

            # 创建 Message
            # M11: 填充多模态字段
            _msg_type = getattr(msg, "msg_type", 1) or 1
            _attachments_list = getattr(msg, "attachments", []) or []
            _file_url = _attachments_list[0].get("url", "") if _attachments_list else ""
            _attachments_json = json.dumps(_attachments_list, ensure_ascii=False) if _attachments_list else "[]"
            _receiver_id = getattr(msg, "receiver_id", "") or ""

            db_msg = Message(
                event_id=event_id,
                message_uid=msg.message_id or None,
                conversation_id=conv.id,
                sender_im_id=msg.sender_id,
                sender_name=msg.sender_name,
                content=msg.content or "",
                is_at_bot=msg.is_at_bot,
                takeover=decision.takeover,
                takeover_reason=decision.reason or "",
                status=status,
                # M11: 多模态字段
                msg_type=_msg_type,
                file_url=_file_url,
                attachments=_attachments_json,
                receiver_id=_receiver_id,
                group_id=msg.group_id,
            )
            session.add(db_msg)
            session.flush()

            logger.info(
                "Message saved: id=%s, sender=%s, at_bot=%s, takeover=%s",
                db_msg.id, msg.sender_id, msg.is_at_bot, decision.takeover,
            )
            return db_msg

    @staticmethod
    def _resolve_conversation_id(session, business_conv_id: str) -> str:
        """将业务 conversation_id 解析为 conversations.id (UUID)。

        如果对应 Conversation 不存在，自动创建一个。
        用于 create_outbound 等需要 FK 引用 conversations.id 的场景。
        """
        conv = (
            session.query(Conversation)
            .filter(Conversation.conversation_id == business_conv_id)
            .first()
        )
        if conv:
            return conv.id

        # 自动创建：出站消息可能在入站消息之前不存在 Conversation
        conv = Conversation(
            im_platform="",
            conversation_type="private",
            conversation_id=business_conv_id,
            takeover_mode="collaborate",
        )
        session.add(conv)
        session.flush()
        return conv.id

    @staticmethod
    def get_by_event_id(event_id: str) -> Optional[Message]:
        """按事件指纹查找（去重用）。"""
        with get_session() as session:
            return session.query(Message).filter(Message.event_id == event_id).first()

    @staticmethod
    def mark_processed(message_id: str) -> None:
        """标记消息已处理。"""
        with get_session() as session:
            msg = session.query(Message).filter(Message.id == message_id).first()
            if msg:
                msg.status = "processed"
                msg.processed_at = datetime.now(timezone.utc).isoformat()
                logger.info("Message marked processed: id=%s", message_id)

    @staticmethod
    def list_by_conversation(conversation_id: str, limit: int = 100) -> List[Message]:
        """按会话查历史消息。"""
        with get_session() as session:
            return (
                session.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def update_sender_user_id(message_id: str, user_id: str) -> None:
        """更新消息的 sender_user_id（用户绑定后回填）。"""
        with get_session() as session:
            msg = session.query(Message).filter(Message.id == message_id).first()
            if msg:
                msg.sender_user_id = user_id

    # ── M3 新增：路由结果回填 ──

    @staticmethod
    def update_intent(message_id: str, intent: str) -> None:
        """更新消息的意图分类（Router 回填）。"""
        with get_session() as session:
            msg = session.query(Message).filter(Message.id == message_id).first()
            if msg:
                msg.intent = intent
                logger.info("Message intent updated: id=%s, intent=%s", message_id, intent)

    @staticmethod
    def update_project_id(message_id: str, project_id: str) -> None:
        """更新消息的 project_id（Router 回填）。"""
        with get_session() as session:
            msg = session.query(Message).filter(Message.id == message_id).first()
            if msg:
                msg.project_id = project_id
                logger.info("Message project_id updated: id=%s, project_id=%s", message_id, project_id)

    # ── M5 查询 ──

    @staticmethod
    def query_messages(
        *,
        project_id: str | None = None,
        time_range: str = "all",
        conversation_id: str | None = None,
        sender_name: str | None = None,
        keyword: str | None = None,
        intent: str | None = None,
        limit: int = 50,
    ) -> list[Message]:
        """多维度消息查询。按时间倒序，支持项目/会话/发送者/关键词/意图过滤。"""
        from datetime import datetime, timezone
        from ..repositories.task_repo import _resolve_time_start

        with get_session() as session:
            q = session.query(Message)

            if project_id:
                q = q.filter(Message.project_id == project_id)
            if conversation_id:
                q = q.filter(Message.conversation_id == conversation_id)
            if sender_name:
                q = q.filter(Message.sender_name.like(f"%{sender_name}%"))
            if keyword:
                q = q.filter(Message.content.like(f"%{keyword}%"))
            if intent:
                q = q.filter(Message.intent == intent)

            if time_range != "all":
                now = datetime.now(timezone.utc)
                start = _resolve_time_start(time_range, now)
                if start:
                    q = q.filter(Message.created_at >= start.isoformat())

            q = q.order_by(Message.created_at.desc()).limit(limit)
            return q.all()

    @staticmethod
    def get_active_conversations(
        time_range: str = "all", limit: int = 20
    ) -> list[dict]:
        """活跃会话排行（按消息数倒序）。

        Returns:
            [{conversation_id, title, count, last_active}, ...]
        """
        from datetime import datetime, timezone
        from ..repositories.task_repo import _resolve_time_start
        from sqlalchemy import func

        with get_session() as session:
            q = session.query(
                Message.conversation_id,
                func.count(Message.id).label("count"),
                func.max(Message.created_at).label("last_active"),
            )

            if time_range != "all":
                now = datetime.now(timezone.utc)
                start = _resolve_time_start(time_range, now)
                if start:
                    q = q.filter(Message.created_at >= start.isoformat())

            q = (
                q.group_by(Message.conversation_id)
                .order_by(func.count(Message.id).desc())
                .limit(limit)
            )
            rows = q.all()

            results = []
            for row in rows:
                # 尝试获取会话标题
                conv = session.query(Conversation).filter(
                    Conversation.id == row.conversation_id
                ).first()
                results.append({
                    "conversation_id": row.conversation_id,
                    "title": conv.title if conv else "未知会话",
                    "count": row.count,
                    "last_active": row.last_active,
                })
            return results

    @staticmethod
    def count_recent_turns(
        conversation_id: str, ttl_seconds: int = 600
    ) -> int:
        """统计指定会话最近 N 秒内的消息轮数（M8b 前导消息评估用）。

        一次"轮" = 一条用户消息（不限方向也可，但这里用总消息数近似）。

        Args:
            conversation_id: 会话的 conversation_id（非 DB id）
            ttl_seconds: 时间窗口（秒），默认 600（10 分钟）

        Returns:
            int: 最近时间窗口内的消息数
        """
        from datetime import datetime, timezone, timedelta

        with get_session() as session:
            cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)).isoformat()
            count = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conversation_id,
                    Message.created_at >= cutoff,
                )
                .count()
            )
            return count

    @staticmethod
    def search_content(
        keyword: str, time_range: str = "all", limit: int = 50
    ) -> list[Message]:
        """按关键词搜索消息内容（PostgreSQL ILIKE）。"""
        from datetime import datetime, timezone
        from ..repositories.task_repo import _resolve_time_start

        with get_session() as session:
            q = session.query(Message).filter(
                Message.content.like(f"%{keyword}%")
            )

            if time_range != "all":
                now = datetime.now(timezone.utc)
                start = _resolve_time_start(time_range, now)
                if start:
                    q = q.filter(Message.created_at >= start.isoformat())

            q = q.order_by(Message.created_at.desc()).limit(limit)
            return q.all()

    # ── M11: 出站消息写入 ──

    @staticmethod
    def create_outbound(
        conversation_id: str,
        content: str,
        *,
        sender_im_id: str = "",
        direction: str = "agent_to_user",
        message_type: str = "",
        reply_to_message_id: str | None = None,
    ) -> Message:
        """创建出站消息记录（Agent回复/前导消息）。

        conversation_id 参数是业务标识符（非 conversations.id UUID），
        方法内部自动解析为 conversations.id 以避免 FK 违规。
        """
        with get_session() as session:
            # 解析 business conversation_id → conversations.id (UUID)
            conv_uuid = MessageRepository._resolve_conversation_id(
                session, conversation_id
            )

            db_msg = Message(
                event_id=f"outbound_{_new_uuid_short()}",
                conversation_id=conv_uuid,
                sender_im_id=sender_im_id,
                sender_name="Emy",
                content=content,
                direction=direction,
                message_type=message_type,
                status="sent",
                takeover=True,
                is_at_bot=False,
            )
            if reply_to_message_id:
                db_msg.message_uid = reply_to_message_id
            session.add(db_msg)
            session.flush()
            return db_msg

    # ── M11: 完整对话历史查询 ──

    @staticmethod
    def list_by_conversation_full(
        conversation_id: str,
        limit: int = 100,
        offset: int = 0,
        include_progress: bool = False,
    ) -> list[Message]:
        """完整对话历史（入站+出站，时间正序）。"""
        with get_session() as session:
            q = session.query(Message).filter(
                Message.conversation_id == conversation_id
            )
            if not include_progress:
                q = q.filter(Message.message_type != "progress")
            q = q.order_by(Message.created_at.asc()).offset(offset).limit(limit)
            return q.all()

    # ── M11: 全文搜索 ──

    @staticmethod
    def search_messages_fulltext(
        keyword: str,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        time_range: str = "all",
        limit: int = 50,
    ) -> list[Message]:
        """全文搜索消息内容（PostgreSQL ILIKE 大小写不敏感）。"""
        from ..repositories.task_repo import _resolve_time_start

        with get_session() as session:
            q = session.query(Message)
            q = q.filter(Message.content.ilike(f"%{keyword}%"))
            if conversation_id:
                q = q.filter(Message.conversation_id == conversation_id)
            if user_id:
                q = q.filter(Message.sender_user_id == user_id)
            if time_range != "all":
                now = datetime.now(timezone.utc)
                start = _resolve_time_start(time_range, now)
                if start:
                    q = q.filter(Message.created_at >= start.isoformat())
            q = q.order_by(Message.created_at.desc()).limit(limit)
            return q.all()

    # ── M11: 跨会话用户历史 ──

    @staticmethod
    def get_user_history(
        user_id: str,
        platform: str | None = None,
        limit: int = 50,
    ) -> list[Message]:
        """查询指定用户的历史消息（跨会话）。"""
        with get_session() as session:
            q = session.query(Message).filter(
                Message.sender_user_id == user_id
            )
            if platform:
                q = q.join(Conversation).filter(
                    Conversation.im_platform == platform
                )
            q = q.order_by(Message.created_at.desc()).limit(limit)
            return q.all()

    # ── M11: 活跃会话统计 ──

    @staticmethod
    def get_daily_stats(date_str: str) -> dict:
        """单日消息统计。"""
        from sqlalchemy import func
        with get_session() as session:
            total = (
                session.query(func.count(Message.id))
                .filter(Message.created_at.like(f"{date_str}%"))
                .scalar()
            ) or 0
            conv_q = (
                session.query(
                    Message.conversation_id,
                    func.count(Message.id).label("count"),
                )
                .filter(Message.created_at.like(f"{date_str}%"))
                .group_by(Message.conversation_id)
                .order_by(func.count(Message.id).desc())
                .limit(20)
                .all()
            )
            return {
                "total": total,
                "by_conversation": [
                    {"conversation_id": cid, "count": cnt}
                    for cid, cnt in conv_q
                ],
            }


    @staticmethod
    def get_recent_by_user_id(user_id: str, limit: int = 20) -> list[dict]:
        """获取用户最近入站消息（跨会话，OpenAI 格式）。

        Args:
            user_id: 用户 ID
            limit: 返回条数上限（默认 20）

        Returns:
            [{role, content, time, sender_name}, ...] 按时间正序
        """
        with get_session() as session:
            rows = (
                session.query(
                    Message.content,
                    Message.created_at,
                    Message.direction,
                    Message.sender_name,
                )
                .filter(Message.sender_user_id == user_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )

            turns = []
            for row in reversed(rows):  # 正序排列
                role = "user" if row.direction == "user_to_agent" else "agent"
                turns.append({
                    "role": role,
                    "time": row.created_at or "",
                    "content": row.content or "",
                    "sender_name": row.sender_name or "",
                })
            return turns


def _new_uuid_short() -> str:
    import uuid
    return str(uuid.uuid4())[:12]
