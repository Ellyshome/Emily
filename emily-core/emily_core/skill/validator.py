# emily-core/emily_core/skill/validator.py
"""Skill 定义校验器。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .definition import SkillDefinition, SkillStep, ParamMapping

_VALID_SOURCES = {"user_input", "prev_step", "fixed", "context"}


@dataclass
class SkillValidationResult:
    """校验结果。"""
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_skill(skill: SkillDefinition) -> SkillValidationResult:
    """校验 SkillDefinition 的完整性和合法性。"""
    result = SkillValidationResult()

    # ── 必填字段 ──
    if not skill.skill_id:
        result.errors.append("skill_id 不能为空")
    if not skill.sop_id:
        result.errors.append("sop_id 不能为空")
    if not skill.version:
        result.errors.append("version 不能为空")
    if not skill.display_name:
        result.errors.append("display_name 不能为空")

    # ── steps 校验 ──
    if not skill.steps:
        result.errors.append("steps 不能为空——Skill 必须至少有一个步骤")
    else:
        step_ids: set[str] = set()
        for i, step in enumerate(skill.steps):
            if not step.id:
                result.errors.append(f"steps[{i}].id 不能为空")
            if step.id in step_ids:
                result.errors.append(f"steps[{i}].id 重复: {step.id}")
            step_ids.add(step.id)

            if not step.tool_name:
                result.warnings.append(f"steps[{i}].tool_name 为空（纯逻辑步骤，不调用工具）")

            # tool_params source 校验
            for pname, mapping in step.tool_params.items():
                if mapping.source not in _VALID_SOURCES:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}.source 非法: {mapping.source}"
                    )
                if mapping.source == "prev_step" and not mapping.path:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=prev_step 时 path 不能为空"
                    )
                if mapping.source == "context" and not mapping.path:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=context 时 path 不能为空"
                    )
                if mapping.source == "fixed" and mapping.value is None:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=fixed 时 value 不能为 None"
                    )
                if mapping.source == "user_input" and not mapping.extraction:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=user_input 时 extraction 不能为空"
                    )

    # ── tools 校验 ──
    if not skill.tools:
        result.warnings.append("tools 为空——Skill 未声明任何可用工具")
    else:
        tool_names = {t.name for t in skill.tools}
        # 检查 steps 中的 tool_name 是否在 tools 白名单中（M3: __DYNAMIC__ 是特殊值，跳过检查）
        for i, step in enumerate(skill.steps):
            if step.tool_name and step.tool_name != "__DYNAMIC__" and step.tool_name not in tool_names:
                result.warnings.append(
                    f"steps[{i}].tool_name='{step.tool_name}' 不在 tools 白名单中"
                )

    result.is_valid = len(result.errors) == 0
    return result
