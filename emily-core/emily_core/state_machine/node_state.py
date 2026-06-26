"""Node status enumeration and transition matrix for the global state machine.

Follows the same pattern as workitem_state.py and session_state.py:
    - Enum for states
    - Module-level TRANSITIONS dict
    - Module-level TERMINAL_STATES frozenset

Source: 需求文件/全局状态机/全局状态机需求-架构师完善版.md §3.2
"""

from enum import Enum


class NodeStatus(Enum):
    """Five standard states for all project nodes."""
    NOT_STARTED = "NOT_STARTED"       # 未启动 — preconditions not satisfied
    IN_PROGRESS = "IN_PROGRESS"       # 进行中 — preconditions met, work executing
    BLOCKED    = "BLOCKED"            # 已阻塞 — blocked by obstacle
    DELAYED    = "DELAYED"            # 已延期 — past planned_end_date
    COMPLETED  = "COMPLETED"          # 已完成 — terminal state

    def __str__(self) -> str:
        return self.value


TRANSITIONS: dict[NodeStatus, list[NodeStatus]] = {
    NodeStatus.NOT_STARTED:  [NodeStatus.IN_PROGRESS],
    NodeStatus.IN_PROGRESS:  [NodeStatus.COMPLETED, NodeStatus.BLOCKED, NodeStatus.DELAYED],
    NodeStatus.BLOCKED:      [NodeStatus.IN_PROGRESS, NodeStatus.DELAYED],
    NodeStatus.DELAYED:      [NodeStatus.IN_PROGRESS, NodeStatus.COMPLETED],
    NodeStatus.COMPLETED:    [],   # terminal — no further transitions allowed
}

TERMINAL_STATES = frozenset({NodeStatus.COMPLETED})


# Human-readable labels (mirrors project convention of Chinese display names)
STATUS_LABELS: dict[NodeStatus, str] = {
    NodeStatus.NOT_STARTED:  "未启动",
    NodeStatus.IN_PROGRESS:  "进行中",
    NodeStatus.BLOCKED:      "已阻塞",
    NodeStatus.DELAYED:      "已延期",
    NodeStatus.COMPLETED:    "已完成",
}


def is_valid_transition(current: NodeStatus | None, target: NodeStatus) -> bool:
    """Check whether a transition is allowed by the transition matrix.

    None current means the node has no recorded status (treated as NOT_STARTED).
    """
    effective = current if current is not None else NodeStatus.NOT_STARTED
    allowed = TRANSITIONS.get(effective, [])
    return target in allowed


def is_terminal(status: NodeStatus) -> bool:
    """Check whether a status is terminal (no further transitions allowed)."""
    return status in TERMINAL_STATES
