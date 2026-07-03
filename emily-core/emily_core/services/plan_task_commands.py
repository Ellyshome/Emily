"""计划任务 Command 数据结构。

所有 Service 层公共方法的入参均使用 Command 数据类封装。
"""

from dataclasses import dataclass, field


@dataclass
class CreateTemplateCommand:
    """创建任务模板命令。"""
    name: str
    description: str = ""
    initiator_id: str = ""
    executor_id: str = ""
    project_id: str = ""
    task_type: str = "ONCE"             # ONCE / WEEKLY / MONTHLY
    deadline_rule: str = ""
    verification_standard: str = "{}"
    workflow_definition_key: str = ""
    creator_id: str = ""


@dataclass
class CreateInstanceCommand:
    """创建任务实例命令。"""
    template_id: str = ""
    title: str = ""
    description: str = ""
    initiator_id: str = ""
    executor_id: str = ""
    project_id: str = ""
    phase_code: str = ""
    node_id: str = ""
    deadline_at: str = ""
    verification_standard: str = "{}"
    period_key: str = ""


@dataclass
class CreateInstanceFromTemplateCommand:
    """从模板创建实例命令（调度机内部使用）。"""
    template_id: str = ""
    title: str = ""
    description: str = ""
    initiator_id: str = ""
    executor_id: str = ""
    project_id: str = ""
    node_id: str = ""
    deadline_at: str = ""
    verification_standard: str = "{}"
    period_key: str = ""
    template_no: str = ""


@dataclass
class SubmitDeliverableCommand:
    """提交任务成果命令。"""
    instance_id: str = ""
    type: str = "TEXT"                  # FILE / TEXT / JSON
    content: str = ""
    file_url: str = ""
    file_name: str = ""
    submitted_by: str = ""
    is_acceptance_check: bool = False   # 是否为完工确认报告


@dataclass
class ReviewTaskCommand:
    """审核任务命令。"""
    instance_id: str = ""
    operator_id: str = ""
    action: str = "confirm"             # confirm / return
    reason: str = ""


@dataclass
class ReviewAnomalyCommand:
    """异常复核命令（P2：上级复核反向下达的任务）。"""
    instance_id: str = ""
    reviewer_id: str = ""
    action: str = "approve"             # approve（确认下发） / reject（终止退回）
    reason: str = ""


@dataclass
class EscalateTaskCommand:
    """执行人升级命令（P2：离职/失能时升级给顺位上级）。"""
    instance_id: str = ""
    reason: str = ""


@dataclass
class AuthCheckResult:
    """鉴权检查结果。"""
    allowed: bool = True
    anomaly: bool = False
    target_status: str = "WAITING"        # 正常 WAITING，异常 ANOMALY_PENDING_REVIEW
    reason: str = ""
    supervisor_id: str = ""


@dataclass
class PeriodCalculationResult:
    """LLM 周期推算结果。"""
    period_key: str = ""                  # "2024-W25" / "2024-M06"
    deadline_at: str = ""                 # ISO8601
    cycle_type: str = ""                  # "WEEKLY" / "MONTHLY"


@dataclass
class MatchResult:
    """计划外事件匹配结果。"""
    matched: bool = False
    instance_id: str = ""
    instance_no: str = ""
    confidence: str = "none"              # "exact" / "llm_fuzzy" / "none"
