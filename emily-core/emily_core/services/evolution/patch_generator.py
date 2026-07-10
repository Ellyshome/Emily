"""PatchGenerator — 进化补丁生成服务。

从 CONFIRMED 规则生成配置文件变更补丁。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("emily.patch_generator")


class PatchGenerator:
    """进化补丁生成器。"""

    CATEGORY_TO_TARGET = {
        "prompt": "prompts/session.md",
        "routing": "prompts/session.md",
        "sop": "sops/",
        "skill": "skills/",
        "hook": "config/hook_config.json",
        "user_memory": "user_memory/",
    }

    def __init__(self, llm_client=None, data_dir: str = ""):
        self._llm = llm_client
        self._data_dir = data_dir or os.environ.get("EMILY_DATA_DIR", "")

    async def generate(self, rule_nos: list[str] | None = None, *, dry_run: bool = False) -> list[dict]:
        """补丁生成流水线。

        Args:
            rule_nos: 指定规则编号列表，为 None 则为所有 CONFIRMED 规则生成
            dry_run: 预览模式

        Returns:
            生成的补丁列表
        """
        from emily_core.repositories.evolution_repo import EvolutionRepo

        # 1. 查规则
        if rule_nos:
            rules = []
            for rn in rule_nos:
                rule = await asyncio.to_thread(EvolutionRepo.get_rule_by_no, rn)
                if rule:
                    rules.append(rule)
        else:
            rules = await asyncio.to_thread(EvolutionRepo.get_rules_by_status, "CONFIRMED")

        if not rules:
            logger.info("No rules to generate patches for")
            return []

        # 2. 确定数据目录
        data_dir = self._data_dir
        if not data_dir:
            candidate = Path(__file__).resolve().parent.parent.parent.parent.parent / "emily-data"
            if candidate.exists():
                data_dir = str(candidate)

        if not data_dir:
            logger.warning("Cannot determine emily-data directory")
            if dry_run:
                return [{"status": "preview", "rules_count": len(rules)}]
            return []

        patches = []

        for rule in rules:
            category = rule.category
            target_path = self.CATEGORY_TO_TARGET.get(category, "")

            # 读取目标文件内容
            current_content = ""
            if target_path and data_dir:
                full_path = Path(data_dir) / target_path
                if full_path.exists() and full_path.is_file():
                    current_content = full_path.read_text(encoding="utf-8")[:5000]
                else:
                    current_content = f"(文件不存在: {target_path})"

            if not self._llm or dry_run:
                patches.append({
                    "status": "preview",
                    "rule_no": rule.rule_no,
                    "category": category,
                    "target_path": target_path,
                    "current_content_preview": current_content[:200],
                })
                continue

            # 3. LLM 生成补丁
            try:
                from emily_core.infrastructure.llm.prompt_loader import load_prompt

                template = load_prompt("evolution_patch")

                user_message = template.replace("{rule_no}", rule.rule_no)
                user_message = user_message.replace("{rule_title}", rule.title)
                user_message = user_message.replace("{rule_description}", rule.description)
                user_message = user_message.replace("{rule_category}", category)
                user_message = user_message.replace("{rule_suggested_action}", rule.suggested_action)
                user_message = user_message.replace("{current_target_content}", current_content)
                user_message = user_message.replace("{target_path}", target_path)

                result = await self._llm.chat_json(template, user_message)
            except Exception as e:
                logger.error("LLM patch generation failed for rule %s: %s", rule.rule_no, e, exc_info=True)
                patches.append({"status": "llm_error", "rule_no": rule.rule_no, "error": str(e)})
                continue

            # 4. 写 DB
            try:
                patch_no = await asyncio.to_thread(EvolutionRepo.generate_patch_no)
                await asyncio.to_thread(
                    EvolutionRepo.create_patch,
                    patch_no,
                    rule.rule_no,
                    target_type=result.get("target_type", category),
                    target_path=result.get("target_path", target_path),
                    patch_content=result.get("patch_content", ""),
                    patch_type=result.get("patch_type", ""),
                    search_anchor=result.get("search_anchor", ""),
                    risk_level=result.get("risk_level", ""),
                    risk_reasoning=result.get("risk_reasoning", ""),
                    validation_criteria=result.get("validation_criteria", ""),
                    expected_effect=result.get("expected_effect", ""),
                )
                result["patch_no"] = patch_no
                result["rule_no"] = rule.rule_no
                result["status"] = "generated"
                patches.append(result)
            except Exception as e:
                logger.error("Failed to save patch for rule %s: %s", rule.rule_no, e, exc_info=True)
                patches.append({"status": "save_error", "rule_no": rule.rule_no, "error": str(e)})

        return patches
