"""能力调用计划与粗排模块（M4）—— 任务级粗排 + 步进式执行游标。

定位（计划 M4 / PRD D5、D7、R4、R5）：
  - 会话层负责任务级**粗排**（调哪些能力、依赖顺序）；能力内部负责任务级**细排**；
  - 任务包是**执行依据**，不是交付物：本模块只提供计划结构与游标，
    实际调用由会话循环（M1）逐项发起、成果逐步回灌、按结果随时重排；
  - **不提供**"整体接收计划并自行执行"的执行器 —— 那等于把执行权移交出去（派发范式复辟）。

本模块与旧工单计划形态（`workitem.orchestrator.WorkItemPlan`）**完全独立**
（PRD §4.4-7：混用会让关系换位半途而废）。
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("emily.session.capability_plan")

# 单次粗排最大步数（与旧编排器上限一致）
MAX_PLAN_STEPS = 5
# 缺省最大追加深度
DEFAULT_MAX_DEPTH = 3

STEP_PENDING = "pending"
STEP_DONE = "done"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
_TERMINAL_STEPS = (STEP_DONE, STEP_FAILED, STEP_SKIPPED)


@dataclass
class CapabilityStep:
    """单个能力调用步。"""

    step_id: str
    capability: str                              # 能力名（必须属于当前可见能力集）
    params: dict = field(default_factory=dict)   # 入参（如 {"request": "..."}）
    depends_on: list = field(default_factory=list)
    objective: str = ""                          # 该步目标（用于进度展示）
    status: str = STEP_PENDING
    result: object = None                        # CapabilityResult | None
    depth: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STEPS


@dataclass
class CapabilityPlan:
    """能力调用计划（会话内内存对象，不落库）。"""

    plan_id: str = ""
    steps: list = field(default_factory=list)
    source: str = "llm"                          # llm | rule
    max_depth: int = DEFAULT_MAX_DEPTH

    def __post_init__(self) -> None:
        if not self.plan_id:
            self.plan_id = f"CP-{uuid.uuid4().hex[:8]}"


class PlanCursor:
    """步进式执行游标 —— 执行权留在调用方（M1 会话循环）。

    语义对照 `SessionScheduler.run_dag`（workitem/scheduler.py:129-208）：
      层内并行 / 层间顺序 / 前置失败下游跳过 / 依赖不可满足防死循环 / 动态追加深度上限。
    """

    def __init__(self, plan: CapabilityPlan, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        self.plan = plan
        self.max_depth = max_depth if max_depth is not None else DEFAULT_MAX_DEPTH

    # ── 查询 ──

    def _by_id(self, step_id: str) -> CapabilityStep | None:
        for s in self.plan.steps:
            if s.step_id == step_id:
                return s
        return None

    def next_runnable(self) -> list:
        """返回当前可执行层（所有前置已 done）。

        依赖不可满足（无 pending 可跑但仍有 pending 存在）→ 剩余全部置 skipped，
        返回空列表（防死循环）。
        """
        pending = [s for s in self.plan.steps if s.status == STEP_PENDING]
        if not pending:
            return []
        layer = [
            s for s in pending
            if all((self._by_id(d) is not None and self._by_id(d).status == STEP_DONE)
                   for d in (s.depends_on or []))
        ]
        if layer:
            return layer
        # 无可执行层但仍有人 pending → 依赖永不可满足（环 / 依赖已 failed/skipped）
        logger.warning("PlanCursor: unresolvable deps, skip %d step(s)", len(pending))
        for s in pending:
            s.status = STEP_SKIPPED
        return []

    @property
    def pending_count(self) -> int:
        return sum(1 for s in self.plan.steps if s.status == STEP_PENDING)

    @property
    def is_complete(self) -> bool:
        return self.pending_count == 0

    # ── 结算 ──

    def mark_done(self, step_id: str, result=None) -> None:
        s = self._by_id(step_id)
        if s is None:
            return
        s.status = STEP_DONE
        s.result = result

    def mark_failed(self, step_id: str, result=None) -> None:
        s = self._by_id(step_id)
        if s is None:
            return
        s.status = STEP_FAILED
        s.result = result
        self._cascade_skip()

    def _cascade_skip(self) -> None:
        """把依赖了 failed/skipped 步的 pending 步级联置 skipped（传递闭包）。"""
        changed = True
        while changed:
            changed = False
            for s in self.plan.steps:
                if s.status != STEP_PENDING:
                    continue
                for d in (s.depends_on or []):
                    dep = self._by_id(d)
                    if dep is not None and dep.status in (STEP_FAILED, STEP_SKIPPED):
                        s.status = STEP_SKIPPED
                        s.result = None
                        changed = True
                        break

    # ── 动态追加（AC-US-05.2）──

    def append(self, step: CapabilityStep, *, depth: int) -> bool:
        """追加一步；超过 max_depth 返回 False（丢弃）。"""
        if depth > self.max_depth:
            logger.info("PlanCursor: drop appended step (depth=%d > max=%d)", depth, self.max_depth)
            return False
        step.depth = depth
        self.plan.steps.append(step)
        return True

    # ── 进度（AC-US-05.3）──

    def progress_text(self) -> str:
        """简版进度文本（对话中展示"要做什么、做到哪"）。"""
        if not self.plan.steps:
            return ""
        total = len(self.plan.steps)
        done = sum(1 for s in self.plan.steps if s.status == STEP_DONE)
        failed = sum(1 for s in self.plan.steps if s.status == STEP_FAILED)
        skipped = sum(1 for s in self.plan.steps if s.status == STEP_SKIPPED)
        icons = {STEP_PENDING: "○", STEP_DONE: "✓", STEP_FAILED: "✗", STEP_SKIPPED: "−"}
        lines = [f"执行计划（{done}/{total} 已完成{'，' + str(failed) + ' 失败' if failed else ''}"
                 f"{'，' + str(skipped) + ' 跳过' if skipped else ''}）："]
        for i, s in enumerate(self.plan.steps, 1):
            label = s.objective or s.capability
            lines.append(f"  {icons.get(s.status, '○')} {i}. {label}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 粗排规划器
# ══════════════════════════════════════════════════════════════════════════════

_PLAN_SYSTEM_PROMPT = (
    "你是任务粗排器。把用户的复合请求拆成若干**能力调用步骤**，只输出 JSON："
    "{\"steps\":[{\"objective\":\"简短目标\",\"capability\":\"能力名\",\"request\":\"该步骤要交给能力的用户请求原文\","
    "\"needs_previous\":true}]}。"
    "steps 按执行顺序排列；needs_previous=true 表示需要上一步的成果作为输入（依赖上一步）。"
    "capability 只能从下方「可用能力」列表中选择，禁止编造。"
    f"最多 {MAX_PLAN_STEPS} 步；若无需多步协作，输出 {{\"steps\":[]}}。"
)


class SessionPlanner:
    """会话层任务级粗排（LLM 单次结构化输出）。"""

    def __init__(self, llm_client=None, config=None) -> None:
        self._llm = llm_client
        self._config = config

    async def plan(
        self,
        user_input: str,
        capability_names: set,
        context_hint: str = "",
    ) -> CapabilityPlan | None:
        """产出能力调用计划；无需多能力协作时返回 None。

        Returns:
            CapabilityPlan | None
        """
        if self._llm is None or not capability_names:
            return None
        try:
            result = await self._llm.chat_messages(
                self._build_messages(user_input, capability_names, context_hint),
                json_mode=True,
            )
        except Exception as e:  # noqa: BLE001 — 规划失败回退单能力路径
            logger.warning("SessionPlanner: LLM planning failed: %s", e)
            return None

        data = (result or {}).get("data") or {}
        steps_raw = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(steps_raw, list) or len(steps_raw) < 2:
            return None

        plan = CapabilityPlan(source="llm")
        prev_id = None
        for i, raw in enumerate(steps_raw[:MAX_PLAN_STEPS], 1):
            if not isinstance(raw, dict):
                continue
            cap = str(raw.get("capability") or "").strip()
            if cap not in capability_names:
                logger.info("SessionPlanner: drop unknown capability %r", cap)
                continue
            step = CapabilityStep(
                step_id=f"s{i}",
                capability=cap,
                params={"request": str(raw.get("request") or user_input)},
                objective=str(raw.get("objective") or ""),
                depends_on=[prev_id] if (prev_id and raw.get("needs_previous", True)) else [],
            )
            plan.steps.append(step)
            prev_id = step.step_id

        if len(plan.steps) < 2:
            return None
        logger.info("SessionPlanner: plan %s with %d steps", plan.plan_id, len(plan.steps))
        return plan

    def _build_messages(self, user_input: str, capability_names: set,
                        context_hint: str) -> list:
        caps = "\n".join(f"- {n}" for n in sorted(capability_names))
        hint = f"\n\n## 会话上下文\n{context_hint}" if context_hint else ""
        return [
            {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"## 用户请求\n{user_input}\n\n## 可用能力\n{caps}{hint}"
            )},
        ]
