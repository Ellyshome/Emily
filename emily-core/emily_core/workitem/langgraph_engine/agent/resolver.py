# emily-core/emily_core/workitem/langgraph_engine/agent/resolver.py
"""ParamResolver —— 参数解析器，作为 function-calling tool 暴露给 LLM。

三层权限模型第二层：超 session 读（查全表）+ session 约束输出过滤。
不泄漏不可见资源的存在性（accessible 外项目返回 found=False，不返回候选）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ....repositories.project_repo import ProjectRepository

logger = logging.getLogger("emily.langgraph.resolver")


class ParamResolver(ABC):
    """参数解析器 ABC —— 作为 function-calling tool 暴露给 LLM。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名（如 'resolve_project'）。"""

    @property
    @abstractmethod
    def spec(self) -> dict:
        """OpenAI tool spec（{"type":"function","function":{...}}）。"""

    @abstractmethod
    async def handle(self, params: dict, session_ctx: Any) -> dict:
        """执行解析。返回 dict（含 found/project_id/candidates/error）。"""


class ProjectResolver(ParamResolver):
    """项目名 → UUID 解析器。三层权限模型第二层。

    ① 超范围读：ProjectRepository.find_by_name_fuzzy 查全表
    ② session 约束：只在 session_ctx.project_ids 集合内解析
    ③ 输出过滤：accessible 外项目不泄漏存在性
    """

    @property
    def name(self) -> str:
        return "resolve_project"

    @property
    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "resolve_project",
                "description": (
                    "项目名称 → UUID 解析。当工具参数需要 project_id（UUID）但你只有项目名时，"
                    "先调本工具拿 project_id。返回 found=true 时含 project_id；"
                    "返回 candidates 时表示有多个匹配，需向用户确认；found=false 表示未找到。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "项目名称（人类可读，如 '翠湖庭院'）",
                        }
                    },
                    "required": ["project_name"],
                },
            },
        }

    async def handle(self, params: dict, session_ctx: Any) -> dict:
        import asyncio

        value = (params.get("project_name") or "").strip()
        if not value:
            return {"found": False, "error": "未提供项目名称"}

        # ① 超范围读：模糊查全表
        matches = await asyncio.to_thread(ProjectRepository.find_by_name_fuzzy, value)
        if not matches:
            return {"found": False, "error": f"未找到项目'{value}'"}

        # ② session 约束：只在用户可访问项目集合内解析
        accessible = set(getattr(session_ctx, "project_ids", []) or []) if session_ctx else set()
        if accessible:
            in_scope = [m for m in matches if m.id in accessible]
        else:
            # session_ctx 无 project_ids（私聊超管等）→ 放行全部匹配
            in_scope = matches

        # ③ 输出过滤：accessible 外不泄漏
        if not in_scope:
            return {"found": False, "error": f"未找到项目'{value}'"}  # 不泄漏存在性

        if len(in_scope) > 1:
            return {
                "found": False,
                "candidates": [{"id": m.id, "name": m.name} for m in in_scope],
                "error": f"找到 {len(in_scope)} 个匹配项目，请确认具体是哪一个",
            }

        return {"found": True, "project_id": in_scope[0].id, "project_name": in_scope[0].name}


@dataclass
class ResolverRegistry:
    """Resolver 注册表。"""
    _resolvers: dict[str, ParamResolver] = field(default_factory=dict)

    def register(self, resolver: ParamResolver) -> None:
        self._resolvers[resolver.name] = resolver

    def list_all(self) -> list[ParamResolver]:
        return list(self._resolvers.values())

    def get(self, name: str) -> ParamResolver | None:
        return self._resolvers.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._resolvers


def build_default_resolvers() -> ResolverRegistry:
    """构建默认 resolver 集合（EmilyCore 启动时调用）。"""
    reg = ResolverRegistry()
    reg.register(ProjectResolver())
    return reg
