"""WorkItem —— 任务执行单元的全息档案 + 状态机（蓝图 §5）。

WorkItem 携带任务的完整档案——所属 Session、路由决策、执行计划、
逐步结果、守护意见、最终成果。公共 Pipeline BUS 的 4 个节点都从
WorkItem 上取数据、写产出，不跨节点隐式传参。

一条消息可拆分为 0..N 个 WorkItem（短路指令 0 个，复合任务 N 个）。
消息级处理（接管决策、意图识别、WorkItem 拆分/排队）在 Session 层完成。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .workitem_state import WorkItemState, TRANSITIONS, TERMINAL_STATES


@dataclass
class WorkItem:
    """任务执行单元 —— 全息档案 + 状态机。"""

    # ── 标识 ──
    id: str = field(default_factory=lambda: f"WI-{uuid.uuid4().hex[:8]}")
    session_id: str = ""                 # 所属 Session（conversation_id）
    state: WorkItemState = WorkItemState.CREATED

    # ── 任务输入（创建时写入）──
    user_input: str = ""                 # 任务对应的用户输入片段
    sop_id: str = ""                     # 匹配到的 SOP（如 SOP-002-REC）
    intent_type: str = ""                # Phase B: "sop"|"compound"|"fallback"|"fast_reply"
    user_id: str = ""
    is_admin: bool = False
    priority: int = 1                    # 1=普通 0=最高
    required_permissions: list[str] = field(default_factory=list)
    required_tools: set[str] = field(default_factory=set)     # Phase B: KnowledgeInjector
    required_tables: set[str] = field(default_factory=set)    # Phase B: KnowledgeInjector

    # ── Node 1（意图+拆分）产出 ──
    route_decision: Any = None           # RouteDecision | None

    # ── Node 0（Session 下发）产出 ──
    output_spec: dict = field(default_factory=dict)
    """M1: Session 下发的成果规格：intent/detail/format/cite_source/max_length/data_fields"""

    # ── M2: Session 下发的执行约束（scope/filters/must_include/must_not）──
    result_constraints: dict = field(default_factory=dict)
    """SessionAgent 从用户表达中提取的结果约束，供 node2 规划和 node4 验证使用。
    
    Structure:
        scope: dict       — {"project": "...", "responsible_user": "...", "time_range": "..."}
        filters: list[str] — ["exclude_completed", "only_pending"]
        must_include: list[str] — ["节点名称", "截止日期"]
        must_not: list[str] — ["不要已完成节点"]
    """

    # ── M0: Session 下发的工作要求（任务化指令）──
    work_spec: dict = field(default_factory=dict)
    """SessionAgent 组装的结构化工作要求，agent loop 据此执行（非用户原文）。

    Structure:
        objective: str       — 任务目标（如 "record_event"）
        sop_id: str          — 匹配的 SOP
        user_request: str    — 用户原始请求（上下文附注，非主指令）
        output_spec: dict    — 成果规格（intent/detail/format/data_fields）
        constraints: dict    — 成果约束（scope/must_include/must_not）
        required_tools: set  — 建议工具集
    """

    # ── 多轮续接 ──
    question: str = ""
    """Emily 上一轮问用户的问题（挂起时写入，供续接判断用）"""

    additional_input: str = ""
    """续接时用户补充的消息内容（SessionAgent 注入，node2 消费）"""

    # ── Node 2（计划+标准）产出 ──
    execution_plan: Any = None           # ExecutionPlan | None
    risk_level: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)

    # ── Node 3（执行+验收）产出 ──
    step_results: list[Any] = field(default_factory=list)  # list[StepResult]

    # ── Node 4（成果总结）产出 ──
    result_text: str = ""                # 兜底用（type=text 异常路径保留），正常路径成果在 structured_result
    structured_result: Any = None        # M2: StructuredResult，回传给 Session 做语言组织

    # ── error_analysis 节点产出 ──
    error_analysis: dict = field(default_factory=dict)
    """error_analysis 节点的分析结果，供归档渲染读取"""

    # ── 专家Agent 字段 ──
    expert_id: str = ""
    """匹配到的专家 UUID（非空时 routing 后走 expert_review）"""

    expert_required: bool = False
    """是否需要专家评审"""

    expert_review_result: dict = field(default_factory=dict)
    """专家评审成果，供 summarizing 构造 StructuredResult"""

    # ── 增量灌注记录（KnowledgeInjector 写入）──
    injected_sops: set[str] = field(default_factory=set)
    injected_tools: set[str] = field(default_factory=set)
    injected_tables: set[str] = field(default_factory=set)

    # ── 元数据 ──
    llm_call_count: int = 0
    warnings: list[str] = field(default_factory=list)
    error_message: str = ""
    pipeline_run_id: str = ""       # Pipeline 执行 run_id，供回查 LLM 日志
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = ""
    completed_at: str = ""

    # ── 状态机方法 ──

    def transition_to(self, new_state: WorkItemState) -> None:
        """执行状态转换（带合法性校验）。

        Raises:
            ValueError: 非法状态转换。
        """
        allowed = TRANSITIONS.get(self.state, [])
        if new_state not in allowed:
            allowed_names = [s.value for s in allowed]
            raise ValueError(
                f"非法 WorkItem 状态转换: {self.state.value} → {new_state.value} "
                f"(允许: {allowed_names})"
            )
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_terminal(self) -> bool:
        """是否为终态（DONE / FAILED）。"""
        return self.state in TERMINAL_STATES

    def add_step_result(self, step_result: Any) -> None:
        """追加一条步骤执行结果。"""
        self.step_results.append(step_result)

    def add_warning(self, msg: str) -> None:
        """追加一条警告。"""
        self.warnings.append(msg)

    def to_summary(self) -> dict:
        """生成摘要（供 Node 4 成果总结使用）。"""
        return {
            "id": self.id,
            "state": self.state.value,
            "sop_id": self.sop_id,
            "user_input": self.user_input[:200],
            "steps_executed": len(self.step_results),
            "rag_hits": sum(
                len(getattr(sr, "rag_results", [])) for sr in self.step_results
            ),
            "tool_calls": sum(
                len(getattr(sr, "tool_calls", [])) for sr in self.step_results
            ),
            "db_operations": sum(
                len(getattr(sr, "db_results", [])) for sr in self.step_results
            ),
            "risk_level": self.risk_level,
            "llm_call_count": self.llm_call_count,
        }
