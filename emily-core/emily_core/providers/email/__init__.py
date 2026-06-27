"""Email 提供者注册表。

导出:
  - EmailProvider / EmailCredentials / EmailAttachment / EmailEnvelope / SendResult: ABC 与数据模型
  - SMTPEmailProvider: SMTP 发送实现（基于 aiosmtplib）
  - IMAPEmailProvider: IMAP 接收实现（基于 aioimaplib）
"""

from .base import EmailProvider, EmailCredentials, EmailAttachment, EmailEnvelope, SendResult
from .smtp_provider import SMTPEmailProvider
from .imap_provider import IMAPEmailProvider

__all__ = [
    "EmailProvider",
    "EmailCredentials",
    "EmailAttachment",
    "EmailEnvelope",
    "SendResult",
    "SMTPEmailProvider",
    "IMAPEmailProvider",
]
