"""CQRS command DTOs for the state machine service.

Follows the plan_task_commands.py pattern: @dataclass for each service method's parameters.
"""

from dataclasses import dataclass, field


# ========================================================================
#  Node
# ========================================================================

@dataclass
class ChangeNodeStatusCommand:
    node_id: str = ""
    target_status: str = ""       # NodeStatus value string
    operator_id: str = ""
    reason: str = ""
    client_ip: str = ""


@dataclass
class CreateNodeCommand:
    node_id: str = ""
    node_name: str = ""
    stage_id: int = 0
    parent_section: str = ""
    node_type: str = "standard"
    owner: str = ""
    approver: str = ""
    is_milestone: bool = False
    recurrence_type: str = "SINGLE"
    sort_order: int = 0


@dataclass
class ForceActivateNodeCommand:
    """Bypass dependency checks for externally-triggered nodes (e.g. 1.1, 5.12.5)."""
    node_id: str = ""
    operator_id: str = ""
    reason: str = ""


# ========================================================================
#  Stage
# ========================================================================

@dataclass
class GetStageProgressCommand:
    stage_id: int = 0


# ========================================================================
#  Query
# ========================================================================

@dataclass
class AuditLogQuery:
    target_type: str = ""
    target_id: str = ""
    limit: int = 100
    offset: int = 0


# ========================================================================
#  Response
# ========================================================================

@dataclass
class ChangeStatusResponse:
    success: bool = False
    node_id: str = ""
    from_status: str = ""
    to_status: str = ""
    precondition_score: int = 0
    cascaded_nodes: list[str] = field(default_factory=list)
    reply: str = ""
    error_code: str = ""


@dataclass
class StageProgress:
    stage_id: int = 0
    stage_name: str = ""
    status: str = ""
    total_nodes: int = 0
    completed_nodes: int = 0
    progress: int = 0
    critical_path: list[str] = field(default_factory=list)


@dataclass
class OverallProgress:
    total_nodes: int = 0
    completed_nodes: int = 0
    progress: int = 0
    stages: list[StageProgress] = field(default_factory=list)


@dataclass
class NodeInfo:
    node_id: str = ""
    node_name: str = ""
    stage_id: int = 0
    node_type: str = ""
    status: str = ""
    precondition_score: int = 0
    risk_level: str = "正常"
    is_milestone: bool = False
    dependencies: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    downstream: list[str] = field(default_factory=list)
