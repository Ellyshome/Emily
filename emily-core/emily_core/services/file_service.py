"""FileService —— 文件元数据管理业务逻辑。

MVP 阶段不做真实文件存储，仅记录元数据入库。
M5+ 引入 StorageService 做文件落地和解析调度。
"""

import logging
from typing import Optional

from ..repositories.file_repo import FileRepository
from ..adapters.standard.command import FileCommand
from ..infrastructure.database.models import File as FileModel

logger = logging.getLogger("emily.service.file")


class FileService:
    def __init__(self):
        self.repo = FileRepository()

    def create_file_record(self, cmd: FileCommand) -> FileModel:
        file_no = self.repo.generate_file_no()
        f = self.repo.create(
            file_no=file_no,
            filename=cmd.filename,
            project_id=cmd.project_id or None,
            message_id=cmd.source_message_id or None,
            file_type=cmd.file_type or None,
            storage_path=cmd.storage_path or None,
            file_size=cmd.file_size,
            uploaded_by=cmd.uploaded_by or None,
            parse_status="pending",
        )
        logger.info("File %s recorded: %s", file_no, cmd.filename)
        return f

    @staticmethod
    def format_reply(f: FileModel) -> str:
        return (
            f"📎 文件已归档\n"
            f"──────────────\n"
            f"编号：{f.file_no}\n"
            f"文件名：{f.filename}\n"
            f"──────────────"
        )
