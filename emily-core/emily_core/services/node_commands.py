"""全景节点图 V2 Command 数据结构 —— Service 层公共方法入参。

参照模式：emily_core/services/ 目录下的 Command 数据类。
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
    deadline: str = ""
    creator_id: str = ""
    remark: str = ""
    responsible_user_id: str = ""   # 责任人（为空时自动取 creator_id），需求 §3.1.2
    node_type: str = "WORK_PACKAGE" # 节点类型：MILESTONE / WORK_PACKAGE / TASK，需求 §3.1.1
    participant_company_ids: list[str] = field(default_factory=list)  # 参与单位ID列表


@dataclass
class UpdateNodeCommand:
    """更新节点字段命令 —— 需求文档 §8.1.3。"""
    node_id: str
    operator_id: str = ""
    node_name: str | None = None
    deadline: str | None = None
    owner_dept_id: str | None = None
    remark: str | None = None


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
# 父子节点挂载 Commands
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MountChildCommand:
    """挂载子节点命令 —— 需求文档 §8.4.1。

    将 child 挂载到 parent 下，子节点进度按 child_weight 计入父节点。
    嵌套深度上限 3 层，单父节点子节点上限由 NodeService.MAX_CHILDREN_PER_PARENT 控制。
    """
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


# ══════════════════════════════════════════════════════════════════════════════
# 节点责任人 + 任务成果提交确认 Commands
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class AssignNodeCommand:
    """变更节点责任人命令。"""
    node_id: str
    responsible_user_id: str
    operator_id: str = ""


@dataclass
class SubmitNodeDeliverableCommand:
    """提交节点成果（PENDING → SUBMITTED）。content/file_url/file_name 为 API 日志预留。"""
    deliverable_id: str
    content: str = ""           # API 日志预留
    file_url: str = ""          # API 日志预留
    file_name: str = ""         # API 日志预留
    attachment_file_id: str = ""
    submitted_by: str = ""
    is_acceptance_check: bool = False


@dataclass
class ConfirmNodeDeliverableCommand:
    """确认节点成果（SUBMITTED → CONFIRMED）。"""
    deliverable_id: str
    confirmed_by: str = ""


@dataclass
class ReturnNodeDeliverableCommand:
    """退回节点成果（SUBMITTED → RETURNED）。"""
    deliverable_id: str
    returned_by: str = ""
    reason: str = ""


@dataclass
class ResubmitNodeDeliverableCommand:
    """重新提交节点成果（RETURNED → SUBMITTED）。content/file_url/file_name 为 API 日志预留。"""
    deliverable_id: str
    content: str = ""           # API 日志预留
    file_url: str = ""          # API 日志预留
    file_name: str = ""         # API 日志预留
    attachment_file_id: str = ""
    submitted_by: str = ""


@dataclass
class CreateTaskNodeCommand:
    """创建 TASK 类型叶子节点命令。"""
    project_id: str
    node_name: str
    responsible_user_id: str = ""       # 为空时取 creator_id
    deadline: str = ""
    parent_node_id: str = ""
    owner_dept_id: str = "项目总"
    description: str = ""
    creator_id: str = ""


@dataclass
class AddParticipantCompanyCommand:
    """添加节点参与单位命令。"""
    node_id: str
    company_id: str
    operator_id: str = ""


@dataclass
class RemoveParticipantCompanyCommand:
    """移除节点参与单位命令。"""
    node_id: str
    company_id: str
    operator_id: str = ""


@dataclass
class SetParticipantCompaniesCommand:
    """批量设置节点参与单位命令（全量替换）。"""
    node_id: str
    company_ids: list[str] = field(default_factory=list)
    operator_id: str = ""


@dataclass
class MyTasksQuery:
    """我的任务查询参数。"""
    user_id: str
    project_id: str = ""
    submission_status: str = ""         # PENDING / SUBMITTED / RETURNED
    page: int = 1
    page_size: int = 20
