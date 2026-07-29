# emily-core/emily_core/workitem/langgraph_engine/nodes.py
"""统一生命周期图节点工厂。

节点：created / routing / executing(agent loop) / summarizing / error_analysis
executing 内嵌 agent loop（agent_node ↔ tool_node，在 loop.py 实现，graph.py 接线）。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("emily.langgraph.nodes")


def _get_context():
    from .state import get_bus_context
    return get_bus_context()


def _enter_stage(state: dict, stage_name: str) -> float:
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
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    timings = dict(state.get("node_timings", {}))
    timings[stage_name] = elapsed_ms
    return {"node_timings": timings}


def _load_sop_text(sop_id: str) -> str:
    """加载 SOP .md 全文。参照 injector.py:_load_sop_text。"""
    if not sop_id:
        return ""
    try:
        from emily_core.infrastructure.llm.prompt_loader import load_prompt
        return load_prompt(sop_id) or ""
    except Exception:
        pass
    # 回退：直接读 sops 目录
    try:
        from emily_core.infrastructure.paths import resolve_data_path
        from pathlib import Path
        sop_dir = resolve_data_path("", "/app/sops", "emily-data/sops")
        for p in Path(sop_dir).glob(f"{sop_id}*.md"):
            return p.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("load SOP %s failed: %s", sop_id, e)
    return ""


def _extract_structured_result(wi, ctx) -> "StructuredResult":
    """从 step_results 提取 StructuredResult（从 workitem_agent.py:730 迁移，零改动）。

    M8 会从 WorkItemAgent 删除本方法，此处为唯一存留副本。
    """
    from ..pipeline.interfaces.execution import StructuredResult
    spec = getattr(wi, "output_spec", {}) or {}
    step_results = getattr(wi, "step_results", []) or []

    failed_steps = [sr for sr in step_results if not getattr(sr, "success", True)]
    if not step_results:
        status = "failed"
    elif failed_steps and len(failed_steps) == len(step_results):
        status = "failed"
    elif failed_steps:
        status = "partial"
    else:
        status = "success"

    data = {}
    for sr in step_results:
        bd = getattr(sr, "business_data", {}) or {}
        for field in spec.get("data_fields", []):
            if field in bd and field not in data:
                data[field] = bd[field]

    summary_facts = []
    for sr in step_results:
        output = (getattr(sr, "output", "") or "").strip()
        if output and len(summary_facts) < 8:
            summary_facts.append(output[:200])

    rag_sources = []
    for sr in step_results:
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in getattr(rr, "chunks", []) or []:
                doc = getattr(chunk, "doc_name", "") or ""
                if doc and doc not in rag_sources:
                    rag_sources.append(doc)
    for sr in step_results:
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in getattr(rr, "chunks", []) or []:
                content = (getattr(chunk, "content", "") or "")[:500]
                if content:
                    summary_facts.append(f"〔{getattr(chunk, 'doc_name', '?')}〕{content}")

    business_object_no = ""
    for sr in step_results:
        bd = getattr(sr, "business_data", {}) or {}
        for key in ("event_no", "task_no", "meeting_no", "object_id"):
            val = bd.get(key, "")
            if val:
                business_object_no = str(val)
                break
        if business_object_no:
            break

    issues = list(getattr(wi, "warnings", []) or [])
    for sr in step_results:
        guardian = getattr(sr, "guardian", None)
        if guardian and getattr(guardian, "reason", ""):
            issues.append(f"[{getattr(sr, 'step_id', '?')}] {guardian.reason}")

    # result_constraints 校验
    rc = getattr(wi, "result_constraints", {}) or {}
    if rc:
        must_include = rc.get("must_include", [])
        if must_include:
            combined = " ".join(summary_facts) if summary_facts else ""
            for item in must_include:
                if item not in combined:
                    issues.append(f"[constraint] 缺少必须信息: {item}")
        must_not = rc.get("must_not", [])
        if must_not:
            combined = " ".join(summary_facts) if summary_facts else ""
            for item in must_not:
                clean = item.replace("不要", "").replace("别", "").strip()
                if clean and clean in combined:
                    issues.append(f"[constraint] 包含违规内容: {item}")

    needs_confirm = any(
        getattr(getattr(sr, "business_data", {}), "needs_confirm", False)
        for sr in step_results
    )

    error_category = ""
    if status == "failed":
        for sr in failed_steps:
            output = (getattr(sr, "output", "") or "")
            if "权限" in output or "permission" in output:
                error_category = "permission"; break
            if "参数" in output or "param" in output:
                error_category = "param_error"; break
            if "不存在" in output or "未找到" in output:
                error_category = "not_found"; break
        error_category = error_category or "system"

    suggested_followup = ""
    if status == "success" and spec.get("intent", "").startswith("query"):
        suggested_followup = "需要看详情吗？"

    return StructuredResult(
        status=status,
        intent=spec.get("intent", wi.sop_id or "fallback"),
        sop_id=wi.sop_id or "",
        risk_level=getattr(wi, "risk_level", "L2") or "L2",
        data=data,
        summary_facts=summary_facts,
        rag_sources=rag_sources,
        business_object_no=business_object_no,
        issues=issues,
        needs_confirm=needs_confirm,
        error_category=error_category,
        suggested_followup=suggested_followup,
    )


def make_created(hook_adapter):
    """created 节点：初始化 BusContext + 灌注。"""
    async def created(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "created")
        if not await hook_adapter.fire_before("created", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "created", t), "wi_state": "failed"}
        try:
            # sop_id 已由 SessionAgent 设置；加载 SOP 全文暂存 baggage
            wi = ctx.work_item
            sop_id = wi.sop_id or ""
            if sop_id:
                sop_text = _load_sop_text(sop_id)
                ctx.set("sop_text", sop_text)
                state["current_sop_id"] = sop_id
            # prompt_info 供 ArchiveHook 使用
            ctx.set("prompt_info_created", {
                "sop_id": sop_id,
                "sop_loaded": bool(sop_id),
            })
        except Exception as e:
            logger.error("created node failed: %s", e, exc_info=True)
            await hook_adapter.fire_error("created", ctx, e)
            ctx.should_abort = True
            return {**_exit_stage(state, "created", t), "wi_state": "failed"}
        await hook_adapter.fire_after("created", ctx)
        return {**_exit_stage(state, "created", t), "wi_state": "routing"}
    created.__name__ = "created"
    return created


def make_routing(hook_adapter):
    """routing 节点：SOP .md 已加载，验证 route_decision。"""
    async def routing(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "routing")
        if not await hook_adapter.fire_before("routing", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "routing", t), "wi_state": "failed"}
        try:
            # route_decision 已由 SessionAgent 设置（node1_intent 的职责上移到 SessionAgent）
            wi = ctx.work_item
            if wi.route_decision is None:
                from ..pipeline.interfaces.routing import RouteDecision, SubTask
                wi.route_decision = RouteDecision(
                    intent_type=getattr(wi, "intent_type", "fallback") or "fallback",
                    sop_id=wi.sop_id or None,
                    confidence="medium" if wi.sop_id else "none",
                    is_compound=False,
                    sub_tasks=[
                        SubTask(
                            id="subtask-001",
                            sop_id=wi.sop_id or "",
                            user_input=wi.user_input,
                        )
                    ] if wi.sop_id else [],
                    _source="session_agent",
                )
            ctx.intent = wi.route_decision
            # prompt_info 供 ArchiveHook 使用
            route = wi.route_decision
            ctx.set("prompt_info_routing", {
                "intent_type": getattr(route, "intent_type", "?"),
                "confidence": getattr(route, "confidence", "?"),
                "sop_id": wi.sop_id or "fallback",
                "is_compound": getattr(route, "is_compound", False),
            })
        except Exception as e:
            logger.error("routing node failed: %s", e, exc_info=True)
            await hook_adapter.fire_error("routing", ctx, e)
            ctx.should_abort = True
            return {**_exit_stage(state, "routing", t), "wi_state": "failed"}
        await hook_adapter.fire_after("routing", ctx)
        return {**_exit_stage(state, "routing", t), "wi_state": "executing"}
    routing.__name__ = "routing"
    return routing


def make_executing(hook_adapter, *, llm_client, business_tools, resolvers, config):
    """executing 节点：包装 agent loop 入口（agent_node）。

    agent_node ↔ tool_node 的循环由 graph.py 的条件边驱动，本节点只触发首轮 agent_node。
    """
    async def executing(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "executing")
        if not await hook_adapter.fire_before("executing", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "executing", t), "wi_state": "failed"}
        try:
            await hook_adapter.fire_after("executing", ctx)
        except Exception as e:
            logger.error("executing node hook failed: %s", e, exc_info=True)
            await hook_adapter.fire_error("executing", ctx, e)
        # agent_node 由 graph.py 的 executing→agent_node 边驱动，executing 节点本身只做 hook + 阶段标记
        return {**_exit_stage(state, "executing", t), "wi_state": "executing"}
    executing.__name__ = "executing"
    return executing


def make_summarizing(hook_adapter):
    """summarizing 节点：从 step_results 提取 StructuredResult + 设 result_text。"""
    async def summarizing(state: dict) -> dict:
        ctx = _get_context()
        wi = ctx.work_item
        t = _enter_stage(state, "summarizing")
        if not await hook_adapter.fire_before("summarizing", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "summarizing", t), "wi_state": "failed"}
        try:
            # M2: 优先使用 complete_work 已构造的 StructuredResult
            # agent loop 完成 work 后由 complete_work 控制工具构造（权威成果）；
            # 若缺失（异常路径），回退到 _extract_structured_result 从 step_results 提取
            if wi.structured_result is None:
                wi.structured_result = _extract_structured_result(wi, ctx)
                logger.info("summarizing: structured_result from fallback extraction, status=%s",
                            wi.structured_result.status if wi.structured_result else 'None')
            else:
                logger.info("summarizing: structured_result from complete_work, status=%s",
                            wi.structured_result.status)
            # result_text 取 agent loop 最终回复（baggage），兜底用 step_results output
            wi.result_text = ctx.get("agent_final_reply", "") or (
                wi.step_results[-1].output if wi.step_results else ""
            )
            ctx.verified_reply = ""
            # prompt_info 供 ArchiveHook 使用
            sr = wi.structured_result
            ctx.set("prompt_info_summarizing", {
                "status": getattr(sr, "status", "?"),
                "business_object_no": getattr(sr, "business_object_no", "") or "",
                "issues_count": len(getattr(sr, "issues", []) or []),
                "llm_call_count": getattr(wi, "llm_call_count", 0),
            })
        except Exception as e:
            logger.error("summarizing failed: %s", e, exc_info=True)
            wi.add_warning(f"summarizing 失败: {e}")
            await hook_adapter.fire_error("summarizing", ctx, e)
        await hook_adapter.fire_after("summarizing", ctx)
        return {**_exit_stage(state, "summarizing", t), "wi_state": "done"}
    summarizing.__name__ = "summarizing"
    return summarizing


def make_error_analysis(hook_adapter, *, llm_client, config):
    """error_analysis 节点：iteration cap / LLM 异常兜底。复用 ErrorAnalyzer。"""
    from .error_analysis import ErrorAnalyzer
    analyzer = ErrorAnalyzer(llm_client=llm_client, config=config)

    async def error_analysis(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "error_analysis")
        if not await hook_adapter.fire_before("error_analysis", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "error_analysis", t), "wi_state": "failed"}
        try:
            result = await analyzer.analyze(ctx)
        except Exception as e:
            logger.error("error_analysis crashed: %s", e, exc_info=True)
            result = {"error_type": "transient_failure", "should_abort": False,
                      "root_cause": f"分析器异常: {e}"}
        try:
            # iteration cap 触发的 should_escalate → 默认 abort
            if state.get("error_analysis", {}).get("should_escalate"):
                result = {**result, "should_abort": True,
                          "root_cause": result.get("root_cause", "iteration cap")}
            if result.get("should_abort"):
                ctx.should_abort = True
                ctx.abort_reason = result.get("root_cause", "error_analysis abort")
                user_prompt = result.get("user_prompt", "")
                if user_prompt and ctx.work_item is not None:
                    ctx.work_item.add_warning(f"需追问用户: {user_prompt}")
            state["error_analysis"] = result
            # prompt_info 供 ArchiveHook 使用
            ctx.set("prompt_info_error_analysis", {
                "error_type": result.get("error_type", "unknown"),
                "root_cause": (result.get("root_cause", "") or "")[:200],
                "should_abort": result.get("should_abort", False),
            })
        except Exception as e:
            logger.error("error_analysis post-processing failed: %s", e, exc_info=True)
            await hook_adapter.fire_error("error_analysis", ctx, e)
        await hook_adapter.fire_after("error_analysis", ctx)
        wi_state = "failed" if result.get("should_abort") else "executing"
        return {**_exit_stage(state, "error_analysis", t), "wi_state": wi_state,
                "iteration_count": 0}
    error_analysis.__name__ = "error_analysis"
    return error_analysis
