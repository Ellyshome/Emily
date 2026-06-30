"""全景节点图 V2 Command 数据结构 —— Service 层公共方法入参。

参照模式：plan_task_commands.py。
"""

from dataclasses import dataclass, field


# ══════════════════════════════════════════════════════════════════════════════
# 节点管理 Commands
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CreateNodeCommand:
    """创建节点命令 —— 需求文档 §8.1.1。"""
    project_id: str
    node_id: str
    node_name: str
    owner_dept_id: str = "项目总"
    related_company_id: str = "建设单位"
    deadline: str = ""
    creator_id: str = ""
    parent_node_id: str = ""
    stage_id: int = 0
    child_weight: float = 1.0
    remark: str = ""
    land_parcel_id: str = ""
    startup_doc_id: str = ""
    sort_order: int = 0


@dataclass
class UpdateNodeCommand:
    """更新节点字段命令 —— 需求文档 §8.1.3。"""
    node_id: str
    operator_id: str = ""
    node_name: str | None = None
    deadline: str | None = None
    owner_dept_id: str | None = None
    related_company_id: str | None = None
    remark: str | None = None
    stage_id: int | None = None
    sort_order: int | None = None
    land_parcel_id: str | None = None
    startup_doc_id: str | None = None


@dataclass
class ActivateNodeCommand:
    """激活节点命令 —— 部门负责人审批通过/拒绝，NOT_ACTIVATED → CONDITIONS_NOT_MET。

    审批通过（approved=True）：节点从未启用流转到条件不足，正式纳入全景图。
    审批拒绝（approved=False）：节点废弃（is_discarded=True）。
    """
    node_id: str
    approver_id: str = ""
    approved: bool = True
    remark: str = ""


@dataclass
class DiscardNodeCommand:
    """废弃节点命令。"""
    node_id: str
    operator_id: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 成果管理 Commands
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CreateDeliverableCommand:
    """新增成果命令 —— 需求文档 §8.2.1。"""
    node_id: str
    deliverable_name: str
    target_amount: float
    unit: str
    is_required: bool = True
    operator_id: str = ""


@dataclass
class UpdateDeliverableProgressCommand:
    """更新成果进度命令 —— 需求文档 §8.2.2。"""
    deliverable_id: str
    current_amount: float
    file_id: str = ""
    operator_id: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 依赖管理 Commands
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AddDependencyCommand:
    """添加依赖命令 —— 需求文档 §8.3.1。"""
    node_id: str                             # 下游节点
    depends_on_deliverable_id: str           # 依赖的成果ID
    weight: float = 1.0
    dependency_type: str = "DELIVERABLE"     # DELIVERABLE / TIME
    operator_id: str = ""


@dataclass
class RemoveDependencyCommand:
    """移除依赖命令 —— 需求文档 §8.3.2。"""
    dependency_id: str
    operator_id: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 子节点管理 Commands
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MountChildCommand:
    """挂载子节点命令 —— 需求文档 §8.4.1。"""
    parent_node_id: str
    child_node_id: str
    child_weight: float = 1.0
    operator_id: str = ""


@dataclass
class UnmountChildCommand:
    """移除子节点命令。"""
    parent_node_id: str
    child_node_id: str
    operator_id: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 结果 DTO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NodeOperationResult:
    """节点操作结果。"""
    success: bool = True
    node_id: str = ""
    status: str = ""
    progress: float = 0.0
    message: str = ""
    error_code: str = ""
    affected_downstream: list[str] = field(default_factory=list)


@dataclass
class CycleCheckResult:
    """循环依赖检测结果。"""
    has_cycle: bool = False
    cycle_path: list[str] = field(default_factory=list)  # 循环链路节点ID列表
    message: str = ""


@dataclass
class StateTransitionResult:
    """状态流转计算结果。"""
    node_id: str = ""
    old_status: str = ""
    new_status: str = ""
    old_progress: float = 0.0
    new_progress: float = 0.0
    should_transition: bool = False
    reason: str = ""
    affected_ancestors: list[str] = field(default_factory=list)
