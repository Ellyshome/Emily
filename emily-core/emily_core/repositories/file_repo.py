"""FileRepository —— 文件记录表 CRUD 抽象层。"""

import logging
from datetime import datetime, timezone
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import File

logger = logging.getLogger("emily.repo.file")


class FileRepository:
    """文件记录 CRUD 操作。"""

    @staticmethod
    def generate_file_no() -> str:
        """生成文件编号 FIL-YYYYMMDD-NNNN。"""
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"FIL-{today_str}-"
        with get_session() as session:
            last = (
                session.query(File)
                .filter(File.file_no.like(f"{prefix}%"))
                .order_by(File.file_no.desc())
                .first()
            )
            if last is None:
                return f"{prefix}0001"
            seq_str = last.file_no[len(prefix):]
            try:
                seq = int(seq_str) + 1
            except ValueError:
                seq = 1
            return f"{prefix}{seq:04d}"

    @staticmethod
    def create(
        *,
        file_no: str,
        filename: str,
        project_id: Optional[str] = None,
        message_id: Optional[str] = None,
        file_type: Optional[str] = None,
        bucket: Optional[str] = None,
        object_key: Optional[str] = None,
        storage_path: Optional[str] = None,
        file_size: int = 0,
        uploaded_by: Optional[str] = None,
        parse_status: str = "pending",
    ) -> File:
        with get_session() as session:
            f = File(
                file_no=file_no,
                filename=filename,
                project_id=project_id,
                message_id=message_id,
                file_type=file_type,
                bucket=bucket,
                object_key=object_key,
                storage_path=storage_path,
                file_size=file_size,
                uploaded_by=uploaded_by,
                parse_status=parse_status,
            )
            session.add(f)
            session.flush()
            logger.info("File created: no=%s, filename=%s", file_no, filename)
            return f

    @staticmethod
    def get_by_id(file_id: str) -> Optional[File]:
        with get_session() as session:
            return session.query(File).filter(File.id == file_id).first()

    # ── M5 查询 ──

    @staticmethod
    def query_files(
        *,
        project_id: str | None = None,
        file_type: str | None = None,
        limit: int = 50,
    ) -> list[File]:
        """按条件查询文件记录（按创建时间倒序）。"""
        with get_session() as session:
            q = session.query(File)

            if project_id:
                q = q.filter(File.project_id == project_id)
            if file_type:
                q = q.filter(File.file_type == file_type)

            q = q.order_by(File.created_at.desc()).limit(limit)
            return q.all()
