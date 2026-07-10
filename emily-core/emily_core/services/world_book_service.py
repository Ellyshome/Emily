"""ProjectWorldBookService —— 世界书增量更新服务。

根据偏差检测结果，只更新过时的层。数据驱动优先，语义偏差才调 LLM。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..repositories.world_book_repo import ProjectWorldBookRepo
from ..services.world_book_builder import ProjectWorldBookBuilder
from ..services.cognition_drift_detector import CognitionDriftDetector

logger = logging.getLogger("emily.world_book_service")

# 数据驱动更新的层（无需 LLM，快）
DATA_DRIVEN_LAYERS = {"personnel", "structure", "temporal", "relation", "introspection"}

# LLM 驱动更新的层（需 LLM，慢但语义深）
LLM_DRIVEN_LAYERS = {"ontology"}


class ProjectWorldBookService:
    """世界书增量更新服务。"""

    def __init__(self, llm_client=None):
        self._llm = llm_client
        self._builder = ProjectWorldBookBuilder()
        self._detector = CognitionDriftDetector()

    async def update_stale(self, project_id: str, *, dry_run: bool = False) -> dict:
        """检测偏差并增量更新过时层。

        Args:
            project_id: 项目 ID
            dry_run: 预览模式

        Returns:
            更新结果 dict
        """
        # 1. 检测偏差
        drift_result = self._detector.detect(project_id)

        if not drift_result.get("has_world_book"):
            # 无世界书，首次构建
            import asyncio
            return await asyncio.to_thread(
                self._builder.build, project_id, generated_by="startup", dry_run=dry_run
            )

        if not drift_result.get("has_drift"):
            return {
                "project_id": project_id,
                "status": "no_drift",
                "message": "世界书与实际数据一致，无需更新",
            }

        stale_layers = drift_result.get("stale_layers", [])
        if not stale_layers:
            return {"project_id": project_id, "status": "no_drift"}

        # 2. 重新构建完整世界书（简洁策略：重建而非逐层修补）
        # 原因：七层数据相互关联，逐层修补可能导致层间引用不一致
        import asyncio
        result = await asyncio.to_thread(
            self._builder.build, project_id, generated_by="scheduler_data", dry_run=dry_run
        )

        result["updated_layers"] = stale_layers
        result["drift_details"] = drift_result.get("drift", {})
        result["status"] = "updated" if not dry_run else "preview"

        return result

    async def update_all(self, *, dry_run: bool = False) -> list[dict]:
        """更新所有项目的过时世界书。"""
        import asyncio
        wbs = await asyncio.to_thread(ProjectWorldBookRepo.list_all)
        results = []
        for wb in wbs:
            try:
                r = await self.update_stale(wb.project_id, dry_run=dry_run)
                results.append(r)
            except Exception as e:
                logger.error("update_stale failed for project %s: %s", wb.project_id, e)
                results.append({"project_id": wb.project_id, "status": "error", "error": str(e)})
        return results
