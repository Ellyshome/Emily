# emily-core/emily_core/session/session_graph.py
"""M3 会话编排图 —— 会话侧唯一编排主体（计划 M3 / PRD US-01、US-05、US-07）。

定位：
  - 把会话主循环从手写迭代（`session/loop.py::_run_loop`）改为图式执行，纳入宪法 C8 的
    "唯一执行引擎"（既有 LangGraph StateGraph）形态。
  - 状态使用 M1 的 `KernelState`（纯可序列化），能力目录使用 M2 的契约（`CapabilitySpec`）。
  - 工具执行前经 M1 的工具上下文端口绑定，能力层不再回退读图内部状态。

本切片（M3 第一切片）落地的节点：
  fast_reply → understand ⇄ execute → summarize
                      ↘ error_analysis（迭代上限兜底）

按注入启用、本切片暂不接线（由后续模块接）：
  - `gate_evaluator`（M6 门禁判定）：未注入时不建 gate 节点，understand 直连 execute
  - `capability_plan_builder`（M4 计划子图）与 `suspend_factory`（M5 挂起中断）：仅登记参数，
    未实现节点；注入时打印显式告警，避免"看起来已接线"的假象（宪法 Q6）
  - `ports`（M8 外壳端口）：仅登记，未使用

状态合并约定：LangGraph 默认通道为「后写覆盖」，因此节点返回 `messages` / `capability_calls`
时必须返回**完整列表**，不能只返回增量。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .kernel_state import (
    DEFAULT_MAX_ITERATIONS,
    KernelState,
    ToolContext,
    bind_tool_context,
    clear_tool_context,
    make_initial_state,
    summarize_state,
    validate_state_serializable,
)
from .suspend_interrupt import NODE_SUSPEND, build_suspend_node, get_pending

logger = logging.getLogger("emily.session.session_graph")

NODE_FAST = "fast_reply"
NODE_UNDERSTAND = "understand"
NODE_PLAN = "plan"
NODE_GATE = "gate"
NODE_EXECUTE = "execute"
NODE_SUMMARIZE = "summarize"
NODE_ERROR = "error_analysis"

#: 计划子图默认深度上限（与 M4 一致）
PLAN_MAX_DEPTH = 3

#: 迭代上限兜底文案（AC-US-01.3：达上限给出可读收尾，而非无响应或报错原文）
CAP_REPLY = "这个请求步骤较多，我先就目前掌握的信息答复；如需继续，请补充一句让我接着做。"
EMPTY_REPLY = "抱歉，我暂时无法完成这个请求，请稍后再试。"


def _merge(state: dict, **changes: Any) -> dict:
    """返回合并后的完整状态（供节点返回值使用）。"""
    merged = dict(state or {})
    merged.update(changes)
    return merged


def _max_iterations(state: dict, config: Any) -> int:
    raw = (state or {}).get("_max_iterations")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    return int(getattr(config, "agent_loop_max_iterations", DEFAULT_MAX_ITERATIONS)
               or DEFAULT_MAX_ITERATIONS)


# ══════════════════════════════════════════════════════════════════════════════
# 节点工厂
# ══════════════════════════════════════════════════════════════════════════════


def make_fast_reply(*, fast_responder: Callable[[str], "str | None"]):
    """快速短路节点：问候/感谢/告别/自我介绍类消息零 LLM 直答（AC-US-01.2）。"""

    async def _node(state: dict) -> dict:
        text = ""
        if fast_responder is not None:
            try:
                text = fast_responder(str(state.get("user_text", "") or "")) or ""
            except Exception as e:  # noqa: BLE001 — 短路失败不阻断主流程
                logger.warning("fast_reply failed: %s", e)
                text = ""
        if text:
            return _merge(state, reply_text=text, _fast=True)
        return _merge(state, _fast=False)

    _node.__name__ = NODE_FAST
    return _node


def make_understand(
    *,
    llm_caller: Callable[[list, list], Any],
    tool_specs_provider: "Callable[[], list] | None",
    prompt_builder: "Callable[[], str] | None",
    history_provider: "Callable[[], list] | None",
    config: Any,
    plan_gate: "Callable[[str], bool] | None" = None,
    plan_available: bool = False,
):
    """理解节点：组装消息 → 调用模型 → 产出文本回复或一次工具调用。

    若配置了计划门（`plan_gate`：疑似复合请求判定）且计划能力可用，则首轮请求置
    `_should_plan`，由路由转入计划节点（M4 计划子图）。
    """

    async def _node(state: dict) -> dict:
        messages = list(state.get("messages") or [])
        first_pass = not messages
        if first_pass:
            system_prompt = ""
            if prompt_builder is not None:
                try:
                    system_prompt = prompt_builder() or ""
                except Exception as e:  # noqa: BLE001
                    logger.warning("prompt_builder failed: %s", e)
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if history_provider is not None:
                try:
                    messages.extend(history_provider() or [])
                except Exception as e:  # noqa: BLE001
                    logger.warning("history_provider failed: %s", e)
            messages.append({"role": "user", "content": str(state.get("user_text", "") or "")})

        specs: list = []
        if tool_specs_provider is not None:
            try:
                specs = tool_specs_provider() or []
            except Exception as e:  # noqa: BLE001
                logger.warning("tool_specs_provider failed: %s", e)
                specs = []

        should_plan = bool(
            plan_available and plan_gate is not None and first_pass
            and not state.get("_plan_done") and not state.get("_should_plan")
            and plan_gate(str(state.get("user_text", "") or ""))
        )

        def _out(**kw: Any) -> dict:
            return _merge(state, _should_plan=should_plan, **kw)

        # 复合请求：先交给计划子图（M4）照单执行，暂不走单轮 ReAct（沿用旧粗排"先计划后执行"语义）
        if should_plan:
            return _out(messages=messages)

        result = await llm_caller(messages, specs)
        if not result:
            return _out(messages=messages, reply_text=EMPTY_REPLY)

        if result.get("type") != "tool_call":
            text = str(result.get("content") or "").strip()
            if text:
                return _out(messages=messages, reply_text=text)
            # 空文本（reasoner 把内容落在 reasoning_content）→ 追问一次再循环
            messages.append({"role": "user", "content": "[系统] 请直接给用户回复。"})
            return _out(
                messages=messages,
                iteration_count=int(state.get("iteration_count", 0) or 0) + 1,
            )

        tool_call = {
            "id": str(result.get("tool_call_id") or ""),
            "name": str(result.get("tool_name") or ""),
            "arguments": dict(result.get("tool_arguments") or {}),
        }
        assistant_msg = {
            "role": "assistant",
            "content": result.get("content") or "",
            "tool_calls": [{
                "id": tool_call["id"], "type": "function",
                "function": {
                    "name": tool_call["name"],
                    "arguments": json.dumps(tool_call["arguments"], ensure_ascii=False),
                },
            }],
        }
        if result.get("reasoning_content"):
            assistant_msg["reasoning_content"] = result["reasoning_content"]
        messages.append(assistant_msg)
        return _out(messages=messages, _pending_tool_call=tool_call)

    _node.__name__ = NODE_UNDERSTAND
    return _node


def make_plan(
    *,
    plan_builder: Callable[[str, Any], Any],
    capability_executor: Callable[[str, dict], Any],
    capability_spec_provider: "Callable[[], list] | None" = None,
    max_depth: int = PLAN_MAX_DEPTH,
):
    """计划节点：构造计划（M4 契约校验）→ 照单执行 → 成果回灌对话。

    执行权留在编排侧：本节点只把计划结果以系统消息回灌，最终回复仍由主循环产出。
    """

    def _specs() -> list:
        if capability_spec_provider is None:
            return []
        try:
            return list(capability_spec_provider() or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("capability_spec_provider failed: %s", e)
            return []

    async def _node(state: dict) -> dict:
        from .plan_graph import PlanRunner, build_plan_subgraph

        messages = list(state.get("messages") or [])
        calls = list(state.get("capability_calls") or [])
        user_text = str(state.get("user_text", "") or "")

        try:
            plan = await plan_builder(user_text, _specs())
        except Exception as e:  # noqa: BLE001 — 计划失败回退单轮循环
            logger.error("plan_builder failed: %s", e, exc_info=True)
            plan = None

        steps = list((plan or {}).get("steps") or [])
        if not steps:
            messages.append({"role": "system", "content": "[系统] 无可用计划，请按单步诉求直接处理。"})
            return _merge(state, messages=messages, _should_plan=False, _plan_done=True)

        specs = _specs()
        subgraph = build_plan_subgraph(
            capability_executor=capability_executor, specs=specs, max_depth=max_depth,
        )
        outcome = await PlanRunner(subgraph, specs=specs, max_depth=max_depth).run(
            steps, plan_id=str(state.get("conversation_id") or ""),
        )

        for record in outcome.get("step_results") or []:
            calls.append({
                "capability": str(record.get("capability") or ""),
                "step_id": str(record.get("step_id") or ""),
                "status": str(record.get("status") or ""),
                "triggered_by": str((state.get("actor_ref") or {}).get("ref_id", "")),
            })
        messages.append({
            "role": "system",
            "content": "[系统] 计划已执行：\n" + str(outcome.get("plan_text") or ""),
        })
        messages.append({
            "role": "user",
            "content": "[系统] 以上能力调用已完成，请据此直接回复用户，不要重复调用。",
        })
        if outcome.get("validation_problems"):
            logger.info("plan validation problems: %s", outcome["validation_problems"])
        return _merge(
            state, messages=messages, capability_calls=calls,
            _should_plan=False, _plan_done=True,
            plan={"steps": steps, "done": outcome.get("done"), "failed": outcome.get("failed"),
                  "skipped": outcome.get("skipped")},
        )

    _node.__name__ = NODE_PLAN
    return _node


def make_execute(
    *,
    capability_executor: Callable[[str, dict], Any],
    config: Any,
    gate_evaluator: "Callable[[str, dict], Any] | None" = None,
    suspend_available: bool = False,
):
    """执行节点：执行一次能力调用，结果按结构化 payload 回灌对话。

    能力要求补充信息时：有挂起节点则转挂起（M5）；无挂起节点则降级为直接把问题作为回复。
    """

    async def _node(state: dict) -> dict:
        call = dict(state.get("_pending_tool_call") or {})
        name = str(call.get("name") or "")
        args = dict(call.get("arguments") or {})
        messages = list(state.get("messages") or [])
        calls = list(state.get("capability_calls") or [])
        iteration = int(state.get("iteration_count", 0) or 0) + 1

        payload: dict
        try:
            payload = await capability_executor(name, args)
        except Exception as e:  # noqa: BLE001 — 能力异常转为结构化失败，不抛出循环外（PRD 约束 3）
            logger.error("capability %s raised: %s", name, e, exc_info=True)
            payload = {"success": False, "status": "failed", "reply": f"调用异常：{e}"}
        if not isinstance(payload, dict):
            payload = {"success": False, "status": "failed", "reply": "能力返回了非结构化结果"}

        messages.append({
            "role": "tool",
            "tool_call_id": str(call.get("id") or ""),
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        })
        calls.append({
            "capability": name,
            "params_digest": json.dumps(args, ensure_ascii=False)[:500],
            "status": str(payload.get("status") or ("success" if payload.get("success") else "failed")),
            "triggered_by": str((state.get("actor_ref") or {}).get("ref_id", "")),
            "needs_input": bool(payload.get("needs_input")),
        })

        question = str(payload.get("question") or "")
        if payload.get("needs_input") and question:
            if suspend_available:
                # 挂起（M5）：交给挂起节点中断等待补充信息
                return _merge(
                    state, messages=messages, capability_calls=calls,
                    iteration_count=iteration, _pending_tool_call={},
                    _should_suspend=True, _suspend_question=question,
                )
            # 无挂起节点：降级为直接把追问作为回复（不空转到上限）
            return _merge(
                state, messages=messages, capability_calls=calls,
                iteration_count=iteration, _pending_tool_call={}, reply_text=question,
            )
        return _merge(
            state, messages=messages, capability_calls=calls,
            iteration_count=iteration, _pending_tool_call={},
        )

    _node.__name__ = NODE_EXECUTE
    return _node


def make_gate(*, gate_evaluator: Callable[[str, dict], Any]):
    """门禁判定节点（M6 接线时启用）：只调用既有判定通道取结论，不在此新造判定。"""

    async def _node(state: dict) -> dict:
        call = dict(state.get("_pending_tool_call") or {})
        decision: dict
        try:
            decision = await gate_evaluator(str(call.get("name") or ""), dict(call.get("arguments") or {})) or {}
        except Exception as e:  # noqa: BLE001 — 判定异常保守拒绝（fail-closed）
            logger.error("gate failed: %s", e, exc_info=True)
            decision = {"decision": "deny", "reason": f"判定异常：{e}"}
        if str(decision.get("decision") or "allow") == "allow":
            return _merge(state, gate_result=decision)
        messages = list(state.get("messages") or [])
        messages.append({
            "role": "tool",
            "tool_call_id": str(call.get("id") or ""),
            "content": json.dumps(
                {"success": False, "status": "failed",
                 "reply": str(decision.get("reason") or "该操作无法执行，您可能没有相应权限。")},
                ensure_ascii=False,
            ),
        })
        return _merge(
            state, gate_result=decision, messages=messages, _pending_tool_call={},
            iteration_count=int(state.get("iteration_count", 0) or 0) + 1,
        )

    _node.__name__ = NODE_GATE
    return _node


def make_summarize(*, archive_sink: "Callable[[dict], Any] | None" = None):
    """收口节点：回复文本已就绪，按需触发归档与摘要（M7 接线时消费）。"""

    async def _node(state: dict) -> dict:
        text = str(state.get("reply_text") or "").strip()
        if not text:
            text = EMPTY_REPLY
        merged = _merge(state, reply_text=text, state_summary=summarize_state(state))
        if archive_sink is not None:
            try:
                await archive_sink(merged)
            except Exception as e:  # noqa: BLE001 — 归档失败不阻断回复（PRD 约束：失败不阻塞）
                logger.warning("archive_sink failed: %s", e)
        return merged

    _node.__name__ = NODE_SUMMARIZE
    return _node


def make_error_analysis(*, config: Any):
    """迭代上限/异常兜底节点：给出可读收尾（AC-US-01.3），不抛异常到循环外。"""

    async def _node(state: dict) -> dict:
        logger.warning(
            "session graph iteration cap reached: conversation=%s iterations=%s",
            state.get("conversation_id"), state.get("iteration_count"),
        )
        text = str(state.get("reply_text") or "").strip() or CAP_REPLY
        return _merge(state, reply_text=text, _capped=True)

    _node.__name__ = NODE_ERROR
    return _node


# ══════════════════════════════════════════════════════════════════════════════
# 条件路由
# ══════════════════════════════════════════════════════════════════════════════


def route_after_fast(state: dict) -> str:
    return NODE_SUMMARIZE if state.get("_fast") else NODE_UNDERSTAND


def route_after_understand(state: dict, *, config: Any = None) -> str:
    if state.get("reply_text"):
        return NODE_SUMMARIZE
    if state.get("_should_plan"):
        return NODE_PLAN
    if state.get("_pending_tool_call"):
        gate_active = bool(state.get("_gate_active"))
        return NODE_GATE if gate_active else NODE_EXECUTE
    if int(state.get("iteration_count", 0) or 0) >= _max_iterations(state, config):
        return NODE_ERROR
    # 空文本追问（reasoner 输出落在思维链）→ 再问一轮
    return NODE_UNDERSTAND


def route_after_plan(state: dict, *, config: Any = None) -> str:
    """计划执行完毕 → 回到主循环据实收口（AC-US-05.3：回复反映实际执行结果）。"""
    if int(state.get("iteration_count", 0) or 0) >= _max_iterations(state, config):
        return NODE_ERROR
    return NODE_UNDERSTAND


def route_after_execute(state: dict, *, config: Any = None) -> str:
    if state.get("_should_suspend") and state.get("_suspend_active"):
        return NODE_SUSPEND
    if state.get("reply_text"):
        return NODE_SUMMARIZE
    if int(state.get("iteration_count", 0) or 0) >= _max_iterations(state, config):
        return NODE_ERROR
    return NODE_UNDERSTAND


def route_after_gate(state: dict, *, config: Any = None) -> str:
    call = state.get("_pending_tool_call") or {}
    if call:
        return NODE_EXECUTE
    if state.get("reply_text"):
        return NODE_SUMMARIZE
    if int(state.get("iteration_count", 0) or 0) >= _max_iterations(state, config):
        return NODE_ERROR
    return NODE_UNDERSTAND


# ══════════════════════════════════════════════════════════════════════════════
# 图构建
# ══════════════════════════════════════════════════════════════════════════════


def build_session_graph(
    *,
    llm_caller: Callable[[list, list], Any],
    capability_executor: Callable[[str, dict], Any],
    fast_responder: "Callable[[str], str | None] | None" = None,
    tool_specs_provider: "Callable[[], list] | None" = None,
    prompt_builder: "Callable[[], str] | None" = None,
    history_provider: "Callable[[], list] | None" = None,
    archive_sink: "Callable[[dict], Any] | None" = None,
    gate_evaluator: "Callable[[str, dict], Any] | None" = None,
    config: Any = None,
    capability_registry: Any = None,
    ports: Any = None,
    checkpointer: Any = True,
    capability_plan_builder: "Callable[[str, Any], Any] | None" = None,
    capability_spec_provider: "Callable[[], list] | None" = None,
    plan_gate: "Callable[[str], bool] | None" = None,
    suspend_factory: Any = None,
):
    """构建并编译会话编排图（唯一编排主体）。

    Args:
        llm_caller: `async (messages, tool_specs) -> dict`，返回
            `{"type": "tool_call"|"text", ...}`（与既有 agent loop 的调用结果同形）。
        capability_executor: `async (name, arguments) -> dict`，返回结构化成果 payload。
        fast_responder: `(user_text) -> str | None`，零 LLM 短路判定。
        tool_specs_provider / prompt_builder / history_provider: 目录与上下文供给。
        archive_sink: `async (state) -> None`，收口时的归档钩子（M7 接线）。
        gate_evaluator: `async (name, arguments) -> dict`，门禁判定（M6 接线；未注入则不建 gate 节点）。
        capability_registry / ports: 预留注入点（M2 契约表 / M8 端口），本切片不使用。
        checkpointer: `True`=用既有 Postgres 检查点；`False`=不启用；或直接传 saver 实例。
    """
    if checkpointer is True:
        from ..workitem.langgraph_engine.checkpointer import build_checkpointer
        saver = build_checkpointer(config) if config is not None else False
    elif checkpointer is False:
        saver = False
    else:
        saver = checkpointer
    # 挂起（M5）依赖检查点：无检查点时不建挂起节点（interrupt 要求可持久化）
    suspend_active = bool(suspend_factory is not None and saver)
    if suspend_factory is not None and not saver:
        logger.warning("build_session_graph: 注入了 suspend_factory 但无检查点，挂起节点未建")
    plan_active = capability_plan_builder is not None
    plan_available = bool(plan_active and plan_gate is not None)
    if plan_active and plan_gate is None:
        logger.warning("build_session_graph: 已注入计划构造器但缺少 plan_gate，计划支路不会触发")
    if capability_registry is None:
        logger.info("build_session_graph: 未注入 capability_registry（M2 契约表），本切片走 tool_specs_provider")
    if ports is not None:
        logger.info("build_session_graph: ports 已注入但本切片未消费（M8 接线）")

    gs = StateGraph(KernelState)
    gs.add_node(NODE_FAST, make_fast_reply(fast_responder=fast_responder))
    gs.add_node(NODE_UNDERSTAND, make_understand(
        llm_caller=llm_caller, tool_specs_provider=tool_specs_provider,
        prompt_builder=prompt_builder, history_provider=history_provider, config=config,
        plan_gate=plan_gate, plan_available=plan_available,
    ))
    gs.add_node(NODE_EXECUTE, make_execute(
        capability_executor=capability_executor, config=config, gate_evaluator=gate_evaluator,
        suspend_available=suspend_active,
    ))
    gs.add_node(NODE_SUMMARIZE, make_summarize(archive_sink=archive_sink))
    gs.add_node(NODE_ERROR, make_error_analysis(config=config))

    gate_active = gate_evaluator is not None
    if gate_active:
        gs.add_node(NODE_GATE, make_gate(gate_evaluator=gate_evaluator))
    if plan_active:
        gs.add_node(NODE_PLAN, make_plan(
            plan_builder=capability_plan_builder,
            capability_executor=capability_executor,
            capability_spec_provider=capability_spec_provider,
        ))
    if suspend_active:
        node_factory = suspend_factory or build_suspend_node
        gs.add_node(NODE_SUSPEND, node_factory(config=config))

    gs.add_edge(START, NODE_FAST)
    gs.add_conditional_edges(
        NODE_FAST, route_after_fast,
        {NODE_UNDERSTAND: NODE_UNDERSTAND, NODE_SUMMARIZE: NODE_SUMMARIZE},
    )
    understand_targets = {
        NODE_EXECUTE: NODE_EXECUTE, NODE_SUMMARIZE: NODE_SUMMARIZE,
        NODE_ERROR: NODE_ERROR, NODE_UNDERSTAND: NODE_UNDERSTAND,
    }
    if gate_active:
        understand_targets[NODE_GATE] = NODE_GATE
    if plan_active:
        understand_targets[NODE_PLAN] = NODE_PLAN
    gs.add_conditional_edges(
        NODE_UNDERSTAND, lambda s: route_after_understand(s, config=config), understand_targets,
    )
    if plan_active:
        gs.add_conditional_edges(
            NODE_PLAN, lambda s: route_after_plan(s, config=config),
            {NODE_UNDERSTAND: NODE_UNDERSTAND, NODE_ERROR: NODE_ERROR},
        )
    if gate_active:
        gs.add_conditional_edges(
            NODE_GATE, lambda s: route_after_gate(s, config=config),
            {NODE_EXECUTE: NODE_EXECUTE, NODE_UNDERSTAND: NODE_UNDERSTAND,
             NODE_SUMMARIZE: NODE_SUMMARIZE, NODE_ERROR: NODE_ERROR},
        )
    execute_targets = {NODE_UNDERSTAND: NODE_UNDERSTAND, NODE_SUMMARIZE: NODE_SUMMARIZE,
                       NODE_ERROR: NODE_ERROR}
    if suspend_active:
        execute_targets[NODE_SUSPEND] = NODE_SUSPEND
    gs.add_conditional_edges(
        NODE_EXECUTE, lambda s: route_after_execute(s, config=config), execute_targets,
    )
    if suspend_active:
        # 续接后回到主循环，由循环据实收口
        gs.add_edge(NODE_SUSPEND, NODE_UNDERSTAND)
    gs.add_edge(NODE_SUMMARIZE, END)
    gs.add_edge(NODE_ERROR, END)

    graph = gs.compile(checkpointer=saver)
    logger.info(
        "session graph built: fast→understand%s⇄execute→summarize, gate=%s, checkpointer=%s, max_iterations=%s",
        "→gate" if gate_active else "", gate_active, bool(saver),
        getattr(config, "agent_loop_max_iterations", DEFAULT_MAX_ITERATIONS),
    )
    return graph


# ══════════════════════════════════════════════════════════════════════════════
# 运行器 —— 与既有 handle 同签名语义，供入口分派调用（M10 接线）
# ══════════════════════════════════════════════════════════════════════════════


class SessionGraphRunner:
    """编排图运行器：构造初始状态、绑定工具上下文、执行图、清理上下文。"""

    def __init__(
        self,
        graph,
        *,
        conversation_id: str,
        actor_ref: "dict | None" = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        recursion_limit: "int | None" = None,
        event_port: "Callable[[str, dict], Any] | None" = None,
    ) -> None:
        self._graph = graph
        self._conversation_id = conversation_id
        self._actor_ref = dict(actor_ref or {})
        self._max_iterations = int(max_iterations or DEFAULT_MAX_ITERATIONS)
        #: 事件端口（M7）：注入时以框架更新流为唯一进度来源
        self._event_port = event_port
        # 框架级上界（D6：把框架约束用满）——每轮节点跳转预算，留出工具回环余量
        self._recursion_limit = int(recursion_limit or (self._max_iterations * 3 + 6))
        #: 最近一次运行的最终状态（供调用方判断是否命中快速短路等）
        self.last_state: dict = {}
        #: 最近一次运行若以挂起收尾，记录中断负载（M5）
        self.last_pending: dict = {}

    async def run(self, message, db_message_id: str = "", current_user_id: str = "") -> "str | None":
        user_text = str(getattr(message, "content", "") or "").strip()
        actor_ref = dict(self._actor_ref)
        if current_user_id:
            actor_ref.setdefault("ref_id", current_user_id)

        state = make_initial_state(
            conversation_id=self._conversation_id,
            actor_ref=actor_ref,
            max_iterations=self._max_iterations,
        )
        state.update({
            "user_text": user_text,
            "db_message_id": db_message_id,
            "_gate_active": NODE_GATE in (getattr(self._graph, "nodes", {}) or {}),
            "_suspend_active": NODE_SUSPEND in (getattr(self._graph, "nodes", {}) or {}),
        })
        violations = validate_state_serializable(state)
        if violations:
            raise ValueError("初始状态含不可序列化内容：" + ", ".join(violations))

        bind_tool_context(ToolContext(user_id=str(actor_ref.get("ref_id", "") or ""),
                                      actor_ref=actor_ref))
        cfg = {"configurable": {"thread_id": self._conversation_id},
               "recursion_limit": self._recursion_limit}
        try:
            if self._event_port is not None:
                from .graph_events import FIRST_PROGRESS, iter_progress
                self._event_port("progress", {"content": FIRST_PROGRESS})
                async for chunk in self._graph.astream(state, cfg, stream_mode="updates"):
                    for _node, text in iter_progress(chunk):
                        self._event_port("progress", {"content": text, "node": _node})
                snapshot = await self._graph.aget_state(cfg)
                final = dict(getattr(snapshot, "values", None) or {})
            else:
                final = await self._graph.ainvoke(state, cfg)
        finally:
            clear_tool_context()
        self.last_state = final if isinstance(final, dict) else {}
        if not isinstance(final, dict):
            final = {}
        reply = str(final.get("reply_text") or "")
        if reply:
            return reply
        # 挂起（M5）：本轮以提问收尾，问题作为回复；挂起态已随检查点落库待续接
        pending = await get_pending(self._graph, self._conversation_id)
        if pending:
            self.last_pending = pending
            return str(pending.get("question") or "") or None
        return None
