"""WorkOrder（流转单）—— 全息数据 + 状态机。

流转单是单次请求的完整档案——携带原始消息、每个阶段的产出、
工具调用链、RAG 检索结果、守护审核意见。
任何后续阶段需要的信息都从流转单上取，不跨阶段隐式传参。

状态机定义了"哪些转换合法"，调度器负责"在合法的岔路口做决策并执行转换"。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .interfaces.routing import RouteDecision, SubTask
    from .interfaces.auth import AuthResult
    from .interfaces.planning import ExecutionPlan, PlanStep
    from .interfaces.execution import (
        ToolCallRecord,
        RagChunk,
        RagResult,
        DbResult,
        GuardianStepVerdict,
        StepResult,
    )


# ════════════════════════════════════════════════════════════════════════════════
# 状态机
# ════════════════════════════════════════════════════════════════════════════════

class WorkOrderStatus(Enum):
    """流转单状态枚举。

    共 11 个状态：8 个过程态 + 3 个终态前过渡态。
    唯一终态：ARCHIVED。
    """
    CREATED = "CREATED"              # 流转单已创建（Step 1 完成）
    ROUTED = "ROUTED"                # 意图已识别（Step 2 完成）
    AUTHED = "AUTHED"                # 鉴权已通过（Step 3 完成）
    PLANNED = "PLANNED"              # 执行计划已制定（Step 4 完成）
    RUNNING = "RUNNING"              # 执行中（Step 5 进行中）
    COMPOSED = "COMPOSED"            # 回复草稿已生成（Step 6 完成）
    VERIFIED = "VERIFIED"            # 出站审核已通过（Step 7 完成）
    ARCHIVED = "ARCHIVED"            # 已归档（Step 8 完成）— 唯一终态
    DONE_FASTREPLY = "DONE_FASTREPLY"  # 快速通道完结（Step 2 短路）
    DONE_DENIED = "DONE_DENIED"       # 鉴权拒绝完结（Step 3 拒绝）
    DONE_ERROR = "DONE_ERROR"         # 异常完结（任意阶段不可恢复错误）


# 合法状态转换表
TRANSITIONS: dict[WorkOrderStatus, list[WorkOrderStatus]] = {
    WorkOrderStatus.CREATED:        [WorkOrderStatus.ROUTED, WorkOrderStatus.DONE_FASTREPLY],
    WorkOrderStatus.ROUTED:         [WorkOrderStatus.AUTHED, WorkOrderStatus.DONE_FASTREPLY],
    WorkOrderStatus.AUTHED:         [WorkOrderStatus.PLANNED, WorkOrderStatus.DONE_DENIED],
    WorkOrderStatus.PLANNED:        [WorkOrderStatus.RUNNING],
    WorkOrderStatus.RUNNING:        [WorkOrderStatus.COMPOSED, WorkOrderStatus.DONE_ERROR],
    WorkOrderStatus.COMPOSED:       [WorkOrderStatus.VERIFIED],
    WorkOrderStatus.VERIFIED:       [WorkOrderStatus.ARCHIVED],
    WorkOrderStatus.ARCHIVED:       [],  # 终态，不可再转换
    WorkOrderStatus.DONE_FASTREPLY: [WorkOrderStatus.COMPOSED],
    WorkOrderStatus.DONE_DENIED:    [WorkOrderStatus.COMPOSED],
    WorkOrderStatus.DONE_ERROR:     [WorkOrderStatus.ARCHIVED],
}

# 终态集合
TERMINAL_STATUSES = frozenset({WorkOrderStatus.ARCHIVED})


# ════════════════════════════════════════════════════════════════════════════════
# 流转单数据结构
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class WorkOrder:
    """流转单 — 单次请求的完整档案 + 状态机。

    携带原始消息、每个阶段的产出、工具调用链、RAG 检索结果、守护审核意见。
    任何后续阶段需要的信息都从流转单上取，不跨阶段隐式传参。
    """

    # ── 标识 ──
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pipeline_run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: WorkOrderStatus = WorkOrderStatus.CREATED

    # ══ 入站信息（intake 阶段写入） ══
    raw_message: Any = None            # StandardMessage 对象
    message_content: str = ""
    message_id: str = ""               # AstrBot event message_id
    conversation_id: str = ""
    platform: str = ""                 # "napcat" / "lagrange"
    sender_name: str = ""
    sender_id: str = ""
    attachments: list[dict] = field(default_factory=list)
    db_message_id: str = ""            # 入库后的 DB 消息 UUID
    downloaded_files: list[dict] = field(default_factory=list)
    user_id: str = ""                  # 绑定的用户 UUID
    user: Any = None                   # User ORM 对象
    is_admin: bool = False
    is_new_user: bool = False

    # ══ 待确认项（intake 阶段检查） ══
    is_confirmation: bool = False
    confirmation_action: str = ""      # "confirm" | "cancel" | ""
    confirmation_result: dict = field(default_factory=dict)

    # ══ ② route 产出 ══
    route_decision: Any = None         # RouteDecision | None
    catalog_version: str = ""
    fast_reply_text: str = ""

    # ══ ③ auth 产出 ══
    auth_result: Any = None            # AuthResult | None
    required_permissions: list[str] = field(default_factory=list)

    # ══ ④ plan 产出 ══
    execution_plan: Any = None         # ExecutionPlan | None
    risk_level: str = ""

    # ══ ⑤ execute 产出 ══
    step_results: list[Any] = field(default_factory=list)  # list[StepResult]

    # ══ ⑥ compose 产出 ══
    draft_reply: str = ""

    # ══ ⑦ verify 产出 ══
    verified_reply: str = ""
    verify_warnings: list[str] = field(default_factory=list)

    # ══ ⑧ archive 产出 ══
    outbound_message_id: str = ""
    progress_sent: str = ""
    archived_at: str = ""

    # ══ 元数据 ══
    is_fast_reply: bool = False
    llm_call_count: int = 0
    total_elapsed_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    error_message: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = ""
    completed_at: str = ""

    # ── 状态机方法 ──

    def transition_to(self, new_status: WorkOrderStatus) -> None:
        """执行状态转换（带合法性校验）。

        Args:
            new_status: 目标状态

        Raises:
            ValueError: 非法状态转换
        """
        allowed = TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            allowed_names = [s.value for s in allowed]
            raise ValueError(
                f"非法状态转换: {self.status.value} → {new_status.value} "
                f"(允许: {allowed_names})"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_terminal(self) -> bool:
        """是否为终态（管道完成/已归档）。"""
        return self.status in TERMINAL_STATUSES

    @property
    def is_finished(self) -> bool:
        """是否已完成（包括所有终态和过渡态）。"""
        return self.status in (
            WorkOrderStatus.ARCHIVED,
            WorkOrderStatus.DONE_FASTREPLY,
            WorkOrderStatus.DONE_DENIED,
            WorkOrderStatus.DONE_ERROR,
        )

    # ── 摘要方法 ──

    def to_summary(self) -> dict:
        """生成流转单摘要（供 compose 阶段 MasterAgent 使用）。"""
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "status": self.status.value,
            "user": self.sender_name,
            "message": self.message_content[:200],
            "intent": {
                "sop_id": (
                    self.route_decision.sop_id
                    if self.route_decision else None
                ),
                "confidence": (
                    self.route_decision.confidence
                    if self.route_decision else "none"
                ),
            },
            "steps_executed": len(self.step_results),
            "rag_hits": sum(
                len(getattr(sr, "rag_results", []))
                for sr in self.step_results
            ),
            "tool_calls": sum(
                len(getattr(sr, "tool_calls", []))
                for sr in self.step_results
            ),
            "db_operations": sum(
                len(getattr(sr, "db_results", []))
                for sr in self.step_results
            ),
            "guardian_flags": sum(
                1 for sr in self.step_results
                if getattr(sr, "guardian", None)
                and getattr(sr.guardian, "verdict", "") in ("FLAG", "REJECT")
            ),
            "risk_level": self.risk_level,
            "llm_call_count": self.llm_call_count,
            "total_elapsed_ms": self.total_elapsed_ms,
        }

    def add_warning(self, msg: str) -> None:
        """追加一条警告消息。"""
        self.warnings.append(msg)

    def add_step_result(self, step_result: Any) -> None:
        """追加一条步骤执行结果。"""
        self.step_results.append(step_result)
