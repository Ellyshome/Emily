# emily-core/emily_core/kernel/context.py
"""内核运行时上下文（M4 / US-04）—— 上下文入口由图配置承载。

定位：
  - 编排图的上下文**入口**是框架官方通道：图声明 `context_schema`，调用方经
    `ainvoke(..., context=KernelContext(...))` 传入，节点经 `runtime.context` 读取。
  - 领域对象（如工单 BusContext）**不进 state**（PRD 约束 D2），但可以经运行时上下文传递——
    这正是官方机制的设计用途：不参与检查点序列化，因此不受"状态可序列化"约束。
  - 进程内 ContextVar 通道（能力层读取用）**降级为唯一的兼容桥** `bind_compat()`，
    不再作为上下文入口；该桥是本项改造后仍保留的自研通道，属显式标注的兼容点（AC-US-04.1）。
  - 不引入框架侧长期记忆存储（PRD §4.4-8）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emily.kernel.context")


@dataclass
class KernelContext:
    """图运行时上下文（经官方 `context=` 传入，不落检查点）。

    Attributes:
        conversation_id: 会话标识（线程命名与日志归属）
        actor_ref: 操作者引用（标识 + 摘要；只含基础类型）
        user_id: 操作者标识
        db_message_id: 入站消息的库内标识
        bus: 工单侧 BusContext（领域对象，仅经运行时上下文传递，不进 state）
        extra: 预留扩展位（基础类型）
    """

    conversation_id: str = ""
    actor_ref: dict = field(default_factory=dict)
    user_id: str = ""
    db_message_id: str = ""
    bus: Any = None
    extra: dict = field(default_factory=dict)

    # ── 兼容桥（★ 唯一标注点）───────────────────────────────────────────────

    def bind_compat(self) -> None:
        """把运行时上下文转接到能力层读取的进程内通道。

        说明：能力层（tools/*）历史实现经 `current_tool_context()` / `get_bus_context()`
        读取上下文，且图节点可能在不同 asyncio 任务中执行（ContextVar 不向上传播），
        故本桥必须在**调用方**（图启动前）执行，而不是在某个节点内部。
        改造后的分工：**入口 = 官方运行时上下文**；本函数 = 自研通道的唯一兼容点。
        """
        try:
            from ..session.kernel_state import ToolContext, bind_tool_context
            bind_tool_context(ToolContext(
                user_id=str(self.user_id or (self.actor_ref or {}).get("ref_id", "") or ""),
                actor_ref=dict(self.actor_ref or {}),
            ))
        except Exception as e:  # noqa: BLE001 — 桥接失败不阻断主流程
            logger.warning("bind_compat: tool context bridge failed: %s", e)
        if self.bus is not None:
            try:
                from ..workitem.langgraph_engine.state import set_bus_context
                set_bus_context(self.bus)
            except Exception as e:  # noqa: BLE001
                logger.warning("bind_compat: bus context bridge failed: %s", e)

    @classmethod
    def from_runtime(cls, runtime: Any) -> "KernelContext":
        """从框架运行时对象取出上下文；无官方上下文时返回空上下文（不静默伪装）。"""
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, KernelContext):
            return ctx
        if ctx is None:
            return cls()
        # 非本类实例（如 dict）时按字段尽力还原，避免调用方拿到不可用的上下文
        if isinstance(ctx, dict):
            return cls(
                conversation_id=str(ctx.get("conversation_id", "") or ""),
                actor_ref=dict(ctx.get("actor_ref") or {}),
                user_id=str(ctx.get("user_id", "") or ""),
                db_message_id=str(ctx.get("db_message_id", "") or ""),
                bus=ctx.get("bus"),
                extra=dict(ctx.get("extra") or {}),
            )
        logger.warning("from_runtime: unexpected context type %s", type(ctx).__name__)
        return cls()
