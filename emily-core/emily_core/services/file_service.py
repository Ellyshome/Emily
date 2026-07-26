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
            file_category=cmd.file_category or "OTHER",
            purpose=getattr(cmd, "purpose", "RECORD") or "RECORD",
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

    def list_by_category(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        file_category: str | None = None,
        limit: int = 50,
    ) -> list[FileModel]:
        """按分类查询文件。"""
        return self.repo.query_by_category(
            project_id=project_id,
            project_ids=project_ids,
            file_category=file_category,
            limit=limit,
        )

    def update_file_category(self, file_id: str, file_category: str, operator_id: str = "") -> FileModel | None:
        """更新文件分类。

        Args:
            file_id: 文件 UUID
            file_category: 目标分类枚举值
            operator_id: 操作人 ID

        Returns:
            更新后的 File 对象，未找到返回 None
        """
        from ..infrastructure.database.models import FileCategory
        validated = FileCategory.validate(file_category)
        result = self.repo.update_category(file_id, validated)
        if result:
            logger.info("File category updated: %s → %s by %s", result.file_no, validated, operator_id)
        return result

    def get_category_summary(self, project_id: str | None = None) -> dict:
        """按分类统计文件数量。"""
        from ..infrastructure.database.models import FileCategory
        counts = self.repo.count_by_category(project_id=project_id)
        return {
            "by_category": {
                FileCategory.display(cat): count
                for cat, count in counts.items()
            },
            "total": sum(counts.values()),
        }
