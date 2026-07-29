# emily-core/emily_core/workitem/langgraph_engine/nodes.py
"""LangGraph 节点适配函数 —— 包装 WorkItemAgent 的 4 个 node handler + error_analysis 节点。

每个节点函数签名：async fn(state: dict) -> dict
  - 从 contextvars.get_bus_context() 获取 BusContext（而非 state["context"]）
  - 调用现有 handler（handler 零改动）
  - flow_control（should_abort/has_failed_step 等）写回 state dict

RetryPolicy 策略：
  - node1/node2（纯 LLM 无副作用）：配 RetryPolicy（langgraph 1.x 移除 RetryPolicy，用 tenacity）
  - node3（工具循环，含 L2 录入非幂等）：不配
  - node4：不配（审核修正由条件边驱动）
  - error_analysis：不配（本身是错误处理）
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("emily.langgraph.nodes")


def _get_context() -> object:
    """从 contextvars 取 BusContext。"""
    from .state import get_bus_context
    return get_bus_context()


def _enter_stage(state: dict, stage_name: str) -> float:
    """节点入口：设置日志 stage + current_stage，返回开始时间戳。"""
    ctx = _get_context()
    ctx.current_stage = stage_name
    try:
        from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
        LLMInteractionLogger.set_stage(stage_name)
    except Exception:
        pass
    state["current_stage"] = stage_name
    return time.monotonic()


def _exit_stage(state: dict, stage_name: str, t_start: float) -> dict:
    """节点出口：记录耗时 + 回写 flow_control，返回增量 dict。"""
    ctx = _get_context()
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    timings = dict(state.get("node_timings", {}))
    timings[stage_name] = elapsed_ms

    # 回写流程控制信号到 state（route_* 条件边从 state 读）
    fc = dict(state.get("flow_control", {}))
    fc["should_abort"] = ctx.should_abort
    fc["abort_reason"] = ctx.abort_reason

    # 检查是否有失败的 step（route_after_node3 读）
    if stage_name == "wi_node3":
        wi = getattr(ctx, "work_item", None)
        has_failed = False
        if wi is not None:
            for sr in getattr(wi, "step_results", []) or []:
                if not getattr(sr, "success", True):
                    has_failed = True
                    break
        fc["has_failed_step"] = has_failed

    return {"node_timings": timings, "flow_control": fc}


# ════════════════════════════════════════════════════════════════════
# 节点工厂函数
# ════════════════════════════════════════════════════════════════════


def make_node1(agent, hook_adapter):
    """构建 node1 节点函数（意图验证+注入）。"""

    async def node1(state: dict) -> dict:
        ctx = _get_context()
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

    重规划时递增 replan_count，注入 replan_hint 到 BusContext.baggage。
    """

    async def node2(state: dict) -> dict:
        ctx = _get_context()
        t_start = _enter_stage(state, "wi_node2")

        # 递增 replan_count（仅当从 error_analysis 回来时）
        entered_before = state.get("_entered_node2", False)
        if entered_before:
            state["replan_count"] = state.get("replan_count", 0) + 1
            state["retry_count"] = 0  # 重规划后重置 retry 预算
        state["_entered_node2"] = True

        # 注入 replan_hint 到 baggage
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
    """构建 node3 节点函数（执行+验收）。不配 RetryPolicy。"""

    async def node3(state: dict) -> dict:
        ctx = _get_context()
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
        ctx = _get_context()
        t_start = _enter_stage(state, "wi_node4")
        if not await hook_adapter.fire_before("wi_node4", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node4", t_start)
        try:
            await agent.node4_summary(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node4", ctx, e)
            if ctx.work_item is not None:
                ctx.work_item.add_warning(f"node4 失败: {e}")
        await hook_adapter.fire_after("wi_node4", ctx)
        return _exit_stage(state, "wi_node4", t_start)

    node4.__name__ = "node4_summary"
    return node4


def make_error_analysis(agent, hook_adapter):
    """构建 error_analysis 节点函数（Self-Reflection）。"""

    from emily_core.workitem.langgraph_engine.error_analysis import ErrorAnalyzer

    analyzer = ErrorAnalyzer(
        llm_client=getattr(agent, "_llm", None),
        config=getattr(agent, "_config", None),
    )

    async def error_analysis(state: dict) -> dict:
        ctx = _get_context()
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
    """返回各节点的重试标记。"""
    return {
        "wi_node1": {"max_attempts": 3},
        "wi_node2": {"max_attempts": 3},
        "wi_node3": None,
        "wi_node4": None,
        "error_analysis": None,
    }
