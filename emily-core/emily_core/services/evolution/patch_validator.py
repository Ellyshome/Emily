"""PatchValidator — 补丁效果验证服务。

对已应用 ≥ 7 天的补丁验证效果：对比前后指标 → CONFIRMED / ROLLED_BACK。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("emily.patch_validator")


class PatchValidator:
    """补丁效果验证器。"""

    async def validate(self, patch_nos: list[str] | None = None, *, dry_run: bool = False) -> list[dict]:
        """验证补丁效果。

        Args:
            patch_nos: 指定补丁编号，None 则验证所有 APPLIED >= 7 天的补丁
            dry_run: 预览模式

        Returns:
            验证结果列表
        """
        from emily_core.repositories.evolution_repo import EvolutionRepo

        # 1. 查待验证补丁
        if patch_nos:
            patches = []
            for pn in patch_nos:
                p = await asyncio.to_thread(EvolutionRepo.get_patch_by_no, pn)
                if p:
                    patches.append(p)
        else:
            all_applied = await asyncio.to_thread(EvolutionRepo.get_patches_by_status, "APPLIED")
            # 筛选 applied >= 7 天的
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            patches = [p for p in all_applied if p.applied_at and p.applied_at < cutoff]

        if not patches:
            logger.info("No patches to validate")
            return []

        results = []
        for patch in patches:
            try:
                # 2. 获取验证标准
                criteria = patch.validation_criteria

                # 3. 对比前后指标（简化：检查 system health score 趋势）
                applied_date = patch.applied_at[:10] if patch.applied_at else ""
                if not applied_date:
                    result = {"status": "error", "patch_no": patch.patch_no, "message": "No applied_at date"}
                    results.append(result)
                    continue

                # 查应用前后各7天的洞察
                applied_dt = datetime.fromisoformat(applied_date)
                before_start = (applied_dt - timedelta(days=7)).strftime("%Y-%m-%d")
                before_end = applied_date

                after_start = applied_date
                after_dt = applied_dt + timedelta(days=7)
                after_end = after_dt.strftime("%Y-%m-%d")

                before_insights = await asyncio.to_thread(
                    EvolutionRepo.get_insights_range, before_start, before_end
                )
                after_insights = await asyncio.to_thread(
                    EvolutionRepo.get_insights_range, after_start, after_end
                )

                # 计算平均健康评分
                before_scores = [i.health_score for i in before_insights if i.health_score > 0]
                after_scores = [i.health_score for i in after_insights if i.health_score > 0]

                avg_before = sum(before_scores) / len(before_scores) if before_scores else 0
                avg_after = sum(after_scores) / len(after_scores) if after_scores else 0

                if dry_run:
                    results.append({
                        "status": "preview",
                        "patch_no": patch.patch_no,
                        "avg_health_before": round(avg_before, 1),
                        "avg_health_after": round(avg_after, 1),
                        "criteria": criteria,
                    })
                    continue

                # 4. 判定
                if avg_after >= avg_before or not before_scores:
                    new_status = "CONFIRMED"
                    decision = "improved_or_stable"
                else:
                    new_status = "ROLLED_BACK"
                    decision = "degraded"
                    # 自动回滚
                    try:
                        from emily_core.services.evolution.patch_applier import PatchApplier
                        applier = PatchApplier()
                        await applier.rollback(patch.patch_no)
                        logger.warning("Auto-rolled back patch %s due to degradation", patch.patch_no)
                    except Exception as e:
                        logger.error("Failed to auto-rollback patch %s: %s", patch.patch_no, e)

                now_str = datetime.now(timezone.utc).isoformat()
                validation_result = json.dumps({
                    "decision": decision,
                    "avg_health_before": round(avg_before, 1),
                    "avg_health_after": round(avg_after, 1),
                    "criteria": criteria,
                })

                await asyncio.to_thread(
                    EvolutionRepo.update_patch_status,
                    patch.patch_no,
                    new_status,
                    validated_at=now_str,
                    validation_result=validation_result,
                )

                results.append({
                    "status": new_status.lower(),
                    "patch_no": patch.patch_no,
                    "decision": decision,
                    "avg_health_before": round(avg_before, 1),
                    "avg_health_after": round(avg_after, 1),
                })

            except Exception as e:
                logger.error("Failed to validate patch %s: %s", patch.patch_no, e, exc_info=True)
                results.append({"status": "error", "patch_no": patch.patch_no, "error": str(e)})

        return results
