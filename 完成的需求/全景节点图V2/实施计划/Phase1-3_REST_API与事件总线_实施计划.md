# 全景节点图V2 Phase 1-3: REST API + 事件总线 + SSE — AI 执行计划

> **基于需求**：[全景节点图-完整需求文档V2.md](全景节点图-完整需求文档V2.md)
> **计划版本**：v1.0
> **目标**：暴露 7 个 REST 端点（节点/成果/依赖/子节点 CRUD）+ NodeEventBus 事件总线 + SSE 实时推送

---

## 你的角色

你是 **Emily 开发者**。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：不修改 Phase 1-1/1-2 产出的任何已有类/方法签名
2. **API 路由遵循现有模式**：使用 `APIRouter` + Pydantic `BaseModel` + `async def` + 返回 `{"code": 0, "data": ...}` 统一格式
3. **NodeEventBus 复用 OutboundEventBus 架构**：不重复造轮子，基于 `asyncio.Queue` pub/sub
4. **SSE 端点遵循现有 SSE 模式**：参照 `api/sse/outbound.py` 的实现风格
5. **所有端点需鉴权入口预留**：Phase 1-3 不做鉴权实现（留给 Phase 1-4），但路由中预留权限检查调用点
6. **每步验证**：每个步骤的验证命令必须通过，否则停止并报告

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `NodeService` | `emily_core/services/node_service.py` | `create_node()`, `update_node()`, `add_dependency()`, `update_deliverable_progress()`, `mount_child()` 等 | API 路由直接调用 |
| `NodeApplication` | `emily_core/application/node_app.py` | 所有 `async` 方法返回 `{"success": bool, ...}` | API 层编排，也可直接调 Service |
| `NodeCommands` | `emily_core/services/node_commands.py` | 全部 Command DTO 类 | 从 API Pydantic 模型转换为 Command |
| `OutboundEventBus` | `emily_core/outbound_bus.py` | `subscribe()` / `publish()` / `unsubscribe()` | 参照其架构构建 NodeEventBus |
| `PermissionApplication` | `emily_core/application/permission_app.py` | `check_permission()` | Phase 1-3 预留调用点 |
| `get_core()` | `api/server.py` | 获取全局 EmilyCore 实例 | 路由中获取 Service/Application 实例 |
| 权限路由模式 | `api/routes/permission.py` | `APIRouter(prefix="/permission")` + `_get_app()` 惰性初始化 | 仿照写节点路由 |

### 架构决策

1. **NodeEventBus 作为独立内存总线**：与 OutboundEventBus 平行，负责节点域内的事件分发。格式统一后发布到 OutboundEventBus 推送 SSE/IM。
2. **路由直接调用 Service**：跳过 Application 层——因为 REST API 只需返回 JSON 数据，不需要 Application 层的"生成回复文本"。Application 层留给 WorkItem 工具调用。
3. **SSE 端点复用 `api/sse/outbound.py` 扩展**：在现有 SSE 出站机制中新增 `node_event` 事件类型，而非新建独立 SSE 端点。同时提供专用的 `GET /api/v1/events/nodes` 端点以提高过滤粒度。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| API 路由 | `emily-core/api/routes/permission.py` | `APIRouter(prefix=...)` + Pydantic `BaseModel` + `HTTPException` |
| API 惰性初始化 | `emily-core/api/routes/permission.py` 中 `set_permission_app()` / `_get_app()` 模式 | 模块级 `_app` + 惰性获取 + 503 兜底 |
| SSE 端点 | `emily-core/api/sse/outbound.py` | `StreamingResponse` + `asyncio.Queue` + `EventSource` |
| 路由注册 | `emily-core/api/server.py` | `app.include_router(x.router, prefix="/api/v1")` |

---

## Phase 1-3: REST API + 事件总线 + SSE

**前置检查**（必须全部通过才进入此阶段）：

```powershell
docker exec emily-core python -c "
from emily_core.services.node_service import NodeService
from emily_core.application.node_app import NodeApplication
from emily_core.services.node_state_machine import determine_node_status
print('Phase 1-2 OK')
"
```
→ 预期输出：`Phase 1-2 OK`

**交付物**：7 个可调用的 REST 端点 + NodeEventBus 内存总线 + SSE 实时推送节点事件

---

### Step 3.1: 创建 NodeEventBus（节点事件内存总线）

**目标**：实现节点域的发布-订阅总线，对接现有 OutboundEventBus。

**操作**：

1. 新建文件 `emily-core/emily_core/node_event_bus.py`
2. 写入以下内容：

```python
"""NodeEventBus —— 全景节点图事件总线（需求文档 §7）。

架构：
  NodeEventBus（节点内部总线，细粒度过滤）
      ↓ 事件封装
  OutboundEventBus（全局出站总线，复用现有）
      ↓ 多通道推送
  SSE前端 / IM群 / Webhook第三方

基于 asyncio.Queue 发布-订阅。支持按节点ID/事件类型/项目/阶段过滤订阅。

参照模式：outbound_bus.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger("emily.node_event_bus")


# ══════════════════════════════════════════════════════════════════════════════
# 事件数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NodeEvent:
    """节点事件。"""
    event_id: str
    node_id: str
    event_type: str
    project_id: str = ""
    stage_id: int = 0
    old_value: str = ""
    new_value: str = ""
    operator_id: str = ""
    remark: str = ""
    created_at: str = ""

    def to_outbound(self) -> dict:
        """转换为 OutboundEventBus 兼容格式。"""
        return {
            "event_id": self.event_id,
            "node_id": self.node_id,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "operator_id": self.operator_id,
            "created_at": self.created_at,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 订阅过滤器
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SubscriptionFilter:
    """细粒度订阅过滤（所有字段为可选，NULL=不过滤）。"""
    node_id: str | None = None          # 特定节点（含子节点事件）
    event_types: list[str] | None = None  # 只订阅这些事件类型
    project_id: str | None = None       # 只订阅某项目
    stage_id: int | None = None         # 只订阅某阶段
    owner_dept_id: str | None = None    # 只订阅某主责条线


# ══════════════════════════════════════════════════════════════════════════════
# NodeEventBus
# ══════════════════════════════════════════════════════════════════════════════

class NodeEventBus:
    """节点事件发布-订阅总线。

    支持两种订阅模式：
    1. 无过滤订阅：收到所有节点事件（用于 SSE 全量推送）
    2. 带过滤订阅：仅收到匹配条件的事件（用于前端按需订阅）
    """

    def __init__(self, max_queue: int = 1000):
        self._subscribers: dict[str, tuple[asyncio.Queue, SubscriptionFilter | None]] = {}
        self._max_queue = max_queue
        self._outbound_bus = None  # 由 EmilyCore 注入

    def set_outbound_bus(self, bus) -> None:
        """注入全局出站总线（用于多通道推送）。"""
        self._outbound_bus = bus

    def subscribe(self, sub_id: str, filter_: SubscriptionFilter | None = None) -> asyncio.Queue:
        """订阅节点事件，返回独立队列。

        Args:
            sub_id: 订阅者唯一ID
            filter_: 可选过滤条件

        Returns:
            独立 asyncio.Queue
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers[sub_id] = (queue, filter_)
        logger.debug("NodeEventBus: subscriber '%s' added (total=%d)", sub_id, len(self._subscribers))
        return queue

    def unsubscribe(self, sub_id: str) -> None:
        """取消订阅。"""
        self._subscribers.pop(sub_id, None)
        logger.debug("NodeEventBus: subscriber '%s' removed (total=%d)", len(self._subscribers))

    async def publish(self, event: NodeEvent) -> None:
        """发布节点事件到所有匹配的订阅者。

        同时：
        - 发布到 OutboundEventBus（如果已注入）用于 SSE/IM 推送
        - 过滤不匹配的订阅者
        """
        dropped = 0
        for sub_id, (q, filter_) in list(self._subscribers.items()):
            if filter_ and not self._matches_filter(event, filter_):
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1
                logger.warning("NodeEventBus: subscriber '%s' queue full, event dropped", sub_id)

        if dropped:
            logger.warning("NodeEventBus: %d subscriber(s) dropped event", dropped)

        # 对接 OutboundEventBus
        if self._outbound_bus is not None:
            self._outbound_bus.publish("node_event", event.to_outbound())

    @staticmethod
    def _matches_filter(event: NodeEvent, filter_: SubscriptionFilter) -> bool:
        """检查事件是否匹配订阅过滤条件（AND 逻辑）。"""
        if filter_.node_id is not None and event.node_id != filter_.node_id:
            return False
        if filter_.event_types is not None and event.event_type not in filter_.event_types:
            return False
        if filter_.project_id is not None and event.project_id != filter_.project_id:
            return False
        if filter_.stage_id is not None and event.stage_id != filter_.stage_id:
            return False
        return True

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
```

**验证**：

```powershell
docker exec emily-core python -c "from emily_core.node_event_bus import NodeEventBus, NodeEvent, SubscriptionFilter; print('NodeEventBus import OK')"
```
→ 预期输出：`NodeEventBus import OK`

---

### Step 3.2: 创建 Pydantic Schema 模块

**目标**：定义 API 层的请求/响应 Pydantic 模型。

**操作**：

1. 新建文件 `emily-core/api/routes/node_schemas.py`
2. 写入以下内容：

```python
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


class NodeDetailResponse(BaseModel):
    """节点详情——由 Service.get_node_detail() 返回的 dict 透传。"""
    pass  # 动态结构，直接返回 dict


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
```

**验证**：

```powershell
docker exec emily-core python -c "from api.routes.node_schemas import CreateNodeRequest, CreateDeliverableRequest, AddDependencyRequest, MountChildRequest, ApiResponse; print('Schemas import OK')"
```
→ 预期输出：`Schemas import OK`

---

### Step 3.3: 创建节点 API 路由

**目标**：实现 7 个 REST 端点。

**操作**：

1. 新建文件 `emily-core/api/routes/node.py`
2. 写入以下内容：

```python
"""全景节点图 REST API 路由 —— 需求文档 §8。

端点：
    POST   /api/v1/project-nodes                          — 创建节点
    GET    /api/v1/project-nodes/{node_id}                — 查询节点详情
    PATCH  /api/v1/project-nodes/{node_id}                — 更新节点字段
    DELETE /api/v1/project-nodes/{node_id}                — 废弃节点
    POST   /api/v1/project-nodes/{node_id}/deliverables   — 新增成果
    PATCH  /api/v1/node-deliverables/{deliverable_id}     — 更新成果进度
    POST   /api/v1/project-nodes/{node_id}/dependencies   — 添加依赖
    DELETE /api/v1/node-dependencies/{dependency_id}      — 移除依赖
    POST   /api/v1/project-nodes/{parent_node_id}/children — 挂载子节点
    DELETE /api/v1/project-nodes/{parent_node_id}/children/{child_node_id} — 移除子节点

参照模式：api/routes/permission.py。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from .node_schemas import (
    CreateNodeRequest,
    UpdateNodeRequest,
    CreateDeliverableRequest,
    UpdateDeliverableProgressRequest,
    AddDependencyRequest,
    MountChildRequest,
    ApiResponse,
)

logger = logging.getLogger("emily.api.node")

router = APIRouter(prefix="/project-nodes", tags=["project-nodes"])

# 延迟初始化（与 permission 路由同模式）
_service = None


def set_node_service(service) -> None:
    """由 EmilyCore 注入 Service 实例。"""
    global _service
    _service = service


def _get_service():
    """惰性获取 NodeService。"""
    global _service
    if _service is not None:
        return _service
    try:
        from api.server import get_core
        core = get_core()
        core._ensure_initialized()
        _service = core._node_service
    except Exception:
        pass
    if _service is None:
        raise HTTPException(status_code=503, detail="Node graph module not initialized")
    return _service


# ══════════════════════════════════════════════════════════════════════════════
# 节点管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("", status_code=201)
async def create_node(body: CreateNodeRequest):
    """创建节点 —— 需求文档 §8.1.1。"""
    from emily_core.services.node_commands import CreateNodeCommand

    svc = _get_service()
    cmd = CreateNodeCommand(
        project_id=body.project_id,
        node_id=body.node_id,
        node_name=body.node_name,
        owner_dept_id=body.owner_dept_id,
        related_company_id=body.related_company_id,
        deadline=body.deadline,
        creator_id=body.creator_id,
        parent_node_id=body.parent_node_id,
        stage_id=body.stage_id,
        child_weight=body.child_weight,
        remark=body.remark,
        land_parcel_id=body.land_parcel_id,
        startup_doc_id=body.startup_doc_id,
        sort_order=body.sort_order,
    )
    result = await svc.create_node(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message, data={"node_id": result.node_id, "status": result.status})


@router.get("/{node_id}")
async def get_node_detail(
    node_id: str,
    include: str = Query(default="", description="children,deliverables,dependencies（逗号分隔，预留）"),
):
    """查询节点详情 —— 需求文档 §8.1.2。"""
    svc = _get_service()
    detail = await svc.get_node_detail(node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"节点 {node_id} 不存在")
    return ApiResponse(data=detail)


@router.patch("/{node_id}")
async def update_node(node_id: str, body: UpdateNodeRequest):
    """更新节点字段 —— 需求文档 §8.1.3。"""
    from emily_core.services.node_commands import UpdateNodeCommand

    svc = _get_service()
    cmd = UpdateNodeCommand(
        node_id=node_id,
        operator_id=body.operator_id,
        node_name=body.node_name,
        deadline=body.deadline,
        owner_dept_id=body.owner_dept_id,
        related_company_id=body.related_company_id,
        remark=body.remark,
        stage_id=body.stage_id,
        sort_order=body.sort_order,
        land_parcel_id=body.land_parcel_id,
        startup_doc_id=body.startup_doc_id,
    )
    result = await svc.update_node(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@router.delete("/{node_id}")
async def discard_node(node_id: str, operator_id: str = Query(default="")):
    """废弃节点。"""
    from emily_core.services.node_commands import DiscardNodeCommand

    svc = _get_service()
    cmd = DiscardNodeCommand(node_id=node_id, operator_id=operator_id)
    result = await svc.discard_node(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


# ══════════════════════════════════════════════════════════════════════════════
# 成果管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{node_id}/deliverables", status_code=201)
async def create_deliverable(node_id: str, body: CreateDeliverableRequest):
    """新增成果 —— 需求文档 §8.2.1。"""
    from emily_core.services.node_commands import CreateDeliverableCommand

    svc = _get_service()
    cmd = CreateDeliverableCommand(
        node_id=node_id,
        deliverable_name=body.deliverable_name,
        target_amount=body.target_amount,
        unit=body.unit,
        is_required=body.is_required,
        operator_id=body.operator_id,
    )
    result = await svc.create_deliverable(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


@router.patch("/node-deliverables/{deliverable_id}")
async def update_deliverable_progress(deliverable_id: str, body: UpdateDeliverableProgressRequest):
    """更新成果进度 —— 需求文档 §8.2.2。
    这是核心入口：更新后自动触发状态流转。
    """
    from emily_core.services.node_commands import UpdateDeliverableProgressCommand

    svc = _get_service()
    cmd = UpdateDeliverableProgressCommand(
        deliverable_id=deliverable_id,
        current_amount=body.current_amount,
        file_id=body.file_id,
        operator_id=body.operator_id,
    )
    result = await svc.update_deliverable_progress(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(
        message=result.message,
        data={
            "node_id": result.node_id,
            "status": result.status,
            "progress": result.progress,
            "affected_ancestors": result.affected_downstream,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# 依赖管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{node_id}/dependencies", status_code=201)
async def add_dependency(node_id: str, body: AddDependencyRequest):
    """添加依赖 —— 需求文档 §8.3.1。含循环检测前置。"""
    from emily_core.services.node_commands import AddDependencyCommand

    svc = _get_service()
    cmd = AddDependencyCommand(
        node_id=node_id,
        depends_on_deliverable_id=body.depends_on_deliverable_id,
        weight=body.weight,
        dependency_type=body.dependency_type,
        operator_id=body.operator_id,
    )
    result = await svc.add_dependency(cmd)
    if not result.success:
        status_code = 400
        if result.error_code == "40001":
            status_code = 400  # 循环依赖
        raise HTTPException(status_code=status_code, detail=result.message)
    return ApiResponse(message=result.message)


@router.delete("/node-dependencies/{dependency_id}")
async def remove_dependency(dependency_id: str, operator_id: str = Query(default="")):
    """移除依赖 —— 需求文档 §8.3.2。"""
    from emily_core.services.node_commands import RemoveDependencyCommand

    svc = _get_service()
    cmd = RemoveDependencyCommand(dependency_id=dependency_id, operator_id=operator_id)
    result = await svc.remove_dependency(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)


# ══════════════════════════════════════════════════════════════════════════════
# 子节点管理
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{parent_node_id}/children", status_code=201)
async def mount_child(parent_node_id: str, body: MountChildRequest):
    """挂载子节点 —— 需求文档 §8.4.1。"""
    from emily_core.services.node_commands import MountChildCommand

    svc = _get_service()
    cmd = MountChildCommand(
        parent_node_id=parent_node_id,
        child_node_id=body.child_node_id,
        child_weight=body.child_weight,
        operator_id=body.operator_id,
    )
    result = await svc.mount_child(cmd)
    if not result.success:
        status_code = 400
        if result.error_code == "40001":
            status_code = 400
        elif result.error_code == "40002":
            status_code = 400
        raise HTTPException(status_code=status_code, detail=result.message)
    return ApiResponse(message=result.message)


@router.delete("/{parent_node_id}/children/{child_node_id}")
async def unmount_child(parent_node_id: str, child_node_id: str, operator_id: str = Query(default="")):
    """移除子节点。"""
    from emily_core.services.node_commands import UnmountChildCommand

    svc = _get_service()
    cmd = UnmountChildCommand(
        parent_node_id=parent_node_id,
        child_node_id=child_node_id,
        operator_id=operator_id,
    )
    result = await svc.unmount_child(cmd)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return ApiResponse(message=result.message)
```

**验证**：

```powershell
docker exec emily-core python -c "from api.routes.node import router; print(f'Routes: {len(router.routes)} endpoints'); [print(f'  {r.methods} {r.path}') for r in router.routes]"
```
→ 预期输出：显示 10 个端点（7 个业务端点 + DELETE/DELETE 子节点 + GET detail + PATCH update）

**失败处理**：如果 import 失败，检查 schema 模块的类名是否匹配。

---

### Step 3.4: 创建 SSE 节点事件端点

**目标**：提供 SSE 实时推送节点事件的端点。

**操作**：

1. 新建文件 `emily-core/api/sse/node_events.py`
2. 写入以下内容：

```python
"""SSE 节点事件推送端点 —— 需求文档 §8.5。

GET /api/v1/events/nodes?project_id={project_id}

SSE Event 消息格式：
event: node_event
data: {event_id, node_id, event_type, old_value, new_value, operator_id, created_at}

参照模式：api/sse/outbound.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("emily.sse.node_events")

router = APIRouter(prefix="/events", tags=["sse"])

# 延迟初始化
_bus = None


def set_node_event_bus(bus) -> None:
    """由 EmilyCore 注入 NodeEventBus。"""
    global _bus
    _bus = bus


async def _event_generator(request: Request, project_id: str = ""):
    """SSE 事件生成器。"""
    sub_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    if _bus is None:
        # 总线未初始化，发送错误后退出
        yield f"event: error\ndata: {json.dumps({'message': 'NodeEventBus not initialized'})}\n\n"
        return

    from emily_core.node_event_bus import SubscriptionFilter

    filter_ = SubscriptionFilter(project_id=project_id) if project_id else None
    _bus.subscribe(sub_id, filter_)

    try:
        # 发送初始连接确认
        yield f"event: connected\ndata: {json.dumps({'sub_id': sub_id, 'project_id': project_id})}\n\n"

        while True:
            # 检查客户端是否断开
            if await request.is_disconnected():
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # 发送心跳保持连接
                yield f": heartbeat\n\n"
                continue

            yield (
                f"event: node_event\n"
                f"data: {json.dumps(event.to_outbound(), ensure_ascii=False)}\n\n"
            )
    finally:
        _bus.unsubscribe(sub_id)
        logger.debug("SSE node_events connection closed: %s", sub_id)


@router.get("/nodes")
async def node_events_stream(
    request: Request,
    project_id: str = Query(default="", description="按项目过滤（为空则接收所有项目事件）"),
):
    """SSE 节点事件流 —— 需求文档 §8.5。"""
    return StreamingResponse(
        _event_generator(request, project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

**验证**：

```powershell
docker exec emily-core python -c "from api.sse.node_events import router; print('SSE router import OK')"
```
→ 预期输出：`SSE router import OK`

---

### Step 3.5: 在 server.py 中注册新路由和 SSE 端点

**目标**：将节点路由和 SSE 端点注册到 FastAPI app。

**操作**：

1. 打开 `emily-core/api/server.py`
2. 找到路由注册区域（`app.include_router` 行），在 `permission` 路由注册之后追加：

```python
# 在 app.include_router(permission.router, prefix="/api/v1") 之后添加：

from .routes import node as node_routes
app.include_router(node_routes.router, prefix="/api/v1")

from .sse import node_events
app.include_router(node_events.router, prefix="/api/v1")
```

3. 完整注册区域应变为：

```python
from .routes import health, message, session, permission
from .sse import outbound
from .routes import node as node_routes    # 新增
from .sse import node_events               # 新增

app.include_router(health.router, prefix="/api/v1")
app.include_router(message.router, prefix="/api/v1")
app.include_router(session.router, prefix="/api/v1")
app.include_router(permission.router, prefix="/api/v1")
app.include_router(outbound.router, prefix="/api/v1")
app.include_router(node_routes.router, prefix="/api/v1")       # 新增
app.include_router(node_events.router, prefix="/api/v1")       # 新增
```

**验证**：

```powershell
# 检查 server.py 语法正确
docker exec emily-core python -c "import ast; ast.parse(open('api/server.py').read()); print('server.py syntax OK')"
```
→ 预期输出：`server.py syntax OK`

**失败处理**：如果语法错误，检查 import 语句和 include_router 调用的正确性。

---

### Step 3.6: 在 EmilyCore 中初始化节点模块

**目标**：在 `EmilyCore.__init__()` 中集成 NodeService 和 NodeEventBus 的惰性初始化。

**操作**：

1. 打开 `emily-core/emily_core/__init__.py`
2. 找到 `EmilyCore.__init__()` 中延迟初始化属性区域，追加：

```python
# 全景节点图 V2
self._node_service = None
self._node_event_bus = None
self._node_app = None
```

3. 在 `_ensure_initialized()` 方法末尾（或合适位置）追加初始化调用：

```python
self._init_node_module()
```

4. 新增初始化方法：

```python
def _init_node_module(self) -> None:
    """初始化全景节点图 V2 模块（Service + EventBus + Application）。"""
    try:
        from .services.node_service import NodeService
        from .node_event_bus import NodeEventBus
        from .application.node_app import NodeApplication

        self._node_service = NodeService()
        self._node_event_bus = NodeEventBus()
        self._node_event_bus.set_outbound_bus(self.outbound_bus)
        self._node_app = NodeApplication(self._node_service)

        # 注入到 API 路由
        try:
            from api.routes.node import set_node_service
            set_node_service(self._node_service)
        except ImportError:
            pass  # 非 API 场景（如脚本直接调用 EmilyCore）
        try:
            from api.sse.node_events import set_node_event_bus
            set_node_event_bus(self._node_event_bus)
        except ImportError:
            pass

        logger.info("Node graph V2 module initialized")
    except Exception:
        logger.warning("Node graph V2 module initialization failed", exc_info=True)
```

**验证**：

```powershell
# 重启 emily-core 后检查初始化日志
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 30 emily-core 2>&1 | grep -i "node"
```
→ 预期输出：包含 `Node graph V2 module initialized`

**失败处理**：如果有错误，检查 EmilyCore 的 import 路径和属性名是否一致。

---

### Phase 1-3 最终验证

端到端验证：HTTP 请求创建节点 → 上传成果 → 触发状态流转 → SSE 收到事件。

```powershell
# 1. 确认 Emily 服务已启动
docker compose -f docker-compose-napcat.yml ps | grep emily-core
# → 预期：emily-core 状态 Up

# 2. 创建节点（HTTP POST）
docker exec emily-core curl -s -X POST http://localhost:18080/api/v1/project-nodes \
  -H "Content-Type: application/json" \
  -d '{"project_id":"e2e-api","node_id":"API-001","node_name":"API测试节点","deadline":"2026-12-31T18:00:00+08:00","creator_id":"test-user"}' 
# → 预期：{"code":0,"message":"...创建成功","data":{"node_id":"API-001","status":"CONDITIONS_NOT_MET"}}

# 3. 查询节点详情
docker exec emily-core curl -s http://localhost:18080/api/v1/project-nodes/API-001
# → 预期：{"code":0,"data":{"node_id":"API-001","status":"CONDITIONS_NOT_MET",...}}

# 4. 为节点新增成果
docker exec emily-core curl -s -X POST http://localhost:18080/api/v1/project-nodes/API-001/deliverables \
  -H "Content-Type: application/json" \
  -d '{"deliverable_name":"测试成果","target_amount":1,"unit":"份"}'
# → 预期：{"code":0,"message":"...创建成功"}

# 5. 找到成果ID并更新进度到100%
# （先查详情获取 deliverable_id）
docker exec emily-core curl -s http://localhost:18080/api/v1/project-nodes/API-001 | python -c "import sys,json; d=json.load(sys.stdin)['data']; print(d['deliverables'][0]['deliverable_id'])"
# → 获取到 deliverable_id 如 API-001-DELV-001

# 然后用获取到的ID更新进度
docker exec emily-core curl -s -X PATCH http://localhost:18080/api/v1/node-deliverables/API-001-DELV-001 \
  -H "Content-Type: application/json" \
  -d '{"current_amount":1}'
# → 预期：{"code":0,"message":"...","data":{"node_id":"API-001","status":"COMPLETED","progress":100.0}}

# 6. 清理
docker exec emily-core python -c "
from emily_core.repositories.node_repo import ProjectNodeRepo
ProjectNodeRepo().discard('API-001')
print('Cleanup done')
"
```

全部通过后进入 Phase 1-4。

---

## 阶段反思指令

完成本阶段后，执行反思：

1. **检查产物**：列出新建/修改文件
   - `emily-core/emily_core/node_event_bus.py`（新建）
   - `emily-core/api/routes/node_schemas.py`（新建）
   - `emily-core/api/routes/node.py`（新建）
   - `emily-core/api/sse/node_events.py`（新建）
   - `emily-core/api/server.py`（修改：注册路由）
   - `emily-core/emily_core/__init__.py`（修改：新增初始化方法）

2. **判断是否继续**：与 Phase 1-1 相同的偏差规则

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
