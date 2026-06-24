"""ChatArchiveService —— 聊天归档业务服务。

M11: 统一管理入站消息增强写入、出站回复存档、前导消息存档、附件关联。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..repositories.message_repo import MessageRepository
from ..repositories.chat_archive_repo import ChatArchiveRepository
from ..adapters.standard.message import StandardMessage
from ..adapters.standard.route_decision import RouteDecision
from ..infrastructure.database.models import Message

logger = logging.getLogger("emily.service.chat_archive")


class ChatArchiveService:
    """聊天归档业务服务。

    职责：记录入站消息（增强版）、记录出站回复/前导消息、关联附件、提供查询。
    """

    def __init__(self, storage_root: str = ""):
        self._storage_root = storage_root

    # ── 写入 ──

    def record_inbound_message(
        self,
        event_id: str,
        msg: StandardMessage,
        decision: RouteDecision,
    ) -> Message:
        """记录入站消息（增强版，含多模态字段）。"""
        return MessageRepository.create_from_standard(event_id, msg, decision)

    def record_outbound_reply(
        self,
        conversation_id: str,
        content: str,
        *,
        sender_im_id: str = "",
        reply_to_message_id: str | None = None,
    ) -> Message:
        """记录出站回复（direction="agent_to_user"）。"""
        return MessageRepository.create_outbound(
            conversation_id=conversation_id,
            content=content,
            sender_im_id=sender_im_id,
            direction="agent_to_user",
            message_type="",
            reply_to_message_id=reply_to_message_id,
        )

    def record_progress_message(
        self,
        conversation_id: str,
        content: str,
        sender_im_id: str = "",
    ) -> Message:
        """记录前导消息（direction="agent_to_user", message_type="progress"）。"""
        return MessageRepository.create_outbound(
            conversation_id=conversation_id,
            content=content,
            sender_im_id=sender_im_id,
            direction="agent_to_user",
            message_type="progress",
        )

    # ── 附件 ──

    def add_attachments(self, message_id: str, attachments: list[dict]) -> int:
        """为消息批量添加附件记录。返回添加数量。"""
        if not attachments:
            return 0
        count = 0
        for att in attachments:
            try:
                ChatArchiveRepository.create_attachment(
                    message_id=message_id,
                    attachment_type=att.get("type", 0),
                    file_url=att.get("url", ""),
                    file_size=att.get("file_size", 0),
                    mime_type=att.get("mime_type", ""),
                    thumbnail_url=att.get("thumb", ""),
                )
                count += 1
            except Exception as e:
                logger.warning("Failed to add attachment for message %s: %s", message_id, e)
        return count

    # ── 查询 ──

    def get_conversation_history(
        self,
        conversation_id: str,
        limit: int = 100,
        offset: int = 0,
        include_progress: bool = False,
    ) -> list[Message]:
        """查询完整对话历史（入站+出站，按时间正序）。"""
        return MessageRepository.list_by_conversation_full(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            include_progress=include_progress,
        )

    def get_user_history(
        self,
        user_id: str,
        platform: str | None = None,
        limit: int = 50,
    ) -> list[Message]:
        """查询指定用户的历史消息（跨会话）。"""
        return MessageRepository.get_user_history(
            user_id=user_id,
            platform=platform,
            limit=limit,
        )

    def search_messages(
        self,
        keyword: str,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        time_range: str = "all",
        limit: int = 50,
    ) -> list[Message]:
        """全文搜索消息。"""
        return MessageRepository.search_messages_fulltext(
            keyword=keyword,
            conversation_id=conversation_id,
            user_id=user_id,
            time_range=time_range,
            limit=limit,
        )

    def get_attachments_for_message(self, message_id: str) -> list[dict]:
        """获取消息的所有附件记录。"""
        return ChatArchiveRepository.get_attachments_for_message(message_id)

    def get_files_for_conversation(self, conversation_id: str) -> list[dict]:
        """列出会话中所有附件文件信息。"""
        return ChatArchiveRepository.get_files_for_conversation(conversation_id)

    def get_daily_stats(self, date_str: str) -> dict:
        """单日消息统计。"""
        return MessageRepository.get_daily_stats(date_str)
