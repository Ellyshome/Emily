"""PipelineBUS —— 系统级公共执行总线（蓝图 §5.4）。

所有 Session 的所有 WorkItem 共享此 BUS 执行，BUS 不属于单个 WorkItem 私有。

4 个固定节点（蓝图 §5.4）：
  Node 1 [意图+拆分] → Node 2 [计划+标准] → Node 3 [执行+验收] → Node 4 [成果总结]

每个节点 3 个 Hook 挂载点（before / after / on_error），三态决策 ALLOW/WARN/BLOCK，
deny always wins（任一 before hook BLOCK → 立即终止），before hook 异常视为 BLOCK。

Hook 系统完整复用迁移过来的 hook.py / hook_registry.py（M15 实现，逻辑不改）。
Phase B/C 已将 MockRouter 替换为 SessionAgent 意图识别，MockPlanner 替换为 LLM 规划，
MockWorkAgent 作为 Mock 模式兜底，RealGuardian 在 LLM 可用时自动启用输出审核（见 workitem_agent.py）。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .hook_registry import HookRegistry
from .hook import HookDecision, HOOK_TYPE_MAP
from ...infrastructure.logging.llm_logger import LLMInteractionLogger
from ...infrastructure.logging.business_event_logger import BusinessEventLogger

if TYPE_CHECKING:
    from .node import PipelineNode
    from .context import BusContext

logger = logging.getLogger("emily.bus")


class PipelineBUS:
    """系统级公共执行总线 —— 4 节点 + Hook 声明式横切。"""

    def __init__(self, name: str = "emily_bus"):
        self.name = name
        self._nodes: list["PipelineNode"] = []
        self._hooks = HookRegistry()

    # ── 节点管理 ──

    def add_node(self, node: "PipelineNode") -> "PipelineBUS":
        """追加一个节点（支持链式调用）。"""
        self._nodes.append(node)
        return self

    def add_nodes(self, nodes: list["PipelineNode"]) -> "PipelineBUS":
        """批量追加节点（支持链式调用）。"""
        self._nodes.extend(nodes)
        return self

    # ── Hook 管理（复用 M15 声明式注册）──

    def register_hook(self, mount_point: str, hook) -> "PipelineBUS":
        """注册 hook 到指定挂载点（格式 "{phase}:{node_name}"）。"""
        self._hooks.register(mount_point, hook)
        return self

    def register_hooks_from_config(self, config: dict, **injected_services) -> None:
        """从 JSON 配置批量注册 hooks（声明式，蓝图 §7.5）。

        配置格式（hook_config.json）::

            {"hooks": {"before:wi_node1": [{"type": "auth", "name": "...", ...}], ...}}
        """
        hooks_section = config.get("hooks", {})
        if not hooks_section:
            logger.info("No hooks found in BUS config")
            return

        count = 0
        for mount_point, hook_specs in hooks_section.items():
            if not isinstance(hook_specs, list):
                logger.warning("Invalid hook config at %s: expected list", mount_point)
                continue
            for spec in hook_specs:
                if not isinstance(spec, dict):
                    continue
                hook = self._build_hook_from_spec(spec, **injected_services)
                if hook:
                    self.register_hook(mount_point, hook)
                    count += 1
        logger.info(
            "BUS registered %d hook(s) at %d mount point(s)",
            count, len(hooks_section),
        )

    def _build_hook_from_spec(self, spec: dict, **injected_services):
        """从配置字典构建 Hook 实例（复用 M15 字段映射）。"""
        hook_type = spec.get("type", "")
        hook_name = spec.get("name", "unnamed_hook")
        if hook_type not in HOOK_TYPE_MAP:
            logger.warning("Unknown hook type '%s' for '%s'", hook_type, hook_name)
            return None
        cls = HOOK_TYPE_MAP[hook_type]
        try:
            kwargs: dict = {}
            if hook_type == "auth":
                kwargs["resource_type"] = spec.get("resource_type", "")
                kwargs["action"] = spec.get("action", "")
            elif hook_type == "audit":
                kwargs["event_type"] = spec.get("event_type", "")
            elif hook_type == "trace":
                kwargs["trace_level"] = spec.get("trace_level", "summary")
                if "agent_trace_service" in injected_services:
                    kwargs["agent_trace_service"] = injected_services["agent_trace_service"]
            elif hook_type == "progress":
                if "progress_sender" in injected_services:
                    kwargs["progress_sender"] = injected_services["progress_sender"]
                if "progress_template" in injected_services:
                    kwargs["progress_template"] = injected_services["progress_template"]
                kwargs["enable_progress"] = spec.get("enabled", True)
            elif hook_type == "plan_task_match":
                # PlanTaskMatchHook 已废弃，跳过
                pass
            kwargs["name"] = hook_name
            kwargs["priority"] = spec.get("priority", 10)
            kwargs["enabled"] = spec.get("enabled", True)
            return cls(**kwargs)
        except Exception as e:
            logger.error("Failed to build hook '%s' (type=%s): %s", hook_name, hook_type, e)
            return None

    def hook_count(self) -> int:
        """已注册 hook 总数。"""
        return self._hooks.hook_count()

    # ── 执行 ──

    async def run(self, context: "BusContext") -> "BusContext":
        """在公共总线上执行一个 WorkItem（顺序经过 4 节点）。"""
        wi = context.work_item
        started_at = datetime.now(timezone.utc).isoformat()
        node_timings: dict[str, int] = {}

        # 注入日志上下文，让 LLM callback 和业务事件日志可获取 pipeline_run_id
        LLMInteractionLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
            user_id=context.user_id,
        )
        BusinessEventLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
        )

        logger.info(
            "BUS[%s] running WorkItem %s: %d nodes",
            self.name, wi.id if wi else "?", len(self._nodes),
        )

        try:
            for node in self._nodes:
                context.current_stage = node.name
                t_node = time.monotonic()

                # ① before hook（可阻断）
                if not await self._fire_before_hooks(node.name, context):
                    logger.warning("BUS blocked at node %s: %s", node.name, context.abort_reason)
                    context.should_abort = True
                    node_timings[node.name] = int((time.monotonic() - t_node) * 1000)
                    # 阻断也写日志
                    await self._log_execution(context, started_at, node_timings)
                    return context

                # ② 执行节点 handler
                try:
                    await node.handler(context)
                except Exception as e:
                    logger.error("BUS node [%s] failed: %s", node.name, e, exc_info=True)
                    await self._fire_error_hooks(node.name, context, e)
                    node_timings[node.name] = int((time.monotonic() - t_node) * 1000)
                    if node.required:
                        context.should_abort = True
                        context.abort_reason = str(e)
                        if wi is not None:
                            wi.error_message = str(e)
                        # 失败也写日志
                        await self._log_execution(context, started_at, node_timings)
                        return context
                    # 非必经节点：记录后继续
                    continue

                # ③ after hook（fire-and-forget）
                await self._fire_after_hooks(node.name, context)
                node_timings[node.name] = int((time.monotonic() - t_node) * 1000)

            logger.info("BUS[%s] completed WorkItem %s", self.name, wi.id if wi else "?")
            # 正常完成写日志
            await self._log_execution(context, started_at, node_timings)
            return context
        finally:
            LLMInteractionLogger.clear_context()
            BusinessEventLogger.clear_context()

    async def _log_execution(self, context: "BusContext",
                              started_at: str, node_timings: dict) -> None:
        """非阻断写入 Pipeline 执行日志。"""
        try:
            from ...infrastructure.logging.pipeline_logger import PipelineExecutionLogger
            await PipelineExecutionLogger.log(context, started_at, node_timings)
        except Exception as e:
            logger.warning("Pipeline execution log write failed: %s", e)

    # ── Hook 触发（复用 M15 语义）──

    async def _fire_before_hooks(self, node_name: str, context: "BusContext") -> bool:
        """触发 before hooks。返回 False 表示被阻断。"""
        mount = f"before:{node_name}"
        for hook in self._hooks.get_enabled(mount):
            try:
                result = await hook.execute(context)
                if result.is_blocked:
                    logger.info(
                        "BUS blocked by hook '%s' at %s: %s",
                        hook.name, mount, result.message,
                    )
                    context.abort_reason = result.message
                    return False
                if result.decision == HookDecision.WARN:
                    context.add_warning(result.message)
            except Exception as e:
                logger.error("Before hook '%s' at %s failed: %s", hook.name, mount, e)
                # before hook 异常视为阻断（安全第一原则）
                context.abort_reason = f"鉴权/核验服务异常: {e}"
                return False
        return True

    async def _fire_after_hooks(self, node_name: str, context: "BusContext") -> None:
        """触发 after hooks。fire-and-forget，失败只记日志。"""
        mount = f"after:{node_name}"
        for hook in self._hooks.get_enabled(mount):
            try:
                await hook.execute(context)
            except Exception as e:
                logger.warning(
                    "After hook '%s' at %s failed (non-blocking): %s",
                    hook.name, mount, e,
                )

    async def _fire_error_hooks(self, node_name: str, context: "BusContext", error: Exception) -> None:
        """触发 on_error hooks。"""
        mount = f"on_error:{node_name}"
        for hook in self._hooks.get_enabled(mount):
            try:
                await hook.execute(context)
            except Exception as e:
                logger.error("Error hook '%s' at %s also failed: %s", hook.name, mount, e)

    # ── 工厂方法 ──

    @classmethod
    def build_default(cls, node_handlers: dict, name: str = "emily_bus") -> "PipelineBUS":
        """构建默认 4 节点 BUS（蓝图 §5.4）。

        Args:
            node_handlers: {"wi_node1": handler, "wi_node2": ..., ...}，
                           每个 handler 为 async fn(BusContext) -> None。
            name: BUS 名称。

        Returns:
            PipelineBUS: 已装配 4 节点的总线（未注册 hook，需另行 register）。
        """
        from .node import PipelineNode

        bus = cls(name=name)
        bus.add_nodes([
            PipelineNode("wi_node1", "意图+拆分", node_handlers["wi_node1"], required=True),
            PipelineNode("wi_node2", "计划+标准", node_handlers["wi_node2"], required=True),
            PipelineNode("wi_node3", "执行+验收", node_handlers["wi_node3"], required=True),
            PipelineNode("wi_node4", "成果总结", node_handlers["wi_node4"], required=False),
        ])
        return bus
