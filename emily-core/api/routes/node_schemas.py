"""全景节点图 REST API Pydantic Schemas —— 请求体 / 响应体。

参照模式：api/routes/permission.py 中的 Pydantic 模型。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ══════════════════════════════════════════════════════════════════════════════

class ApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: dict | None = None


class ErrorResponse(BaseModel):
    code: int = 40001
    message: str = ""
    detail: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# 节点管理
# ══════════════════════════════════════════════════════════════════════════════

class CreateNodeRequest(BaseModel):
    project_id: str = Field(..., description="项目归属ID")
    node_id: str = Field(..., description="节点编号（业务主键），例：SG-JG-01-2026")
    node_name: str = Field(..., description="节点名称")
    owner_dept_id: str = Field(default="项目总", description="主责条线")
    participant_company_ids: list[str] = Field(default_factory=list, description="参与单位ID列表（至少一项；首项自动成为关联单位）")
    deadline: str = Field(..., description="截止时间（ISO8601）")
    remark: str = Field(default="", description="备注")
    creator_id: str = Field(default="", description="创建人ID")
    responsible_user_id: str = Field(default="", description="责任人ID（为空时取creator_id）")
    node_type: str = Field(default="WORK_PACKAGE", description="节点类型：MILESTONE / WORK_PACKAGE / TASK")


class UpdateNodeRequest(BaseModel):
    node_name: str | None = Field(default=None, description="节点名称")
    deadline: str | None = Field(default=None, description="截止时间")
    owner_dept_id: str | None = Field(default=None, description="主责条线")
    remark: str | None = Field(default=None, description="备注")
    operator_id: str = Field(default="", description="操作人ID")


# ══════════════════════════════════════════════════════════════════════════════
# 节点审批
# ══════════════════════════════════════════════════════════════════════════════

class ActivateNodeRequest(BaseModel):
    approved: bool = Field(..., description="是否通过审批")
    approver_id: str = Field(..., description="审批人ID")
    remark: str = Field(default="", description="审批意见")


# ══════════════════════════════════════════════════════════════════════════════
# 成果管理
# ══════════════════════════════════════════════════════════════════════════════

class CreateDeliverableRequest(BaseModel):
    deliverable_name: str = Field(..., description="成果名称")
    target_amount: float = Field(..., description="目标量")
    unit: str = Field(..., description="量纲（份/吨/平方米...）")
    is_required: bool = Field(default=True, description="是否必需成果")
    operator_id: str = Field(default="", description="操作人ID")


class UpdateDeliverableProgressRequest(BaseModel):
    current_amount: float = Field(..., description="当前量")
    file_id: str = Field(default="", description="关联文件ID")
    operator_id: str = Field(default="", description="操作人ID")


# ══════════════════════════════════════════════════════════════════════════════
# 依赖管理
# ══════════════════════════════════════════════════════════════════════════════

class AddDependencyRequest(BaseModel):
    depends_on_deliverable_id: str = Field(..., description="依赖的成果ID")
    weight: float = Field(default=1.0, description="权重（0.0000-1.0000，阻塞场景用 ≥999）")
    dependency_type: str = Field(default="DELIVERABLE", description="DELIVERABLE / TIME")
    operator_id: str = Field(default="", description="操作人ID")


# ══════════════════════════════════════════════════════════════════════════════
# 子节点管理
# ══════════════════════════════════════════════════════════════════════════════

class MountChildRequest(BaseModel):
    child_node_id: str = Field(..., description="子节点编号")
    operator_id: str = Field(default="", description="操作人ID")


# ══════════════════════════════════════════════════════════════════════════════
# 节点责任人 + 任务成果提交确认
# ══════════════════════════════════════════════════════════════════════════════


class AssignNodeRequest(BaseModel):
    responsible_user_id: str = Field(..., description="新责任人ID（users.id）")
    operator_id: str = Field(default="", description="操作人ID")


class SubmitDeliverableRequest(BaseModel):
    content: str = Field(default="", description="提交内容")
    file_url: str = Field(default="", description="文件URL")
    file_name: str = Field(default="", description="文件名")
    attachment_file_id: str = Field(default="", description="附件文件ID")
    is_acceptance_check: bool = Field(default=False, description="是否为完工确认报告")


class ConfirmDeliverableRequest(BaseModel):
    reason: str = Field(default="", description="确认说明")
    operator_id: str = Field(default="", description="确认人ID")


class ReturnDeliverableRequest(BaseModel):
    reason: str = Field(..., description="退回原因（必填）")
    operator_id: str = Field(default="", description="退回人ID")


class ResubmitDeliverableRequest(BaseModel):
    content: str = Field(default="", description="重新提交内容")
    file_url: str = Field(default="", description="文件URL")
    file_name: str = Field(default="", description="文件名")
    attachment_file_id: str = Field(default="", description="附件文件ID")


class MyTasksRequest(BaseModel):
    project_id: str = Field(default="", description="项目ID（可选）")
    submission_status: str = Field(default="", description="提交状态过滤")
    page: int = Field(default=1, description="页码")
    page_size: int = Field(default=20, description="每页数量")


# ══════════════════════════════════════════════════════════════════════════════
# 参与单位管理
# ══════════════════════════════════════════════════════════════════════════════

class AddParticipantCompanyRequest(BaseModel):
    company_id: str = Field(..., description="参与单位ID（company_info.id）")
    operator_id: str = Field(default="", description="操作人ID")


class RemoveParticipantCompanyRequest(BaseModel):
    company_id: str = Field(..., description="参与单位ID（company_info.id）")
    operator_id: str = Field(default="", description="操作人ID")


class SetParticipantCompaniesRequest(BaseModel):
    company_ids: list[str] = Field(default_factory=list, description="参与单位ID列表（全量替换）")
    operator_id: str = Field(default="", description="操作人ID")
