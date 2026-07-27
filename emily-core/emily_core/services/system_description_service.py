"""SystemDescriptionService —— 认知书（系统自我描述）增量更新服务。

检测偏差 → 全量重建 → 存储更新。与世界书模式一致。

参照模式：emily_core/services/world_book_service.py
"""

from __future__ import annotations

import asyncio
import logging

from ..services.schema_drift_detector import SchemaDriftDetector
from ..services.system_description_builder import SystemDescriptionBuilder

logger = logging.getLogger("emily.system_description_service")


class SystemDescriptionService:
    """认知书（系统自我描述）更新服务。"""

    def __init__(self, llm_client=None):
        self._builder = SystemDescriptionBuilder()
        self._detector = SchemaDriftDetector()
        # llm_client 保留但当前不使用（LLM 兜底字段描述为后续优化）
        self._llm = llm_client

    async def check_and_update(self, *, dry_run: bool = False) -> dict:
        """检测偏差并更新系统描述。

        Args:
            dry_run: 预览模式

        Returns:
            更新结果 dict
        """
        # 1. 检测偏差
        drift_result = self._detector.detect()

        if not drift_result.get("has_description"):
            # 无系统描述，首次构建
            result = await asyncio.to_thread(
                self._builder.build, generated_by="startup", dry_run=dry_run
            )
            result["status"] = "built" if not dry_run else "preview"
            return result

        if not drift_result.get("has_drift"):
            return {
                "status": "no_drift",
                "message": "\u7cfb\u7edf\u63cf\u8ff0\u4e0e\u5f53\u524d\u4ee3\u7801\u7ed3\u6784\u4e00\u81f4\uff0c\u65e0\u9700\u66f4\u65b0",
            }

        stale_domains = drift_result.get("stale_domains", [])
        logger.info("SystemDescription drift detected in domains: %s", stale_domains)

        # 2. 全量重建（三域相互引用，不宜单域修补）
        result = await asyncio.to_thread(
            self._builder.build, generated_by="scheduler", dry_run=dry_run
        )

        result["updated_domains"] = stale_domains
        result["drift_details"] = drift_result.get("drift", {})
        result["status"] = "updated" if not dry_run else "preview"

        return result

    async def force_rebuild(self, *, dry_run: bool = False) -> dict:
        """强制重建系统描述（无视偏差检测结果）。"""
        result = await asyncio.to_thread(
            self._builder.build, generated_by="manual", dry_run=dry_run
        )
        result["status"] = "rebuilt" if not dry_run else "preview"
        return result
