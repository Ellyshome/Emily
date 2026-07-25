"""IMAP 邮件接收 Provider —— 基于 aioimaplib。

设计原则：
  - 每次调用独立建连、操作、断开。不维护连接池。
  - 内部不抛异常：所有错误路径返回空列表或 False。
  - 参照 pgvector_provider.py 的实现模式。
"""

from __future__ import annotations

import logging
import re
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime
from typing import Optional

import aioimaplib

from .base import (
    EmailAttachment,
    EmailCredentials,
    EmailEnvelope,
    EmailProvider,
)

logger = logging.getLogger("emily.email.imap")

# 匹配 IMAP SEARCH 返回的 UID 列表（数字序列）
_UID_RE = re.compile(r"\d+")


class IMAPEmailProvider(EmailProvider):
    """IMAP 邮件接收器 —— 基于 aioimaplib。"""

    def __init__(self):
        self._available: Optional[bool] = None  # 三态：None=未检测 / True / False

    # ── is_available ──

    async def is_available(self) -> bool:
        """检查 aioimaplib 是否可用。"""
        if self._available is not None:
            return self._available
        try:
            import aioimaplib
            self._available = True
        except ImportError:
            self._available = False
        return self._available

    # ── fetch_inbox ──

    async def fetch_inbox(
        self,
        creds: EmailCredentials,
        subject_filter: Optional[str] = None,
        from_self: bool = False,
        unread_only: bool = True,
        limit: int = 20,
    ) -> list[EmailEnvelope]:
        """获取收件箱邮件列表。"""
        if not creds.username or not creds.password:
            logger.warning("IMAP fetch_inbox: 凭证不完整")
            return []

        masked_user = _mask_email(creds.username)
        try:
            imap = aioimaplib.IMAP4_SSL(
                host=creds.imap_host,
                port=creds.imap_port,
                timeout=15,
            )
            await imap.wait_hello_from_server()

            await imap.login(creds.username, creds.password)
            logger.debug("IMAP login ok: user=%s", masked_user)

            await imap.select("INBOX")

            # 构造 SEARCH 命令
            criteria = []
            if unread_only:
                criteria.append("UNSEEN")
            if subject_filter:
                criteria.append(f'SUBJECT "{subject_filter}"')
            if from_self:
                # 搜索 From 和 To 都是自己
                criteria.append(f"FROM {creds.username}")
                criteria.append(f"TO {creds.username}")

            search_cmd = " ".join(criteria) if criteria else "ALL"
            logger.debug("IMAP SEARCH: %s", search_cmd)

            # QQ IMAP 不支持 UID SEARCH，使用常规 SEARCH 获取序列号
            if criteria:
                search_resp = await imap.search(None, *criteria)
            else:
                search_resp = await imap.search(None, "ALL")

            if search_resp.result != "OK":
                logger.warning("IMAP SEARCH failed: result=%s", search_resp.result)
                await imap.logout()
                return []

            seq_nums = _parse_uids(search_resp.lines)
            if not seq_nums:
                await imap.logout()
                return []

            # 取最新的 N 封，转换为 UID
            seq_nums = seq_nums[-limit:] if len(seq_nums) > limit else seq_nums
            seq_set = ",".join(seq_nums)

            # 用序列号获取 UID
            uid_resp = await imap.fetch(seq_set, "(UID)")
            uid_list = _extract_uids_from_fetch(uid_resp.lines)

            if not uid_list:
                await imap.logout()
                return []

            uid_set = ",".join(uid_list)

            # 用 UID 获取完整邮件
            fetch_resp = await imap.uid("FETCH", uid_set, "(RFC822)")
            if fetch_resp.result != "OK":
                logger.warning("IMAP FETCH failed: result=%s", fetch_resp.result)
                await imap.logout()
                return []

            envelopes = _parse_fetch_response(fetch_resp.lines, subject_filter, uid_list)

            await imap.logout()
            logger.info("IMAP fetch_inbox: %d envelopes, user=%s", len(envelopes), masked_user)
            return envelopes

        except aioimaplib.Abort as e:
            logger.error("IMAP abort: user=%s error=%s", masked_user, e)
            return []
        except (OSError, TimeoutError) as e:
            logger.warning("IMAP connection failed: host=%s:%d error=%s",
                          creds.imap_host, creds.imap_port, e)
            return []
        except Exception as e:
            logger.error("IMAP fetch_inbox unknown error: user=%s error=%s",
                        masked_user, e, exc_info=True)
            return []

    # ── fetch_orders ──

    async def fetch_orders(
        self,
        creds: EmailCredentials,
        since_uid: Optional[str] = None,
    ) -> list[EmailEnvelope]:
        """获取 Order 邮件（便捷方法）。"""
        envelopes = await self.fetch_inbox(
            creds=creds,
            subject_filter="order",
            from_self=True,
            unread_only=True,
            limit=50,
        )

        # 标记 is_order + 增量过滤
        for env in envelopes:
            env.is_order = True

        if since_uid and envelopes:
            # 跳过 UID <= since_uid 的邮件（增量拉取）
            try:
                since_uid_int = int(since_uid)
                envelopes = [e for e in envelopes if int(e.uid) > since_uid_int]
            except (ValueError, TypeError):
                pass

        return envelopes

    # ── mark_read ──

    async def mark_read(
        self,
        creds: EmailCredentials,
        uids: list[str],
    ) -> bool:
        """将指定邮件标记为已读。"""
        if not uids:
            return True
        if not creds.username or not creds.password:
            return False

        masked_user = _mask_email(creds.username)
        try:
            imap = aioimaplib.IMAP4_SSL(
                host=creds.imap_host,
                port=creds.imap_port,
                timeout=15,
            )
            await imap.wait_hello_from_server()
            await imap.login(creds.username, creds.password)
            await imap.select("INBOX")

            uid_set = ",".join(uids)
            result, _ = await imap.uid("STORE", uid_set, "+FLAGS", "\\Seen")
            await imap.logout()

            ok = result == "OK"
            logger.debug("IMAP mark_read: %d uids, ok=%s, user=%s", len(uids), ok, masked_user)
            return ok

        except Exception as e:
            logger.error("IMAP mark_read failed: uids=%s error=%s", uids, e, exc_info=True)
            return False

    # ── send（IMAP 不支持）──

    async def send(self, *args, **kwargs):
        raise NotImplementedError("IMAPEmailProvider 不支持发送，请使用 SMTPEmailProvider")


# ══════════════════════════════════════════════════════════════════════════════
# 解析工具
# ══════════════════════════════════════════════════════════════════════════════


def _parse_uids(data) -> list[str]:
    """从 IMAP SEARCH/FETCH 返回行中提取数字列表。"""
    if not data or not isinstance(data, list):
        return []
    # data[0] 形如 b"1 2 3 5 8" 或 b"* SEARCH 1 2 3"
    for item in data:
        if isinstance(item, (bytes, bytearray)):
            raw = item.decode("utf-8", errors="replace")
            nums = _UID_RE.findall(raw)
            if nums:
                return nums
        elif isinstance(item, str):
            nums = _UID_RE.findall(item)
            if nums:
                return nums
    return []


def _extract_uids_from_fetch(fetch_lines) -> list[str]:
    """从 FETCH (UID) 响应中提取 UID 列表（保持顺序）。"""
    uids = []
    for line in fetch_lines:
        if isinstance(line, (bytes, bytearray)):
            text = line.decode("utf-8", errors="replace")
        elif isinstance(line, tuple):
            # tuple 中的 bytes
            for item in line:
                if isinstance(item, (bytes, bytearray)):
                    text = item.decode("utf-8", errors="replace")
                    break
            else:
                continue
        else:
            text = str(line)

        # 匹配 "UID 123" 模式
        import re
        match = re.search(r"UID\s+(\d+)", text, re.IGNORECASE)
        if match:
            uids.append(match.group(1))

    return uids if uids else _parse_uids(fetch_lines)


def _parse_fetch_response(fetch_lines: list, subject_filter: Optional[str] = None, uid_list: list = None) -> list[EmailEnvelope]:
    """解析 IMAP FETCH 返回的 RFC822 数据为 EmailEnvelope 列表。

    aioimaplib fetch 返回格式：
        [(b'...', ...), ...]  一系列行。每封邮件之间由单独的 ")" 行分隔。
    """
    envelopes = []
    filter_lower = subject_filter.lower() if subject_filter else None

    # 提取所有 RFC822 body 块
    emails_raw = _split_fetch_to_emails(fetch_lines)

    for i, raw_email in enumerate(emails_raw):
        try:
            msg = message_from_bytes(raw_email, policy=policy.default)
            env = _envelope_from_message(msg, filter_lower)
            if env is not None:
                # 按顺序分配 UID
                if uid_list and i < len(uid_list):
                    env.uid = uid_list[i]
                envelopes.append(env)
        except Exception as e:
            logger.warning("Failed to parse email: %s", e)

    return envelopes


def _split_fetch_to_emails(fetch_data: list) -> list[bytes]:
    """将 IMAP FETCH 返回行列表拆分为每封邮件的原始字节。

    aioimaplib IMAP4_SSL fetch 返回格式（典型）：
      - 即使指定 UID FETCH，返回的数据仍然包含序号行
      - 数据行是 bytes 或包含 bytes 的 tuple
    """
    emails = []
    current = b""

    for item in fetch_data:
        chunk = b""
        if isinstance(item, (bytes, bytearray)):
            chunk = bytes(item)
        elif isinstance(item, tuple):
            for sub in reversed(item):
                if isinstance(sub, (bytes, bytearray)):
                    chunk = bytes(sub)
                    break

        if not chunk:
            continue

        # 检测是否是新的 FETCH 结果开始
        stripped = chunk.strip()
        # 新邮件开始的标志：包含 "FETCH" 和 "RFC822"
        if b"FETCH" in stripped and b"RFC822" in stripped:
            if current:
                emails.append(current)
            current = b""
            # 跳过 FETCH 头和大小行，后面是邮件体
            continue

        current += chunk

    if current:
        emails.append(current)

    return emails


def _envelope_from_message(msg, filter_lower: Optional[str] = None) -> Optional[EmailEnvelope]:
    """从 email.message.Message 构造 EmailEnvelope。"""
    subject = msg.get("Subject", "") or ""
    if filter_lower and filter_lower not in subject.lower():
        return None

    # 提取 UID（从 message 对象或后续在上层设置）
    uid = msg.get("UID", "") or msg.get("X-UID", "") or ""

    # 提取正文
    body_plain = ""
    body_html = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                continue

            if content_type == "text/plain":
                payload = _decode_payload(part)
                if payload:
                    body_plain += payload
            elif content_type == "text/html" and body_html is None:
                body_html = _decode_payload(part)
    else:
        payload = _decode_payload(msg)
        content_type = msg.get_content_type()
        if content_type == "text/html":
            body_html = payload
        else:
            body_plain = payload or ""

    # 提取附件
    attachments = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in disposition:
            filename = part.get_filename() or "attachment"
            content = part.get_payload(decode=True)
            content_type = part.get_content_type()
            if content:
                attachments.append(EmailAttachment(
                    filename=filename,
                    content=content,
                    content_type=content_type,
                ))

    # 日期
    date = None
    date_str = msg.get("Date", "")
    if date_str:
        try:
            date = parsedate_to_datetime(date_str)
        except Exception as e:
            logger.debug("email date parse failed: %s", e, exc_info=True)

    return EmailEnvelope(
        uid=uid,
        message_id=msg.get("Message-ID", "") or "",
        sender=msg.get("From", "") or "",
        recipient=msg.get("To", "") or "",
        subject=subject,
        body_plain=body_plain.strip(),
        body_html=body_html,
        date=date,
        attachments=attachments,
        is_order=False,
    )


def _decode_payload(part) -> str:
    """解码 MIME part 的 payload 为字符串。"""
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception as e:
        logger.debug("email body decode failed: %s", e, exc_info=True)
        return ""


def _mask_email(email: str) -> str:
    """邮箱地址脱敏。"""
    if "@" not in email:
        return "***"
    user, domain = email.split("@", 1)
    masked_user = user[:2] + "****" if len(user) > 2 else "****"
    return f"{masked_user}@{domain}"
