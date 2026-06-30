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
    related_company_id: str = Field(default="建设单位", description="关联单位")
    deadline: str = Field(..., description="截止时间（ISO8601）")
    parent_node_id: str = Field(default="", description="父节点ID")
    stage_id: int = Field(default=0, description="所属阶段ID")
    child_weight: float = Field(default=1.0, description="子节点权重")
    remark: str = Field(default="", description="备注")
    land_parcel_id: str = Field(default="", description="关联地块ID")
    sort_order: int = Field(default=0, description="排序序号")
    creator_id: str = Field(default="", description="创建人ID")
    startup_doc_id: str = Field(default="", description="启动文档ID")


class UpdateNodeRequest(BaseModel):
    node_name: str | None = Field(default=None, description="节点名称")
    deadline: str | None = Field(default=None, description="截止时间")
    owner_dept_id: str | None = Field(default=None, description="主责条线")
    related_company_id: str | None = Field(default=None, description="关联单位")
    remark: str | None = Field(default=None, description="备注")
    stage_id: int | None = Field(default=None, description="阶段ID")
    sort_order: int | None = Field(default=None, description="排序序号")
    land_parcel_id: str | None = Field(default=None, description="地块ID")
    startup_doc_id: str | None = Field(default=None, description="启动文档ID")
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
    child_weight: float = Field(default=1.0, description="子节点权重")
    operator_id: str = Field(default="", description="操作人ID")
