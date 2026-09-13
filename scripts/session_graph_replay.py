# emily-core/scripts/session_graph_replay.py
"""M11 会话编排内核回归语料回放（可重复、零副作用）。

用途：
  - 用桩件（脚本化模型结果 + 桩能力执行）回放编排内核的关键行为，
    作为内核的常驻回归资产；不触网、不写业务库（检查点用内存实现）。

覆盖用例：
  1 快速短路零模型调用    2 工具回环产出最终回复   3 迭代上限收敛为可读收尾
  4 门禁拒绝时不执行能力  5 挂起以提问收尾且可续接  6 计划层内并行
  7 事件端口产出进度      8 外壳端口装配           9 状态可序列化

运行：
  docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/session_graph_replay.py
"""
from __future__ import annotations

import asyncio
import sys
import time

from emily_core.session import plan_graph as pg
from emily_core.session import session_graph as sg
from emily_core.session.capability_contract import CapabilitySpec
from emily_core.session.graph_events import build_event_port
from emily_core.session.graph_gate import build_gate_evaluator
from emily_core.session.kernel_state import validate_state_serializable
from emily_core.session.ports import build_ports
from emily_core.session.suspend_interrupt import build_suspend_node, get_pending, resume_graph

RESULTS: list = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


class Cfg:
    agent_loop_max_iterations = 4


def _caller(script):
    state = {"n": 0}

    async def _llm(messages, specs):
        i = min(state["n"], len(script) - 1)
        state["n"] += 1
        return script[i]
    return _llm, state


def _tool(name="query_data", args=None, cid="c1"):
    return {"type": "tool_call", "tool_name": name, "tool_arguments": args or {"q": "x"},
            "tool_call_id": cid, "content": ""}


def _text(t):
    return {"type": "text", "content": t}


def _memory_saver():
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    except Exception:
        return False


async def main() -> None:
    # 1 快速短路零模型调用
    llm, calls = _caller([_text("不应到达")])

    async def _ok(name, params):
        return {"success": True, "status": "success", "reply": "ok"}

    g = sg.build_session_graph(llm_caller=llm, capability_executor=_ok, config=Cfg(),
                               checkpointer=False,
                               fast_responder=lambda t: "您好" if "你好" in t else None)
    r = sg.SessionGraphRunner(g, conversation_id="replay-fast", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "你好"})(), current_user_id="u")
    rec("1 快速短路零模型调用", reply == "您好" and calls["n"] == 0, f"llm={calls['n']}")

    # 2 工具回环
    llm, _ = _caller([_tool(), _text("已查到记录")])
    g = sg.build_session_graph(llm_caller=llm, capability_executor=_ok, config=Cfg(),
                               checkpointer=False)
    r = sg.SessionGraphRunner(g, conversation_id="replay-loop", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "查数据"})(), current_user_id="u")
    rec("2 工具回环产出最终回复", reply == "已查到记录", f"reply={reply!r}")

    # 3 迭代上限收敛
    llm, _ = _caller([_tool()])
    g = sg.build_session_graph(llm_caller=llm, capability_executor=_ok, config=Cfg(),
                               checkpointer=False)
    r = sg.SessionGraphRunner(g, conversation_id="replay-cap", actor_ref={"ref_id": "u"},
                              max_iterations=2)
    reply = await r.run(type("M", (), {"content": "循环"})(), current_user_id="u")
    rec("3 迭代上限收敛为可读收尾", reply == sg.CAP_REPLY, f"reply={reply!r}")

    # 4 门禁拒绝时不执行能力
    executed = []

    async def _guard(name, params):
        executed.append(name)
        return {"success": True}

    gate = build_gate_evaluator(visible_provider=lambda: ["query_data"])
    llm, _ = _caller([_tool("forbidden_tool"), _text("已被告知无权限")])
    g = sg.build_session_graph(llm_caller=llm, capability_executor=_guard,
                               gate_evaluator=gate, config=Cfg(), checkpointer=False)
    r = sg.SessionGraphRunner(g, conversation_id="replay-gate", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "越权"})(), current_user_id="u")
    rec("4 门禁拒绝时不执行能力", executed == [] and reply == "已被告知无权限",
        f"exec={executed} reply={reply!r}")

    # 5 挂起以提问收尾且可续接（内存检查点，跨步不跨进程）
    async def _need(name, params):
        return {"needs_input": True, "question": "请补充项目名称"}

    saver = _memory_saver()
    llm, _ = _caller([_tool(), _text("已登记完成")])
    g = sg.build_session_graph(llm_caller=llm, capability_executor=_need,
                               suspend_factory=build_suspend_node, config=Cfg(),
                               checkpointer=saver)
    r = sg.SessionGraphRunner(g, conversation_id="replay-suspend", actor_ref={"ref_id": "u"})
    reply = await r.run(type("M", (), {"content": "记一条事件"})(), current_user_id="u")
    pending = await get_pending(g, "replay-suspend")
    rec("5 挂起以提问收尾", reply == "请补充项目名称" and bool(pending), f"reply={reply!r}")
    if pending:
        final = await resume_graph(g, "replay-suspend", "翠湖庭院", actor_ref={"ref_id": "u"})
        rec("5 挂起可续接并产出最终回复",
            str((final or {}).get("reply_text") or "") == "已登记完成",
            f"reply={str((final or {}).get('reply_text'))!r}")

    # 6 计划层内并行
    order = []

    async def _timed(name, params):
        s = time.perf_counter()
        await asyncio.sleep(0.2)
        order.append((params.get("q"), s, time.perf_counter()))
        return {"success": True, "status": "success"}

    specs = [CapabilitySpec(name="query_data", kind="query",
                            params_schema={"type": "object",
                                           "properties": {"q": {"type": "string"}},
                                           "required": ["q"]})]
    sub = pg.build_plan_subgraph(capability_executor=_timed, specs=specs)
    out = await pg.PlanRunner(sub, specs=specs).run([
        {"step_id": "s1", "capability": "query_data", "params": {"q": "a"}, "depends_on": []},
        {"step_id": "s2", "capability": "query_data", "params": {"q": "b"}, "depends_on": []},
        {"step_id": "s3", "capability": "query_data", "params": {"q": "c"},
         "depends_on": ["s1", "s2"]},
    ])
    layer = [x for x in order if x[0] in ("a", "b")]
    overlap = (len(layer) == 2 and layer[0][1] < layer[1][2] and layer[1][1] < layer[0][2])
    rec("6 计划层内并行且分两层完成",
        overlap and sorted(out["done"]) == ["s1", "s2", "s3"], f"done={out['done']}")

    # 7 事件端口产出进度
    emitted = []
    llm, _ = _caller([_tool(), _text("答复")])
    g = sg.build_session_graph(llm_caller=llm, capability_executor=_ok, config=Cfg(),
                               checkpointer=_memory_saver())
    r = sg.SessionGraphRunner(g, conversation_id="replay-events", actor_ref={"ref_id": "u"},
                              event_port=build_event_port(
                                  publish=lambda k, p: emitted.append((k, p.get("content"))),
                                  conversation_id="replay-events"))
    await r.run(type("M", (), {"content": "查数据"})(), current_user_id="u")
    texts = [c for k, c in emitted if k == "progress"]
    rec("7 事件端口产出进度且同源", len(texts) >= 2 and any("能力" in str(t) for t in texts),
        str(texts[:4]))

    # 8 外壳端口装配（缺失时按能力缺失降级，不报错）
    ports = build_ports(core=None)
    rec("8 端口装配可降级", ports.describe().get("events") is False, str(ports.describe()))

    # 9 状态可序列化（收口状态经 archive_sink 校验）
    seen = {}

    async def _sink(state):
        seen["violations"] = validate_state_serializable(state)

    llm, _ = _caller([_text("答复")])
    g = sg.build_session_graph(llm_caller=llm, capability_executor=_ok, config=Cfg(),
                               checkpointer=False, archive_sink=_sink)
    r = sg.SessionGraphRunner(g, conversation_id="replay-state", actor_ref={"ref_id": "u"})
    await r.run(type("M", (), {"content": "你好啊"})(), current_user_id="u")
    rec("9 状态可序列化", seen.get("violations") == [], str(seen.get("violations")))


    # 10 契约校验拦截丢参（入计划前拦截，防历史"未命名事件"类降级）
    problems = pg.validate_steps(
        [{"step_id": "s1", "capability": "record_event", "params": {"request": "记一条事件"}}],
        [CapabilitySpec(name="record_event", kind="write",
                        params_schema={"type": "object",
                                       "properties": {"project_id": {"type": "string"}},
                                       "required": ["project_id"]})],
    )
    rec("10 契约校验拦截缺参与未声明参数",
        any("缺少必填参数" in p for p in problems) and any("未声明参数" in p for p in problems),
        str(problems))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        rec("回放整体执行", False, repr(e))
    failed = [n for n, ok in RESULTS if not ok]
    print("-" * 60)
    print(f"会话编排内核回放：共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}")
    sys.exit(1 if failed else 0)
