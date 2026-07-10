"""evolution.py — 进化管理 API 路由。

管理员专属 API（permission_level >= 5）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("emily.api.evolution")

router = APIRouter(prefix="/api/v1/evolution", tags=["evolution"])


# ══════════════════════════════════════════════════════════════════════════════
# 洞察
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/insights/generate")
async def generate_insight(
    date: str = Query(default="", description="复盘结束日期 YYYY-MM-DD"),
    days: int = Query(default=1, description="复盘天数（默认1，最小1）"),
):
    """手动触发生成洞察。"""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    try:
        from emily_core.services.evolution.insight_generator import InsightGenerator
        generator = InsightGenerator()
        result = await generator.generate(date, days=days, dry_run=False)
        return {"status": "ok", "data": result}
    except Exception as e:
        logger.error("generate_insight failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/insights/{date}")
async def get_insight(date: str):
    """查看洞察（date 支持 YYYY-MM-DD 或 YYYY-MM-DD~YYYY-MM-DD）。"""
    from emily_core.repositories.evolution_repo import EvolutionRepo
    import asyncio

    insight = await asyncio.to_thread(EvolutionRepo.get_insight_by_date, date)
    if insight is None:
        raise HTTPException(status_code=404, detail=f"洞察 {date} 不存在")

    return {
        "insight_date": insight.insight_date,
        "analysis_days": insight.analysis_days,
        "total_messages": insight.total_messages,
        "sop_hit_rate": insight.sop_hit_rate,
        "fallback_rate": insight.fallback_rate,
        "health_score": insight.health_score,
        "anomaly_flags": insight.anomaly_flags,
        "insight_text": insight.insight_text,
        "created_at": insight.created_at,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 规则
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/rules/induct")
async def induct_rules(
    end_date: str = Query(default="", description="分析截止日期"),
    days: int = Query(default=7, description="回顾天数"),
):
    """手动触发规则归纳。"""
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    try:
        from emily_core.services.evolution.rule_inductor import RuleInductor
        inductor = RuleInductor()
        rules = await inductor.induct(end_date, days=days)
        return {"status": "ok", "rules": rules}
    except Exception as e:
        logger.error("induct_rules failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rules")
async def list_rules(
    status: str = Query(default="CONFIRMED", description="状态过滤"),
):
    """列出规则。"""
    from emily_core.repositories.evolution_repo import EvolutionRepo
    import asyncio

    rules = await asyncio.to_thread(EvolutionRepo.get_rules_by_status, status)
    return {
        "rules": [
            {
                "rule_no": r.rule_no,
                "title": r.title,
                "category": r.category,
                "confidence": r.confidence,
                "status": r.status,
                "suggested_action": r.suggested_action,
                "created_at": r.created_at,
            }
            for r in rules
        ]
    }


@router.post("/rules/{rule_no}/confirm")
async def confirm_rule(rule_no: str):
    """确认规则。"""
    from emily_core.repositories.evolution_repo import EvolutionRepo
    import asyncio

    success = await asyncio.to_thread(
        EvolutionRepo.update_rule_status,
        rule_no,
        "CONFIRMED",
        confirmed_at=datetime.now().isoformat(),
    )
    if not success:
        raise HTTPException(status_code=404, detail=f"规则 {rule_no} 不存在")
    return {"status": "ok", "rule_no": rule_no, "new_status": "CONFIRMED"}


@router.post("/rules/{rule_no}/discard")
async def discard_rule(rule_no: str):
    """废弃规则。"""
    from emily_core.repositories.evolution_repo import EvolutionRepo
    import asyncio

    success = await asyncio.to_thread(
        EvolutionRepo.update_rule_status,
        rule_no,
        "DISCARDED",
    )
    if not success:
        raise HTTPException(status_code=404, detail=f"规则 {rule_no} 不存在")
    return {"status": "ok", "rule_no": rule_no, "new_status": "DISCARDED"}


# ══════════════════════════════════════════════════════════════════════════════
# 补丁
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/patches/generate")
async def generate_patches(
    rule_no: str = Query(default="", description="指定规则编号"),
):
    """手动触发生成补丁。"""
    try:
        from emily_core.services.evolution.patch_generator import PatchGenerator
        generator = PatchGenerator()
        rule_nos = [rule_no] if rule_no else None
        patches = await generator.generate(rule_nos)
        return {"status": "ok", "patches": patches}
    except Exception as e:
        logger.error("generate_patches failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patches")
async def list_patches(
    status: str = Query(default="DRAFT", description="状态过滤"),
):
    """列出补丁。"""
    from emily_core.repositories.evolution_repo import EvolutionRepo
    import asyncio

    patches = await asyncio.to_thread(EvolutionRepo.get_patches_by_status, status)
    return {
        "patches": [
            {
                "patch_no": p.patch_no,
                "rule_no": p.rule_no,
                "target_path": p.target_path,
                "patch_type": p.patch_type,
                "risk_level": p.risk_level,
                "status": p.status,
                "risk_reasoning": p.risk_reasoning,
                "created_at": p.created_at,
            }
            for p in patches
        ]
    }


@router.post("/patches/{patch_no}/approve")
async def approve_patch(patch_no: str):
    """审批补丁。"""
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    result = await applier.approve(patch_no)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Unknown error"))
    return result


@router.post("/patches/{patch_no}/reject")
async def reject_patch(patch_no: str, reason: str = ""):
    """拒绝补丁。"""
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    result = await applier.reject(patch_no, reason)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Unknown error"))
    return result


@router.post("/patches/{patch_no}/rollback")
async def rollback_patch(patch_no: str):
    """回滚补丁。"""
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    result = await applier.rollback(patch_no)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "Unknown error"))
    return result
