"""FileApplication —— 文件归档编排。

MVP 阶段仅记录文件元数据入库，不做真实文件上传/存储。
"""

import logging

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import FileCommand
from ..services.file_service import FileService

logger = logging.getLogger("emily.app.file")


class FileApplication:
    def __init__(self, file_service: FileService):
        self.file_service = file_service
        self._journal = None  # M8c

    def set_journal(self, journal) -> None:
        """注入事件日志服务（M8c）。"""
        self._journal = journal

    async def handle_file(
        self, route_result: RouteResult, user_id: str, message_id: str
    ) -> HandlerResult:
        try:
            data = route_result.data or {}
            cmd = FileCommand(
                project_id=route_result.project_id,
                project_name=route_result.project_name,
                filename=data.get("filename", "未命名文件"),
                file_type=data.get("file_type", ""),
                uploaded_by=user_id,
                source_message_id=message_id,
            )
            f = self.file_service.create_file_record(cmd)
            # M8c: 写入项目日志
            if self._journal is not None:
                self._journal.append(
                    name=cmd.uploaded_by or "用户",
                    summary=f"归档文件：{f.filename}（{f.file_no}）",
                )
            reply = FileService.format_reply(f)
            return HandlerResult(
                success=True, object_type="file", object_id=f.id, reply=reply,
            )
        except Exception as e:
            logger.error("File record creation failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False, error_code="file_create_failed", reply=f"文件归档失败：{e}",
            )
