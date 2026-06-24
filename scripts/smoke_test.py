"""Emily 离线端到端冒烟测试（Mock 大脑，无需 LLM）。

验证 Session 主线编排：StandardMessage → SessionPool → SessionAgent →
WorkItem → 4 节点公共 Pipeline BUS → ReplyMessage。

两种模式：
  · 默认（无 DB）：跳过用户绑定的 DB 写入（打桩），仅验证编排骨架。
  · --with-db：连接真实空 PostgreSQL（EMILY_DATABASE_URL），自动建 22 表后跑通。

用法（从 emily-core/ 目录）::

    python ../scripts/smoke_test.py
    EMILY_DATABASE_URL=postgresql://emily:emily_secret_2026@localhost:15432/emily \
        python ../scripts/smoke_test.py --with-db
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 允许从仓库根或 emily-core/ 运行
_HERE = Path(__file__).resolve()
_CORE_DIR = _HERE.parents[1] / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


async def run(with_db: bool) -> int:
    from emily_core import EmilyCore
    from emily_core.config import Config
    from emily_core.adapters.standard.message import StandardMessage

    if with_db:
        from emily_core import bootstrap
        core = bootstrap.init()  # 读 EMILY_DATABASE_URL，自动建表
    else:
        core = EmilyCore(Config())  # 无 LLM key → Mock 大脑

        # 打桩用户绑定（避免 DB），其余编排真实执行
        class _U:
            id = "u-smoke"
        core.user_binding_service.get_or_create_user = lambda **k: (_U(), True)

    # ── 用例 1：闲聊短路（不创建 WorkItem）──
    greet = StandardMessage(
        message_id="m0", platform="qq", conversation_type="private",
        conversation_id="smoke-conv", sender_id="q1", sender_name="张工", content="你好",
    )
    r0 = await core.handle_message(greet)
    assert r0 is not None and "你好" in r0.content, f"greeting failed: {r0}"
    print(f"[1] 闲聊短路 OK: {r0.content[:40]}")

    # ── 用例 2：任务消息 → WorkItem → 4 节点 BUS ──
    task = StandardMessage(
        message_id="m1", platform="qq", conversation_type="private",
        conversation_id="smoke-conv", sender_id="q1", sender_name="张工",
        content="帮我创建事件：样板段放线完成",
    )
    r1 = await core.handle_message(task)
    assert r1 is not None and "[Mock" in r1.content, f"task reply failed: {r1}"
    print(f"[2] 任务→WorkItem→4节点BUS OK: {r1.content[:60]}")

    # ── 用例 2b：Phase C executor_mode=mock 验证 ──
    agent = core._session_pool.lookup("smoke-conv")
    done = agent.scheduler._done
    assert done and done[-1].state.value == "DONE", f"WorkItem not DONE: {done}"
    print(f"[2b] Phase C mock executor OK: executor_mode={core.config.executor_mode}")

    # ── 校验：SessionPool 复用同一 Session ──
    assert core._session_pool.size == 1, f"expected 1 session, got {core._session_pool.size}"
    agent = core._session_pool.lookup("smoke-conv")
    done = agent.scheduler._done
    assert done and done[-1].state.value == "DONE", f"WorkItem not DONE: {done}"
    print(f"[3] SessionPool 复用 OK: size={core._session_pool.size}, WI={done[-1].id} state={done[-1].state.value}")

    # ── 用例 3：终止 Session ──
    ok = await core.terminate_session("smoke-conv")
    assert ok and core._session_pool.size == 0, "terminate failed"
    print(f"[4] 终止 Session OK: pool size={core._session_pool.size}")

    print("\n✅ 全部冒烟用例通过（Session 主线编排端到端跑通）")
    print(f"   health: {core.health()}")
    print(f"   Phase C: executor_mode={core.config.executor_mode} guardian_mode={core.config.guardian_mode}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Emily 离线端到端冒烟测试")
    parser.add_argument("--with-db", action="store_true", help="连接真实空 PostgreSQL（EMILY_DATABASE_URL）")
    args = parser.parse_args()
    return asyncio.run(run(args.with_db))


if __name__ == "__main__":
    raise SystemExit(main())
