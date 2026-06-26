"""Global State Machine — package exports.

Sub-modules:
    node_state: NodeStatus enum, TRANSITIONS, TERMINAL_STATES
    stage_state: StageStatus enum, STAGE_TRANSITIONS, STAGE_LABELS
"""

from emily_core.state_machine.node_state import (
    NodeStatus,
    STATUS_LABELS,
    TERMINAL_STATES,
    TRANSITIONS,
    is_terminal,
    is_valid_transition,
)
from emily_core.state_machine.stage_state import (
    STAGE_BOUNDARIES,
    STAGE_LABELS,
    STAGE_TRANSITIONS,
    StageStatus,
)

__all__ = [
    "NodeStatus",
    "StageStatus",
    "TRANSITIONS",
    "STAGE_TRANSITIONS",
    "TERMINAL_STATES",
    "STATUS_LABELS",
    "STAGE_LABELS",
    "STAGE_BOUNDARIES",
    "is_valid_transition",
    "is_terminal",
]
