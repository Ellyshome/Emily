"""数据同步 Handler —— 定时同步外部数据源。"""

from __future__ import annotations

import logging

from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.data_sync")


class DataSyncHandler(SchedulerJobHandler):
    """定时同步外部数据（文件索引、知识库等）。"""

    action_type = "sync_external_data"
    description = "定时同步外部数据源"

    def __init__(self, file_service=None, rag_provider=None):
        self._file_service = file_service
        self._rag_provider = rag_provider

    async def execute(self, params: dict) -> JobResult:
        sync_type = params.get("sync_type", "files")

        # TODO: 接入具体同步逻辑（file_service / rag_provider 注入可用）
        # 未实现前返回失败而非假成功；接入前不得在 scheduler_config.json 注册为 enabled。
        logger.info("Data sync triggered: type=%s", sync_type)

        return JobResult(success=False, summary=f"数据同步未实现（类型：{sync_type}）")
