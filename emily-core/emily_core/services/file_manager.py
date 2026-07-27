"""FileManager —— 文件对象统一服务（定位 B 收敛层）。

聚合 FileService（元数据）+ FileStorageService（物理存储）+ 
SessionAccessibleFileRepo（可见性）的对外能力，作为 Application 层唯一入口。

不替代底层 Repo，只在 Service 层做 Facade：权限校验 + 编排 + 统一出口。
"""

import logging
from typing import Optional

from ..infrastructure.database import get_session
from ..infrastructure.database.models import File, SessionAccessibleFile
from ..adapters.standard.command import FileCommand

logger = logging.getLogger("emily.service.file_manager")


class FileManager:
    """文件对象统一服务 Facade。

    聚合三类子系统的对外能力：
      - FileService: 元数据 CRUD
      - FileStorageService: 物理存储（下载/落盘）
      - SessionAccessibleFileRepo: 可见性（权限过滤）
    """

    def __init__(self, file_service, storage_service, accessible_repo):
        self._file_svc = file_service          # FileService
        self._storage = storage_service        # FileStorageService
        self._accessible = accessible_repo     # SessionAccessibleFileRepo

    # ── 检索（权限统一出口）──

    def query_visible_files(
        self, user_id: str, *,
        file_category: str | None = None,
        keyword: str = "",
        limit: int = 50,
    ) -> list[File]:
        """按分类/关键词查询用户可见文件。

        统一走 session_accessible_files：project_ids + confidentiality + 节点授权 + 显式。
        取代 FileRepository.query_files / query_by_category 的 project_ids 单层过滤。
        """
        try:
            with get_session() as session:
                # 1. 取 user 的 session_accessible_files file_id 集合
                accessible = session.query(SessionAccessibleFile.file_id).filter(
                    SessionAccessibleFile.user_id == user_id,
                ).subquery()

                # 2. 在 files 表按 file_id 集合 + is_deleted 过滤
                q = session.query(File).filter(
                    File.id.in_(accessible),
                    File.is_deleted == False,
                )

                if file_category:
                    q = q.filter(File.file_category == file_category)

                if keyword:
                    q = q.filter(File.filename.ilike(f"%{keyword}%"))

                return q.order_by(File.created_at.desc()).limit(limit).all()
        except Exception as e:
            logger.error("query_visible_files(%s) failed: %s", user_id, e)
            return []

    def search_files(self, user_id: str, keyword: str, top_k: int = 5) -> list[dict]:
        """自然语言搜索（委托 SessionAccessibleFileRepo.search）。"""
        return self._accessible.search(user_id, keyword, top_k)

    def get_visible_summary(self, user_id: str) -> dict:
        """可见文件摘要（委托 SessionAccessibleFileRepo.get_file_summary）。"""
        return self._accessible.get_file_summary(user_id)

    # ── 物理存储 ──

    async def store_attachment(self, message_id, url, attachment_type, **kw) -> dict | None:
        """下载附件并落盘（委托 FileStorageService.store_attachment_async）。"""
        return await self._storage.store_attachment_async(
            message_id=message_id,
            attachment_url=url,
            attachment_type=attachment_type,
            **kw,
        )

    def resolve_local_path(self, file_no: str) -> str | None:
        """file_no → 本地绝对路径（M2 send_file 用）。"""
        return self._storage.get_local_path(file_no)

    # ── 归档 ──

    def create_record(self, cmd: FileCommand):
        """元数据录入（委托 FileService.create_file_record）。"""
        return self._file_svc.create_file_record(cmd)

    def update_category(self, file_id, category, operator_id="") -> Optional[File]:
        return self._file_svc.update_file_category(file_id, category, operator_id)

    def get_by_file_no(self, file_no: str) -> Optional[File]:
        """按文件编号查询（委托 FileRepository）。"""
        return self._file_svc.repo.get_by_file_no(file_no)

    # ── 权限校验（M2/M4 复用）──

    def can_access(self, user_id: str, file_id: str) -> bool:
        """校验用户是否可访问某文件（在 session_accessible_files 中）。"""
        try:
            with get_session() as session:
                exists = session.query(SessionAccessibleFile).filter(
                    SessionAccessibleFile.user_id == user_id,
                    SessionAccessibleFile.file_id == file_id,
                ).first()
                return exists is not None
        except Exception as e:
            logger.error("can_access(%s, %s) failed: %s", user_id, file_id, e)
            return False

    # ── 列表查询（委托 FileService，向后兼容旧路径）──

    def list_by_category(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        file_category: str | None = None,
        limit: int = 50,
    ) -> list[File]:
        """按分类查询文件（旧路径，不走权限过滤）。"""
        return self._file_svc.list_by_category(
            project_id=project_id,
            project_ids=project_ids,
            file_category=file_category,
            limit=limit,
        )

    # ── M4 关联/版本/删除（M4 阶段补实现）──

    def link_to_module(
        self, file_id: str, module_id: str, module_type: str, operator_id: str = "",
    ) -> Optional[File]:
        """关联文件到业务对象（节点/事件/会议等）。"""
        try:
            with get_session() as session:
                f = session.query(File).filter(
                    File.id == file_id, File.is_deleted == False,
                ).first()
                if f is None:
                    return None
                f.source_module_id = module_id
                f.source_module_type = module_type
                session.commit()
                logger.info(
                    "File %s linked to module %s(%s) by %s",
                    f.file_no, module_id, module_type, operator_id,
                )
                return f
        except Exception as e:
            logger.error("link_to_module failed: %s", e)
            return None

    def create_version(
        self, parent_file_no: str, new_file_id: str, version_label: str,
        operator_id: str = "",
    ) -> Optional[File]:
        """创建新版本：旧版本 is_latest=False，新版本 parent_file_id 指向旧版本。"""
        try:
            with get_session() as session:
                parent = session.query(File).filter(
                    File.file_no == parent_file_no, File.is_deleted == False,
                ).first()
                if parent is None:
                    return None
                # 旧版本置为非最新
                parent.is_latest = False

                child = session.query(File).filter(File.id == new_file_id).first()
                if child is None:
                    return None
                child.parent_file_id = parent.id
                child.version = version_label
                child.is_latest = True
                session.commit()
                logger.info(
                    "Version %s created for %s (parent=%s) by %s",
                    version_label, child.file_no, parent.file_no, operator_id,
                )
                return child
        except Exception as e:
            logger.error("create_version failed: %s", e)
            return None

    def soft_delete(self, file_id: str, operator_id: str = "") -> bool:
        """软删除：is_deleted=True。"""
        try:
            with get_session() as session:
                f = session.query(File).filter(
                    File.id == file_id, File.is_deleted == False,
                ).first()
                if f is None:
                    return False
                f.is_deleted = True
                session.commit()
                logger.info("File %s soft-deleted by %s", f.file_no, operator_id)
                return True
        except Exception as e:
            logger.error("soft_delete failed: %s", e)
            return False

    def list_versions(self, file_no: str) -> list[File]:
        """列出某文件的所有版本（按 version 排序）。"""
        try:
            with get_session() as session:
                root = session.query(File).filter(
                    File.file_no == file_no, File.is_deleted == False,
                ).first()
                if root is None:
                    return []
                # 查找所有 parent_file_id 指向 root 的版本 + root 自身
                # 先找到 root 的 id（可能 root 本身也是某个版本的 child）
                root_id = root.id if root.parent_file_id is None else root.parent_file_id
                versions = session.query(File).filter(
                    (File.id == root_id) | (File.parent_file_id == root_id),
                    File.is_deleted == False,
                ).order_by(File.version).all()
                return versions
        except Exception as e:
            logger.error("list_versions(%s) failed: %s", file_no, e)
            return []

    # ── M5 附件链 ──

    def link_to_master(self, file_id: str, master_file_id: str, operator_id: str = "") -> dict:
        """挂载附件到主文件。禁止嵌套校验（Service 层）。

        Args:
            file_id: 附件文件 UUID
            master_file_id: 主文件 UUID
            operator_id: 操作人 ID

        Returns:
            {"success": bool, "error"?: str, "file_no"?: str}
        """
        # 1. 主文件必须存在且未删除
        master = self._file_svc.repo.get_by_id(master_file_id)
        if master is None or master.is_deleted:
            return {"success": False, "error": "主文件不存在或已删除"}
        # 2. 禁止嵌套：主文件自身 attachment_of 必须为 NULL
        if getattr(master, "attachment_of", None) is not None:
            return {"success": False, "error": "禁止嵌套：目标文件本身是附件，不能作为主文件"}
        # 3. 禁止自挂
        if file_id == master_file_id:
            return {"success": False, "error": "不能挂载到自己"}
        # 4. 附件文件必须存在
        child = self._file_svc.repo.get_by_id(file_id)
        if child is None or child.is_deleted:
            return {"success": False, "error": "附件文件不存在或已删除"}
        # 5. 调 Repository 更新
        result = self._file_svc.repo.update_attachment_of(file_id, master_file_id)
        if result is None:
            return {"success": False, "error": "挂载失败"}
        logger.info("link_to_master: %s → %s by %s", file_id, master_file_id, operator_id)
        return {"success": True, "file_no": result.file_no}

    def unlink_attachment(self, file_id: str, operator_id: str = "") -> dict:
        """卸载附件，提升为独立文件（attachment_of=NULL）。

        Returns:
            {"success": bool, "error"?: str, "file_no"?: str}
        """
        result = self._file_svc.repo.update_attachment_of(file_id, None)
        if result is None:
            return {"success": False, "error": "文件不存在"}
        logger.info("unlink_attachment: %s by %s", file_id, operator_id)
        return {"success": True, "file_no": result.file_no}

    def list_attachments(self, master_file_id: str) -> list[File]:
        """列出主文件下的所有附件。"""
        return self._file_svc.repo.query_attachments(master_file_id)

    def update_purpose(self, file_id: str, purpose: str, operator_id: str = "") -> dict:
        """校正 purpose，标 purpose_confirmed=True。

        Returns:
            {"success": bool, "error"?: str, "file_no"?: str, "purpose"?: str}
        """
        from ..infrastructure.database.models import FilePurpose
        validated = FilePurpose.validate(purpose)
        from ..infrastructure.database import get_session
        with get_session() as session:
            f = session.query(File).filter(File.id == file_id, File.is_deleted == False).first()
            if f is None:
                return {"success": False, "error": "文件不存在"}
            f.purpose = validated
            f.purpose_confirmed = True
            session.commit()
            logger.info("File %s purpose updated: %s (confirmed) by %s", f.file_no, validated, operator_id)
            return {"success": True, "file_no": f.file_no, "purpose": validated}

    def set_rag_indexed(self, file_id: str, indexed: bool, collection: str = "") -> bool:
        """标记 RAG 入库状态。

        Args:
            file_id: 文件 UUID
            indexed: 是否已入库
            collection: RAG 集合名

        Returns:
            True 成功，False 失败
        """
        from ..infrastructure.database import get_session
        with get_session() as session:
            f = session.query(File).filter(File.id == file_id).first()
            if f is None:
                return False
            f.rag_indexed = indexed
            if collection:
                f.rag_collection = collection
            session.commit()
            return True
