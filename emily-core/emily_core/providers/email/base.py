"""Email Provider 抽象接口 + 数据对象。

定义 Email 模块的公共数据模型和 Provider 接口。
参照 providers/rag/base.py 的 ABC + dataclass 模式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ============ 数据对象 ============


@dataclass
class EmailCredentials:
    """调用者自备的邮箱凭证（模块不存储凭证）。"""

    smtp_host: str          # 如 smtp.qq.com
    smtp_port: int          # 如 465 (SSL)
    imap_host: str          # 如 imap.qq.com
    imap_port: int          # 如 993 (SSL)
    username: str           # 邮箱地址
    password: str           # SMTP/IMAP 授权码（非登录密码）
    use_ssl: bool = True


@dataclass
class EmailAttachment:
    """邮件附件。"""

    filename: str           # 展示文件名
    content: bytes          # 文件内容
    content_type: str       # MIME 类型，如 application/pdf


@dataclass
class EmailEnvelope:
    """一封收到的邮件。"""

    uid: str                # IMAP UID（全局唯一递增），用于标记已读 / 去重
    message_id: str         # RFC 822 Message-ID 头
    sender: str             # From 地址
    recipient: str          # To 地址
    subject: str
    body_plain: str         # 纯文本正文
    body_html: Optional[str] = None   # HTML 正文（如有）
    date: Optional[datetime] = None
    attachments: list[EmailAttachment] = field(default_factory=list)
    is_order: bool = False  # 是否匹配 subject=order 约定


@dataclass
class SendResult:
    """发送结果。"""

    success: bool
    message_id: Optional[str] = None  # SMTP 返回的 Message-ID
    error: Optional[str] = None       # 失败原因字符串（success=False 时有值）


# ============ Provider 抽象 ============


class EmailProvider(ABC):
    """邮件能力提供者抽象 —— 类比 RagProvider（providers/rag/base.py）。

    设计原则：
      - 无状态：不存储凭证、不维护连接池、不持有会话。每次调用传入凭证。
      - 失败即反馈：调用失败直接返回错误信息，不做内部重试。
      - 异步原生：全链路 async，不阻塞事件循环。
    """

    @abstractmethod
    async def send(
        self,
        creds: EmailCredentials,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        attachments: Optional[list[EmailAttachment]] = None,
    ) -> SendResult:
        """发送邮件。

        Args:
            creds: 邮箱凭证
            to: 收件人邮箱地址
            subject: 邮件主题
            body: 邮件正文
            html: True=正文按 HTML 发送，False=纯文本
            attachments: 附件列表（可选）

        Returns:
            SendResult: success + message_id 或 error
        """
        ...

    @abstractmethod
    async def fetch_inbox(
        self,
        creds: EmailCredentials,
        subject_filter: Optional[str] = None,
        from_self: bool = False,
        unread_only: bool = True,
        limit: int = 20,
    ) -> list[EmailEnvelope]:
        """获取收件箱邮件列表。

        Args:
            creds: 邮箱凭证
            subject_filter: 主题过滤关键词（None=不过滤）
            from_self: True=只查自己发给自己的邮件
            unread_only: True=只查未读
            limit: 最多返回数量

        Returns:
            list[EmailEnvelope]: 邮件列表（可能为空）
        """
        ...

    @abstractmethod
    async def fetch_orders(
        self,
        creds: EmailCredentials,
        since_uid: Optional[str] = None,
    ) -> list[EmailEnvelope]:
        """获取 Order 邮件。

        等价于 fetch_inbox(creds, subject_filter="order", from_self=True, unread_only=True)。
        返回的 Envelope 已设 is_order=True。

        Args:
            creds: 邮箱凭证
            since_uid: 起始 UID（增量拉取，None=全部）

        Returns:
            list[EmailEnvelope]: Order 邮件列表（可能为空）
        """
        ...

    @abstractmethod
    async def mark_read(
        self,
        creds: EmailCredentials,
        uids: list[str],
    ) -> bool:
        """将指定邮件标记为已读。

        Args:
            creds: 邮箱凭证
            uids: 要标记的 IMAP UID 列表

        Returns:
            bool: 是否全部成功
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """检查 Provider 是否可用（三态缓存模式）。"""
        ...
