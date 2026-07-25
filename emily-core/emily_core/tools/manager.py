"""ToolManager — BusinessFlowToolRegistry 的对外聚合层。

补三件事：统一调用入口、自描述、CLI/HTTP 测试接口。
不替代 Registry，注册仍走 tools/registry.py 的 register_all。

边界：ToolManager 只管 LLM 运行时工具（BusinessFlowTool.handler，进程内 async）。
开发者/维护脚本（scripts/*.py，subprocess CLI）归 ScriptManager 管，见 emily_core/scripts/manager.py。
两者共享 service 层（node_batch / InsightGenerator 等），互不调用。
"""

from __future__ import annotations
import logging
from typing import Any

from .business_flow_tools import BusinessFlowTool, BusinessFlowToolRegistry

logger = logging.getLogger("emily.tool.manager")


class ToolManager:
    """BusinessFlowToolRegistry 的聚合层，提供统一调用/自描述/测试接口。

    仅管 LLM 运行时工具；开发者脚本见 ScriptManager。
    """

    def __init__(self, registry: BusinessFlowToolRegistry):
        self._registry = registry

    # ── 自描述 ────────────────────────────────────────

    def list(self) -> list[dict]:
        """列出所有工具的元信息（轻量，不含 schema）。"""
        return [
            {
                "name": t.name,
                "category": t.category,
                "permission": t.permission_flag,
                "description": t.description,
                "has_schema": bool(t.parameters and t.parameters.get("properties")),
            }
            for t in self._registry._tools.values()
        ]

    def describe(self, name: str | None = None) -> dict:
        """单个或全部工具的完整描述（含 schema，AI 友好）。

        Args:
            name: 指定工具名；None 返回全部。
        Returns:
            name 非空: {"name", "category", "permission", "description", "parameters"}
            name 为空: {"tools": [...], "count": N}
            工具不存在: {"error": "...", "code": 2}
        """
        if name:
            t = self._registry.get(name)
            if not t:
                return {"error": f"tool '{name}' not found", "code": 2}
            return self._tool_to_dict(t)
        tools = [self._tool_to_dict(t) for t in self._registry._tools.values()]
        return {"tools": tools, "count": len(tools)}

    def schema(self, name: str) -> dict:
        """单工具 JSON Schema（仅 parameters）。"""
        t = self._registry.get(name)
        return t.parameters if t else {}

    def export(self) -> dict:
        """导出全部工具 schema，供 AI prompt 注入。等价 describe(name=None)。"""
        return self.describe(None)

    # ── 统一调用 ──────────────────────────────────────

    async def call(self, name: str, params: dict | None = None) -> dict:
        """统一调用入口。绕过 SOP，直接调 handler。

        Returns:
            成功: {"success": True, "result": <handler 返回>, "tool": name}
            失败: {"success": False, "error": "...", "tool": name, "code": 1}
            不存在: {"success": False, "error": "...", "tool": name, "code": 2}
        """
        t = self._registry.get(name)
        if not t:
            return {"success": False, "error": f"tool '{name}' not found",
                    "tool": name, "code": 2}
        try:
            result = await t.handler(params or {})
            return {"success": True, "result": result, "tool": name}
        except Exception as e:
            logger.warning("toolmgr call '%s' failed: %s", name, e, exc_info=True)
            return {"success": False, "error": str(e), "tool": name, "code": 1}

    # ── 依赖就绪检查 ──────────────────────────────────

    def selfcheck(self) -> dict:
        """检查每个工具的依赖是否就绪（handler 是否可调用）。

        策略：检查 handler 是否为 stub（knowledge_search 的 _rag_stub 等已知 stub 模式）。
        返回 [{"name", "category", "ready", "note"}]
        """
        results = []
        for t in self._registry._tools.values():
            ready, note = self._check_ready(t)
            results.append({"name": t.name, "category": t.category,
                            "ready": ready, "note": note})
        return {"tools": results, "count": len(results)}

    def _check_ready(self, t: BusinessFlowTool) -> tuple[bool, str]:
        """单工具就绪检查。通过函数名判断 stub。"""
        if not t.handler:
            return False, "no handler"

        # 检查 handler 是否已知 stub 模式
        handler_name = getattr(t.handler, "__name__", "")
        if "stub" in handler_name.lower():
            return False, "stub handler"
        # 检查 partial 包装的原始函数
        if hasattr(t.handler, "func"):
            wrapped_name = getattr(t.handler.func, "__name__", "")
            if "stub" in wrapped_name.lower():
                return False, "stub handler"

        return True, "ok"

    # ── 内部 ──────────────────────────────────────────

    @staticmethod
    def _tool_to_dict(t: BusinessFlowTool) -> dict:
        return {
            "name": t.name,
            "category": t.category,
            "permission": t.permission_flag,
            "description": t.description,
            "parameters": t.parameters,
        }
