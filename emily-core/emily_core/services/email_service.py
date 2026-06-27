"""邮箱服务 —— 薄封装层。

组合 SMTPEmailProvider + IMAPEmailProvider，向上层提供统一入口。
不持有状态，不维护凭证，不启动后台任务。

设计原则（需求规格 §4.3）：
  - 纯透传：不做额外交互、缓存、重试、排队
  - 分层一致：Provider → Service → 调用方
"""

from __future__ import annotations

from typing import Optional

from ..providers.email.base import (
    EmailAttachment,
    EmailCredentials,
    EmailEnvelope,
    SendResult,
)
from ..providers.email.smtp_provider import SMTPEmailProvider
from ..providers.email.imap_provider import IMAPEmailProvider


class EmailService:
    """邮箱服务 —— 薄封装，组合 SMTP + IMAP Provider。

    不持有状态，不维护凭证，不启动后台任务。
    """

    def __init__(self, smtp: SMTPEmailProvider, imap: IMAPEmailProvider):
        self._smtp = smtp
        self._imap = imap

    async def send(
        self,
        creds: EmailCredentials,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        attachments: Optional[list[EmailAttachment]] = None,
    ) -> SendResult:
        """发送邮件。"""
        return await self._smtp.send(
            creds=creds,
            to=to,
            subject=subject,
            body=body,
            html=html,
            attachments=attachments,
        )

    async def fetch_inbox(
        self,
        creds: EmailCredentials,
        subject_filter: Optional[str] = None,
        from_self: bool = False,
        unread_only: bool = True,
        limit: int = 20,
    ) -> list[EmailEnvelope]:
        """获取收件箱邮件列表。"""
        return await self._imap.fetch_inbox(
            creds=creds,
            subject_filter=subject_filter,
            from_self=from_self,
            unread_only=unread_only,
            limit=limit,
        )

    async def fetch_orders(
        self,
        creds: EmailCredentials,
        since_uid: Optional[str] = None,
    ) -> list[EmailEnvelope]:
        """获取 Order 邮件。"""
        return await self._imap.fetch_orders(creds=creds, since_uid=since_uid)

    async def mark_read(
        self,
        creds: EmailCredentials,
        uids: list[str],
    ) -> bool:
        """将指定邮件标记为已读。"""
        return await self._imap.mark_read(creds=creds, uids=uids)
