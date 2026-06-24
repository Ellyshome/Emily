"""ChatArchiveRepository —— 聊天归档查询专用 Repository。

M11: 提供消息附件关联查询 + 对话文件查询 + 单日统计。
"""

import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    Message, MessageAttachment, File, Conversation,
)

logger = logging.getLogger("emily.repo.chat_archive")


class ChatArchiveRepository:
    """聊天归档查询专用 Repository。"""

    # ── 附件 ──

    @staticmethod
    def create_attachment(
        *,
        message_id: str,
        attachment_type: int = 0,
        file_url: str = "",
        file_size: int = 0,
        mime_type: str = "",
        thumbnail_url: str = "",
    ) -> MessageAttachment:
        """创建附件记录。"""
        from ..infrastructure.database.models import _new_uuid
        with get_session() as session:
            att = MessageAttachment(
                id=_new_uuid(),
                message_id=message_id,
                attachment_type=attachment_type,
                file_url=file_url,
                file_size=file_size,
                mime_type=mime_type,
                thumbnail_url=thumbnail_url,
            )
            session.add(att)
            session.flush()
            logger.debug(
                "Attachment created: msg=%s, type=%d, url=%s",
                message_id, attachment_type, file_url[:80],
            )
            return att

    @staticmethod
    def get_attachments_for_message(message_id: str) -> list[dict]:
        """获取消息的所有附件记录。"""
        with get_session() as session:
            rows = (
                session.query(MessageAttachment)
                .filter(MessageAttachment.message_id == message_id)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "attachment_type": r.attachment_type,
                    "file_url": r.file_url,
                    "local_path": r.local_path,
                    "file_size": r.file_size,
                    "mime_type": r.mime_type,
                    "thumbnail_url": r.thumbnail_url,
                    "file_id": r.file_id,
                }
                for r in rows
            ]

    @staticmethod
    def get_files_for_conversation(conversation_id: str) -> list[dict]:
        """列出会话中所有附件文件信息。"""
        with get_session() as session:
            rows = (
                session.query(MessageAttachment, File, Message)
                .join(Message, MessageAttachment.message_id == Message.id)
                .outerjoin(File, MessageAttachment.file_id == File.id)
                .filter(Message.conversation_id == conversation_id)
                .order_by(MessageAttachment.created_at.desc())
                .all()
            )
            results = []
            for att, file, msg in rows:
                results.append({
                    "attachment_id": att.id,
                    "attachment_type": att.attachment_type,
                    "file_url": att.file_url,
                    "local_path": att.local_path,
                    "file_size": att.file_size,
                    "mime_type": att.mime_type,
                    "file_id": file.id if file else None,
                    "file_no": file.file_no if file else None,
                    "filename": file.filename if file else None,
                    "message_id": msg.id,
                    "message_preview": (msg.content or "")[:80],
                    "created_at": str(att.created_at) if att.created_at else None,
                })
            return results
