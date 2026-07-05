"""Skill 管理 API —— 热重载等运维端点。"""

from __future__ import annotations

from fastapi import APIRouter

from ..server import get_core

router = APIRouter(tags=["skills"])


@router.post("/skills/reload")
async def reload_skills():
    """热重载 Skill 注册表（无需重启容器）。

    适用场景：sop_to_skill.py 转换新 Skill 后，调用此端点
    使运行中的 EmilyCore 感知新的 .skill.yaml 文件。
    """
    core = get_core()
    return core.reload_skills()
