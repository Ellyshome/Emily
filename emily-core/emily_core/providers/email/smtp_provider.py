"""SMTP 邮件发送 Provider —— 基于 aiosmtplib。

设计原则：
  - 每次调用独立建连、发送、断开。不维护连接池。
  - 内部不抛异常：所有错误路径返回 SendResult(success=False, error=...)
  - 参照 pgvector_provider.py 的实现模式（is_available 三态缓存 + 结构化日志）
"""

from __future__ import annotations

import logging
import uuid
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders as email_encoders
from email.utils import formatdate
from typing import Optional

import aiosmtplib
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPResponseException,
    SMTPTimeoutError,
)

from .base import (
    EmailAttachment,
    EmailCredentials,
    EmailProvider,
    SendResult,
)

logger = logging.getLogger("emily.email.smtp")


class SMTPEmailProvider(EmailProvider):
    """SMTP 邮件发送器 —— 基于 aiosmtplib，支持 SSL/TLS/STARTTLS。"""

    def __init__(self):
        self._available: Optional[bool] = None  # 三态：None=未检测 / True / False

    # ── is_available ──

    async def is_available(self) -> bool:
        """检查 aiosmtplib 是否可用（仅检查依赖导入，不建连）。"""
        if self._available is not None:
            return self._available
        try:
            import aiosmtplib
            self._available = True
        except ImportError:
            self._available = False
        return self._available

    # ── send ──

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
        # 1. 快速校验
        if not creds.username or not creds.password:
            return SendResult(success=False, error="AUTH_FAILED: 凭证不完整")
        if not to or "@" not in to:
            return SendResult(success=False, error=f"INVALID_RECIPIENT: {to}")

        # 2. 构造 MIME 邮件
        try:
            msg = self._build_mime(
                from_addr=creds.username,
                to=to,
                subject=subject,
                body=body,
                html=html,
                attachments=attachments,
            )
        except Exception as e:
            logger.error("MIME construction failed: %s", e, exc_info=True)
            return SendResult(success=False, error=f"MIME_ERROR: {e}")

        # 3. 发送
        masked_user = _mask_email(creds.username)
        try:
            smtp = aiosmtplib.SMTP(
                hostname=creds.smtp_host,
                port=creds.smtp_port,
                use_tls=creds.use_ssl,
                timeout=15,
            )
            await smtp.connect()
            logger.debug("SMTP connected: host=%s port=%d user=%s",
                         creds.smtp_host, creds.smtp_port, masked_user)

            await smtp.login(creds.username, creds.password)
            logger.debug("SMTP login ok: user=%s", masked_user)

            errors, response_msg = await smtp.send_message(msg)
            await smtp.quit()

            if errors:
                # 部分收件人失败（如 BCC 问题），但主发送可能成功
                error_details = "; ".join(f"{addr}: {resp}" for addr, resp in errors.items())
                logger.warning("SMTP send partial errors: %s", error_details)

            logger.info("Email sent: from=%s to=%s subject=%.80r",
                        masked_user, _mask_email(to), subject)
            # aiosmtplib 5.x 返回 tuple(errors_dict, response_message)
            # 提取 Message-ID（从邮件头的 msg['Message-ID'] 或留空）
            msg_id = msg.get("Message-ID", "") if hasattr(msg, "get") else ""
            return SendResult(
                success=True,
                message_id=msg_id,
            )

        except SMTPAuthenticationError as e:
            logger.warning("SMTP auth failed: user=%s error=%s", masked_user, e)
            return SendResult(success=False, error="AUTH_FAILED")
        except (SMTPConnectError, SMTPTimeoutError, OSError) as e:
            logger.warning("SMTP connection failed: host=%s:%d error=%s",
                          creds.smtp_host, creds.smtp_port, e)
            return SendResult(success=False, error="CONNECTION_TIMEOUT")
        except SMTPResponseException as e:
            # QQ 限流等会返回特定错误码
            if e.code == 550 or e.code == 554:
                logger.warning("SMTP rejected: code=%d msg=%s", e.code, e.message)
                return SendResult(success=False, error=f"RATE_LIMITED: {e.message}")
            logger.warning("SMTP response error: code=%d msg=%s", e.code, e.message)
            return SendResult(success=False, error=f"SMTP_ERROR_{e.code}: {e.message}")
        except Exception as e:
            logger.error("SMTP send unknown error: %s", e, exc_info=True)
            return SendResult(success=False, error=str(e))

    # ── 未实现的方法（SMTP 只做发送）──

    async def fetch_inbox(self, *args, **kwargs):
        raise NotImplementedError("SMTPEmailProvider 不支持收件，请使用 IMAPEmailProvider")

    async def fetch_orders(self, *args, **kwargs):
        raise NotImplementedError("SMTPEmailProvider 不支持收件，请使用 IMAPEmailProvider")

    async def mark_read(self, *args, **kwargs):
        raise NotImplementedError("SMTPEmailProvider 不支持标记已读，请使用 IMAPEmailProvider")

    # ── 内部方法 ──

    def _build_mime(
        self,
        from_addr: str,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        attachments: Optional[list[EmailAttachment]] = None,
    ) -> MIMEMultipart:
        """构造 MIME multipart 邮件。"""
        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = f"<{uuid.uuid4()}@emily>"

        # 正文
        subtype = "html" if html else "plain"
        msg.attach(MIMEText(body, subtype, "utf-8"))

        # 附件
        if attachments:
            for att in attachments:
                part = MIMEBase(*att.content_type.split("/", 1), name=att.filename)
                part.set_payload(att.content)
                email_encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=("utf-8", "", att.filename),
                )
                msg.attach(part)

        return msg


# ── 日志脱敏工具 ──


def _mask_email(email: str) -> str:
    """邮箱地址脱敏：前 2 位 + **** + @域名。"""
    if "@" not in email:
        return "***"
    user, domain = email.split("@", 1)
    masked_user = user[:2] + "****" if len(user) > 2 else "****"
    return f"{masked_user}@{domain}"
