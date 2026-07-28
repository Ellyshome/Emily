# emily-core/emily_core/workitem/langgraph_engine/hook_adapter.py
"""HookAdapter —— 声明式 Hook 桥接到 LangGraph 节点回调。

复用 HookRegistry + Hook 子类，保留 hook_config.json 声明式配置和三态语义。

语义映射：
  PipelineBUS._fire_before_hooks  →  HookAdapter.fire_before(node_name, ctx) -> bool
  PipelineBUS._fire_after_hooks   →  HookAdapter.fire_after(node_name, ctx) -> None
  PipelineBUS._fire_error_hooks   →  HookAdapter.fire_error(node_name, ctx, err) -> None
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.langgraph.hook_adapter")


class HookAdapter:
    """Hook 适配器 —— 桥接 HookRegistry 到 graph 节点回调。"""

    def __init__(self, registry):
        self._registry = registry

    async def fire_before(self, node_name: str, ctx) -> bool:
        """触发 before:{node_name} hooks。返回 False 表示被阻断。"""
        mount = f"before:{node_name}"
        for hook in self._registry.get_enabled(mount):
            try:
                from emily_core.workitem.pipeline.hook import HookDecision
                result = await hook.execute(ctx)
                if result.is_blocked:
                    logger.info(
                        "graph blocked by hook '%s' at %s: %s",
                        hook.name, mount, result.message,
                    )
                    ctx.abort_reason = result.message
                    return False
                if result.decision == HookDecision.WARN:
                    ctx.add_warning(result.message)
            except Exception as e:
                logger.error("Before hook '%s' at %s failed: %s", hook.name, mount, e)
                ctx.abort_reason = f"鉴权/核验服务异常: {e}"
                return False
        return True

    async def fire_after(self, node_name: str, ctx) -> None:
        """触发 after:{node_name} hooks。fire-and-forget。"""
        mount = f"after:{node_name}"
        for hook in self._registry.get_enabled(mount):
            try:
                await hook.execute(ctx)
            except Exception as e:
                logger.warning(
                    "After hook '%s' at %s failed (non-blocking): %s",
                    hook.name, mount, e,
                )

    async def fire_error(self, node_name: str, ctx, error: Exception) -> None:
        """触发 on_error:{node_name} hooks。"""
        mount = f"on_error:{node_name}"
        for hook in self._registry.get_enabled(mount):
            try:
                await hook.execute(ctx)
            except Exception as e:
                logger.error("Error hook '%s' at %s also failed: %s", hook.name, mount, e)


def build_hook_adapter_from_config(
    hook_config: dict,
    injected_services: dict,
):
    """从 hook_config.json 构建 HookAdapter。

    复用 PipelineBUS._build_hook_from_spec 的 Hook 构建逻辑。
    """
    from emily_core.workitem.pipeline.hook import HOOK_TYPE_MAP
    from emily_core.workitem.pipeline.hook_registry import HookRegistry

    registry = HookRegistry()
    hooks_section = hook_config.get("hooks", {})
    if not hooks_section:
        logger.info("No hooks found in config for graph engine")
        return HookAdapter(registry)

    count = 0
    for mount_point, hook_specs in hooks_section.items():
        if not isinstance(hook_specs, list):
            continue
        for spec in hook_specs:
            if not isinstance(spec, dict):
                continue
            hook_type = spec.get("type", "")
            hook_name = spec.get("name", "unnamed_hook")
            if hook_type not in HOOK_TYPE_MAP:
                logger.warning("Unknown hook type '%s' for '%s'", hook_type, hook_name)
                continue
            cls = HOOK_TYPE_MAP[hook_type]
            try:
                kwargs: dict = {}
                if hook_type == "auth":
                    kwargs["resource_type"] = spec.get("resource_type", "")
                    kwargs["action"] = spec.get("action", "")
                elif hook_type == "audit":
                    kwargs["event_type"] = spec.get("event_type", "")
                elif hook_type == "progress":
                    if "progress_sender" in injected_services:
                        kwargs["progress_sender"] = injected_services["progress_sender"]
                    if "progress_template" in injected_services:
                        kwargs["progress_template"] = injected_services["progress_template"]
                    kwargs["enable_progress"] = spec.get("enabled", True)
                elif hook_type == "archive":
                    if "archive_writer" in injected_services:
                        kwargs["archive_writer"] = injected_services["archive_writer"]
                kwargs["name"] = hook_name
                kwargs["priority"] = spec.get("priority", 10)
                kwargs["enabled"] = spec.get("enabled", True)
                hook = cls(**kwargs)
                registry.register(mount_point, hook)
                count += 1
            except Exception as e:
                logger.error("Failed to build hook '%s' (type=%s): %s", hook_name, hook_type, e)

    logger.info("HookAdapter registered %d hook(s)", count)
    return HookAdapter(registry)
