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

M1 改造（内核能力补全 B1）后的计划支路：
  understand ──▶ plan_build ──▶ **plan（计划子图，直接挂为父图节点）** ──▶ plan_echo ──▶ understand
  - 计划子图不再由节点内手工 ainvoke：它是父图的一个节点，状态随父线程落检查点，
    步骤级执行因此可恢复、可观测（AC-US-01.1 / 01.3）。
  - 计划子图与父图的状态键逐一对齐（`plan_*`），子图内部游标用 `_plan_*` 以免跨轮残留。

按注入启用、本切片暂不接线（由后续模块接）：
  - `gate_evaluator`（M6 门禁判定）：未注入时不建 gate 节点，understand 直连 execute
  - `capability_plan_builder`（M4 计划子图）与 `suspend_factory`（M5 挂起中断）：未注入时不建对应节点；
    注入时打印显式告警，避免"看起来已接线"的假象（宪法 Q6）
  - `ports`（M8 外壳端口）：仅登记，未使用

状态合并约定：LangGraph 默认通道为「后写覆盖」，因此节点返回 `messages` / `capability_calls`
时必须返回**完整列表**，不能只返回增量。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ..kernel import policies, react_kernel
from ..kernel.context import KernelContext
from .kernel_state import (
    DEFAULT_MAX_ITERATIONS,
    KernelState,
    clear_tool_context,
    make_initial_state,
    summarize_state,
    validate_state_serializable,
)
from .suspend_interrupt import NODE_SUSPEND, build_suspend_node, get_pending

logger = logging.getLogger("emily.session.session_graph")

NODE_FAST = "fast_reply"
NODE_UNDERSTAND = "understand"
NODE_PLAN_BUILD = "plan_build"
NODE_PLAN = "plan"          # 计划子图：以节点形态挂入本图（M1）
NODE_PLAN_ECHO = "plan_echo"
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


def _session_ports(
    state: dict,
    *,
    llm_caller: "Callable[[list, list], Any] | None" = None,
    tool_specs_provider: "Callable[[], list] | None" = None,
    prompt_builder: "Callable[[], str] | None" = None,
    history_provider: "Callable[[], list] | None" = None,
    config: Any = None,
    execute: "Callable[[str, dict], Any] | None" = None,
) -> react_kernel.LoopPorts:
    """构造会话侧循环端口，与共享内核的中性契约对接（M2）。

    会话与工单的差异**只在这里表达**：本函数注入提示/工具集/执行通道，
    循环机制本身全部来自 `react_kernel`（单份实现）。
    """

    def _specs() -> list:
        if tool_specs_provider is None:
            return []
        try:
            return list(tool_specs_provider() or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("tool_specs_provider failed: %s", e)
            return []

    def _prompt() -> str:
        if prompt_builder is None:
            return ""
        try:
            return prompt_builder() or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("prompt_builder failed: %s", e)
            return ""

    def _history() -> list:
        if history_provider is None:
            return []
        try:
            return list(history_provider() or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("history_provider failed: %s", e)
            return []

    def _nudge(result: dict, attempt: int) -> "react_kernel.NudgeOutcome | None":
        """会话侧纠错策略：无结果/有文本 → 不收口重试；空文本 → 追问一次。"""
        if not result:
            return None
        if str((result or {}).get("content") or "").strip():
            return None
        return react_kernel.NudgeOutcome(retry_text="[系统] 请直接给用户回复。")

    return react_kernel.LoopPorts(
        llm_call=(lambda m, s: llm_caller(m, s)) if llm_caller is not None else None,
        tool_specs=_specs,
        prompt=_prompt,
        history=_history,
        user_input=lambda: str(state.get("user_text", "") or ""),
        execute=execute,
        max_iterations=lambda: _max_iterations(state, config),
        text_nudge=_nudge,
        classify_error=react_kernel.default_classify_error,
    )


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
    """理解节点：装配消息 → 模型调用 → 归一（循环机制来自共享内核）。

    若配置了计划门（`plan_gate`：疑似复合请求判定）且计划能力可用，则首轮请求置
    `_should_plan`，由路由转入计划支路（plan_build → 计划子图 → plan_echo）。
    """

    async def _node(state: dict) -> dict:
        messages = list(state.get("messages") or [])
        first_pass = not messages
        should_plan = bool(
            plan_available and plan_gate is not None and first_pass
            and not state.get("_plan_done") and not state.get("_should_plan")
            and plan_gate(str(state.get("user_text", "") or ""))
        )

        ports = _session_ports(
            state, llm_caller=llm_caller, tool_specs_provider=tool_specs_provider,
            prompt_builder=prompt_builder, history_provider=history_provider, config=config,
        )

        # 复合请求：先把首轮消息装配好（系统提示 + 历史 + 用户输入）再交计划支路，
        # 保证计划成果回灌后主循环仍有完整上下文
        if should_plan:
            if not messages:
                messages = react_kernel.assemble_messages(ports=ports)
            return _merge(state, messages=messages, _should_plan=True)

        patch = await react_kernel.llm_step(state, ports=ports)
        if react_kernel.decide_next(patch) == react_kernel.OUTCOME_FINAL:
            text = str(patch.get(react_kernel.LoopKeys().text) or "").strip()
            patch["reply_text"] = text or EMPTY_REPLY
        return _merge(state, **patch)

    _node.__name__ = NODE_UNDERSTAND
    return _node


def make_plan_build(
    *,
    plan_builder: Callable[[str, Any], Any],
    capability_spec_provider: "Callable[[], list] | None" = None,
    max_depth: int = PLAN_MAX_DEPTH,
):
    """计划生成节点：调用计划构造器 + 契约校验 → 写入计划状态，交计划子图执行。

    本节点只负责"生成并落到父图状态"；执行权归**计划子图节点**（父图节点之一），
    不再在节点内手工调用子图（M1 改造）。无可用计划时直接收口回主循环，不进子图。
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
        from .plan_graph import _step_id, validate_steps

        messages = list(state.get("messages") or [])
        user_text = str(state.get("user_text", "") or "")
        specs = _specs()

        try:
            plan = await plan_builder(user_text, specs)
        except Exception as e:  # noqa: BLE001 — 计划失败回退单轮循环
            logger.error("plan_builder failed: %s", e, exc_info=True)
            plan = None

        steps = list((plan or {}).get("steps") or [])
        if not steps:
            messages.append({"role": "system", "content": "[系统] 无可用计划，请按单步诉求直接处理。"})
            return _merge(state, messages=messages, _should_plan=False, _plan_done=True,
                          plan_steps=[], plan_problems=[], plan_max_depth=max_depth)

        # 入计划前按契约校验（AC-US-02.1：不丢参）；不通过的步骤先标记失败，交级联跳过处理
        problems = validate_steps(steps, specs)
        if problems:
            logger.warning("plan validation problems: %s", problems)
        bad_ids = {p.split(":", 1)[0] for p in problems}
        seeded_failed = [sid for sid in
                         (_step_id(s, i) for i, s in enumerate(steps)) if sid in bad_ids]

        return _merge(
            state, messages=messages,
            plan_steps=[dict(s) for s in steps], plan_problems=problems,
            plan_max_depth=max_depth, plan_failed=seeded_failed,
            plan_done=[], plan_skipped=[], plan_step_results=[], plan_text="",
            _should_plan=False,
        )

    _node.__name__ = NODE_PLAN_BUILD
    return _node


def make_plan_echo():
    """计划回灌节点：把计划子图的成果以系统消息回灌对话，交回主循环据实收口。"""

    async def _node(state: dict, runtime: Any = None) -> dict:
        messages = list(state.get("messages") or [])
        calls = list(state.get("capability_calls") or [])
        # 操作者身份取自官方运行时上下文（M4），状态里的 actor_ref 作兼容回退
        official_actor = KernelContext.from_runtime(runtime).actor_ref
        actor_ref = dict(official_actor or state.get("actor_ref") or {})
        for record in state.get("plan_step_results") or []:
            calls.append({
                "capability": str(record.get("capability") or ""),
                "step_id": str(record.get("step_id") or ""),
                "status": str(record.get("status") or ""),
                "triggered_by": str(actor_ref.get("ref_id", "")),
            })
        messages.append({
            "role": "system",
            "content": "[系统] 计划已执行：\n" + str(state.get("plan_text") or ""),
        })
        messages.append({
            "role": "user",
            "content": "[系统] 以上能力调用已完成，请据此直接回复用户，不要重复调用。",
        })
        return _merge(
            state, messages=messages, capability_calls=calls,
            _should_plan=False, _plan_done=True,
            plan={"steps": list(state.get("plan_steps") or []),
                  "done": list(state.get("plan_done") or []),
                  "failed": list(state.get("plan_failed") or []),
                  "skipped": list(state.get("plan_skipped") or [])},
        )

    _node.__name__ = NODE_PLAN_ECHO
    return _node


def make_execute(
    *,
    capability_executor: Callable[[str, dict], Any],
    config: Any,
    gate_evaluator: "Callable[[str, dict], Any] | None" = None,
    suspend_available: bool = False,
):
    """执行节点：执行一次能力调用（机制来自共享内核），结果按结构化 payload 回灌对话。

    能力要求补充信息时：有挂起节点则转挂起（M5）；无挂起节点则降级为直接把问题作为回复。
    """

    async def _node(state: dict, runtime: Any = None) -> dict:
        if not state.get("_pending_tool_call"):
            # 门禁拒绝等路径已回填工具消息并清空待执行调用 → 不重复执行
            return _merge(state, _kernel_outcome=react_kernel.OUTCOME_CONTINUE)

        # 操作者身份取自**官方运行时上下文**（M4）；状态里的 actor_ref 作兼容回退
        official_actor = KernelContext.from_runtime(runtime).actor_ref
        triggered_by = str((official_actor or state.get("actor_ref") or {}).get("ref_id", ""))

        executed: dict = {}

        async def _execute(name: str, arguments: dict) -> dict:
            executed["name"] = str(name or "")
            executed["args"] = dict(arguments or {})
            try:
                payload = await capability_executor(name, arguments)
            except Exception as e:  # noqa: BLE001 — 能力异常转为结构化失败，不抛出循环外（PRD 约束 3）
                logger.error("capability %s raised: %s", name, e, exc_info=True)
                payload = {"success": False, "status": "failed", "reply": f"调用异常：{e}"}
            if not isinstance(payload, dict):
                payload = {"success": False, "status": "failed", "reply": "能力返回了非结构化结果"}
            executed["payload"] = payload
            return payload

        ports = _session_ports(state, config=config, execute=_execute)
        patch = await react_kernel.tool_step(state, ports=ports)
        patch["iteration_count"] = int(state.get("iteration_count", 0) or 0) + 1

        payload = dict(executed.get("payload") or {})
        calls = list(state.get("capability_calls") or [])
        if executed:
            calls.append({
                "capability": str(executed.get("name") or ""),
                "params_digest": json.dumps(executed.get("args") or {}, ensure_ascii=False)[:500],
                "status": str(payload.get("status")
                              or ("success" if payload.get("success") else "failed")),
                "triggered_by": triggered_by,
                "needs_input": bool(payload.get("needs_input")),
            })
        patch["capability_calls"] = calls

        question = str(payload.get("question") or "")
        if payload.get("needs_input") and question:
            if suspend_available:
                # 挂起（M5）：交给挂起节点中断等待补充信息
                patch.update({"_should_suspend": True, "_suspend_question": question})
            else:
                # 无挂起节点：降级为直接把追问作为回复（不空转到上限）
                patch.update({"reply_text": question})
        return _merge(state, **patch)

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
    """理解之后：按共享内核的中性裁决映射到本图节点（路由映射只在此处）。

    - `final`  → 回复就绪，收口
    - `tool`   → 有待执行调用（有门禁则先过门禁）
    - `retry`  → 纠错重试，再问一轮
    - `reject` / `cap` / `error` → 交兜底节点给出可读收尾
    """
    if state.get("_should_plan"):
        return NODE_PLAN_BUILD
    outcome = react_kernel.decide_next(state)
    if outcome == react_kernel.OUTCOME_FINAL:
        return NODE_SUMMARIZE
    if outcome == react_kernel.OUTCOME_TOOL:
        return NODE_GATE if state.get("_gate_active") else NODE_EXECUTE
    if outcome == react_kernel.OUTCOME_RETRY:
        return NODE_UNDERSTAND
    if outcome in (react_kernel.OUTCOME_REJECT, react_kernel.OUTCOME_CAP,
                   react_kernel.OUTCOME_ERROR):
        return NODE_ERROR
    if state.get("reply_text"):
        return NODE_SUMMARIZE
    if state.get("_pending_tool_call"):
        return NODE_GATE if state.get("_gate_active") else NODE_EXECUTE
    if int(state.get("iteration_count", 0) or 0) >= _max_iterations(state, config):
        return NODE_ERROR
    # 无裁决（尚未调用模型）→ 再进一轮
    return NODE_UNDERSTAND


def route_after_plan_build(state: dict, *, config: Any = None) -> str:
    """计划生成之后：有步骤 → 进计划子图节点；无步骤 → 回主循环单步处理。"""
    if list(state.get("plan_steps") or []):
        return NODE_PLAN
    return NODE_UNDERSTAND


def route_after_plan_echo(state: dict, *, config: Any = None) -> str:
    """计划执行完毕 → 回到主循环据实收口（AC-US-05.3：回复反映实际执行结果）。"""
    if int(state.get("iteration_count", 0) or 0) >= _max_iterations(state, config):
        return NODE_ERROR
    return NODE_UNDERSTAND


def route_after_execute(state: dict, *, config: Any = None) -> str:
    """执行之后：挂起优先，其次直接回复（降级追问），否则回循环继续。"""
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

    gs = StateGraph(KernelState, context_schema=KernelContext)

    # ── 节点级策略（M3 / US-02）──
    # 重试只挂"纯模型调用"节点；执行类节点不挂（避免重复副作用）；
    # 挂起节点不挂超时（interrupt 需无限期等待用户补充信息）。
    retry_policy = policies.build_retry_policy(config)

    def _timeout(node_name: str, default: int = policies.DEFAULT_NODE_TIMEOUT_SECONDS):
        return policies.node_timeout(config, node_name, default_seconds=default)

    gs.add_node(NODE_FAST, make_fast_reply(fast_responder=fast_responder),
                timeout=_timeout(NODE_FAST))
    gs.add_node(NODE_UNDERSTAND, make_understand(
        llm_caller=llm_caller, tool_specs_provider=tool_specs_provider,
        prompt_builder=prompt_builder, history_provider=history_provider, config=config,
        plan_gate=plan_gate, plan_available=plan_available,
    ), retry_policy=retry_policy, timeout=_timeout(NODE_UNDERSTAND))
    gs.add_node(NODE_EXECUTE, make_execute(
        capability_executor=capability_executor, config=config, gate_evaluator=gate_evaluator,
        suspend_available=suspend_active,
    ), timeout=_timeout(NODE_EXECUTE, policies.DEFAULT_TOOL_TIMEOUT_SECONDS))
    gs.add_node(NODE_SUMMARIZE, make_summarize(archive_sink=archive_sink),
                timeout=_timeout(NODE_SUMMARIZE))
    gs.add_node(NODE_ERROR, make_error_analysis(config=config),
                timeout=_timeout(NODE_ERROR))

    gate_active = gate_evaluator is not None
    if gate_active:
        gs.add_node(NODE_GATE, make_gate(gate_evaluator=gate_evaluator),
                    timeout=_timeout(NODE_GATE))
    if plan_active:
        from .plan_graph import build_plan_subgraph
        # 计划子图：编译产物**不传 checkpointer**，以节点形态挂入父图（继承父线程检查点）
        plan_subgraph = build_plan_subgraph(
            capability_executor=capability_executor, max_depth=PLAN_MAX_DEPTH,
        )
        gs.add_node(NODE_PLAN_BUILD, make_plan_build(
            plan_builder=capability_plan_builder,
            capability_spec_provider=capability_spec_provider,
        ), retry_policy=retry_policy, timeout=_timeout(NODE_PLAN_BUILD))
        # 计划子图节点不设超时：其耗时随步骤数增长，由能力层单次调用超时兜底
        gs.add_node(NODE_PLAN, plan_subgraph)
        gs.add_node(NODE_PLAN_ECHO, make_plan_echo(), timeout=_timeout(NODE_PLAN_ECHO))
    if suspend_active:
        node_factory = suspend_factory or build_suspend_node
        # 挂起节点不设超时：interrupt 需无限期等待用户补充信息
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
        understand_targets[NODE_PLAN_BUILD] = NODE_PLAN_BUILD
    gs.add_conditional_edges(
        NODE_UNDERSTAND, lambda s: route_after_understand(s, config=config), understand_targets,
    )
    if plan_active:
        gs.add_conditional_edges(
            NODE_PLAN_BUILD, lambda s: route_after_plan_build(s, config=config),
            {NODE_PLAN: NODE_PLAN, NODE_UNDERSTAND: NODE_UNDERSTAND},
        )
        # 计划子图 → 回灌（静态边：子图执行完毕即回灌，不再由节点内部决定）
        gs.add_edge(NODE_PLAN, NODE_PLAN_ECHO)
        gs.add_conditional_edges(
            NODE_PLAN_ECHO, lambda s: route_after_plan_echo(s, config=config),
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
    """编排图运行器：构造初始状态、经官方运行时上下文传入内核上下文、执行图。

    M4（US-04）后：上下文**入口**是官方 `context=` 通道（图声明 `context_schema`），
    进程内通道只由 `KernelContext.bind_compat()` 这一个标注兼容点转接。
    """

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
        #: 最近一次运行使用的官方运行时上下文（供调用方与回归断言核验）
        self.last_context = KernelContext()

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

        # ★ 上下文入口：官方运行时上下文（经 context= 传入图）
        kernel_ctx = KernelContext(
            conversation_id=self._conversation_id,
            actor_ref=actor_ref,
            user_id=str(actor_ref.get("ref_id", "") or ""),
            db_message_id=str(db_message_id or ""),
            extra={"gate_active": bool(state.get("_gate_active")),
                   "suspend_active": bool(state.get("_suspend_active"))},
        )
        self.last_context = kernel_ctx
        # 唯一兼容桥：转接到能力层读取的进程内通道
        kernel_ctx.bind_compat()

        cfg = {"configurable": {"thread_id": self._conversation_id},
               "recursion_limit": self._recursion_limit}
        try:
            if self._event_port is not None:
                from .graph_events import FIRST_PROGRESS, iter_progress
                self._event_port("progress", {"content": FIRST_PROGRESS})
                async for chunk in self._graph.astream(state, cfg, stream_mode="updates",
                                                       context=kernel_ctx):
                    for _node, text in iter_progress(chunk):
                        self._event_port("progress", {"content": text, "node": _node})
                snapshot = await self._graph.aget_state(cfg)
                final = dict(getattr(snapshot, "values", None) or {})
            else:
                final = await self._graph.ainvoke(state, cfg, context=kernel_ctx)
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
