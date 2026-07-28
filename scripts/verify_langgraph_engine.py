# scripts/verify_langgraph_engine.py
"""LangGraph 执行引擎验证脚本（含 error_analysis 纠错路径）。

用法：
  uv run python scripts/verify_langgraph_engine.py --dry-run          # 打印 graph Mermaid
  uv run python scripts/verify_langgraph_engine.py --mock             # 正常路径
  uv run python scripts/verify_langgraph_engine.py --mock-failure     # 失败纠错路径
  uv run python scripts/verify_langgraph_engine.py --mock-permission  # 权限失败 abort 路径
  uv run python scripts/verify_langgraph_engine.py --status           # 查看引擎配置
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "emily-core"))


def cmd_dry_run() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config

    class MockAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): pass
        async def node2_plan(self, ctx): pass
        async def node3_execute(self, ctx): pass
        async def node4_summary(self, ctx): pass

    adapter = build_hook_adapter_from_config({}, {})
    g = build_workitem_graph(MockAgent(), adapter, max_replan=1)
    print("=== WorkItem LangGraph 结构（含 error_analysis）===")
    print(f"节点数: {len(g.get_graph().nodes)}")
    print(f"max_replan: {g._max_replan}")
    print("\n=== Mermaid ===")
    print(g.get_graph().draw_mermaid())
    return 0


def _make_mock_agents():
    """返回 (call_log, NormalAgent, FailureAgent, PermissionAgent)。"""
    call_log = []

    class MockStepResult:
        def __init__(self, success, output='', tool_name=''):
            self.success = success; self.output = output
            self.step_id = 'step-01'; self.tool_calls = []
    class NormalAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): call_log.append('node1')
        async def node2_plan(self, ctx): call_log.append('node2')
        async def node3_execute(self, ctx):
            call_log.append('node3')
            ctx.work_item.step_results = [MockStepResult(True)]
        async def node4_summary(self, ctx): call_log.append('node4')
    class FailureAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): call_log.append('node1')
        async def node2_plan(self, ctx): call_log.append('node2')
        async def node3_execute(self, ctx):
            call_log.append('node3')
            if not getattr(ctx.work_item, '_replanned', False):
                ctx.work_item.step_results = [MockStepResult(False, '参数缺失', 'record_event')]
                ctx.work_item._replanned = True
            else:
                ctx.work_item.step_results = [MockStepResult(True)]
        async def node4_summary(self, ctx): call_log.append('node4')
    class PermissionAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): call_log.append('node1')
        async def node2_plan(self, ctx): call_log.append('node2')
        async def node3_execute(self, ctx):
            call_log.append('node3')
            ctx.work_item.step_results = [MockStepResult(False, '您没有相应权限。')]
        async def node4_summary(self, ctx): call_log.append('node4')

    return call_log, NormalAgent, FailureAgent, PermissionAgent


def cmd_mock() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    call_log, NormalAgent, _, _ = _make_mock_agents()
    async def main():
        g = build_workitem_graph(NormalAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
        ctx = BusContext(); ctx.work_item = WorkItem()
        state = make_initial_state(ctx, max_replan=1)
        result = await g.ainvoke(state, config={'configurable': {'thread_id': 'mock-normal'}})
        print("=== 正常路径 ===")
        print(f"执行顺序: {call_log}")
        print(f"replan_count: {result.get('replan_count')}")
        assert call_log == ['node1', 'node2', 'node3', 'node4']
        print("PASS 正常路径通过")
    asyncio.run(main())
    return 0


def cmd_mock_failure() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    call_log, _, FailureAgent, _ = _make_mock_agents()
    async def main():
        g = build_workitem_graph(FailureAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
        ctx = BusContext(); ctx.work_item = WorkItem()
        state = make_initial_state(ctx, max_replan=1)
        result = await g.ainvoke(state, config={'configurable': {'thread_id': 'mock-failure'}})
        print("=== 失败纠错路径 ===")
        print(f"执行顺序: {call_log}")
        print(f"error_type: {result.get('error_type')}")
        print(f"replan_count: {result.get('replan_count')}")
        print(f"最终 should_abort: {ctx.should_abort}")
        # 验证 error_analysis 已运行（error_type 和 error_analysis 字段均由它写入 state）
        assert result.get('error_type'), 'error_analysis 未产出 error_type'
        assert result.get('error_analysis', {}).get('should_retry') or result.get('error_analysis', {}).get('should_replan'), \
            'error_analysis 应建议 retry 或 replan'
        # node3 第一次失败后重试成功了（出现了2次 node3），且最后到达 node4
        assert call_log.count('node3') == 2, '应有一次失败+一次重试'
        assert call_log[-1] == 'node4', '最后应到达 node4'
        print("PASS 失败纠错路径通过（error_analysis 触发→retry→成功）")
    asyncio.run(main())
    return 0


def cmd_mock_permission() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    call_log, _, _, PermissionAgent = _make_mock_agents()
    async def main():
        g = build_workitem_graph(PermissionAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
        ctx = BusContext(); ctx.work_item = WorkItem()
        state = make_initial_state(ctx, max_replan=1)
        result = await g.ainvoke(state, config={'configurable': {'thread_id': 'mock-perm'}})
        print("=== 权限失败路径 ===")
        print(f"执行顺序: {call_log}")
        print(f"error_type: {result.get('error_type')}")
        print(f"should_abort: {ctx.should_abort}")
        assert result.get('error_type') == 'permission_denied', '权限失败应分类为 permission_denied'
        assert ctx.should_abort, '权限失败应 abort'
        assert 'node4' not in call_log, '权限失败不应到 node4'
        print("PASS 权限失败路径通过（代码预分类 abort，未调 LLM）")
    asyncio.run(main())
    return 0


def cmd_status() -> int:
    try:
        from emily_core.config import Config
        c = Config()
        print(f"workitem_engine: {c.workitem_engine}")
        print(f"langgraph_max_replan: {c.langgraph_max_replan}")
    except Exception as e:
        print(f"Config 读取失败: {e}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LangGraph 执行引擎验证（含纠错闭环）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="打印 graph 结构")
    group.add_argument("--mock", action="store_true", help="正常路径 mock")
    group.add_argument("--mock-failure", action="store_true", help="失败纠错路径 mock")
    group.add_argument("--mock-permission", action="store_true", help="权限失败 abort 路径")
    group.add_argument("--status", action="store_true", help="查看引擎配置")
    args = parser.parse_args()

    handlers = {
        ("dry_run", cmd_dry_run), ("mock", cmd_mock), ("mock_failure", cmd_mock_failure),
        ("mock_permission", cmd_mock_permission), ("status", cmd_status),
    }
    for attr, fn in handlers:
        if getattr(args, attr):
            return fn()
    return 0


if __name__ == "__main__":
    sys.exit(main())
