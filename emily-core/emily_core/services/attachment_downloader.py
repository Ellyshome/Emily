"""AttachmentDownloader —— 入站附件下载与解析服务。

消息入库后，进入会话前同步解析当前消息附件：遍历 attachments → 去重（URL 级）
→ 下载 → 写 message_attachments 表 + files 表，并返回可供 Agent 使用的 manifest。

manifest 条目：
  {file_name, type, file_no, local_path, file_size, status}
status: ready / skipped_chat / already_downloaded / failed / missing_url / timeout

说明：演示链路要求 Agent 能看到“当前消息带了哪些文件”，因此 EmilyCore 在进入会话前
await 本服务；下载失败不阻断会话，只把失败状态写进 manifest。
"""

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("emily.service.attachment_downloader")

_TYPE_LABELS = {2: "图片", 3: "文件", 4: "语音", 5: "视频", 6: "卡片"}


class AttachmentDownloader:
    """入站附件下载与 manifest 生成。"""

    def __init__(self, file_manager):
        self._fm = file_manager

    async def download_for_message(
        self, message_id: str, attachments: list[dict], timeout_seconds: float = 90.0,
    ) -> list[dict]:
        """解析一条消息的所有附件，返回 manifest 列表。

        Args:
            message_id: 消息 UUID（messages 表 id）
            attachments: StandardMessage.attachments 列表
                [{url, type, file_name, summary, ...}, ...]
            timeout_seconds: 单条消息附件解析总超时
        """
        if not attachments:
            return []

        async def _run() -> list[dict]:
            result: list[dict] = []
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                try:
                    item = await self._download_one(message_id, att)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "Attachment download failed msg=%s url=%s: %s",
                        message_id, str(att.get("url", ""))[:80], e,
                    )
                    item = self._entry(
                        att, status="failed", error=str(e)[:200],
                    )
                if item:
                    result.append(item)
            return result

        try:
            return await asyncio.wait_for(_run(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("Attachment download timeout msg=%s (%ss)", message_id, timeout_seconds)
            return [
                self._entry(a, status="timeout")
                for a in attachments if isinstance(a, dict)
            ]

    async def _download_one(self, message_id: str, att: dict) -> dict | None:
        """下载单个附件并落盘，返回 manifest 条目。"""
        url = str(att.get("url") or "")
        if not url:
            return self._entry(att, status="missing_url")

        att_type = att.get("type", 0)
        source_filename = att.get("file_name", "") or att.get("summary", "") or ""
        mime = att.get("mime", "")

        # URL 级去重：已有记录时尝试回填 file_no / 本地路径
        if self._is_already_downloaded(message_id, url):
            logger.debug("Attachment already downloaded: msg=%s url=%s", message_id, url[:80])
            existing = self._existing_manifest(message_id, url, source_filename, att_type)
            if existing is not None:
                return existing
            return self._entry(att, status="already_downloaded")

        # M5: 规则引擎判定候选 purpose
        from ..services.file_rule_engine import FileRuleEngine
        candidate_purpose = FileRuleEngine.guess_purpose(source_filename, mime)

        # CHAT 不入库（只留 messages.attachments URL）
        if candidate_purpose == "CHAT":
            logger.info("Attachment skipped (CHAT): msg=%s file=%s", message_id, source_filename)
            return self._entry(att, status="skipped_chat")

        result = await self._fm.store_attachment(
            message_id=message_id,
            url=url,
            attachment_type=att_type,
            source_filename=source_filename,
            purpose=candidate_purpose,
            purpose_confirmed=False,
        )
        if not result:
            return self._entry(att, status="failed", error="store_attachment returned None")

        logger.info(
            "Attachment auto-downloaded: msg=%s file_no=%s purpose=%s",
            message_id, result.get("file_no"), candidate_purpose,
        )
        return {
            "file_name": source_filename or os.path.basename(result.get("local_path", "")),
            "type": att_type,
            "file_no": result.get("file_no", ""),
            "local_path": result.get("local_path", ""),
            "file_size": result.get("file_size", att.get("file_size", 0)),
            "status": "ready",
        }

    def _existing_manifest(self, message_id: str, url: str,
                           source_filename: str, att_type: int) -> dict | None:
        """已下载过的附件：从 message_attachments + files 回填 manifest。"""
        try:
            from ..infrastructure.database.session import get_session
            from ..infrastructure.database.models import File, MessageAttachment

            with get_session() as session:
                att = (
                    session.query(MessageAttachment)
                    .filter(MessageAttachment.message_id == message_id,
                            MessageAttachment.file_url == url)
                    .order_by(MessageAttachment.created_at.desc())
                    .first()
                )
                if not att or not att.file_id:
                    return None
                f = session.query(File).filter(File.id == att.file_id).first()
                if not f:
                    return None
                local_path = self._fm.resolve_local_path(f.file_no) if self._fm else None
                return {
                    "file_name": source_filename or f.filename or os.path.basename(local_path or ""),
                    "type": att_type,
                    "file_no": f.file_no,
                    "local_path": local_path or "",
                    "file_size": f.file_size or att.file_size or 0,
                    "status": "ready" if local_path else "already_downloaded",
                }
        except Exception as e:  # noqa: BLE001
            logger.warning("_existing_manifest failed: %s", e)
            return None

    @staticmethod
    def _entry(att: dict, status: str, error: str = "") -> dict:
        """构造失败/跳过的 manifest 条目。"""
        entry = {
            "file_name": att.get("file_name", "") or att.get("summary", "") or "",
            "type": att.get("type", 0),
            "file_no": "",
            "local_path": "",
            "file_size": att.get("file_size", 0),
            "status": status,
        }
        if error:
            entry["error"] = error
        return entry

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


def format_attachment_manifest(manifest: list[dict] | None) -> str:
    """把 manifest 渲染为可注入当前用户消息的文本块。

    只暴露文件元数据、file_no 与本地路径，不暴露 URL 或文件内容。
    """
    if not manifest:
        return ""

    lines = ["【当前消息附件】"]
    for idx, item in enumerate(manifest, 1):
        name = item.get("file_name") or "未命名附件"
        label = _TYPE_LABELS.get(item.get("type", 3), "文件")
        status = item.get("status", "")
        if status == "ready":
            detail = f"file_no={item.get('file_no', '')}；本地路径={item.get('local_path', '')}"
        elif status == "skipped_chat":
            detail = "表情/梗图，未入库"
        elif status == "missing_url":
            detail = "缺少下载地址，无法读取"
        elif status == "timeout":
            detail = "下载超时，请让用户重发或稍后重试"
        else:
            detail = f"未就绪（{status}）"
        size = item.get("file_size") or 0
        size_text = f"；{size} 字节" if size else ""
        lines.append(f"{idx}. {name}（{label}）：{detail}{size_text}")

    lines.append(
        "使用说明：已就绪的 Office 文件可直接把“本地路径”传给 officeCLI；"
        "多个 Office 文件先确认审哪一份；图片默认全部归到本次上报/审核；PDF 本次不支持。"
    )
    return "\n".join(lines)
