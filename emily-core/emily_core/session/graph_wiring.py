# emily-core/emily_core/session/graph_wiring.py
"""M3 第二切片 —— 会话编排图的真实依赖接线（计划 M3 接线矩阵）。

设计目标：
  - 图模块不耦合会话循环；本模块把既有 `SessionLoop` 的上下文、能力目录、能力执行与
    模型调用装配为图所需的注入点，**行为与现有循环保持一致**（同一套 prompt、同一套
    权限裁剪通道 `build_tool_specs`、同一套工具执行通道 `_execute_tool`）。
  - 不重复实现：prompt、历史、模型调用、工具执行全部委托既有方法，避免两套语义漂移。
  - 归档与记录：由 `handle_via_graph` 复用既有方法完成（轮次归档、能力清单、会话记录、
    上下文压缩），与 `SessionLoop.handle` 的收尾一致。

与既有循环的差异（有意为之）：
  - 快速短路改由图层的 `fast_reply` 节点判定；命中快速短路时不写入会话记录与归档，
    与 `SessionLoop.handle` 的早返回语义一致。
  - 中断挂起（M5）与门禁判定（M6）尚未接线：能力要求补充信息时走降级路径（直接把问题
    作为回复），门禁仍由既有的分级兜底与工具裁剪承担。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .kernel_state import ToolContext, bind_tool_context, clear_tool_context
from .session_graph import SessionGraphRunner, build_session_graph
from .suspend_interrupt import (
    NOT_INITIATOR_REPLY,
    get_pending,
    resume_graph,
    resolve_current_actor,
)

logger = logging.getLogger("emily.session.graph_wiring")

#: 检查点实例按配置缓存：避免每个会话循环各建一个连接池
_SAVER_CACHE: dict = {}


def _shared_saver(config):
    """按配置复用检查点实例（M5 挂起与断点恢复依赖它）。"""
    if config is None:
        return False
    key = id(config)
    if key not in _SAVER_CACHE:
        try:
            from ..workitem.langgraph_engine.checkpointer import build_checkpointer
            _SAVER_CACHE[key] = build_checkpointer(config)
        except Exception as e:  # noqa: BLE001
            logger.error("build_checkpointer failed: %s", e)
            _SAVER_CACHE[key] = False
    return _SAVER_CACHE[key]


@dataclass
class GraphBundle:
    """图注入点集合 + 运行期上下文。"""

    callables: dict = field(default_factory=dict)
    records: list = field(default_factory=list)      # 本轮能力调用记录（供归档）
    runtime: dict = field(default_factory=dict)      # message / db_message_id 等逐次刷新
    ports: Any = None                                # 外壳端口集合（M8）


def build_bundle(*, loop, allowed_sops: "set | None" = None) -> GraphBundle:
    """把既有会话循环装配为图注入点集合。"""
    bundle = GraphBundle()
    bundle.runtime.update({"message": None, "db_message_id": ""})

    def _actor() -> dict:
        return dict(getattr(loop, "_last_actor", None) or {})

    def _entries() -> list:
        catalog = getattr(loop, "_catalog", None)
        if catalog is None:
            return []
        try:
            return list(catalog.list_capabilities(_actor(), loop.context, allowed_sops=allowed_sops) or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("catalog.list_capabilities failed: %s", e)
            return []

    def _capability_names() -> set:
        return {str(getattr(e, "name", "") or "") for e in _entries() if getattr(e, "name", "")}

    def _fast_responder(text: str) -> "str | None":
        try:
            from .session_agent import SessionAgent
        except Exception as e:  # noqa: BLE001
            logger.warning("fast responder unavailable: %s", e)
            return None
        try:
            return SessionAgent._try_fast_reply(str(text or ""))
        except Exception as e:  # noqa: BLE001
            logger.warning("fast responder failed: %s", e)
            return None

    def _prompt_builder() -> str:
        base = ""
        try:
            base = loop._build_system_prompt(_entries()) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("build system prompt failed: %s", e)
        # ── 检索通道治理 P0（M1 通道元数据 + M2 分工表）──
        # 目的：让模型知道"去哪找、每条通道能答什么"，针对误选通道的根因。
        if getattr(loop, "_retrieval_guidance_enabled", True):
            try:
                from ..retrieval import channel_registry, strategy as strategy_mod
                blocks = [channel_registry.describe_for_prompt(),
                          strategy_mod.dispatch_table_text()]
                blocks = [b for b in blocks if b]
                if blocks:
                    base = (base + "\n\n" + "\n\n".join(blocks)) if base else "\n\n".join(blocks)
            except Exception as e:  # noqa: BLE001
                logger.warning("retrieval guidance failed: %s", e)
        return base

    def _history_provider() -> list:
        msgs = []
        try:
            msgs = list(loop._llm_history() or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("llm history failed: %s", e)
            msgs = []
        # ── M8：会话期注入以**独立 system 消息**参与消息列表 ──
        # 不拼接进系统提示词（历史实测：拼接会抑制工具调用）。默认关闭，待专项验证后再开。
        if getattr(loop, "_context_injection_enabled", False):
            head = []
            try:
                head.extend(list(getattr(loop, "_turn_group_injections", None) or []))
            except Exception as e:  # noqa: BLE001
                logger.debug("group injection attach failed: %s", e)
            try:
                user_text = str(getattr(loop, "_turn_user_text", "") or "")
                if any(k in user_text for k in ("确认", "确定", "是的", "好的", "可以", "取消",
                                                "放弃", "不要", "别")):
                    pending = loop._pending_event_injection()
                    if isinstance(pending, dict) and pending.get("content"):
                        head.append({"role": "system", "content": str(pending["content"])})
            except Exception as e:  # noqa: BLE001
                logger.debug("pending injection attach failed: %s", e)
            head = [m for m in head if isinstance(m, dict) and m.get("content")]
            if head:
                msgs = head + msgs
        return msgs

    def _tool_specs_provider() -> list:
        catalog = getattr(loop, "_catalog", None)
        if catalog is None:
            return []
        try:
            return list(catalog.build_tool_specs(_actor(), loop.context, allowed_sops=allowed_sops) or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("build_tool_specs failed: %s", e)
            return []

    def _capability_spec_provider() -> list:
        core = getattr(loop, "_core", None)
        if core is None:
            return []
        try:
            from .capability_contract import build_specs
            return list(build_specs(core, _actor(), loop.context, allowed_sops=allowed_sops) or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("capability_contract.build_specs failed: %s", e)
            return []

    async def _llm_caller(messages: list, tool_specs: list):
        return await loop._llm_call(messages, tool_specs)

    async def _capability_executor(capability: str, params: dict) -> dict:
        message = bundle.runtime.get("message")
        db_message_id = str(bundle.runtime.get("db_message_id") or "")
        try:
            payload = await loop._execute_tool(
                str(capability or ""), dict(params or {}), message, db_message_id,
                bundle.records, _capability_names(),
            )
        except Exception as e:  # noqa: BLE001 — 结构化失败回传，不抛出循环（PRD 约束）
            logger.error("capability %s failed: %s", capability, e, exc_info=True)
            return {"success": False, "status": "failed", "reply": f"调用异常：{e}"}
        if isinstance(payload, dict):
            return payload
        return {"success": False, "status": "failed", "reply": "能力返回了非结构化结果"}

    async def _plan_builder(user_text: str, specs: list):
        planner = getattr(loop, "_planner", None)
        if planner is None:
            return None
        plan_names = {str(getattr(s, "name", "") or "") for s in (specs or [])
                      if getattr(s, "kind", "") == "sop"}
        if not plan_names:
            plan_names = {str(getattr(e, "name", "") or "") for e in _entries()
                          if getattr(e, "kind", "") == "sop"}
        if not plan_names:
            return None
        try:
            plan = await planner.plan(str(user_text or ""), plan_names)
        except Exception as e:  # noqa: BLE001
            logger.error("planner.plan failed: %s", e, exc_info=True)
            return None
        if plan is None:
            return None
        steps = []
        for i, step in enumerate(getattr(plan, "steps", None) or []):
            steps.append({
                "step_id": str(getattr(step, "step_id", "") or f"step-{i + 1}"),
                "capability": str(getattr(step, "capability", "") or ""),
                "params": dict(getattr(step, "params", None) or {}),
                "depends_on": list(getattr(step, "depends_on", None) or []),
                "depth": int(getattr(step, "depth", 0) or 0),
            })
        return {"steps": steps} if steps else None

    def _plan_gate(text: str) -> bool:
        try:
            from .loop import _looks_compound
        except Exception as e:  # noqa: BLE001
            logger.debug("plan gate unavailable: %s", e)
            return False
        try:
            return bool(_looks_compound(str(text or "")))
        except Exception as e:  # noqa: BLE001
            logger.debug("plan gate failed: %s", e)
            return False

    # 门禁判定（M6）：收敛为图内单一判定点；判定结论来自既有可见性通道（fail-closed）
    from .graph_gate import build_gate_evaluator
    gate_evaluator = build_gate_evaluator(visible_provider=_capability_names)

    async def _archive_sink(state: dict) -> None:
        """归档钩子（M7）：能力调用清单由既有归档通道写入，内核不直接落盘。"""
        return None

    def _event_emit(kind: str, payload: dict) -> None:
        """事件出口（M7）：统一走既有出站通道，图内不另建事件通道。"""
        bus = getattr(loop, "_outbound_bus", None)
        if bus is None:
            return
        try:
            bus.publish(str(kind), dict(payload or {}))
        except Exception as e:  # noqa: BLE001
            logger.debug("event emit failed: %s", e)

    bundle.callables.update({
        "fast_responder": _fast_responder,
        "prompt_builder": _prompt_builder,
        "history_provider": _history_provider,
        "tool_specs_provider": _tool_specs_provider,
        "capability_spec_provider": _capability_spec_provider,
        "llm_caller": _llm_caller,
        "capability_executor": _capability_executor,
        "plan_builder": _plan_builder,
        "plan_gate": _plan_gate,
        "gate_evaluator": gate_evaluator,
        "archive_sink": _archive_sink,
        "event_emit": _event_emit,
    })
    return bundle


def build_wired_graph(*, loop, allowed_sops: "set | None" = None, checkpointer: Any = None):
    """构建（或复用）接线后的会话编排图与注入点集合。"""
    cached_graph = getattr(loop, "_wired_session_graph", None)
    cached_bundle = getattr(loop, "_wired_graph_bundle", None)
    if cached_graph is not None and cached_bundle is not None:
        return cached_graph, cached_bundle
    bundle = build_bundle(loop=loop, allowed_sops=allowed_sops)
    from .ports import build_ports
    core = getattr(loop, "_core", None)
    bundle.ports = build_ports(
        core=core, events=bundle.callables.get("event_emit"),
        conversation_id=str(getattr(loop, "conversation_id", "") or ""),
    )
    graph = build_session_graph(
        llm_caller=bundle.callables["llm_caller"],
        capability_executor=bundle.callables["capability_executor"],
        fast_responder=bundle.callables["fast_responder"],
        tool_specs_provider=bundle.callables["tool_specs_provider"],
        prompt_builder=bundle.callables["prompt_builder"],
        history_provider=bundle.callables["history_provider"],
        capability_spec_provider=bundle.callables["capability_spec_provider"],
        capability_plan_builder=bundle.callables["plan_builder"],
        plan_gate=bundle.callables["plan_gate"],
        gate_evaluator=bundle.callables.get("gate_evaluator"),
        archive_sink=bundle.callables.get("archive_sink"),
        ports=bundle.ports,
        config=getattr(loop, "_config", None),
        checkpointer=_shared_saver(getattr(loop, "_config", None)),
    )
    try:
        loop._wired_session_graph = graph
        loop._wired_graph_bundle = bundle
    except Exception:  # noqa: BLE001 — loop 不支持挂属性时不影响功能
        pass
    try:
        from .graph_gate import describe_convergence
        logger.info("门禁判定收敛：%s", describe_convergence())
    except Exception as e:  # noqa: BLE001
        logger.debug("describe convergence failed: %s", e)
    return graph, bundle


async def handle_via_graph(loop, message, db_message_id: str = "", current_user_id: str = ""):
    """图式处理一条入站消息：前置与收尾与 `SessionLoop.handle` 保持一致。

    轮次归档段与记录在**确认非快速短路之后**才写入：快速短路命中时与 `handle()` 的早返回
    一致，不留"有开头无收尾"的归档段。
    """
    loop._last_actor = await loop._fetch_actor(current_user_id)

    graph, bundle = build_wired_graph(loop=loop)
    bundle.runtime["message"] = message
    bundle.runtime["db_message_id"] = db_message_id

    actor = dict(getattr(loop, "_last_actor", None) or {})
    actor_ref = {
        "kind": "user",
        "ref_id": str(actor.get("user_id") or getattr(loop.context, "user_id", "") or ""),
        "digest": f"L{actor.get('level', '')}",
    }
    final_reply = None
    runner = None
    try:
        # 群聊注入（原系统特性恢复）：每轮计算一次，经提示构建并入系统提示
        try:
            loop._turn_user_text = str(getattr(message, "content", "") or "")
            loop._turn_group_injections = await loop._group_injections(message, db_message_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("group injections failed: %s", e)
            loop._turn_group_injections = []
        runner = SessionGraphRunner(
            graph,
            conversation_id=loop.conversation_id,
            actor_ref=actor_ref,
            max_iterations=int(getattr(getattr(loop, "_config", None),
                                       "agent_loop_max_iterations", 12) or 12),
            event_port=bundle.callables.get("event_emit"),
        )
        # 挂起续接（M5）：该会话存在待补充信息时，本条消息视为补充，续接原挂起
        pending = await get_pending(graph, loop.conversation_id)
        if pending:
            if not resolve_current_actor(pending, actor_ref):
                logger.info("suspend: 非发起者回复，未消费挂起 conv=%s", loop.conversation_id)
                return loop._reply(message, NOT_INITIATOR_REPLY)
            logger.info("suspend resume: conv=%s capability=%s", loop.conversation_id,
                        pending.get("capability"))
            bind_tool_context(ToolContext(user_id=actor_ref["ref_id"], actor_ref=actor_ref))
            try:
                final = await resume_graph(
                    graph, loop.conversation_id,
                    str(getattr(message, "content", "") or ""),
                    recursion_limit=getattr(runner, "_recursion_limit", 50),
                    actor_ref=actor_ref,
                )
            finally:
                clear_tool_context()
            runner.last_state = final
            final_reply = str(final.get("reply_text") or "") or None
        else:
            final_reply = await runner.run(message, db_message_id=db_message_id,
                                          current_user_id=current_user_id)
    except Exception as e:  # noqa: BLE001
        logger.error("handle_via_graph failed: %s", e, exc_info=True)
        final_reply = "抱歉，处理时出现了异常，请稍后重试或换个说法。"

    if final_reply is None:
        return None

    # 快速短路命中：与 handle() 的早返回一致，不写记录与归档
    if (getattr(runner, "last_state", None) or {}).get("_fast"):
        return loop._reply(message, final_reply)

    loop._turn_counter += 1
    loop._append_archive_turn_start(message)
    loop._record_turn(message, final_reply)
    loop._maybe_compact()
    loop._last_calls = list(bundle.records)
    loop.append_capability_section(list(bundle.records))
    loop._append_archive_turn_end(final_reply)
    await loop.touch_archive_index()
    return loop._reply(message, final_reply)
