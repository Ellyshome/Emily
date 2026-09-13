# emily-core/emily_core/session/kernel_state.py
"""M1 状态与序列化边界模块 —— 编排图状态、领域引用与工具上下文端口。

定位（计划 M1 / PRD US-07、US-08）：
  - 编排图状态只允许基础类型与标识、摘要，领域对象不进状态（PRD 约束 3、11）
  - 领域对象以 DomainRef（kind / ref_id / digest）引用，按标识重建（AC-US-08.2）
  - validate_state_serializable() 是静态守卫，供图编译前与语料回放断言使用
  - 工具上下文端口（ToolContext）把能力层与图内部状态解耦（AC-US-08.4）

过渡桥（Round-1）：
  current_tool_context() 在端口未绑定时，回退读取既有图上下文，读取的字段与迁移前
  完全一致，保证迁移期行为不变。图内在 M3 显式绑定（bind_tool_context）后，删除
  _read_graph_context_fallback()，依赖方向即完全倒转为「图 → 端口 ← 能力」。

本模块只依赖标准库（图上下文的 import 在函数内延迟执行），因此可独立导入与自测。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypedDict

logger = logging.getLogger("emily.session.kernel_state")

#: 与既有 agent loop 配置默认值保持一致（config.agent_loop_max_iterations）
DEFAULT_MAX_ITERATIONS = 12

#: 允许进入状态的基础类型（msgpack / JSON 可控）
_PRIMITIVES = (str, int, float, bool, type(None))

#: 允许的容器类型（set / frozenset 不可序列化，故不纳入）
_CONTAINERS = (dict, list, tuple)


@dataclass
class DomainRef:
    """领域对象引用 —— 只携带标识与摘要，不携带对象本体。

    纪律：状态里存 `to_dict()` 的结果，不存 DomainRef 实例（dataclass 不可 msgpack）。
    """

    kind: str
    ref_id: str
    digest: str = ""

    def to_dict(self) -> dict:
        return {"kind": str(self.kind or ""), "ref_id": str(self.ref_id or ""), "digest": str(self.digest or "")}

    @classmethod
    def from_dict(cls, data: "dict | None") -> "DomainRef":
        d = data or {}
        return cls(
            kind=str(d.get("kind", "") or ""),
            ref_id=str(d.get("ref_id", "") or ""),
            digest=str(d.get("digest", "") or ""),
        )


class KernelState(TypedDict, total=False):
    """编排图状态。

    纪律：只允许基础类型与 dict / list / tuple 嵌套；不得出现领域对象、外部资源实例、
    闭包或 ORM 对象。违反者由 validate_state_serializable() 拦截。
    """

    conversation_id: str
    actor_ref: dict
    messages: list
    plan: dict
    iteration_count: int
    _max_iterations: int
    pending_call: dict
    capability_calls: list
    gate_result: dict
    reply_text: str
    # ── 编排图运行期字段（M3）──
    user_text: str
    db_message_id: str
    _pending_tool_call: dict
    _gate_active: bool
    _fast: bool
    _capped: bool
    _should_plan: bool
    _plan_done: bool
    _should_suspend: bool
    _suspend_question: str
    _suspend_active: bool
    state_summary: dict


def make_initial_state(
    *,
    conversation_id: str,
    actor_ref: "DomainRef | dict | None" = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> dict:
    """构造初始状态。

    入参只接受标识与摘要（DomainRef 或等价 dict），不接受领域对象本体。
    """
    if isinstance(actor_ref, DomainRef):
        ref = actor_ref.to_dict()
    else:
        ref = dict(actor_ref or {})
    return {
        "conversation_id": str(conversation_id or ""),
        "actor_ref": ref,
        "messages": [],
        "plan": {},
        "iteration_count": 0,
        "_max_iterations": int(max_iterations or DEFAULT_MAX_ITERATIONS),
        "pending_call": {},
        "capability_calls": [],
        "gate_result": {},
        "reply_text": "",
    }


def validate_state_serializable(state: Any, *, _path: str = "") -> list:
    """递归校验状态是否只含可序列化内容，返回违规字段路径列表（空列表 = 通过）。

    违规判定：出现非基础类型、非 dict / list / tuple 的值，或非 str 的字典键。
    """
    violations: list = []
    if isinstance(state, _PRIMITIVES):
        return violations
    if isinstance(state, dict):
        for key, value in state.items():
            if not isinstance(key, str):
                prefix = f"{_path}." if _path else ""
                violations.append(f"{prefix}<key:{type(key).__name__}>")
                continue
            child = f"{_path}.{key}" if _path else key
            violations.extend(validate_state_serializable(value, _path=child))
        return violations
    if isinstance(state, (list, tuple)):
        for index, value in enumerate(state):
            violations.extend(validate_state_serializable(value, _path=f"{_path}[{index}]"))
        return violations
    violations.append(_path or "<root>")
    return violations


def assert_state_serializable(state: Any) -> None:
    """校验失败即抛错，供图编译前调用。"""
    violations = validate_state_serializable(state)
    if violations:
        raise ValueError("KernelState 含不可序列化内容：" + ", ".join(violations))


def summarize_state(state: dict) -> dict:
    """产出可归档的状态摘要（只含标量与计数，供轮次归档与排查）。"""
    if not isinstance(state, dict):
        return {}
    return {
        "conversation_id": str(state.get("conversation_id", "") or ""),
        "iteration_count": int(state.get("iteration_count", 0) or 0),
        "capability_call_count": len(state.get("capability_calls") or []),
        "has_pending_call": bool(state.get("pending_call")),
        "reply_chars": len(str(state.get("reply_text", "") or "")),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 工具上下文端口 —— 能力层与图内部状态的解耦点
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ToolContext:
    """工具执行所需的操作者上下文（只含基础类型与字典）。

    Attributes:
        user_id: 当前操作者标识
        perm_dict: 权限快照（只读字典）
        actor_ref: 操作者引用（标识 + 摘要）
    """

    user_id: str = ""
    perm_dict: "dict | None" = None
    actor_ref: dict = field(default_factory=dict)


_tool_context: ContextVar = ContextVar("emily_tool_context", default=None)


def bind_tool_context(ctx: "ToolContext | None") -> None:
    """绑定当前执行上下文的工具上下文（由图层在调用工具前绑定）。"""
    _tool_context.set(ctx)


def clear_tool_context() -> None:
    """清除绑定（图执行结束后调用，防上下文残留）。"""
    _tool_context.set(None)


def current_tool_context() -> ToolContext:
    """取当前工具上下文；未绑定时走过渡桥回退（行为与迁移前一致）。"""
    ctx = _tool_context.get()
    if ctx is not None:
        return ctx
    return _read_graph_context_fallback()


def current_tool_user_id() -> str:
    """当前操作者标识（供能力 handler 使用，不再直接依赖图内部状态）。"""
    return current_tool_context().user_id


def current_tool_perm_dict() -> "dict | None":
    """当前操作者权限快照（同上）。"""
    return current_tool_context().perm_dict


def _read_graph_context_fallback() -> ToolContext:
    """过渡桥：端口未绑定时回退读取既有图上下文。

    读取路径与迁移前 tools/expert_manage_tool.py 中的两个私有 helper 完全一致，
    因此不改变任何运行时行为。M3 图内绑定落地后删除本函数。
    """
    try:
        from ..workitem.langgraph_engine.state import get_bus_context
    except Exception:  # noqa: BLE001 — 图模块不可用时视为无上下文
        logger.debug("kernel_state: graph context module unavailable, empty ToolContext")
        return ToolContext()
    try:
        bus = get_bus_context()
    except Exception:  # noqa: BLE001 — RuntimeError 等一律视为无上下文
        return ToolContext()

    user_id = ""
    work_item = getattr(bus, "work_item", None)
    if work_item is not None:
        user_id = str(getattr(work_item, "user_id", "") or "")
    session_ctx = getattr(bus, "session_ctx", None)
    if not user_id and session_ctx is not None:
        user_id = str(getattr(session_ctx, "user_id", "") or "")

    perm_dict = None
    if session_ctx is not None:
        perm_dict = getattr(session_ctx, "perm_dict", None)

    return ToolContext(user_id=user_id, perm_dict=perm_dict)
