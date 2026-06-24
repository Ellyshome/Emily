"""PipelineContext — 管道上下文，在阶段间流动的共享状态。

每个阶段的 handler 可以读写 context 字段，
hook 通过 context 获取所需信息。

M15: 嵌入 WorkOrder 状态机 — context.work_order 携带完整流转单。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..adapters.standard.message import StandardMessage
    from ..adapters.standard.route_decision import RouteDecision
    from .work_order import WorkOrder


def _new_pipeline_run_id() -> str:
    """生成管道运行 ID（短 UUID）。"""
    return str(uuid.uuid4())[:8]


# ════════════════════════════════════════════════════════════════════════════════
# SubTask 数据结构
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class SubTask:
    """分解后的子任务（decompose 阶段产出）。

    单意图: decompose 透传，直接进入 execute
    复合意图: 拆解为 SubTask DAG，每个 SubTask 独立执行
    """
    id: str                        # "subtask-001"
    sop_id: str                    # "SOP-002-REC"
    user_input: str                # 子任务对应的用户输入片段
    depends_on: list[str] = field(default_factory=list)  # 依赖的 SubTask ID
    priority: int = 1              # 1=普通 0=最高


# ════════════════════════════════════════════════════════════════════════════════
# PipelineContext
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineContext:
    """管道上下文 — 在阶段间流动的共享状态。

    每个阶段的 handler 可以读写 context 字段，
    hook 通过 context 获取所需信息。

    使用 baggage dict 存储任意阶段间传递的临时数据。
    """

    # ── M15: 流转单（状态机 + 全息数据）──
    work_order: "WorkOrder" = None  # type: ignore[assignment]

    # ── 运行标识 ──
    pipeline_run_id: str = field(default_factory=_new_pipeline_run_id)

    # ── 入站消息 ──
    message: "StandardMessage" = None    # type: ignore[assignment]
    event_id: str = ""
    decision: "RouteDecision" = None     # type: ignore[assignment]

    # ── 用户信息（bind 阶段填充）──
    user_id: str = ""
    user: Any = None
    is_new_user: bool = False

    # ── 消息持久化（record 阶段填充）──
    db_message_id: str = ""

    # ── 附件下载（download 阶段填充）──
    downloaded_files: list[dict] = field(default_factory=list)
    # [{"file_id": ..., "file_no": "FIL-...", "local_path": "...", "file_size": N,
    #   "attachment_type": 2/3/4/5, "source_filename": "..."}, ...]

    # ── 确认检查（confirm 阶段处理）──
    has_pending_confirmation: bool = False
    confirmation_reply: str = ""

    # ── 意图分类（classify 阶段填充）──
    intent: Any = None             # SOPMatchDecision
    is_fast_reply: bool = False
    fast_reply_text: str = ""
    is_admin: bool = False

    # ── 任务拆解（decompose 阶段填充）──
    sub_tasks: list[SubTask] = field(default_factory=list)

    # ── Agent 执行（execute 阶段填充）──
    agent_result: Any = None       # AgentResult
    agent_reply: str = ""

    # ── 回复核验（verify 阶段填充）──
    verified_reply: str = ""
    verify_warnings: list[str] = field(default_factory=list)

    # ── 流程控制 ──
    should_abort: bool = False
    abort_reason: str = ""
    current_stage: str = ""

    # ── hook 执行期间累积的警告 ──
    warnings: list[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        """追加一条警告消息。"""
        self.warnings.append(msg)

    # ── 存储任意阶段间传递数据 ──
    baggage: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """从 baggage 获取值（便捷方法）。"""
        return self.baggage.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """向 baggage 写入值（便捷方法）。"""
        self.baggage[key] = value
