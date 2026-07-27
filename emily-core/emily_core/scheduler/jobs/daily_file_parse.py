"""每日文件解析盘点 Handler —— 批量解析 content_summary 为空的文件。"""

from __future__ import annotations

import asyncio
import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.daily_file_parse")

BATCH_LIMIT = 50
INTER_FILE_DELAY_SEC = 0.5  # 限流，防打爆 LLM


class DailyFileParseHandler(SchedulerJobHandler):
    """每日批量解析 content_summary 为空的文件，并触发节点成果匹配。"""

    action_type = "daily_file_parse"
    description = "每日批量解析文件摘要 + 节点成果匹配"

    def __init__(self, file_service=None, outbound_bus=None):
        self._file_service = file_service
        self._outbound_bus = outbound_bus

    async def execute(self, params: dict) -> JobResult:
        limit = params.get("batch_limit", BATCH_LIMIT)
        dry_run = params.get("dry_run", False)

        # 延迟导入
        from emily_core.services.file_service import FileService
        from emily_core.services.file_parser_service import FileParserService

        file_service = self._file_service or FileService()

        pending = file_service.get_by_summary_null(limit=limit)
        logger.info("批量解析：待处理 %d 个文件", len(pending))

        if not pending:
            return JobResult(success=True, summary="无待解析文件")

        parsed_count = 0
        failed_count = 0
        match_count = 0

        # 尝试导入匹配器（M4 部署后生效）
        matcher = None
        try:
            from emily_core.services.node_deliverable_matcher import NodeDeliverableMatcher
            matcher = NodeDeliverableMatcher(self._outbound_bus)
        except ImportError:
            logger.debug("NodeDeliverableMatcher 未部署，跳过节点匹配")

        from emily_core.services.file_storage_service import FileStorageService
        storage_svc = FileStorageService()

        for f in pending:
            try:
                local_path = storage_svc.get_local_path(f.file_no)
                if local_path is None:
                    logger.debug("无法解析文件路径: %s", f.file_no)
                    continue

                result = await FileParserService.parse_and_summarize(
                    local_path, f.filename
                )
                if result is None:
                    continue

                file_service.update_summary(str(f.id), result.summary)
                parsed_count += 1

                # 触发节点成果匹配（M4 部署后生效）
                if matcher:
                    matched = await matcher.match_and_notify(f, result)
                    if matched:
                        match_count += 1

            except Exception as e:
                logger.warning("批量解析失败 %s: %s", getattr(f, 'file_no', '?'), e)
                failed_count += 1
                continue

            await asyncio.sleep(INTER_FILE_DELAY_SEC)

        summary = f"解析完成 {parsed_count} 个文件，匹配 {match_count} 个节点，失败 {failed_count}"
        logger.info("批量解析完成: %s", summary)
        return JobResult(success=True, summary=summary)
