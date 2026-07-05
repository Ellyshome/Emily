"""BusContext —— 公共 Pipeline BUS 在 4 个节点间流动的共享状态。

复用旧 PipelineContext 的接口契约，使迁移过来的 Hook 子类（AuthHook/AuditHook/
VerifyHook/TraceHook/ProgressHook/DeepAuditHook）无需改动即可工作。

Hook 通过本对象读取：user_id / is_admin / message / intent / verified_reply /
agent_reply / baggage / current_stage / pipeline_run_id，以及 get()/set()/add_warning()。

**权限架构 v1.2 调整**：
  - WorkItemAgent 通过本对象以只读方式访问 SessionContext 中的权限信息
  - 不直接将权限信息注入到 WorkItemAgent 内部，避免上下文污染
  - 通过 session_context 属性访问，仅允许读取，不允许修改

每个 WorkItem 在 BUS 上执行时创建一个 BusContext，绑定该 WorkItem。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..workitem import WorkItem
    from ...adapters.standard.message import StandardMessage
    from ...session.session_context import SessionContext


def _new_run_id() -> str:
    """生成 BUS 运行 ID（短 UUID）。"""
    return str(uuid.uuid4())[:8]


@dataclass
class BusContext:
    """公共 Pipeline BUS 节点间共享状态。

    权限架构 v1.2：
    - _session_context 是私有字段，仅在初始化时设置
    - WorkItemAgent 通过只读方法获取权限信息，无法修改
    - 确保权限数据的安全性和不可变性
    """

    # ── 绑定的 WorkItem（全息档案）──
    work_item: "WorkItem" = None  # type: ignore[assignment]

    # ── SessionContext（私有，只读访问）──
    _session_context: Optional["SessionContext"] = None

    # ── 运行标识 ──
    pipeline_run_id: str = field(default_factory=_new_run_id)
    current_stage: str = ""

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

    # ── SessionContext 只读访问方法（权限架构 v1.2）──

    def get_session_context(self) -> Optional["SessionContext"]:
        """获取 SessionContext（只读）。

        WorkItemAgent / AuthHook 通过此方法获取会话状态信息与权限列表。
        """
        return self._session_context

    def has_sop_permission(self, sop_id: str) -> bool:
        """检查是否有权限使用指定 SOP（便捷方法）。"""
        if self._session_context is None:
            return False
        return self._session_context.has_sop_permission(sop_id)

    def has_db_permission(self, table: str, operation: str = "read") -> bool:
        """检查是否有权限访问指定数据库表（便捷方法）。"""
        if self._session_context is None:
            return False
        return self._session_context.has_db_permission(table, operation)

    def meets_grouping_requirement(self, required_grouping: int) -> bool:
        """检查是否满足权限层级要求（累进继承，便捷方法）。"""
        if self._session_context is None:
            return False
        return self._session_context.meets_grouping_requirement(required_grouping)

    # ── 常规方法 ──

    def add_warning(self, msg: str) -> None:
        """追加一条警告消息。"""
        self.warnings.append(msg)

    def get(self, key: str, default: Any = None) -> Any:
        """从 baggage 获取值（便捷方法）。"""
        return self.baggage.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """向 baggage 写入值（便捷方法）。"""
        self.baggage[key] = value
