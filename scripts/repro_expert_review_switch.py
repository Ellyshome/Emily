"""repro_expert_review_switch.py — 复现/回归：expert_review_enabled 死开关缺陷。

修复前：Config(expert_review_enabled=False) 被路由忽略，绑定专家的 WI 仍进 expert_review。
修复后：开关 False 时路由直达 executing；True 时维持原行为（expert_review）。

用法:
    python scripts/repro_expert_review_switch.py
退出码: 0 = 开关生效（修复后预期）; 1 = 缺陷存在（开关被忽略）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "emily-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def _route(config):
    """构造"绑定专家的 WI"并调用路由函数。

    优先用修复后的闭包工厂 make_route_after_routing(config)；
    若函数不存在（修复前代码），回退旧 route_after_routing(state)——它不接收 config，
    必然忽略开关，正好用于复现缺陷。
    """
    from emily_core.workitem.workitem import WorkItem
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.langgraph_engine.state import set_bus_context
    from emily_core.workitem.langgraph_engine import graph as G

    wi = WorkItem(id="repro-switch-1")
    wi.expert_required = True          # 复现 session_agent.py:719 的绑定结果
    wi.expert_id = "expert-x"
    set_bus_context(BusContext(work_item=wi))

    make = getattr(G, "make_route_after_routing", None)
    if make is not None:
        return make(config)({})
    return G.route_after_routing({})


def main() -> int:
    from emily_core.config import Config

    route_off = _route(Config(expert_review_enabled=False))
    if route_off == "expert_review":
        print("REPRO OK: 缺陷复现 — expert_review_enabled=False 被忽略，路由仍返回 expert_review")
        return 1
    if route_off != "executing":
        print(f"UNEXPECTED: False 分支 route={route_off!r}")
        return 2
    print("PASS: 开关生效 — False 时跳过专家评审，直达 executing")

    route_on = _route(Config(expert_review_enabled=True))
    print(f"对照组: True 时 route={route_on}（预期 expert_review）")
    return 0 if route_on == "expert_review" else 2


if __name__ == "__main__":
    sys.exit(main())
