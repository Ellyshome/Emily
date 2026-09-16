"""FileService —— 文件元数据管理业务逻辑。

MVP 阶段不做真实文件存储，仅记录元数据入库。
M5+ 引入 StorageService 做文件落地和解析调度。
"""

import logging
from typing import Optional

from ..repositories.file_repo import FileRepository
from ..adapters.standard.command import FileCommand
from ..infrastructure.database.models import File as FileModel
from ..infrastructure.logging.audit import audited

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
            confidentiality=getattr(cmd, "confidentiality", 1),
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

    @audited(
        category="file",
        action="confidentiality_updated",
        target_type="file",
        actor_arg="operator_id",
        target_arg="file_id",
    )
    def update_confidentiality(
        self, file_id: str, confidentiality: int, operator_id: str = "",
    ) -> dict:
        """调整文件密级（需求：仅 L5/L6 管理员或上传者本人可改）。

        权限判定：
          - 上传者本人：file.uploaded_by == operator_id
          - 管理员：operator.level >= 5（含 L6）

        返回 {"success": bool, "error"?: str, "file_no"?: str, "old"?: int, "new"?: int}
        """
        from ..repositories.permission_repo import PermissionRepository

        # 值域校验：密级仅 0/1/2
        if confidentiality not in (0, 1, 2):
            return {"success": False, "error": "invalid_confidentiality",
                    "reply": "密级值域非法：仅允许 0=公开 / 1=内部 / 2=机密"}

        f = self.repo.get_by_id(file_id)
        if f is None or f.is_deleted:
            return {"success": False, "error": "file_not_found", "reply": "文件不存在或已删除"}

        # 权限校验：上传人本人 或 L5/L6
        operator_level = 0
        if operator_id:
            operator = PermissionRepository.get_user(operator_id)
            operator_level = operator.level if operator is not None else 0

        is_uploader = bool(operator_id) and (f.uploaded_by == operator_id)
        is_admin = operator_level >= 5
        if not (is_uploader or is_admin):
            return {"success": False, "error": "permission_denied",
                    "reply": "仅上传人本人或 L5/L6 管理员可调整文件密级"}

        old_conf = f.confidentiality or 1
        result = self.repo.update_confidentiality(file_id, confidentiality, operator_id)
        if result is None:
            return {"success": False, "error": "update_failed", "reply": "密级更新失败"}

        # 即时重算可见性快照（密级变动立即生效）
        try:
            resynced = _resync_all_visible_snapshots()
            logger.info("confidentiality change resynced %d user snapshots", resynced)
        except Exception as e:
            logger.error("resync after confidentiality change failed: %s", e)

        return {"success": True, "file_no": result.file_no,
                "old": old_conf, "new": confidentiality,
                "reply": f"文件「{result.filename}」密级已从 {old_conf} 调整到 {confidentiality}"}

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

    def update_summary(self, file_id: str, summary: str) -> None:
        """更新文件的 content_summary 和 summary_generated_at。"""
        self.repo.update_summary(file_id, summary)

    def get_by_summary_null(self, limit: int = 50) -> list[FileModel]:
        """取出 content_summary 为空的文件，按时间升序。"""
        return self.repo.get_by_summary_null(limit)


def _resync_all_visible_snapshots() -> int:
    """密级变动后即时重算全部用户的可见文件快照（session_accessible_files）。

    密级调整是低频高敏操作：全量重建快照，保证 list/search/can_access 等快照路径
    立即反映新密级。explicit 白名单记录由 sync_for_user 保留，不受影响。
    RAG 检索走 VisibleFileSetResolver 实时计算，无需重建。
    """
    from ..infrastructure.database.models import User
    from ..infrastructure.database.session import get_session
    from ..repositories.permission_repo import PermissionRepository
    from ..repositories.session_accessible_file_repo import SessionAccessibleFileRepo
    from ..services.permission_service import PermissionService

    repo = PermissionRepository()
    with get_session() as session:
        user_rows = session.query(User.id, User.level, User.company).filter(
            User.is_deleted == False,
        ).all()

    total = 0
    for uid, level, company_id in user_rows:
        company = repo.get_company(company_id) if company_id else None
        info_level = PermissionService._derive_info_level(level or 1)
        project_ids = PermissionService._derive_project_ids(None, company)
        authorized_node_ids = PermissionService._derive_authorized_nodes(None, company)
        total += SessionAccessibleFileRepo.sync_for_user(
            uid,
            project_ids=project_ids,
            info_level=info_level,
            authorized_node_ids=authorized_node_ids,
        )
    return total
