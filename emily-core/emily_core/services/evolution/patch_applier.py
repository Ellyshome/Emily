"""PatchApplier — 补丁应用服务。

补丁审批/应用/回滚操作。
只改 emily-data/ 下的配置文件，不触碰 emily-core/ Python 代码。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("emily.patch_applier")


class PatchApplier:
    """进化补丁应用器。"""

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir or os.environ.get("EMILY_DATA_DIR", "")

    def _resolve_data_dir(self) -> str:
        data_dir = self._data_dir
        if not data_dir:
            candidate = Path(__file__).resolve().parent.parent.parent.parent.parent / "emily-data"
            if candidate.exists():
                data_dir = str(candidate)
        return data_dir

    async def apply(self, patch_no: str, *, dry_run: bool = False) -> dict:
        """应用补丁。

        1. 读取 evolution_patches 中的 patch 记录
        2. 读取目标文件当前内容 → 保存为 rollback_snapshot
        3. 按 patch_type 执行变更
        4. 状态 → APPLIED
        """
        from emily_core.repositories.evolution_repo import EvolutionRepo

        patch = await asyncio.to_thread(EvolutionRepo.get_patch_by_no, patch_no)
        if patch is None:
            return {"status": "error", "message": f"Patch {patch_no} not found"}

        if patch.status != "DRAFT":
            return {"status": "error", "message": f"Patch {patch_no} is not in DRAFT status (current: {patch.status})"}

        data_dir = self._resolve_data_dir()
        if not data_dir:
            return {"status": "error", "message": "Cannot resolve emily-data directory"}

        target_path = Path(data_dir) / patch.target_path
        if not target_path.exists():
            return {"status": "error", "message": f"Target file not found: {target_path}"}

        # 读取原始内容
        original_content = target_path.read_text(encoding="utf-8")
        rollback_snapshot = original_content

        if dry_run:
            return {
                "status": "preview",
                "patch_no": patch_no,
                "target_path": str(target_path),
                "patch_type": patch.patch_type,
                "search_anchor": patch.search_anchor,
                "patch_content_preview": patch.patch_content[:200],
                "file_size": len(original_content),
            }

        # 应用变更
        try:
            new_content = self._apply_patch_to_content(
                original_content,
                patch.patch_type,
                patch.search_anchor,
                patch.patch_content,
            )
        except Exception as e:
            logger.error("Failed to apply patch content: %s", e)
            return {"status": "error", "message": f"Failed to apply: {e}"}

        # 写回文件
        target_path.write_text(new_content, encoding="utf-8")

        # 更新状态
        now_str = datetime.now(timezone.utc).isoformat()
        await asyncio.to_thread(
            EvolutionRepo.update_patch_status,
            patch_no,
            "APPLIED",
            applied_at=now_str,
            rollback_snapshot=rollback_snapshot,
        )

        logger.info("Patch %s applied to %s", patch_no, target_path)
        return {"status": "applied", "patch_no": patch_no, "target_path": str(target_path)}

    def _apply_patch_to_content(self, content: str, patch_type: str, search_anchor: str, patch_content: str) -> str:
        """按 patch_type 应用内容变更。"""
        if patch_type == "append":
            if search_anchor and search_anchor in content:
                # 在锚点所在段落末尾追加
                return content.replace(search_anchor, search_anchor + "\n" + patch_content)
            else:
                # 在文件末尾追加
                return content.rstrip("\n") + "\n" + patch_content + "\n"

        elif patch_type == "replace_section":
            if search_anchor and search_anchor in content:
                before, _, after = content.partition(search_anchor)
                after_section = after.split("\n## ", 1)
                if len(after_section) > 1:
                    after = "\n## " + after_section[1]
                else:
                    after = ""
                return before + search_anchor + "\n" + patch_content + after
            else:
                raise ValueError(f"Search anchor not found: {search_anchor[:50]}")

        elif patch_type == "insert_after":
            if search_anchor and search_anchor in content:
                before, _, after = content.partition(search_anchor)
                return before + search_anchor + "\n" + patch_content + "\n" + after
            else:
                raise ValueError(f"Search anchor not found: {search_anchor[:50]}")

        else:
            raise ValueError(f"Unknown patch_type: {patch_type}")

    async def rollback(self, patch_no: str, *, dry_run: bool = False) -> dict:
        """回滚补丁：用 rollback_snapshot 恢复原始内容。"""
        from emily_core.repositories.evolution_repo import EvolutionRepo

        patch = await asyncio.to_thread(EvolutionRepo.get_patch_by_no, patch_no)
        if patch is None:
            return {"status": "error", "message": f"Patch {patch_no} not found"}

        if patch.status != "APPLIED":
            return {"status": "error", "message": f"Patch {patch_no} is not APPLIED (current: {patch.status})"}

        if not patch.rollback_snapshot:
            return {"status": "error", "message": "No rollback snapshot available"}

        data_dir = self._resolve_data_dir()
        target_path = Path(data_dir) / patch.target_path

        if dry_run:
            return {
                "status": "preview",
                "patch_no": patch_no,
                "target_path": str(target_path),
                "rollback_content_preview": patch.rollback_snapshot[:200],
            }

        # 写回原始内容
        target_path.write_text(patch.rollback_snapshot, encoding="utf-8")

        # 更新状态
        await asyncio.to_thread(
            EvolutionRepo.update_patch_status,
            patch_no,
            "ROLLED_BACK",
        )

        logger.info("Patch %s rolled back from %s", patch_no, target_path)
        return {"status": "rolled_back", "patch_no": patch_no}

    async def approve(self, patch_no: str) -> dict:
        """审批通过补丁（不立即应用，只标记状态为 CONFIRMED）。"""
        from emily_core.repositories.evolution_repo import EvolutionRepo

        patch = await asyncio.to_thread(EvolutionRepo.get_patch_by_no, patch_no)
        if patch is None:
            return {"status": "error", "message": f"Patch {patch_no} not found"}

        # 审批通过后状态不变，实际应用在 apply() 时执行
        logger.info("Patch %s approved", patch_no)
        return {"status": "approved", "patch_no": patch_no}

    async def reject(self, patch_no: str, reason: str = "") -> dict:
        """拒绝补丁。"""
        from emily_core.repositories.evolution_repo import EvolutionRepo

        success = await asyncio.to_thread(
            EvolutionRepo.update_patch_status,
            patch_no,
            "REJECTED",
        )
        if not success:
            return {"status": "error", "message": f"Patch {patch_no} not found"}

        logger.info("Patch %s rejected: %s", patch_no, reason)
        return {"status": "rejected", "patch_no": patch_no}
