# emily-core/emily_core/skill/definition.py
"""Skill 定义 dataclass —— 三段结构（instructions / tools / steps）。

设计原则：
  - Skill 不持有数据，不声明 datasets
  - 数据来源 = session-context（source: context）
  - 数据边界 = session-context + 工具层自动过滤
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParamMapping:
    """参数来源映射 —— 定义 tool_params 中每个参数值如何获取。"""

    source: str          # "user_input" | "prev_step" | "fixed" | "context"
    path: str = ""       # prev_step / context 时的 dot-path
    value: Any = None    # fixed 时的固定值
    extraction: str = "" # user_input 时的 LLM 提取提示
    required: bool = False
    default: Any = None
    enum: list[str] = field(default_factory=list)
    max_length: int = 0


@dataclass(frozen=True)
class SkillStep:
    """Skill 中的单个执行步骤。"""

    id: str
    description: str
    tool_name: str
    tool_params: dict[str, ParamMapping] = field(default_factory=dict)
    output_key: str = ""


@dataclass(frozen=True)
class SkillTool:
    """Skill 中引用的工具声明（白名单）。"""

    name: str
    description: str = ""


@dataclass(frozen=True)
class SkillDefinition:
    """完整 Skill 定义 —— 三段结构。"""

    skill_id: str
    sop_id: str
    version: str
    display_name: str
    instructions: str
    tools: list[SkillTool]
    steps: list[SkillStep]
