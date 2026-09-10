"""SessionOrchestrator —— 会话编排策略组件（Session 层）。

与 FocusLock / ConfirmQueue / SessionScheduler 同级，由 SessionAgent 持有：
    self.orchestrator = SessionOrchestrator(...)

职责边界（只做规划）：
  - plan()        ：路由 intent + 上下文 + 操作者 → 带依赖的 WI 计划（DAG）
  - on_wi_done()  ：WI 完成后回调，产出动态追加计划（M9）
  不做执行 / 合成 / 归档。

规划策略（混合模式）：
  1) 顺序连接词命中（"查X然后给Y发提醒"）→ 调 LLM 做目标分解 → 依赖 DAG
  2) is_compound 且无顺序语义 → 并行 WI（零 LLM，与现状一致）
  3) 其余（简单 / fallback / SYS-confirm 已在调用方分流）→ 单 WI（零 LLM）
  LLM 不可用 / 解析失败 → 回退 flat 拆分（fail-open，与现状一致）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .session_context import SessionContext
    from ..workitem import WorkItem

logger = logging.getLogger("emily.session.orchestrator")


@dataclass
class WorkItemPlan:
    """编排器产出的单个 WI 计划项。"""

    wi: "WorkItem"
    depends_on: list[str] = field(default_factory=list)
    dynamic: bool = False
    depth: int = 0


# 顺序语义连接词（规则判定：命中则不并行，交 LLM 做目标分解）
_ORDER_CONNECTORS = (
    "然后", "再", "之后", "接着", "随后", "并把", "并将", "并给", "再给", "并且给",
)

_PLAN_SYSTEM_PROMPT = (
    "你是任务编排器。把用户的复合请求拆成有先后依赖的执行步骤。"
    "只输出 JSON：{\"steps\":[{\"objective\":\"简短目标\",\"user_input\":\"该步骤的用户请求原文\","
    "\"sop_id\":\"可选，SOP 编号或 null\",\"needs_previous\":true}]}。"
    "steps 按执行顺序排列；needs_previous=true 表示需要上一步结果作为输入。"
)


class SessionOrchestrator:
    """会话编排策略组件。"""

    def __init__(
        self,
        llm_client: Any = None,
        skill_registry: Any = None,
        config: Any = None,
        wi_factory: Callable[[dict, dict | None], "WorkItem"] | None = None,
    ) -> None:
        self._llm = llm_client
        self._skill_registry = skill_registry
        self._config = config
        self._wi_factory = wi_factory
        self._dynamic_emitted = 0

    # ── 外部注入 ──

    def attach_core(self, core) -> None:
        """注入 EmilyCore（取 config；LLM 未注入时一并取）。"""
        self._config = getattr(core, "config", None) if core is not None else None
        if self._llm is None and core is not None:
            self._llm = getattr(core, "_llm_client", None)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "orchestrator_enabled", True))

    @property
    def max_depth(self) -> int:
        return int(getattr(self._config, "orchestrator_max_depth", 3) or 3)

    @property
    def max_dynamic(self) -> int:
        return int(getattr(self._config, "orchestrator_max_dynamic_wis", 2) or 2)

    # ── 主入口：规划 ──

    async def plan(
        self,
        intent: dict,
        context: "SessionContext | None" = None,
        actor: dict | None = None,
        *,
        fallback_tier: str = "basic",
    ) -> list[WorkItemPlan]:
        """产出初始 WI 计划（混合策略）。"""
        self._dynamic_emitted = 0
        is_compound = bool(intent.get("is_compound"))
        sub_tasks = [st for st in (intent.get("sub_tasks") or []) if isinstance(st, dict)]

        # 1) 顺序语义（复合或单 SOP）→ 尝试依赖分解
        if self._is_ordered(intent) and (is_compound or intent.get("sop_id")):
            plans = await self._plan_dependency(intent, context, actor, sub_tasks)
            if plans:
                return plans
            logger.info("orchestrator: dependency planning unavailable, fallback to flat")

        # 2) 并行复合（零 LLM，与现状一致）
        if is_compound and sub_tasks:
            plans = [WorkItemPlan(wi=self._build(intent, st), depth=0) for st in sub_tasks[:5]]
            plans = [p for p in plans if p.wi is not None]
            if plans:
                return plans

        # 3) 单 WI（零 LLM）
        wi = self._build(intent, None)
        if wi is None:
            return []
        return [WorkItemPlan(wi=wi, depth=0)]

    @staticmethod
    def _is_ordered(intent: dict) -> bool:
        """顺序语义判定：显式标记或用户原文含顺序连接词。"""
        if intent.get("dependency") is True:
            return True
        text = str(intent.get("user_input", "") or "") + str(intent.get("reasoning", "") or "")
        return any(c in text for c in _ORDER_CONNECTORS)

    async def _plan_dependency(
        self,
        intent: dict,
        context: "SessionContext | None",
        actor: dict | None,
        sub_tasks: list[dict],
    ) -> list[WorkItemPlan]:
        """调 LLM 做目标分解 → 依赖 DAG。失败返回 []（调用方回退 flat）。"""
        if self._llm is None:
            return []
        try:
            messages = self._build_plan_messages(intent, context, sub_tasks)
            result = await self._llm.chat_messages(messages, json_mode=True)
            steps = (result.get("data") or {}).get("steps") or []
            if not isinstance(steps, list) or len(steps) < 2:
                return []

            plans: list[WorkItemPlan] = []
            prev_id: str | None = None
            for st in steps[:5]:
                if not isinstance(st, dict):
                    continue
                wi = self._build(intent, st)
                if wi is None:
                    continue
                deps = [prev_id] if (prev_id and st.get("needs_previous", True)) else []
                plans.append(WorkItemPlan(wi=wi, depends_on=deps, depth=0))
                prev_id = wi.id
            return plans if len(plans) >= 2 else []
        except Exception as e:
            logger.warning("orchestrator dependency planning failed: %s", e)
            return []

    def _build_plan_messages(
        self, intent: dict, context: "SessionContext | None", sub_tasks: list[dict]
    ) -> list[dict]:
        catalog = ""
        if self._skill_registry is not None:
            try:
                catalog = self._skill_registry.dump_as_text()
            except Exception as e:
                logger.debug("orchestrator: sop catalog dump failed: %s", e)
        user_req = str(intent.get("user_input", "") or "")
        subs = "；".join(str(st.get("user_input", "")) for st in sub_tasks) or "（无）"
        return [
            {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"## 用户请求\n{user_req}\n\n"
                f"## 路由已识别的子任务\n{subs}\n\n"
                f"## 可用 SOP 目录（只能从中选 sop_id，或填 null）\n{catalog[:2000]}"
            )},
        ]

    def _build(self, intent: dict, subtask: dict | None) -> "WorkItem | None":
        """经 wi_factory 物化 WI（工厂由 SessionAgent 注入）。"""
        if self._wi_factory is None:
            logger.error("orchestrator: wi_factory 未注入，无法物化 WI")
            return None
        try:
            return self._wi_factory(intent, subtask)
        except Exception as e:
            logger.error("orchestrator: wi_factory failed: %s", e, exc_info=True)
            return None

    # ── M9：动态追加（跨域检索编排）──

    async def on_wi_done(
        self, wi: "WorkItem", done: list["WorkItem"], *, depth: int = 0
    ) -> list[WorkItemPlan]:
        """WI 完成后回调：信息不足时追加 READ 类检索 WI。"""
        if getattr(wi, "dynamic", False):
            return []                                  # 动态 WI 不再递归追加
        if wi.state.value != "DONE":
            return []                                  # 仅成功结果触发
        if self._dynamic_emitted >= self.max_dynamic:
            logger.info("orchestrator: dynamic WI cap %d reached", self.max_dynamic)
            return []
        if depth + 1 > self.max_depth:
            logger.info("orchestrator: append depth %d exceeds max %d", depth + 1, self.max_depth)
            return []

        spec = self._detect_gap(wi)
        if spec is None:
            return []
        objective, user_input, must_include = spec

        new_wi = self._build_retrieval_wi(objective, user_input, must_include)
        if new_wi is None:
            return []
        new_wi.dynamic = True
        new_wi.depth = depth + 1
        self._dynamic_emitted += 1
        logger.info("orchestrator: append retrieval WI %s after %s (depth=%d)",
                    new_wi.id, wi.id, new_wi.depth)
        return [WorkItemPlan(wi=new_wi, dynamic=True, depth=new_wi.depth)]

    @staticmethod
    def _detect_gap(wi: "WorkItem") -> tuple[str, str, list[str]] | None:
        """规则化信息缺口判定（不调 LLM）。

        Returns:
            (objective, user_input, must_include) 或 None（无缺口）
        """
        sr = getattr(wi, "structured_result", None)
        if sr is None:
            return None
        intent_name = str(getattr(sr, "intent", "") or "")
        is_empty = bool(getattr(sr, "is_empty", False))
        data_fields = getattr(sr, "data_fields", None) or {}
        user_input = (getattr(wi, "user_input", "") or "")[:60]

        # 缺口 1：查询结果为空且非知识检索类 → 补知识库/规则依据
        if is_empty and "knowledge" not in intent_name:
            return ("knowledge_lookup",
                    f"补充与「{user_input}」相关的规范、制度或项目背景信息",
                    ["依据", "来源"])
        # 缺口 2：项目态势类查询但缺节点进度 → 补项目态势全文
        if intent_name in ("query_project_summary", "query_node", "summary") \
                and not data_fields.get("node_progress"):
            return ("meta_cognition",
                    f"检索项目态势全文，补充「{user_input}」涉及的节点与进度",
                    ["节点", "进度"])
        return None

    def _build_retrieval_wi(
        self, objective: str, user_input: str, must_include: list[str]
    ) -> "WorkItem | None":
        """构造只读检索 WI（fallback 类，工具集由 FallbackPolicy 只读白名单裁剪）。"""
        pseudo = {
            "sop_id": None,
            "fallback": True,
            "confidence": "high",
            "output_spec": {
                "intent": objective, "detail": "standard",
                "format": "natural", "cite_source": True,
            },
            "result_constraints": {"must_include": list(must_include)},
        }
        wi = self._build(pseudo, None)
        if wi is None:
            return None
        wi.user_input = user_input
        return wi
