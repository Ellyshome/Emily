"""全景节点图 V2 Command 数据结构 —— Service 层公共方法入参。

参照模式：emily_core/services/ 目录下的 Command 数据类。
"""

from dataclasses import dataclass, field

from .node_state_machine import NODE_TYPE_TASK


# ══════════════════════════════════════════════════════════════════════════════
# 节点管理 Commands
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CreateNodeCommand:
    """创建节点命令 —— 需求文档 §8.1.1。"""
    project_id: str
    node_id: str
    node_name: str
    related_company_id: str = ""           # 关联单位中文名/ID（兼容批量种子；为空时从 participant_company_ids 推导）
    deadline: str = ""
    creator_id: str = ""
    remark: str = ""
    responsible_user_id: str = ""   # 责任人（为空时自动取 creator_id），需求 §3.1.2
    node_type: str = NODE_TYPE_TASK    # 节点类型：MILESTONE / TASK（**由声明决定**，不随结构变化）
    participant_company_ids: list[str] = field(default_factory=list)  # 参与单位ID列表
    # 随节点一并声明的必需成果（节点状态自证化 US-04 / R4）：
    # 至少一条 is_required=True，否则创建被拒。字段兼容 deliverable_name/name、
    # target_amount/target、unit、is_required。
    deliverables: list[dict] = field(default_factory=list)
    # 来源参考模板 ref_id（emy-console 选模板建节点时记录，用于保留与模板的溯源关系）
    template_ref_id: str = ""
    # ── 创建期一次性落库编排（US-11）：确认装配草稿后随节点一并落库 ──
    # 参与人候选（每项 user_id + 可选 role），服务端逐项校验存在性后落库
    participant_user_ids: list[dict] = field(default_factory=list)
    # 共享文件候选（file_id 列表），越权/不存在项丢弃并回报
    shared_file_ids: list[str] = field(default_factory=list)
    # 前置依赖候选（每项 depends_on_deliverable_id + 可选 weight / dependency_type）。
    # **必须最后落库**：依赖只能指向已存在的成果。
    dependencies: list[dict] = field(default_factory=list)
    # 节点角色（容器识别唯一键，默认 BUSINESS）。容器角色仅由 NodeContainerService
    # 内部设置；**不出现在工具 schema 中**，故 LLM 无法经工具指定。
    node_role: str = "BUSINESS"
    # 计划启动时间（ISO8601，空=未设置）：激活条件之一（US-18.1/18.2）。
    # 相对锚点（如"取得施工许可证后 30 天"）由服务端换算为具体日期后落库。
    planned_start_at: str = ""


@dataclass
class PromoteNodeCommand:
    """认领迁正命令（临时节点 → 正式归属位置）。"""
    node_id: str                              # 待迁正的临时节点
    target_parent_id: str = ""                # 转入的正式父节点（空 = 挂到根图）
    operator_id: str = ""
    remark: str = ""


@dataclass
class ReassignEventCommand:
    """事件归位命令（未归类收容节点 → 具体节点）。"""
    event_id: str
    target_node_id: str
    operator_id: str = ""
    remark: str = ""


@dataclass
class DisableNodeCommand:
    """停用节点命令（US-17）：退出管控，非终态可恢复。"""
    node_id: str
    operator_id: str = ""
    reason: str = ""


@dataclass
class EnableNodeCommand:
    """恢复节点命令（US-17）：回到真实三态。"""
    node_id: str
    operator_id: str = ""
    remark: str = ""


@dataclass
class UpdateNodeCommand:
    """更新节点字段命令 —— 需求文档 §8.1.3。"""
    node_id: str
    operator_id: str = ""
    node_name: str | None = None
    deadline: str | None = None
    remark: str | None = None
    # 关联单位（中文名或 company_info.id，服务层经 CompanyResolver 解析）
    related_company_id: str | None = None


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
    # 创建期一次性落库的回报（US-11）：节点必成功，其余逐项计数；
    # failed = 落库失败项（附原因），discarded = 越权/不存在被丢弃项（附原因）
    created: dict = field(default_factory=dict)
    failed: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)


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
