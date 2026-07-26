# emily-core/emily_core/skill/parser.py
"""Skill YAML 解析器 —— 将 .skill.yaml 文件解析为 SkillDefinition。"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .definition import ParamMapping, SkillStep, SkillTool, SkillDefinition
from .validator import validate_skill

logger = logging.getLogger("emily.skill.parser")


def _extract_sop_id_from_stem(stem: str) -> str:
    """从文件名 stem 推导 sop_id。

    格式示例：
      SOP-002-REC-event-record → SOP-002-REC
      SOP-002-REC → SOP-002-REC
      SOP-011-SYS-node_manage → SOP-011-SYS
    """
    if not stem:
        return ""
    parts = stem.split("-")
    # SOP 编号格式: SOP-NNN-TYPE[-suffix]
    if len(parts) >= 3 and parts[0] == "SOP":
        return f"{parts[0]}-{parts[1]}-{parts[2]}"
    return stem


class SkillParseError(Exception):
    """Skill 解析异常。"""


def _parse_param_mapping(name: str, data: dict) -> ParamMapping:
    """将 dict 解析为 ParamMapping。"""
    if not isinstance(data, dict):
        # 简写：直接是值 → fixed
        return ParamMapping(source="fixed", value=data)

    source = data.get("source", "fixed")
    return ParamMapping(
        source=source,
        path=data.get("path", ""),
        value=data.get("value"),
        extraction=data.get("extraction", ""),
        required=data.get("required", False),
        default=data.get("default"),
        enum=data.get("enum", []),
        max_length=data.get("max_length", 0),
    )


def _parse_step(data: dict) -> SkillStep:
    """将 dict 解析为 SkillStep。"""
    tool_params: dict[str, ParamMapping] = {}
    raw_params = data.get("tool_params", {})

    if isinstance(raw_params, dict):
        # 规范格式：{param_name: {source: ..., ...}}
        for pname, pdata in raw_params.items():
            tool_params[pname] = _parse_param_mapping(pname, pdata)
    elif isinstance(raw_params, list):
        # LLM 常见输出格式：[{name: param_name, source: ..., ...}]
        for item in raw_params:
            if not isinstance(item, dict):
                continue
            pname = item.get("name", "")
            if not pname:
                continue
            # 将 list 格式转为 dict 格式的 pdata（去掉 name 字段）
            pdata = {k: v for k, v in item.items() if k != "name"}
            tool_params[pname] = _parse_param_mapping(pname, pdata)

    return SkillStep(
        id=data.get("id", ""),
        description=data.get("description", ""),
        tool_name=data.get("tool_name", "") or None,
        tool_params=tool_params,
        output_key=data.get("output_key", ""),
    )


def _parse_tool(data: dict) -> SkillTool:
    """将 dict 解析为 SkillTool。"""
    return SkillTool(
        name=data.get("name", ""),
        description=data.get("description", ""),
    )


def parse_skill_text(text: str, source_name: str = "<text>") -> SkillDefinition:
    """解析 Skill YAML 文本为 SkillDefinition。

    Args:
        text: YAML 文本内容
        source_name: 来源标识（用于错误提示）

    Returns:
        SkillDefinition

    Raises:
        SkillParseError: YAML 格式错误或校验失败
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SkillParseError(f"YAML 解析失败 ({source_name}): {e}") from e

    if not isinstance(data, dict):
        raise SkillParseError(f"Skill 文件根节点必须是 dict ({source_name})")

    # 自动修复：从文件名推导缺失的顶层字段
    # LLM 生成时常截断 skill_id/sop_id/version/display_name，
    # 文件名格式为 "SOP-002-REC.skill.yaml" 或 "SOP-002-REC-event-record.skill.yaml"
    filename_stem = Path(source_name).stem.replace(".skill", "") if source_name != "<text>" else ""
    _inferred_sop_id = _extract_sop_id_from_stem(filename_stem)

    if not data.get("skill_id") and filename_stem:
        data["skill_id"] = filename_stem
    if not data.get("sop_id") and _inferred_sop_id:
        data["sop_id"] = _inferred_sop_id
    if not data.get("version"):
        data["version"] = "1.0"
    if not data.get("display_name") and _inferred_sop_id:
        data["display_name"] = _inferred_sop_id

    # 解析 tools（M3: 支持 auto_generate: true 语法）
    raw_tools = data.get("tools", [])
    if isinstance(raw_tools, dict) and raw_tools.get("auto_generate"):
        tools = []  # 由 SkillRegistry._derive_sop999_tools() 运行时填充
    else:
        tools = [_parse_tool(t) for t in raw_tools]

    # 解析 steps
    steps = [_parse_step(s) for s in data.get("steps", [])]

    # 自动修复：LLM 输出截断导致 steps 为空时，从 tools 生成兜底步骤
    if not steps and tools:
        logger.info("Skill %s: steps 为空，从 tools 自动生成兜底步骤", source_name)
        for i, tool in enumerate(tools, 1):
            steps.append(SkillStep(
                id=f"step-{i:02d}",
                description=f"调用 {tool.name}",
                tool_name=tool.name,
                tool_params={},
                output_key=f"{tool.name}_result",
            ))

    # 构建 SkillDefinition
    skill = SkillDefinition(
        skill_id=data.get("skill_id", ""),
        sop_id=data.get("sop_id", ""),
        version=str(data.get("version", "")),
        display_name=data.get("display_name", ""),
        instructions=data.get("instructions", ""),
        tools=tools,
        steps=steps,
    )

    # 校验
    result = validate_skill(skill)
    if not result.is_valid:
        raise SkillParseError(
            f"Skill 校验失败 ({source_name}): {'; '.join(result.errors)}"
        )

    if result.warnings:
        for w in result.warnings:
            logger.warning("Skill %s 校验警告: %s", source_name, w)

    return skill


def parse_skill_file(path: Path) -> SkillDefinition:
    """读取 Skill YAML 文件并解析。"""
    path = Path(path)
    if not path.exists():
        raise SkillParseError(f"文件不存在: {path}")

    text = path.read_text(encoding="utf-8")
    return parse_skill_text(text, source_name=path.name)
