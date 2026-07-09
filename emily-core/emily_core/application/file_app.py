"""FileApplication —— 文件归档编排。

MVP 阶段仅记录文件元数据入库，不做真实文件上传/存储。
M13: 整合 FileStorageService 实现物理文件下载存储（TC-A01）。
"""

import logging

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import FileCommand
from ..services.file_service import FileService

logger = logging.getLogger("emily.app.file")


class FileApplication:
    """文件归档应用服务。

    M13 (TC-A01): 注入 FileStorageService 实现物理文件下载。
    """

    def __init__(self, file_service: FileService, storage_service=None):
        self.file_service = file_service
        self.storage_service = storage_service  # M13: FileStorageService（可选）
        self._journal = None  # M8c

    def set_journal(self, journal) -> None:
        """注入事件日志服务（M8c）。"""
        self._journal = journal

    def set_storage_service(self, storage_service) -> None:
        """注入文件物理存储服务（M13）。"""
        self.storage_service = storage_service

    async def handle_file(
        self, route_result: RouteResult, user_id: str, message_id: str,
        attachment_url: str = "", attachment_type: int = 0,
        source_filename: str = "",
    ) -> HandlerResult:
        """处理文件归档。

        Args:
            route_result: 路由结果（含 LLM 提取的参数）
            user_id: 创建者系统用户 ID
            message_id: 来源消息 ID
            attachment_url: M13: 附件下载 URL（有则物理存储）
            attachment_type: M13: 附件类型（1=图片 2=图片 3=文件 4=语音 5=视频）
            source_filename: M13: 源文件名
        """
        try:
            data = route_result.data or {}
            filename = data.get("filename", "未命名文件")

            cmd = FileCommand(
                project_id=route_result.project_id,
                project_name=route_result.project_name,
                filename=filename,
                file_type=data.get("file_type", ""),
                uploaded_by=user_id,
                source_message_id=message_id,
                file_category=data.get("file_category", "OTHER"),
            )
            f = self.file_service.create_file_record(cmd)

            # ═══ M13 (TC-A01): 如果有附件 URL，下载并存到物理磁盘 ═══
            local_path = ""
            if attachment_url and self.storage_service:
                try:
                    store_result = self.storage_service.store_attachment(
                        message_id=message_id,
                        attachment_url=attachment_url,
                        attachment_type=attachment_type or 3,  # 默认 file 类型
                        source_filename=source_filename or filename,
                    )
                    if store_result:
                        local_path = store_result.get("local_path", "")
                        logger.info("File physically stored: %s", local_path)
                except Exception as e:
                    logger.warning("Physical file storage failed (non-blocking): %s", e)

            # M8c: 写入项目日志
            if self._journal is not None:
                from ._user_utils import resolve_user_name
                user_name = resolve_user_name(cmd.uploaded_by) or "用户"
                summary = f"归档文件：{f.filename}（{f.file_no}）"
                if local_path:
                    summary += f" → {local_path}"
                self._journal.append(name=user_name, summary=summary)

            reply = FileService.format_reply(f)
            if local_path:
                reply += f"\n文件已保存到本地存储。"
            return HandlerResult(
                success=True, object_type="file", object_id=f.id, reply=reply,
            )
        except Exception as e:
            logger.error("File record creation failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False, error_code="file_create_failed", reply=f"文件归档失败：{e}",
            )

    async def handle_list_by_category(
        self,
        file_category: str | None = None,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        keyword: str = "",
        limit: int = 10,
    ) -> HandlerResult:
        """按分类查询文件列表。"""
        try:
            from ..infrastructure.database.models import FileCategory

            files = self.file_service.list_by_category(
                project_id=project_id,
                project_ids=project_ids,
                file_category=file_category,
                limit=limit,
            )

            if not files:
                cat_name = FileCategory.display(file_category) if file_category else "文件"
                return HandlerResult(
                    success=True,
                    reply=f"📎 {cat_name}类暂无文件记录。",
                )

            # 格式化列表
            cat_name = FileCategory.display(file_category) if file_category else "全部"
            lines = [f"📎 {cat_name}类文件（共 {len(files)} 份）", "──────────────"]
            for i, f in enumerate(files[:10], 1):
                cat_display = FileCategory.display(getattr(f, 'file_category', 'OTHER'))
                lines.append(f"{i}. {f.filename} ({f.file_no}) [{cat_display}]")
            if len(files) > 10:
                lines.append(f"... 还有 {len(files) - 10} 份")

            return HandlerResult(
                success=True,
                reply="\n".join(lines),
                data={
                    "total": len(files),
                    "category": file_category,
                    "files": [
                        {
                            "file_no": f.file_no,
                            "filename": f.filename,
                            "file_category": getattr(f, 'file_category', 'OTHER'),
                        }
                        for f in files[:20]
                    ],
                },
            )
        except Exception as e:
            logger.error("List files by category failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False,
                error_code="file_query_failed",
                reply=f"文件查询失败：{e}",
            )

    async def handle_update_category(
        self,
        file_no: str,
        file_category: str,
        user_id: str = "",
    ) -> HandlerResult:
        """修改文件分类。"""
        try:
            from ..infrastructure.database.models import FileCategory

            # 按编号查找文件
            f = self.file_service.repo.get_by_file_no(file_no)
            if f is None:
                return HandlerResult(
                    success=False,
                    reply=f"找不到文件编号 {file_no}",
                )

            old_category = getattr(f, 'file_category', 'OTHER')
            old_display = FileCategory.display(old_category)
            new_display = FileCategory.display(file_category)

            result = self.file_service.update_file_category(
                file_id=f.id,
                file_category=file_category,
                operator_id=user_id,
            )

            if result is None:
                return HandlerResult(
                    success=False,
                    reply=f"文件分类更新失败",
                )

            return HandlerResult(
                success=True,
                reply=f"✅ 文件「{f.filename}」已从「{old_display}」改到「{new_display}」",
                data={
                    "file_no": file_no,
                    "old_category": old_category,
                    "new_category": file_category,
                },
            )
        except Exception as e:
            logger.error("Update file category failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False,
                error_code="file_category_update_failed",
                reply=f"分类更新失败：{e}",
            )
