"""BusContext —— 公共 Pipeline BUS 在 4 个节点间流动的共享状态。

复用旧 PipelineContext 的接口契约，使迁移过来的 Hook 子类（AuthHook/AuditHook/
VerifyHook/TraceHook/ProgressHook/DeepAuditHook）无需改动即可工作。

Hook 通过本对象读取：user_id / is_admin / message / intent / verified_reply /
agent_reply / baggage / current_stage / pipeline_run_id，以及 get()/set()/add_warning()。

每个 WorkItem 在 BUS 上执行时创建一个 BusContext，绑定该 WorkItem。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..workitem import WorkItem
    from ...adapters.standard.message import StandardMessage


def _new_run_id() -> str:
    """生成 BUS 运行 ID（短 UUID）。"""
    return str(uuid.uuid4())[:8]


@dataclass
class BusContext:
    """公共 Pipeline BUS 节点间共享状态。"""

    # ── 绑定的 WorkItem（全息档案）──
    work_item: "WorkItem" = None  # type: ignore[assignment]

    # ── 运行标识 ──
    pipeline_run_id: str = field(default_factory=_new_run_id)
    current_stage: str = ""               # 当前节点名（hook 日志用）

    # ── 入站消息（Session 层注入，供 hook 读取 message.content）──
    message: "StandardMessage" = None     # type: ignore[assignment]

    # ── 用户信息（hook 鉴权读取）──
    user_id: str = ""
    is_admin: bool = False

    # ── 意图（hook 读取 intent.sop_id 等）──
    intent: Any = None                    # RouteDecision

    # ── 是否复合任务（trace hook 读取）──
    sub_tasks: list[Any] = field(default_factory=list)

    # ── DB 消息 ID（trace hook 读取）──
    db_message_id: str = ""

    # ── Agent 执行结果（hook / 节点读取）──
    agent_result: Any = None
    agent_reply: str = ""

    # ── 回复核验（verify hook 读写）──
    verified_reply: str = ""
    verify_warnings: list[str] = field(default_factory=list)

    # ── 流程控制 ──
    should_abort: bool = False
    abort_reason: str = ""

    # ── hook 累积警告 ──
    warnings: list[str] = field(default_factory=list)

    # ── 任意节点间传递数据 ──
    baggage: dict[str, Any] = field(default_factory=dict)

    def add_warning(self, msg: str) -> None:
        """追加一条警告消息。"""
        self.warnings.append(msg)

    def get(self, key: str, default: Any = None) -> Any:
        """从 baggage 获取值（便捷方法）。"""
        return self.baggage.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """向 baggage 写入值（便捷方法）。"""
        self.baggage[key] = value
