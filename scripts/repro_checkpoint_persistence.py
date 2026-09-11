"""repro_checkpoint_persistence.py — 复现/回归：MemorySaver 断点不跨进程存续缺陷。

用与 workitem/langgraph_engine/graph.py 同语义的最小 interrupt 图（ask_user → interrupt），
把"挂起 → 进程重启 → resume"拆成两次独立进程运行。

用法:
    python scripts/repro_checkpoint_persistence.py              # Phase A: 挂起后退出（模拟服务进程）
    python scripts/repro_checkpoint_persistence.py --resume     # Phase B: 全新进程 resume
    python scripts/repro_checkpoint_persistence.py --legacy-memory   # 用 MemorySaver 模拟修复前

连接串优先级：--conn > 环境变量 EMILY_DATABASE_URL > 本机默认（localhost:25432）。
退出码: 0 = 断点跨进程续跑成功（修复后预期）; 1 = 缺陷存在（重启即丢）; 2 = 环境错误。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TypedDict

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "emily-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

if sys.platform == "win32":
    # psycopg 异步不支持 ProactorEventLoop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_THREAD_ID = "repro-checkpoint-thread-1"
_DEFAULT_CONN = "postgresql://emily:emily_secret_2026@127.0.0.1:25432/emily"


class _State(TypedDict, total=False):
    answer: str


def _tool_node(state: _State) -> dict:
    # 与 workitem/langgraph_engine/agent/loop.py ask_user→interrupt 同语义
    from langgraph.types import interrupt
    reply = interrupt("请补充信息")
    return {"answer": reply}


def _build(saver):
    from langgraph.graph import StateGraph, START, END

    g = StateGraph(_State)
    g.add_node("tool_node", _tool_node)
    g.add_edge(START, "tool_node")
    g.add_edge("tool_node", END)
    return g.compile(checkpointer=saver)


def _conn_string(args) -> str:
    conn = args.conn or os.environ.get("EMILY_DATABASE_URL") or _DEFAULT_CONN
    if "connect_timeout" not in conn:
        conn += ("&" if "?" in conn else "?") + "connect_timeout=5"
    return conn


async def _make_saver(args):
    if args.legacy_memory:
        from langgraph.checkpoint.memory import MemorySaver
        print("[saver] MemorySaver（模拟修复前：不跨进程）")
        return MemorySaver(), None

    from emily_core.workitem.langgraph_engine.checkpointer import LazyPostgresCheckpointer
    use_pool = sys.platform != "win32"      # Windows: psycopg_pool worker 不可用，用单连接
    saver = LazyPostgresCheckpointer(_conn_string(args), use_pool=use_pool)
    await saver._ensure()
    if saver.degraded:
        print("[saver] Postgres 不可达 → 已回退 MemorySaver")
    else:
        print(f"[saver] AsyncPostgresSaver ready (pool={use_pool})")
    return saver, saver


async def _main(args) -> int:
    saver, cleanup = await _make_saver(args)
    graph = _build(saver)
    cfg = {"configurable": {"thread_id": _THREAD_ID}}

    if not args.resume:
        await graph.ainvoke({"answer": ""}, cfg)
        snap = await graph.aget_state(cfg)
        print(f"Phase A: 已挂起于 interrupt，next={tuple(snap.next or ())}，进程退出（模拟重启）。")
        if cleanup:
            await cleanup.aclose()
        return 0

    # Phase B：全新进程、全新 saver 实例，同 thread_id
    snap = await graph.aget_state(cfg)
    nxt = tuple(snap.next or ())
    if not nxt:
        print("REPRO OK: 缺陷复现 — 重启后 thread 检查点不存在，resume 无法落到断点。")
        if cleanup:
            await cleanup.aclose()
        return 1

    from langgraph.types import Command
    result = await graph.ainvoke(Command(resume="用户回复"), cfg)
    print(f"PASS: 断点跨进程续跑成功，answer={result.get('answer')!r}")
    # 清理演示 thread
    if cleanup:
        try:
            await cleanup.adelete_thread(_THREAD_ID)
        except Exception as e:
            print(f"[cleanup] {e}")
        await cleanup.aclose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--resume", action="store_true", help="Phase B：在新进程续接断点")
    p.add_argument("--legacy-memory", action="store_true", help="用 MemorySaver 模拟修复前")
    p.add_argument("--conn", default="", help="Postgres 连接串")
    args = p.parse_args()
    try:
        return asyncio.run(_main(args))
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
