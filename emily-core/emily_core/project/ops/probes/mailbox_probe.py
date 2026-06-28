"""MailboxProbe — 邮箱轮询探针。

复用 EmailService.fetch_orders() 获取 Order 邮件，
白名单过滤 + 幂等去重后写入 ops_mail_audit 表。
严格约束：不得使用 raw imaplib，必须通过 EmailService。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING

from ..probe_base import Probe, ProbeFinding, TickContext

if TYPE_CHECKING:
    from ..config import OpsConfig

logger = logging.getLogger("emily.ops.mailbox")


def _run_async_in_sync(coro, timeout: int = 30):
    """在同步上下文中运行异步协程。

    用于 ProjectAgent._do_tick()（同步方法）桥接异步 EmailService。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # 在已有 event loop 中，使用新线程运行
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=timeout)
    else:
        return asyncio.run(coro)


class MailboxProbe(Probe):
    """邮箱轮询探针。

    复用 EmailService.fetch_orders() 拉取 Order 邮件，
    白名单过滤发件人，mail_uid 幂等去重后写入 DB。
    严格使用 EmailService — 代码中不得出现 import imaplib。
    """

    def __init__(
        self,
        config: "OpsConfig",
        email_service,
        fallback,
        ops_repo,
    ):
        self._config = config
        self._email_service = email_service
        self._fallback = fallback
        self._ops_repo = ops_repo

    def name(self) -> str:
        return "mailbox_probe"

    def enabled(self) -> bool:
        return self._config.mailbox_enabled

    def interval_seconds(self) -> int:
        return 300  # 邮箱轮询间隔 5 分钟

    def run(self, ctx: TickContext) -> list[ProbeFinding]:
        """执行邮箱轮询。桥接异步 EmailService.fetch_orders()。"""
        try:
            return _run_async_in_sync(self._run_async(ctx))
        except Exception as e:
            logger.error("MailboxProbe async bridge failed: %s", e)
            try:
                self._fallback.write_mail_error(ctx, str(e))
            except Exception:
                pass
            return [
                ProbeFinding(
                    finding_type="MAIL_ERROR",
                    severity="WARNING",
                    target_id="mailbox",
                    message=f"邮箱轮询失败: {e}",
                )
            ]

    async def _run_async(self, ctx: TickContext) -> list[ProbeFinding]:
        """异步核心逻辑：拉取邮件 → 白名单过滤 → 幂等去重 → 写入 DB。"""
        from emily_core.providers.email.base import EmailCredentials

        findings: list[ProbeFinding] = []

        # 1. 构造凭证
        creds = EmailCredentials(
            smtp_host="",  # 仅 IMAP 拉取，不需要 SMTP
            smtp_port=465,
            imap_host=self._config.mail_imap_host,
            imap_port=self._config.mail_imap_port,
            username=self._config.mail_username,
            password=self._config.mail_password,
        )

        if not creds.imap_host or not creds.username:
            return []  # 未配置邮箱凭证，跳过

        # 2. 拉取邮件（复用 EmailService）
        try:
            orders = await self._email_service.fetch_orders(creds=creds)
        except Exception as e:
            logger.warning("MailboxProbe: fetch_orders failed: %s", e)
            self._fallback.write_mail_error(ctx, str(e))
            return [
                ProbeFinding(
                    finding_type="MAIL_ERROR",
                    severity="WARNING",
                    target_id="mailbox",
                    message=f"邮件拉取失败: {e}",
                )
            ]

        if not orders:
            return []

        # 3. 解析白名单
        whitelist_raw = self._config.mail_sender_whitelist
        whitelist = (
            [w.strip().lower() for w in whitelist_raw.split(",") if w.strip()]
            if whitelist_raw
            else []
        )

        # 4. 遍历 orders
        for order in orders:
            sender_lower = (order.sender or "").lower()

            # 白名单检查
            if whitelist and sender_lower not in whitelist:
                findings.append(ProbeFinding(
                    finding_type="MAIL_UNAUTHORIZED",
                    severity="WARNING",
                    target_id=order.uid,
                    message=f"未授权发件人: {order.sender}",
                    metadata={
                        "mail_uid": order.uid,
                        "mail_from": order.sender,
                        "mail_subject": order.subject,
                    },
                ))
                continue

            # 幂等去重
            try:
                if self._ops_repo.mail_uid_exists(order.uid):
                    continue
            except Exception:
                # DB 不可用时跳过（不丢数据，已有白名单过滤）
                pass

            # 写入 ops_mail_audit
            try:
                self._ops_repo.save_mail_audit({
                    "tick_id": ctx.tick_id,
                    "mail_uid": order.uid,
                    "mail_from": order.sender,
                    "mail_subject": order.subject,
                    "mail_date": order.date,
                    "command_text": order.body_plain or "",
                    "received_at": ctx.start_time,
                })
            except Exception as e:
                logger.warning("MailboxProbe: save_mail_audit failed for uid=%s: %s", order.uid, e)
                try:
                    self._fallback.write_mail_error(ctx, str(e))
                except Exception:
                    pass

            findings.append(ProbeFinding(
                finding_type="MAIL_COMMAND",
                severity="INFO",
                target_id=order.uid,
                message=f"收到运维邮件: {order.subject or '(无主题)'}",
                metadata={
                    "mail_uid": order.uid,
                    "mail_from": order.sender,
                    "mail_subject": order.subject,
                },
            ))

        return findings
