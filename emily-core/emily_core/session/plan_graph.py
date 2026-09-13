# emily-core/emily_core/session/plan_graph.py
"""M4 计划子图 —— 任务级粗排的执行形态（计划 M4 / PRD US-02）。

定位：
  - 把"按依赖分层推进"的计划执行改为图式表达：层内并行分支、层间顺序、失败级联跳过、深度守卫。
  - 入计划前按 M2 契约校验参数，消除历史"参数丢失导致降级记录"的缺陷（AC-US-02.1）。
  - 执行权留在编排侧：本模块只负责"照单执行并回报结果"，不做重排决策（重排由 M3 循环按结果决定）。

图形态：
    START ──dispatch──▶ run_step × N（层内并行）──▶ plan_join ──dispatch──▶ … ──▶ END
  - dispatch：取当前可执行层；无则结束；有则以 `Send` 并行派发该层全部步骤
  - plan_join：单一写者，依据并行回填的 `step_results` 计算 done/failed/skipped、级联跳过与动态追加

状态合并约定：
  - `step_results` 使用 add 归约器（并行安全）；`done/failed/skipped` 只在 plan_join 单点写入，避免并行冲突。
"""
from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

logger = logging.getLogger("emily.session.plan_graph")

NODE_RUN_STEP = "run_step"
NODE_PLAN_JOIN = "plan_join"

DEFAULT_MAX_DEPTH = 3

#: 不做参数约束校验的能力类别（SOP 能力入参为请求原文，见 M2 契约说明）
_SKIP_SCHEMA_KINDS = frozenset({"sop"})


class PlanState(TypedDict, total=False):
    """计划子图状态（只含基础类型与摘要）。"""

    plan_id: str
    steps: list               # [{step_id, capability, params, depends_on, depth}]
    step_results: Annotated[list, operator.add]   # 并行回填（add 归约）
    done: list
    failed: list
    skipped: list
    depth: int
    max_depth: int
    layer_index: int
    _processed: int           # 已并入终态的 step_results 游标（plan_join 单点维护）
    plan_text: str            # 供对话回灌的成果摘要


# ══════════════════════════════════════════════════════════════════════════════
# 计划校验（入计划前，依据 M2 契约）
# ══════════════════════════════════════════════════════════════════════════════


def _step_id(step: dict, index: int) -> str:
    return str((step or {}).get("step_id") or f"step-{index + 1}")


def validate_steps(steps: list, specs: Any) -> list:
    """按契约校验计划步骤，返回问题清单。

    校验项：能力存在、参数满足 schema 的 required、无多余参数（防丢参/错参）。
    `specs` 为可迭代的 `CapabilitySpec`（M2）或 None（跳过契约校验）。
    """
    problems: list = []
    if specs is None:
        return problems
    index = {getattr(s, "name", ""): s for s in specs}
    for i, step in enumerate(steps or []):
        sid = _step_id(step, i)
        name = str((step or {}).get("capability") or "")
        if not name:
            problems.append(f"{sid}: 缺少 capability")
            continue
        spec = index.get(name)
        if spec is None:
            problems.append(f"{sid}: 能力 {name} 不在当前可见契约内")
            continue
        if getattr(spec, "kind", "") in _SKIP_SCHEMA_KINDS:
            continue
        schema = dict(getattr(spec, "params_schema", None) or {})
        if not schema:
            problems.append(f"{sid}: 能力 {name} 缺少参数 schema")
            continue
        params = dict((step or {}).get("params") or {})
        required = [str(k) for k in (schema.get("required") or [])]
        missing = [k for k in required if k not in params]
        if missing:
            problems.append(f"{sid}: 能力 {name} 缺少必填参数 {missing}")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            extra = [k for k in params if k not in properties]
            if extra:
                problems.append(f"{sid}: 能力 {name} 含未声明参数 {extra}")
    return problems


# ══════════════════════════════════════════════════════════════════════════════
# 分层与级联
# ══════════════════════════════════════════════════════════════════════════════


def _finished(state: dict) -> set:
    return set(state.get("done") or []) | set(state.get("failed") or []) | set(state.get("skipped") or [])


def next_layer(state: dict) -> list:
    """取当前可执行层：依赖已全部完成，且自身未进入终态。"""
    done = set(state.get("done") or [])
    finished = _finished(state)
    layer = []
    for i, step in enumerate(state.get("steps") or []):
        sid = _step_id(step, i)
        if sid in finished:
            continue
        deps = [str(d) for d in ((step or {}).get("depends_on") or [])]
        if all(d in done for d in deps):
            layer.append(dict(step, step_id=sid))
    return layer


def cascade_skip(state: dict) -> list:
    """上游失败 → 下游级联跳过（传递闭包），返回新增跳过项。"""
    failed = set(state.get("failed") or [])
    skipped = set(state.get("skipped") or [])
    steps = list(state.get("steps") or [])
    changed = True
    added: list = []
    while changed:
        changed = False
        for i, step in enumerate(steps):
            sid = _step_id(step, i)
            if sid in failed or sid in skipped:
                continue
            deps = [str(d) for d in ((step or {}).get("depends_on") or [])]
            if any(d in failed or d in skipped for d in deps):
                skipped.add(sid)
                added.append(sid)
                changed = True
    return added


def can_append(state: dict, *, depth: int) -> bool:
    """动态追加的深度守卫：超过 max_depth 不再追加。"""
    return int(depth or 0) <= int(state.get("max_depth") or DEFAULT_MAX_DEPTH)


# ══════════════════════════════════════════════════════════════════════════════
# 子图构建
# ══════════════════════════════════════════════════════════════════════════════


def build_plan_subgraph(
    *,
    capability_executor: Callable[[str, dict], Any],
    specs: Any = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    on_layer_done: "Callable[[dict], list] | None" = None,
):
    """构建计划子图。

    Args:
        capability_executor: `async (capability, params) -> dict`，与 M3 的执行端口同形。
        specs: M2 契约列表（用于入计划校验）；None 表示不校验。
        max_depth: 动态追加的最大深度。
        on_layer_done: `(state) -> list[step]`，每层完成后可动态追加步骤（受深度守卫约束）。
    """
    validation_problems = {"items": []}

    async def _run_step(payload: dict) -> dict:
        step = dict((payload or {}).get("_step") or {})
        sid = str(step.get("step_id") or "")
        name = str(step.get("capability") or "")
        params = dict(step.get("params") or {})
        record = {"step_id": sid, "capability": name, "depth": int(step.get("depth") or 0)}
        try:
            result = await capability_executor(name, params)
        except Exception as e:  # noqa: BLE001 — 失败结构化回传，不抛出子图
            logger.error("plan step %s (%s) raised: %s", sid, name, e, exc_info=True)
            result = {"success": False, "status": "failed", "reply": f"调用异常：{e}"}
        if not isinstance(result, dict):
            result = {"success": False, "status": "failed", "reply": "能力返回了非结构化结果"}
        record["status"] = str(result.get("status") or ("success" if result.get("success") else "failed"))
        record["digest"] = str(result.get("reply") or result.get("summary") or "")[:300]
        record["needs_input"] = bool(result.get("needs_input"))
        return {"step_results": [record]}

    def _dispatch(state: dict):
        layer = next_layer(state)
        if not layer:
            return END
        return [Send(NODE_RUN_STEP, {"_step": step}) for step in layer]

    async def _plan_join(state: dict) -> dict:
        results = list(state.get("step_results") or [])
        processed = int(state.get("_processed") or 0)
        layer_results = results[processed:]
        done = list(state.get("done") or [])
        failed = list(state.get("failed") or [])
        for r in layer_results:
            sid = str(r.get("step_id") or "")
            if r.get("needs_input"):
                failed.append(sid)
            elif str(r.get("status")) == "failed":
                failed.append(sid)
            else:
                done.append(sid)

        merged = dict(state, done=done, failed=failed, step_results=results)
        added_skips = cascade_skip(merged)
        skipped = list(state.get("skipped") or []) + added_skips

        steps = list(state.get("steps") or [])
        depth = int(state.get("depth") or 0)
        if on_layer_done is not None:
            try:
                extra = on_layer_done(dict(state, done=done, failed=failed, skipped=skipped)) or []
            except Exception as e:  # noqa: BLE001
                logger.warning("on_layer_done failed: %s", e)
                extra = []
            for step in extra:
                step = dict(step or {})
                step_depth = int(step.get("depth") or (depth + 1))
                if not can_append(dict(state), depth=step_depth):
                    logger.info("plan: drop appended step %s depth=%s > max=%s",
                                step.get("step_id"), step_depth, state.get("max_depth"))
                    continue
                steps.append(dict(step, depth=step_depth))

        done_set = set(done)
        text_lines = []
        for step in steps:
            sid = _step_id(step, len(text_lines))
            if sid in done_set:
                text_lines.append(f"[完成] {sid} {step.get('capability')}")
            elif sid in failed:
                text_lines.append(f"[失败] {sid} {step.get('capability')}")
            else:
                text_lines.append(f"[跳过] {sid} {step.get('capability')}")
        text = "\n".join(text_lines)

        return {
            "done": done, "failed": failed, "skipped": skipped,
            "steps": steps, "layer_index": int(state.get("layer_index") or 0) + 1,
            "_processed": len(results),
            "plan_text": text,
        }

    gs = StateGraph(PlanState)
    gs.add_node(NODE_RUN_STEP, _run_step)
    gs.add_node(NODE_PLAN_JOIN, _plan_join)
    gs.add_conditional_edges(START, _dispatch, [NODE_RUN_STEP, END])
    gs.add_edge(NODE_RUN_STEP, NODE_PLAN_JOIN)
    gs.add_conditional_edges(NODE_PLAN_JOIN, _dispatch, [NODE_RUN_STEP, END])
    graph = gs.compile()
    logger.info("plan subgraph built: max_depth=%s, specs=%s",
                max_depth, len(specs or []) if specs is not None else 0)
    return graph


class PlanRunner:
    """计划子图运行器：校验入计划、构造初始状态、执行、回传结果摘要。"""

    def __init__(self, graph, *, specs: Any = None, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        self._graph = graph
        self._specs = specs
        self._max_depth = int(max_depth or DEFAULT_MAX_DEPTH)

    async def run(self, steps: list, *, plan_id: str = "", recursion_limit: int = 200) -> dict:
        problems = validate_steps(steps, self._specs)
        state: dict = {
            "plan_id": plan_id,
            "steps": [dict(s) for s in (steps or [])],
            "step_results": [],
            "done": [], "failed": [], "skipped": [],
            "depth": 0, "max_depth": self._max_depth, "layer_index": 0,
            "_processed": 0, "plan_text": "",
        }
        if problems:
            logger.warning("plan validation problems: %s", problems)
            # 校验不通过的步骤直接标记失败，交由级联跳过处理（不执行）
            bad_ids = {p.split(":", 1)[0] for p in problems}
            state["failed"] = [sid for sid in
                               (_step_id(s, i) for i, s in enumerate(state["steps"])) if sid in bad_ids]
        final = await self._graph.ainvoke(state, {"recursion_limit": int(recursion_limit)})
        return {
            "plan_id": plan_id,
            "done": list((final or {}).get("done") or []),
            "failed": list((final or {}).get("failed") or []),
            "skipped": list((final or {}).get("skipped") or []),
            "step_results": list((final or {}).get("step_results") or []),
            "plan_text": str((final or {}).get("plan_text") or ""),
            "validation_problems": problems,
        }
