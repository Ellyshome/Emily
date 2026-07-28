# emily-core/emily_core/workitem/langgraph_engine/nodes.py
"""LangGraph 节点适配函数 —— 包装 WorkItemAgent 的 4 个 node handler + error_analysis 节点。

每个节点函数签名：async fn(state: WorkItemGraphState) -> dict
  - 从 state["context"] 取 BusContext
  - 调用现有 handler（handler 零改动）
  - 返回 dict（State 增量更新）

RetryPolicy 策略：
  - node1/node2（纯 LLM 无副作用）：配 RetryPolicy，node 级重试安全（langgraph 1.x 移除了 RetryPolicy，用装饰器实现）
  - node3（工具循环，含 L2 录入非幂等）：不配 RetryPolicy
  - node4（成果总结）：不配 RetryPolicy
  - error_analysis：不配 RetryPolicy（本身是错误处理，LLM 失败走代码兜底分类）
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("emily.langgraph.nodes")


# NOTE: langgraph >= 1.0 移除了 RetryPolicy，改用 tenacity 装饰器。
# 保留 node_retry_policies() 函数仅作标记（None 表示不重试，dict 表示重试参数）。
# retry 由 make_node_* 工厂内部 tenacity 包装实现。


def _get_context(state: dict) -> Any:
    """从 state 取 BusContext，缺失则抛错。"""
    ctx = state.get("context")
    if ctx is None:
        raise RuntimeError("WorkItemGraphState missing 'context' field")
    return ctx


def _enter_stage(state: dict, stage_name: str) -> float:
    """节点入口：设置日志 stage + current_stage，返回开始时间戳。"""
    ctx = _get_context(state)
    ctx.current_stage = stage_name
    try:
        from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
        LLMInteractionLogger.set_stage(stage_name)
    except Exception:
        pass
    state["current_stage"] = stage_name
    return time.monotonic()


def _exit_stage(state: dict, stage_name: str, t_start: float) -> dict:
    """节点出口：记录耗时，返回增量 dict。"""
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    timings = dict(state.get("node_timings", {}))
    timings[stage_name] = elapsed_ms
    return {"node_timings": timings}


# ════════════════════════════════════════════════════════════════════
# 节点工厂函数
# ════════════════════════════════════════════════════════════════════


def make_node1(agent, hook_adapter):
    """构建 node1 节点函数（意图验证+注入）。"""

    async def node1(state: dict) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node1")
        if not await hook_adapter.fire_before("wi_node1", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node1", t_start)
        try:
            await agent.node1_intent(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node1", ctx, e)
            ctx.should_abort = True
            ctx.abort_reason = str(e)
            if ctx.work_item is not None:
                ctx.work_item.error_message = str(e)
            return _exit_stage(state, "wi_node1", t_start)
        await hook_adapter.fire_after("wi_node1", ctx)
        return _exit_stage(state, "wi_node1", t_start)

    node1.__name__ = "node1_intent"
    return node1


def make_node2(agent, hook_adapter):
    """构建 node2 节点函数（计划+标准）。

    重规划时（从 error_analysis 回来）递增 replan_count，并把 replan_hint
    写入 BusContext.baggage，供 _llm_plan 读取注入 prompt。
    """

    async def node2(state: dict) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node2")

        # 递增 replan_count（仅当从 error_analysis 回来时）
        entered_before = state.get("_entered_node2", False)
        if entered_before:
            state["replan_count"] = state.get("replan_count", 0) + 1
        state["_entered_node2"] = True

        # 注入 replan_hint 到 baggage（_llm_plan 会读取并追加到 prompt）
        replan_hint = state.get("replan_hint", "")
        if replan_hint:
            ctx.set("replan_hint", replan_hint)
            logger.info("node2: replan_hint injected (replan_count=%d)", state.get("replan_count", 0))

        if not await hook_adapter.fire_before("wi_node2", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node2", t_start)
        try:
            await agent.node2_plan(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node2", ctx, e)
            ctx.should_abort = True
            ctx.abort_reason = str(e)
            if ctx.work_item is not None:
                ctx.work_item.error_message = str(e)
            return _exit_stage(state, "wi_node2", t_start)
        await hook_adapter.fire_after("wi_node2", ctx)
        return _exit_stage(state, "wi_node2", t_start)

    node2.__name__ = "node2_plan"
    return node2


def make_node3(agent, hook_adapter):
    """构建 node3 节点函数（执行+验收）。不配 RetryPolicy（含工具副作用）。"""

    async def node3(state: dict) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node3")
        if not await hook_adapter.fire_before("wi_node3", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node3", t_start)
        try:
            await agent.node3_execute(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node3", ctx, e)
            ctx.should_abort = True
            ctx.abort_reason = str(e)
            if ctx.work_item is not None:
                ctx.work_item.error_message = str(e)
            return _exit_stage(state, "wi_node3", t_start)
        await hook_adapter.fire_after("wi_node3", ctx)
        return _exit_stage(state, "wi_node3", t_start)

    node3.__name__ = "node3_execute"
    return node3


def make_node4(agent, hook_adapter):
    """构建 node4 节点函数（成果总结）。不配 RetryPolicy。"""

    async def node4(state: dict) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node4")
        if not await hook_adapter.fire_before("wi_node4", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node4", t_start)
        try:
            await agent.node4_summary(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node4", ctx, e)
            # node4 非必经（对齐 PipelineBUS required=False），异常只记录不 abort
            if ctx.work_item is not None:
                ctx.work_item.add_warning(f"node4 失败: {e}")
        await hook_adapter.fire_after("wi_node4", ctx)
        return _exit_stage(state, "wi_node4", t_start)

    node4.__name__ = "node4_summary"
    return node4


def make_error_analysis(agent, hook_adapter):
    """构建 error_analysis 节点函数（错误分析，Self-Reflection）。

    从 BusContext.work_item.step_results 找失败 step → ErrorAnalyzer.analyze
    → 写入 state.error_analysis / replan_hint / error_type。
    """

    from emily_core.workitem.langgraph_engine.error_analysis import ErrorAnalyzer

    analyzer = ErrorAnalyzer(
        llm_client=getattr(agent, "_llm", None),
        config=getattr(agent, "_config", None),
    )

    async def error_analysis(state: dict) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "error_analysis")

        if not await hook_adapter.fire_before("error_analysis", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "error_analysis", t_start)

        try:
            result = await analyzer.analyze(ctx)
        except Exception as e:
            logger.error("error_analysis crashed: %s, fallback to transient", e, exc_info=True)
            result = {
                "error_type": "transient_failure",
                "root_cause": f"分析器异常: {e}",
                "replan_hint": "",
                "should_replan": False,
                "should_retry": True,
                "should_abort": False,
                "user_prompt": "",
            }

        logger.info(
            "error_analysis: type=%s replan=%s retry=%s abort=%s hint=%s",
            result.get("error_type"),
            result.get("should_replan"),
            result.get("should_retry"),
            result.get("should_abort"),
            (result.get("replan_hint", "") or "")[:60],
        )

        state_update = {
            "error_analysis": result,
            "error_type": result.get("error_type", ""),
            "replan_hint": result.get("replan_hint", ""),
        }

        if result.get("should_abort"):
            ctx.should_abort = True
            ctx.abort_reason = result.get("root_cause", "error_analysis abort")
            user_prompt = result.get("user_prompt", "")
            if user_prompt and ctx.work_item is not None:
                ctx.work_item.add_warning(f"需追问用户: {user_prompt}")

        await hook_adapter.fire_after("error_analysis", ctx)
        state_update.update(_exit_stage(state, "error_analysis", t_start))
        return state_update

    error_analysis.__name__ = "error_analysis"
    return error_analysis


def node_retry_policies() -> dict:
    """返回各节点的重试标记（langgraph >= 1.0 RetryPolicy 移除，保留仅作文档）。

    None = 不重试；dict 表示支持重试（实际 retry 包装在 graph 层外）。
    """
    return {
        "wi_node1": {"max_attempts": 3},   # 纯 LLM，可重试
        "wi_node2": {"max_attempts": 3},   # 纯 LLM，可重试
        "wi_node3": None,                   # 工具循环含副作用
        "wi_node4": None,                   # 审核修正由条件边驱动
        "error_analysis": None,             # 自身是错误处理
    }
