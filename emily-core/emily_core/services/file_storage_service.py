"""FileStorageService —— 文件存储业务服务。

M11: 从IM URL下载附件到本地、生成文件编号、管理files表和message_attachments表联动。
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..repositories.file_repo import FileRepository

logger = logging.getLogger("emily.service.file_storage")


class FileStorageService:
    """文件存储业务服务。

    职责：从 IM URL 下载附件到本地、生成文件编号、计算哈希/MIME、管理 files 表和 message_attachments 表联动。
    """

    def __init__(self, storage_root: str = "", platform: str = ""):
        if not storage_root:
            storage_root = str(
                Path(__file__).parent.parent.parent / "data" / "files"
            )
        self._storage_root = Path(storage_root)
        self._platform = platform or "napcat"

    # ── 目录管理 ──

    def ensure_dir(self, date_str: str | None = None) -> Path:
        """创建并返回存储目录 data/files/{platform}/{YYYY-MM}/。

        Args:
            date_str: 日期字符串 YYYY-MM-DD，None 则用今天
        """
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m")
        elif len(date_str) > 7:
            date_str = date_str[:7]  # YYYY-MM
        target = self._storage_root / self._platform / date_str
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ── 文件存储 ──

    @staticmethod
    def generate_file_no() -> str:
        """生成文件编号 FIL-YYYYMMDD-NNNN（复用 FileRepository 已有逻辑）。"""
        return FileRepository.generate_file_no()

    async def download_from_url(self, url: str) -> bytes | None:
        """从 URL 下载文件内容（异步，带超时）。

        Args:
            url: 文件URL

        Returns:
            bytes: 下载内容，失败返回 None
        """
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp not available, falling back to urllib")
            import urllib.request
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    return resp.read()
            except Exception as e:
                logger.warning("Download failed (urllib): %s — %s", url[:100], e)
                return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.warning("Download failed: HTTP %d — %s", resp.status, url[:100])
                    return None
        except Exception as e:
            logger.warning("Download failed (aiohttp): %s — %s", url[:100], e)
            return None

    def store_attachment(
        self,
        message_id: str,
        attachment_url: str,
        attachment_type: int,
        source_filename: str = "",
        mime_type: str = "",
        file_size: int = 0,
    ) -> dict | None:
        """从 URL 下载附件并保存到本地（同步版本，urllib）。

        返回 {file_id, local_path, file_no} 或 None。
        在异步上下文中推荐使用 store_attachment_async()。
        """
        import urllib.request

        file_no = self.generate_file_no()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m")
        target_dir = self.ensure_dir(today_str)

        # 确定扩展名
        ext = self._infer_extension(source_filename, mime_type)

        filename = f"{file_no}{ext}"
        local_path = target_dir / filename

        try:
            with urllib.request.urlopen(attachment_url, timeout=30) as resp:
                data = resp.read()
        except Exception as e:
            logger.warning("Attachment download failed: %s — %s", attachment_url[:100], e)
            return None

        # 写入磁盘
        try:
            local_path.write_bytes(data)
            logger.info("File saved: %s (%d bytes)", local_path, len(data))
        except Exception as e:
            logger.warning("File write failed: %s — %s", local_path, e)
            return None

        return self._finalize_store(
            data=data,
            file_no=file_no,
            message_id=message_id,
            attachment_url=attachment_url,
            attachment_type=attachment_type,
            source_filename=source_filename,
            mime_type=mime_type,
            file_size=file_size,
            local_path=local_path,
        )

    async def store_attachment_async(
        self,
        message_id: str,
        attachment_url: str,
        attachment_type: int,
        source_filename: str = "",
        mime_type: str = "",
        file_size: int = 0,
    ) -> dict | None:
        """从 URL 下载附件并保存到本地（异步版本，aiohttp 优先）。

        M13: 供管道 download 阶段调用，在异步上下文中直接 await。

        返回 {file_id, local_path, file_no, file_size, attachment_type} 或 None。
        """
        data = await self.download_from_url(attachment_url)
        if data is None:
            return None

        file_no = self.generate_file_no()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m")
        target_dir = self.ensure_dir(today_str)

        ext = self._infer_extension(source_filename, mime_type)
        filename = f"{file_no}{ext}"
        local_path = target_dir / filename

        try:
            local_path.write_bytes(data)
            logger.info("File saved: %s (%d bytes)", local_path, len(data))
        except Exception as e:
            logger.warning("File write failed: %s — %s", local_path, e)
            return None

        result = self._finalize_store(
            data=data,
            file_no=file_no,
            message_id=message_id,
            attachment_url=attachment_url,
            attachment_type=attachment_type,
            source_filename=source_filename,
            mime_type=mime_type,
            file_size=file_size,
            local_path=local_path,
        )
        if result:
            result["attachment_type"] = attachment_type
        return result

    @staticmethod
    def _infer_extension(source_filename: str, mime_type: str) -> str:
        """根据文件名或 MIME 类型推断文件扩展名。"""
        if source_filename and "." in source_filename:
            return os.path.splitext(source_filename)[1]
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "application/pdf": ".pdf", "application/zip": ".zip",
                "audio/amr": ".amr", "audio/mp3": ".mp3",
                "video/mp4": ".mp4",
            }
            return ext_map.get(mime_type, "")
        return ""

    def _finalize_store(
        self,
        data: bytes,
        file_no: str,
        message_id: str,
        attachment_url: str,
        attachment_type: int,
        source_filename: str,
        mime_type: str,
        file_size: int,
        local_path: Path,
    ) -> dict | None:
        """完成存储流程：写 files 表 + message_attachments 表。"""
        actual_size = len(data)

        # 写入 files 表
        try:
            file_record = FileRepository.create(
                file_no=file_no,
                filename=source_filename or local_path.name,
                project_id=None,
                message_id=message_id,
                file_type=_attachment_type_label(attachment_type),
                storage_path=str(local_path.relative_to(self._storage_root)),
                file_size=actual_size or file_size,
            )
        except Exception as e:
            logger.warning("File record creation failed: %s", e)
            return None

        # 写入 message_attachments 表
        try:
            from ..repositories.chat_archive_repo import ChatArchiveRepository
            ChatArchiveRepository.create_attachment(
                message_id=message_id,
                attachment_type=attachment_type,
                file_url=attachment_url,
                file_size=actual_size or file_size,
                mime_type=mime_type,
            )
            # 回填 file_id
            from ..infrastructure.database.session import get_session
            from ..infrastructure.database.models import MessageAttachment as MA
            with get_session() as session:
                att = (
                    session.query(MA)
                    .filter(MA.message_id == message_id, MA.file_url == attachment_url)
                    .order_by(MA.created_at.desc())
                    .first()
                )
                if att:
                    att.file_id = file_record.id
                    att.local_path = str(local_path.relative_to(self._storage_root))
        except Exception as e:
            logger.warning("Attachment record update failed: %s", e)

        return {
            "file_id": file_record.id,
            "file_no": file_no,
            "local_path": str(local_path),
            "file_size": actual_size or file_size,
        }

    # ── 查询 ──

    def get_local_path(self, file_no: str) -> str | None:
        """根据文件编号解析本地绝对路径。"""
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import File as FileModel

        with get_session() as session:
            f = session.query(FileModel).filter(FileModel.file_no == file_no).first()
            if f and f.storage_path:
                return str(self._storage_root / f.storage_path)
        return None

    def list_files_for_conversation(self, conversation_id: str) -> list[dict]:
        """列出会话中所有附件文件信息。"""
        from ..repositories.chat_archive_repo import ChatArchiveRepository
        return ChatArchiveRepository.get_files_for_conversation(conversation_id)


def _attachment_type_label(t: int) -> str:
    return {1: "image", 2: "image", 3: "file", 4: "voice", 5: "video"}.get(t, "file")
