"""Scheduler Command 数据结构。参照模式：emily_core/services/node_commands.py。"""

from dataclasses import dataclass, field


@dataclass
class CreateJobCommand:
    """创建调度作业命令。"""
    name: str
    action_type: str
    handler_module: str
    job_type: str = "ONCE"                # ONCE / CRON / INTERVAL
    cron_expression: str = ""
    interval_seconds: int = 0
    deadline_rule: str = ""
    action_params: str = "{}"
    creator_id: str = ""


@dataclass
class ActivateJobCommand:
    """激活/停用作业命令。"""
    job_id: str
    activate: bool = True                 # True=激活, False=停用
    operator_id: str = ""


@dataclass
class TriggerJobCommand:
    """手动触发作业命令。"""
    job_id: str
    operator_id: str = ""


@dataclass
class SchedulerOperationResult:
    """调度操作结果。"""
    success: bool = True
    job_id: str = ""
    execution_id: str = ""             # 执行记录 ID（引擎状态流转用）
    execution_no: str = ""
    message: str = ""
    error_code: str = ""
