# emily-core/emily_core/skill/__init__.py
"""Skill 模块 —— 降级为 SOP .md 索引器（L3 agent loop 迁移后）。"""

from .registry import SkillRegistry, SkillRegistryStatus, SopDoc

__all__ = [
    "SkillRegistry",
    "SkillRegistryStatus",
    "SopDoc",
]
