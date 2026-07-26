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
        file_category: str = "OTHER",
        purpose: str = "RECORD",
        purpose_confirmed: bool = False,
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
                file_category=file_category,
                purpose=purpose,
                purpose_confirmed=purpose_confirmed,
            )
            session.add(f)
            session.flush()
            logger.info("File created: no=%s, filename=%s, category=%s", file_no, filename, file_category)
            return f

    @staticmethod
    def get_by_id(file_id: str) -> Optional[File]:
        with get_session() as session:
            return session.query(File).filter(File.id == file_id).first()

    @staticmethod
    def get_by_file_no(file_no: str) -> Optional[File]:
        """按文件编号查询。"""
        with get_session() as session:
            return session.query(File).filter(File.file_no == file_no).first()

    # ── M5 查询 ──

    @staticmethod
    def query_files(
        *,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        file_type: str | None = None,
        limit: int = 50,
    ) -> list[File]:
        """按条件查询文件记录（按创建时间倒序）。支持 project_ids 多项目范围过滤。"""
        with get_session() as session:
            q = session.query(File)

            if project_ids:
                q = q.filter(File.project_id.in_(project_ids))
            elif project_id:
                q = q.filter(File.project_id == project_id)
            if file_type:
                q = q.filter(File.file_type == file_type)

            q = q.order_by(File.created_at.desc()).limit(limit)
            return q.all()

    @staticmethod
    def query_by_category(
        *,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        file_category: str | None = None,
        limit: int = 50,
    ) -> list[File]:
        """按分类查询文件记录（按创建时间倒序）。

        Args:
            project_id: 单项目过滤
            project_ids: 多项目范围过滤
            file_category: 文件分类枚举值
            limit: 返回数量上限
        """
        with get_session() as session:
            q = session.query(File).filter(File.is_deleted == False)

            if project_id:
                q = q.filter(File.project_id == project_id)
            if project_ids:
                q = q.filter(File.project_id.in_(project_ids))
            if file_category:
                q = q.filter(File.file_category == file_category)

            return q.order_by(File.created_at.desc()).limit(limit).all()

    @staticmethod
    def update_category(file_id: str, file_category: str) -> File | None:
        """更新文件分类。"""
        with get_session() as session:
            f = session.query(File).filter(File.id == file_id, File.is_deleted == False).first()
            if f is None:
                return None
            f.file_category = file_category
            session.commit()
            logger.info("File %s category updated: %s", f.file_no, file_category)
            return f

    @staticmethod
    def count_by_category(project_id: str | None = None) -> dict[str, int]:
        """按分类统计文件数量。"""
        with get_session() as session:
            q = session.query(File).filter(File.is_deleted == False)
            if project_id:
                q = q.filter(File.project_id == project_id)
            files = q.all()
            counts: dict[str, int] = {}
            for f in files:
                cat = f.file_category or "OTHER"
                counts[cat] = counts.get(cat, 0) + 1
            return counts

    # ── M5 附件链 CRUD ──

    @staticmethod
    def update_attachment_of(file_id: str, master_file_id: str | None) -> File | None:
        """纯 CRUD：更新 attachment_of 字段。master_file_id=None 表示卸载。"""
        with get_session() as session:
            f = session.query(File).filter(File.id == file_id, File.is_deleted == False).first()
            if f is None:
                return None
            f.attachment_of = master_file_id
            session.commit()
            logger.info("File %s attachment_of updated: %s", f.file_no, master_file_id or "NULL(unlinked)")
            return f

    @staticmethod
    def query_attachments(master_file_id: str) -> list[File]:
        """查询主文件下的所有附件（不含主文件本身）。"""
        with get_session() as session:
            return session.query(File).filter(
                File.attachment_of == master_file_id,
                File.is_deleted == False,
            ).order_by(File.created_at).all()
