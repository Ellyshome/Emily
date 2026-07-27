"""AttachmentDownloader —— 入站附件异步下载服务。

消息入库后异步触发：遍历 attachments → 去重（URL 级）→ 下载 →
写 message_attachments 表 + files 表。

不阻塞 Session 主线；失败仅记日志。
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("emily.service.attachment_downloader")


class AttachmentDownloader:
    """入站附件异步下载服务。

    每个附件独立 try/except，单个失败不影响其他。
    依赖 FileManager.store_attachment 做实际下载落盘。
    """

    def __init__(self, file_manager):
        self._fm = file_manager

    async def download_for_message(self, message_id: str, attachments: list[dict]) -> None:
        """异步下载一条消息的所有附件。

        Args:
            message_id: 消息 UUID（messages 表 id）
            attachments: StandardMessage.attachments 列表
                [{url, type, file_name, summary, ...}, ...]
        """
        if not attachments:
            return

        for att in attachments:
            if not isinstance(att, dict):
                continue
            try:
                await self._download_one(message_id, att)
            except Exception as e:
                logger.warning(
                    "Attachment download failed msg=%s url=%s: %s",
                    message_id, str(att.get("url", ""))[:80], e,
                )

    async def _download_one(self, message_id: str, att: dict) -> None:
        """下载单个附件并落盘。"""
        url = att.get("url", "")
        if not url:
            return

        # URL 级去重：检查 message_attachments 表是否已有此 URL
        if self._is_already_downloaded(message_id, url):
            logger.debug("Attachment already downloaded: msg=%s url=%s", message_id, url[:80])
            return

        att_type = att.get("type", 0)
        source_filename = att.get("file_name", "") or att.get("summary", "") or ""
        mime = att.get("mime", "")

        # M5: 规则引擎判定候选 purpose
        from ..services.file_rule_engine import FileRuleEngine
        candidate_purpose = FileRuleEngine.guess_purpose(source_filename, mime)

        # CHAT 不入库（只留 messages.attachments URL）
        if candidate_purpose == "CHAT":
            logger.info("Attachment skipped (CHAT): msg=%s file=%s", message_id, source_filename)
            return

        # 委托 FileManager.store_attachment 下载落盘（带候选 purpose）
        result = await self._fm.store_attachment(
            message_id=message_id,
            url=url,
            attachment_type=att_type,
            source_filename=source_filename,
            purpose=candidate_purpose,
            purpose_confirmed=False,
        )
        if result:
            logger.info(
                "Attachment auto-downloaded: msg=%s file_no=%s purpose=%s",
                message_id, result.get("file_no"), candidate_purpose,
            )

    @staticmethod
    def _is_already_downloaded(message_id: str, url: str) -> bool:
        """URL 级去重：检查 message_attachments 表。"""
        try:
            from ..infrastructure.database.session import get_session
            from ..infrastructure.database.models import MessageAttachment

            with get_session() as session:
                exists = session.query(MessageAttachment).filter(
                    MessageAttachment.message_id == message_id,
                    MessageAttachment.file_url == url,
                ).first()
                return exists is not None
        except Exception as e:
            logger.warning("_is_already_downloaded check failed: %s", e)
            return False
