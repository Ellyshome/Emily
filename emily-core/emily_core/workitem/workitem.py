"""WorkItem —— 任务执行单元的全息档案 + 状态机（蓝图 §5）。

借鉴旧 WorkOrder 的"全息数据"思想：WorkItem 携带任务的完整档案——
所属 Session、路由决策、执行计划、逐步结果、守护意见、最终成果。
公共 Pipeline BUS 的 4 个节点都从 WorkItem 上取数据、写产出，不跨节点隐式传参。

与旧 WorkOrder 的区别：
- WorkOrder 是"单条消息"的流转单（8 阶段，含 intake/route/auth 等消息级阶段）。
- WorkItem 是"单个任务"的执行单元（4 节点，消息级处理已上移到 Session 层）。
  一条消息可拆分为 0..N 个 WorkItem（短路指令 0 个，复合任务 N 个）。
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
    user_id: str = ""
    is_admin: bool = False
    priority: int = 1                    # 1=普通 0=最高
    required_permissions: list[str] = field(default_factory=list)

    # ── Node 1（意图+拆分）产出 ──
    route_decision: Any = None           # RouteDecision | None

    # ── Node 2（计划+标准）产出 ──
    execution_plan: Any = None           # ExecutionPlan | None
    risk_level: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)

    # ── Node 3（执行+验收）产出 ──
    step_results: list[Any] = field(default_factory=list)  # list[StepResult]

    # ── Node 4（成果总结）产出 ──
    result_text: str = ""                # 人类可读的最终成果

    # ── 增量灌注记录（KnowledgeInjector 写入）──
    injected_sops: set[str] = field(default_factory=set)
    injected_tools: set[str] = field(default_factory=set)
    injected_tables: set[str] = field(default_factory=set)

    # ── 元数据 ──
    llm_call_count: int = 0
    warnings: list[str] = field(default_factory=list)
    error_message: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = ""
    completed_at: str = ""

    # ── 状态机方法 ──

    @property
    def message_content(self) -> str:
        """兼容别名 —— 迁移自 M15 的 Mock 组件读取 work_order.message_content。

        WorkItem 用 user_input 表达任务输入；此别名让复用的 MockRouter 等无需改动。
        """
        return self.user_input

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
