"""meta_cognition.py — 元认知管理 API 路由。

提供系统描述的构建/查询/偏差检测 API。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("emily.api.meta_cognition")

router = APIRouter(prefix="/api/v1/meta-cognition", tags=["meta-cognition"])


@router.post("/system-description/build")
async def build_system_description(
    force: bool = Query(default=False, description="是否强制重建（无视偏差检测）"),
    dry_run: bool = Query(default=False, description="预览模式（不写 DB）"),
):
    """手动触发系统描述构建。"""
    try:
        from emily_core.services.system_description_service import SystemDescriptionService

        svc = SystemDescriptionService()
        if force:
            result = await svc.force_rebuild(dry_run=dry_run)
        else:
            result = await svc.check_and_update(dry_run=dry_run)

        return {"status": "ok", "data": result}
    except Exception as e:
        logger.error("build_system_description failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system-description")
async def get_system_description():
    """查询当前系统描述。"""
    try:
        from emily_core.repositories.system_description_repo import SystemDescriptionRepo

        desc = await asyncio.to_thread(SystemDescriptionRepo.get_latest)
        if desc is None:
            return {"status": "not_found", "message": "系统描述尚未构建"}

        return {
            "status": "ok",
            "version": desc.version,
            "token_count": desc.token_count,
            "generated_at": desc.generated_at,
            "generated_by": desc.generated_by,
            "schema_hash": desc.schema_hash[:12] + "...",
            "permission_hash": desc.permission_hash[:12] + "...",
            "file_model_hash": desc.file_model_hash[:12] + "...",
            "content_text": desc.content_text,
        }
    except Exception as e:
        logger.error("get_system_description failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system-description/check")
async def check_system_description_drift():
    """检测系统描述偏差。"""
    try:
        from emily_core.services.schema_drift_detector import SchemaDriftDetector

        detector = SchemaDriftDetector()
        result = detector.detect()
        return {"status": "ok", "data": result}
    except Exception as e:
        logger.error("check_system_description_drift failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
