# scripts/kernel_regression.py
"""内核能力补全回归断言（M5）—— 零副作用、可重复。

用途：
  - 为《LangGraph内核能力补全》各项验收标准提供**可执行判定**（宪法 Q1/Q2）。
  - 全部用桩件驱动：不触网、不写业务库；检查点用内存实现。

覆盖：
  --case plan-node       计划子图以节点形态挂入父图（含子图检查点命名空间、跨轮不残留）  [US-01]
  --case plan-cap        无可用计划时不进子图、不空转                                   [US-01]
  --case plan-validate   入计划契约校验：非法步骤标记失败并级联跳过                      [US-01]
  --case loop-shared     共享对话循环内核：两侧消费者可达 + 内核不感知调用方             [US-03]
  --case retry-policy    节点级重试/超时策略已声明（静态可达性）                        [US-02]
  --case all             跑全部

运行：
  docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/kernel_regression.py --case all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from emily_core.session import session_graph as sg
from emily_core.session.capability_contract import CapabilitySpec

RESULTS: list = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


class Cfg:
    agent_loop_max_iterations = 6


def _tool(name="query_data", args=None, cid="c1"):
    return {"type": "tool_call", "tool_name": name, "tool_arguments": args or {"q": "x"},
            "tool_call_id": cid, "content": ""}


def _text(t):
    return {"type": "text", "content": t}


def _caller(script):
    state = {"n": 0}

    async def _llm(messages, specs):
        i = min(state["n"], len(script) - 1)
        state["n"] += 1
        return script[i]
    return _llm, state


def _memory_saver():
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    except Exception:
        return False


_SPECS = [CapabilitySpec(name="query_data", kind="query",
                         params_schema={"type": "object",
                                        "properties": {"q": {"type": "string"}},
                                        "required": ["q"]})]

_PLAN_3STEPS = {"steps": [
    {"step_id": "s1", "capability": "query_data", "params": {"q": "a"}, "depends_on": []},
    {"step_id": "s2", "capability": "query_data", "params": {"q": "b"}, "depends_on": []},
    {"step_id": "s3", "capability": "query_data", "params": {"q": "c"}, "depends_on": ["s1", "s2"]},
]}


async def _async_ok(name=None, params=None) -> dict:
    return {"success": True, "status": "success", "reply": "ok"}


def _plan_builder(plan: dict):
    """把计划字典包成异步构造器（与生产接线同形）。"""
    async def _b(text, specs):
        return plan
    return _b


def _plan_graph(*, llm, executor, saver, plan_builder=None, specs=None):
    """构造带计划支路的会话图（父图 + 计划子图节点）。"""
    return sg.build_session_graph(
        llm_caller=llm,
        capability_executor=executor,
        tool_specs_provider=lambda: [{"type": "function", "function": {
            "name": "query_data", "description": "查询", "parameters": _SPECS[0].params_schema}}],
        prompt_builder=lambda: "你是回归桩件。",
        history_provider=lambda: [],
        capability_spec_provider=lambda: list(specs if specs is not None else _SPECS),
        capability_plan_builder=plan_builder or _plan_builder(_PLAN_3STEPS),
        plan_gate=lambda text: "计划" in str(text or ""),
        config=Cfg(),
        checkpointer=saver,
    )


async def case_plan_node() -> None:
    """计划子图以节点形态挂入父图：执行可达 + 子图检查点 + 跨轮不残留。"""
    calls: list = []
    order: list = []
    saver = _memory_saver()

    async def _timed(name, params):
        s = time.perf_counter()
        await asyncio.sleep(0.15)
        order.append((params.get("q"), s, time.perf_counter()))
        calls.append(params.get("q"))
        return {"success": True, "status": "success", "reply": "ok"}

    llm, _ = _caller([_text("计划已执行，答复如下")])
    g = _plan_graph(llm=llm, executor=_timed, saver=saver)

    # 断言 1：plan 节点挂有**编译后的子图**（而非普通协程函数）→ 证明是子图挂载，非节点内手工调用
    spec = (getattr(g, "nodes", {}) or {}).get(sg.NODE_PLAN)
    subs = list(getattr(spec, "subgraphs", None) or [])
    other_subs = len(list(getattr((getattr(g, "nodes", {}) or {}).get(sg.NODE_UNDERSTAND),
                                  "subgraphs", None) or []))
    rec("plan-node 1 计划子图以节点形态挂入（节点挂有子图）",
        len(subs) == 1 and hasattr(subs[0], "get_graph") and other_subs == 0,
        f"plan.subgraphs={[type(s).__name__ for s in subs]} understand.subgraphs={other_subs}")

    # 断言 2：父图包含 plan_build / plan / plan_echo 三节点
    names = set((getattr(g, "nodes", {}) or {}).keys())
    rec("plan-node 2 父图含计划三节点",
        {sg.NODE_PLAN_BUILD, sg.NODE_PLAN, sg.NODE_PLAN_ECHO} <= names,
        f"nodes={sorted(n for n in names if 'plan' in n)}")

    r = sg.SessionGraphRunner(g, conversation_id="kr-plan", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "帮我查计划里的数据"})(), current_user_id="u")

    rec("plan-node 3 计划步骤经子图执行完毕", sorted(calls) == ["a", "b", "c"],
        f"calls={calls} reply={reply!r}")

    layer = [x for x in order if x[0] in ("a", "b")]
    overlap = (len(layer) == 2 and layer[0][1] < layer[1][2] and layer[1][1] < layer[0][2])
    rec("plan-node 4 层内并行语义保持", overlap, f"order={[x[0] for x in order]}")

    # 断言 5：子图状态落在父线程的**子命名空间**下 → 计划执行随父线程可恢复
    ns_seen = set()
    try:
        async for cp in saver.alist({"configurable": {"thread_id": "kr-plan"}}):
            cfg = getattr(cp, "config", {}) or {}
            ns_seen.add(str((cfg.get("configurable") or {}).get("checkpoint_ns", "")))
    except Exception as e:  # noqa: BLE001
        rec("plan-node 5 子图检查点命名空间", False, f"alist failed: {e}")
        return
    nested = [ns for ns in ns_seen if sg.NODE_PLAN in ns]
    rec("plan-node 5 计划执行落父线程的子命名空间检查点", bool(nested),
        f"ns={sorted(ns_seen)}")

    # 断言 6：同一线程再次触发计划 → 不残留上一轮步骤结果（内部游标 _plan_* 不进父图 schema）
    calls.clear()
    llm2, _ = _caller([_text("第二轮答复")])
    r2 = sg.SessionGraphRunner(g, conversation_id="kr-plan", actor_ref={"ref_id": "u"})
    final2 = await r2.run(type("M", (), {"content": "再查一次计划"})(), current_user_id="u")
    results = list((r2.last_state or {}).get("plan_step_results") or [])
    rec("plan-node 6 跨轮不残留（本轮结果为 3 条，非累加）",
        sorted(calls) == ["a", "b", "c"] and len(results) == 3,
        f"calls={calls} results={len(results)}")


async def case_plan_cap() -> None:
    """无可用计划：不进子图、不空转、给出可读回复。"""
    calls: list = []
    saver = _memory_saver()

    async def _ex(name, params):
        calls.append(name)
        return {"success": True, "status": "success", "reply": "ok"}

    llm, state = _caller([_text("我按单步处理了")])
    g = _plan_graph(llm=llm, executor=_ex, saver=saver,
                    plan_builder=_plan_builder({"steps": []}))
    r = sg.SessionGraphRunner(g, conversation_id="kr-plan-cap", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "帮我查计划"})(), current_user_id="u")
    rec("plan-cap 无计划则不执行子图且给出可读回复",
        calls == [] and reply == "我按单步处理了", f"calls={calls} reply={reply!r}")


async def case_plan_validate() -> None:
    """入计划契约校验：非法步骤先标记失败 → 级联跳过 → 只执行合法步骤。"""
    calls: list = []

    async def _ex(name, params):
        calls.append(params.get("q"))
        return {"success": True, "status": "success", "reply": "ok"}

    bad_plan = {"steps": [
        # 缺必填参数 q（应被契约拦截）
        {"step_id": "s1", "capability": "query_data", "params": {}, "depends_on": []},
        # 依赖 s1：上游失败 → 级联跳过
        {"step_id": "s2", "capability": "query_data", "params": {"q": "b"}, "depends_on": ["s1"]},
        {"step_id": "s3", "capability": "query_data", "params": {"q": "c"}, "depends_on": []},
    ]}
    llm, _ = _caller([_text("答复")])
    g = _plan_graph(llm=llm, executor=_ex, saver=_memory_saver(),
                    plan_builder=_plan_builder(bad_plan))
    r = sg.SessionGraphRunner(g, conversation_id="kr-plan-val", actor_ref={"ref_id": "u"})
    await r.run(type("M", (), {"content": "帮我查计划"})(), current_user_id="u")
    st = r.last_state or {}
    rec("plan-validate 非法步骤不执行、下游级联跳过、合法步骤执行",
        calls == ["c"] and "s1" in (st.get("plan_failed") or [])
        and "s2" in (st.get("plan_skipped") or []),
        f"calls={calls} failed={st.get('plan_failed')} skipped={st.get('plan_skipped')}")


def case_loop_shared() -> None:
    """共享循环内核：消费者 ≥2 且内核不感知调用方。"""
    import inspect
    src = ""
    try:
        from emily_core.kernel import react_kernel as rk
        src = inspect.getsource(rk)
    except Exception as e:  # noqa: BLE001
        rec("loop-shared 共享内核模块存在", False, f"import failed: {e}")
        return
    rec("loop-shared 1 共享内核模块存在", bool(src))
    consumers = []
    for mod_name in ("emily_core.session.session_graph",
                     "emily_core.workitem.langgraph_engine.agent.loop"):
        try:
            mod = __import__(mod_name, fromlist=["*"])
            mod_src = inspect.getsource(mod)
            if "react_kernel" in mod_src:
                consumers.append(mod_name)
        except Exception as e:  # noqa: BLE001
            print(f"  (skip {mod_name}: {e})")
    rec("loop-shared 2 两侧消费者可达（≥2）", len(consumers) >= 2, f"consumers={consumers}")
    leaks = [t for t in ("workitem", "session") if t in src]
    rec("loop-shared 3 内核不感知调用方（无按侧字面量）", not leaks, f"leaks={leaks}")


class _NullRegistry:
    """空工具/解析器注册表（桩件）。"""

    def get(self, name):
        return None

    def has(self, name):
        return False

    def __contains__(self, name):
        return False

    def list_names(self):
        return []

    def list_all(self):
        return []


def _workitem_graph(script: list):
    """构造工单侧图 + 桩件（LLM 脚本驱动；空工具集与空解析器）。"""
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config

    class CfgW:
        agent_loop_max_iterations = 6
        llm_agent_loop_max_tokens = 1024
        llm_dynamic_output = False
        langgraph_checkpointer = "memory"
        expert_review_enabled = False

    state = {"n": 0}

    class StubLLM:
        agent_loop_model = "stub-model"
        router_model = "stub-model"
        model = "stub-model"

        async def chat_messages(self, messages, *, tools=None, model=None, max_tokens=None, **kw):
            i = min(state["n"], len(script) - 1)
            state["n"] += 1
            return script[i]

    reg = _NullRegistry()
    g = build_workitem_graph(
        hook_adapter=build_hook_adapter_from_config({}, {}),
        llm_client=StubLLM(),
        business_tools=reg,
        resolvers=reg,
        config=CfgW(),
        max_iterations=6,
    )
    return g, state


async def case_workitem_loop() -> None:
    """工单侧循环走共享内核：控制工具收口 + 执行通道未注册工具的处置。"""
    from emily_core.workitem.langgraph_engine.state import (
        set_bus_context, clear_bus_context, make_initial_state,
    )
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    def _tc(name, args):
        return {"type": "tool_call", "tool_name": name, "tool_arguments": args,
                "tool_call_id": f"c-{name}", "content": ""}

    # 场景 A：直接 complete_work 收口（控制工具路径）
    script = [_tc("complete_work", {"status": "success", "summary": ["桩件完成"],
                                    "data": {"n": 1}, "business_object_no": "EVT-STUB-1"})]
    g, calls = _workitem_graph(script)
    ctx = BusContext()
    ctx.work_item = WorkItem(user_input="桩件请求")
    set_bus_context(ctx)
    try:
        final = await g.ainvoke(make_initial_state(pipeline_run_id="kr-wi", max_iterations=6),
                                config={"configurable": {"thread_id": "kr-wi-a"}})
    finally:
        clear_bus_context()
    sr = getattr(ctx.work_item, "structured_result", None)
    rec("workitem-loop 1 控制工具 complete_work 经共享内核收口",
        final.get("wi_state") == "done" and sr is not None
        and getattr(sr, "business_object_no", "") == "EVT-STUB-1",
        f"wi_state={final.get('wi_state')} llm_calls={calls['n']} obj={getattr(sr, 'business_object_no', None)}")

    # 场景 B：执行通道遇到未注册工具 → 结构化失败回填 → 再由 complete_work 收口
    script2 = [_tc("record_event", {"request": "桩件"}), script[0]]
    g2, calls2 = _workitem_graph(script2)
    ctx2 = BusContext()
    ctx2.work_item = WorkItem(user_input="桩件请求2")
    set_bus_context(ctx2)
    try:
        final2 = await g2.ainvoke(make_initial_state(pipeline_run_id="kr-wi2", max_iterations=6),
                                  config={"configurable": {"thread_id": "kr-wi-b"}})
    finally:
        clear_bus_context()
    steps = list(getattr(ctx2.work_item, "step_results", []) or [])
    rec("workitem-loop 2 执行通道失败结构化回填后继续循环",
        final2.get("wi_state") == "done" and len(steps) == 1
        and getattr(steps[0], "success", True) is False and calls2["n"] == 2,
        f"wi_state={final2.get('wi_state')} steps={len(steps)} llm_calls={calls2['n']}")

    # 场景 C：模型返回纯文本 → 走纠错策略（追加纠正消息并重试），不直接当成果
    script3 = [{"type": "text", "content": "我这就帮你办", "tool_call_id": ""}, script[0]]
    g3, calls3 = _workitem_graph(script3)
    ctx3 = BusContext()
    ctx3.work_item = WorkItem(user_input="桩件请求3")
    set_bus_context(ctx3)
    try:
        final3 = await g3.ainvoke(make_initial_state(pipeline_run_id="kr-wi3", max_iterations=6),
                                  config={"configurable": {"thread_id": "kr-wi-c"}})
    finally:
        clear_bus_context()
    msgs = list(final3.get("messages") or [])
    corrected = any(m.get("role") == "user" and "[系统纠正]" in str(m.get("content") or "")
                    for m in msgs)
    rec("workitem-loop 3 文本回复走纠错策略而非直接收口",
        corrected and calls3["n"] == 2 and final3.get("wi_state") == "done",
        f"corrected={corrected} llm_calls={calls3['n']} wi_state={final3.get('wi_state')}")


def case_retry_policy() -> None:
    """节点级重试/超时声明可达（静态扫描）。"""
    from pathlib import Path
    root = Path("/app/emily_core")
    retry_hits, timeout_hits = [], []
    for p in list(root.rglob("*.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        rel = str(p.relative_to(root))
        if "retry_policy=" in text:
            retry_hits.append(rel)
        if "timeout=_timeout(" in text:
            timeout_hits.append(rel)
    rec("retry-policy 1 节点级重试声明可达（两张图）", len(retry_hits) >= 2, f"files={retry_hits}")
    rec("retry-policy 2 节点级超时声明可达（两张图）", len(timeout_hits) >= 2, f"files={timeout_hits}")

    # 执行类节点不得挂重试（避免重复副作用）
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph  # noqa: F401
    import inspect
    from emily_core.workitem.langgraph_engine import graph as wg
    src = inspect.getsource(wg)
    tool_line = [ln for ln in src.splitlines() if 'add_node("tool_node"' in ln]
    followed = src.split('add_node("tool_node"', 1)[1][:400] if tool_line else ""
    rec("retry-policy 3 执行类节点不挂重试（不产生重复副作用）",
        "retry_policy" not in followed.split("gs.add_node", 1)[0],
        f"tool_node 声明片段={followed.splitlines()[0] if followed else 'n/a'}")


async def case_retry_transient() -> None:
    """瞬时故障：由框架节点级策略重试；耗尽后上抛给调用方兜底。"""
    class FlakyCfg(Cfg):
        node_retry_max_attempts = 2
        node_retry_initial_interval = 0.01
        node_retry_backoff_factor = 1.0
        node_timeout_seconds = 30

    calls = {"n": 0}

    async def _ok(name, params):
        return {"success": True, "status": "success", "reply": "ok"}

    async def _flaky_once(messages, specs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("stub transient failure")
        return _text("重试后成功")

    g = sg.build_session_graph(llm_caller=_flaky_once, capability_executor=_ok,
                               config=FlakyCfg(), checkpointer=False)
    r = sg.SessionGraphRunner(g, conversation_id="kr-retry-ok", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "查数据"})(), current_user_id="u")
    rec("retry-transient 1 瞬时故障由框架重试后正常收口",
        reply == "重试后成功" and calls["n"] == 2, f"reply={reply!r} llm_calls={calls['n']}")

    # 持续瞬时故障 → 重试耗尽 → 上抛（由调用方兜底为可读回复，会话侧为 handle_via_graph）
    calls2 = {"n": 0}

    async def _always_transient(messages, specs):
        calls2["n"] += 1
        raise ConnectionError("stub persistent transient failure")

    g2 = sg.build_session_graph(llm_caller=_always_transient, capability_executor=_ok,
                                config=FlakyCfg(), checkpointer=False)
    r2 = sg.SessionGraphRunner(g2, conversation_id="kr-retry-fail", actor_ref={"ref_id": "u"})
    raised = None
    try:
        await r2.run(type("M", (), {"content": "查数据"})(), current_user_id="u")
    except Exception as e:  # noqa: BLE001
        raised = e
    rec("retry-transient 2 重试耗尽后上抛（交调用方出可读回复）",
        raised is not None and calls2["n"] == 2,
        f"raised={type(raised).__name__ if raised else None} llm_calls={calls2['n']}")


async def case_node_timeout() -> None:
    """节点超时：超时后节点中断，不出现无响应（调用方兜底）。"""
    import asyncio as _aio

    class SlowCfg(Cfg):
        node_retry_max_attempts = 1
        node_timeout_seconds = 1

    async def _ok(name, params):
        return {"success": True, "status": "success", "reply": "ok"}

    async def _slow(messages, specs):
        await _aio.sleep(3)
        return _text("不该等到")

    g = sg.build_session_graph(llm_caller=_slow, capability_executor=_ok,
                               config=SlowCfg(), checkpointer=False)
    r = sg.SessionGraphRunner(g, conversation_id="kr-timeout", actor_ref={"ref_id": "u"})
    import time as _t
    t0 = _t.perf_counter()
    raised = None
    try:
        await r.run(type("M", (), {"content": "查数据"})(), current_user_id="u")
    except Exception as e:  # noqa: BLE001
        raised = e
    elapsed = _t.perf_counter() - t0
    rec("node-timeout 单节点超时在设定时间内中断",
        raised is not None and elapsed < 2.5,
        f"raised={type(raised).__name__ if raised else None} elapsed={elapsed:.2f}s")


async def case_ctx_official() -> None:
    """内核上下文入口为官方运行时上下文（US-04）：入口唯一、兼容点唯一、节点优先读官方上下文。"""
    from pathlib import Path
    from emily_core.kernel.context import KernelContext
    from emily_core.session.kernel_state import make_initial_state as session_initial_state

    # 1) 两张图都声明了上下文 schema
    g_session = _plan_graph(llm=_caller([_text("x")])[0],
                            executor=_async_ok,
                            saver=False)
    g_wi, _ = _workitem_graph([{"type": "text", "content": "x", "tool_call_id": ""}])
    rec("ctx-official 1 会话图声明 context_schema",
        getattr(g_session, "context_schema", None) is KernelContext,
        f"schema={getattr(g_session, 'context_schema', None)}")
    rec("ctx-official 2 工单图声明 context_schema",
        getattr(g_wi, "context_schema", None) is KernelContext,
        f"schema={getattr(g_wi, 'context_schema', None)}")

    # 2) 会话侧：capability_calls 的 triggered_by 取自**官方上下文**（与 state.actor_ref 不同仍取官方值）
    async def _ok(name, params):
        return {"success": True, "status": "success", "reply": "ok"}

    llm, _ = _caller([_tool(), _text("已查到")])
    g = _plan_graph(llm=llm, executor=_ok, saver=False)
    kctx = KernelContext(conversation_id="kr-ctx", actor_ref={"ref_id": "OFFICIAL"},
                         user_id="OFFICIAL")
    kctx.bind_compat()
    state = session_initial_state(conversation_id="kr-ctx",
                                  actor_ref={"ref_id": "STATE-ACTOR"}, max_iterations=4)
    state.update({"user_text": "查数据", "_gate_active": False, "_suspend_active": False})
    final = await g.ainvoke(state, {"configurable": {"thread_id": "kr-ctx"}}, context=kctx)
    calls = list((final or {}).get("capability_calls") or [])
    rec("ctx-official 3 节点从官方上下文取操作者身份（非 state 回退值）",
        bool(calls) and calls[0].get("triggered_by") == "OFFICIAL",
        f"triggered_by={calls[0].get('triggered_by') if calls else None}")

    # 3) 工单侧：节点优先取 runtime.context.bus
    from emily_core.workitem.langgraph_engine.agent.loop import _ctx_from_runtime
    from emily_core.workitem.pipeline.context import BusContext

    official_bus = BusContext()
    official_bus.work_item = None
    other_bus = BusContext()
    other_bus.work_item = None
    from emily_core.workitem.langgraph_engine.state import set_bus_context, clear_bus_context
    set_bus_context(other_bus)
    try:
        class _R:
            context = KernelContext(bus=official_bus)
        picked = _ctx_from_runtime(_R())
    finally:
        clear_bus_context()
    rec("ctx-official 4 工单节点优先取官方运行时上下文里的 bus",
        picked is official_bus, f"picked_is_official={picked is official_bus}")

    # 4) 兼容点唯一：自研通道的调用点只允许出现在 kernel/context.py
    root = Path("/app/emily_core")
    offenders = []
    for p in list(root.rglob("*.py")):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:  # noqa: BLE001
            continue
        rel = str(p.relative_to(root))
        if rel == "kernel/context.py":
            continue
        for ln in lines:
            s = ln.strip()
            if s.startswith("#") or "def " in s or "import " in s:
                continue
            for token in ("set_bus_context(", "bind_tool_context("):
                if token not in s:
                    continue
                head = s.split(token)[0]
                # 排除"出现在字符串字面量里"的提及（如 RuntimeError 文案）
                if head.count('"') % 2 or head.count("'") % 2:
                    continue
                offenders.append(f"{rel}: {s[:80]}")
    rec("ctx-official 5 自研上下文通道调用点唯一（仅兼容桥）",
        not offenders, f"offenders={offenders}")


async def case_plan_node_pg() -> None:
    """计划子图以节点形态挂入 + **生产检查点（Postgres）**：计划执行状态落库可恢复。

    与 plan-node 的差别：使用生产 checkpointer（AsyncPostgresSaver 懒加载），
    证明计划执行的检查点确实由父线程承载并落库（AC-US-01.1），而非仅内存实现。
    """
    from emily_core.config import Config
    from emily_core.workitem.langgraph_engine.checkpointer import build_checkpointer

    cfg = Config()
    saver = build_checkpointer(cfg)
    if not saver:
        rec("plan-node-pg 生产检查点可用", False, "build_checkpointer 返回空（回退内存？）")
        return
    rec("plan-node-pg 1 生产检查点可用（Postgres）", True, f"saver={type(saver).__name__}")

    calls: list = []

    async def _ex(name, params):
        calls.append(params.get("q"))
        return {"success": True, "status": "success", "reply": "ok"}

    llm, _ = _caller([_text("计划已执行（生产检查点）")])
    g = sg.build_session_graph(
        llm_caller=llm, capability_executor=_ex,
        tool_specs_provider=lambda: [],
        prompt_builder=lambda: "回归桩件（生产检查点）",
        history_provider=lambda: [],
        capability_spec_provider=lambda: list(_SPECS),
        capability_plan_builder=_plan_builder(_PLAN_3STEPS),
        plan_gate=lambda text: True,
        config=cfg, checkpointer=saver,
    )
    r = sg.SessionGraphRunner(g, conversation_id="kr-plan-pg", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "帮我查计划里的数据"})(), current_user_id="u")
    rec("plan-node-pg 2 计划步骤经子图执行（生产检查点）",
        sorted(calls) == ["a", "b", "c"], f"calls={calls} reply={reply!r}")


CASES = {
    "plan-node": case_plan_node,
    "plan-node-pg": case_plan_node_pg,
    "plan-cap": case_plan_cap,
    "plan-validate": case_plan_validate,
    "loop-shared": case_loop_shared,
    "workitem-loop": case_workitem_loop,
    "retry-policy": case_retry_policy,
    "retry-transient": case_retry_transient,
    "node-timeout": case_node_timeout,
    "ctx-official": case_ctx_official,
}


async def _run_async(names: list) -> None:
    for n in names:
        fn = CASES.get(n)
        if fn is None:
            rec(f"case {n}", False, "未注册")
            continue
        out = fn()
        if asyncio.iscoroutine(out):
            await out


def main() -> int:
    ap = argparse.ArgumentParser(description="内核能力补全回归断言")
    ap.add_argument("--case", default="all",
                    help="plan-node | plan-cap | plan-validate | loop-shared | retry-policy | all")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    names = list(CASES) if args.case == "all" else [c.strip() for c in args.case.split(",")]
    asyncio.run(_run_async(names))

    failed = [n for n, ok in RESULTS if not ok]
    if args.json:
        print(json.dumps({"total": len(RESULTS), "failed": failed,
                          "results": [{"name": n, "ok": ok} for n, ok in RESULTS]},
                         ensure_ascii=False))
    print("-" * 60)
    print(f"内核能力补全回归：共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
