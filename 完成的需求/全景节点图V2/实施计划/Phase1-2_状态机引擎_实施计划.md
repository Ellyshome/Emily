# 全景节点图V2 Phase 1-2: 状态机引擎 + 服务层 — AI 执行计划

> **基于需求**：[全景节点图-完整需求文档V2.md](全景节点图-完整需求文档V2.md)
> **计划版本**：v1.0
> **目标**：实现三态流转计算引擎、父子进度加权汇总、循环依赖检测（BFS）、Command DTO + NodeStateMachineService + NodeApplication

---

## 你的角色

你是 **Emily 开发者**。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：不修改 ProjectNodeRepo 等 Phase 1-1 产出的方法签名
2. **sync Repo 模式**：Service 层对慢速 Repo 调用必须用 `asyncio.to_thread()` 包裹
3. **Command DTO 封装入参**：Service 公共方法使用 dataclass Command 对象作为入参
4. **状态流转自动触发**：不上锁、不等人工审批——成果上传后自动计算并流转
5. **最大递归深度 3 层**：子节点进度更新触发祖先重算，最多递归 3 层
6. **循环依赖 BFS 检测前置**：添加依赖前必须执行 BFS 检测，命中则阻止操作
7. **每步验证**：每个步骤的验证命令必须通过，否则停止并报告
8. **参照模式**：所有新代码必须参照下方"代码模式参照表"中的源文件。风格不一致视为失败

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `ProjectNodeRepo` | `emily_core/repositories/node_repo.py` | `get_by_node_id()`, `update_status()`, `update_progress()`, `find_by_parent()`, `find_downstream()`, `get_ancestor_chain()`, `count_children()` | Service 层直接调用 |
| `NodeDependencyRepo` | `emily_core/repositories/node_repo.py` | `find_by_node()`, `find_downstream()`, `exists()` | 依赖检查 + 循环检测 |
| `NodeDeliverableRepo` | `emily_core/repositories/node_repo.py` | `find_by_node()`, `update_progress()`, `get_completion_ratio()` | 成果完成度计算 |
| `NodeEventRepo` | `emily_core/repositories/node_repo.py` | `create()` | 状态变更事件记录 |
| `_new_id(prefix)` | `emily_core/infrastructure/database/models.py` | 生成 `PREFIX-YYYYMMDD-hex8` | 生成 event_id |
| `_parse_decimal()` | `emily_core/repositories/node_repo.py` | `str -> float` | Service 层使用 |
| `_to_decimal_str()` | `emily_core/repositories/node_repo.py` | `float -> str` | Service 层使用 |
| `BEIJING_TZ` | `emily_core/repositories/node_repo.py` | `timezone(timedelta(hours=8))` | 业务时间计算 |
| `get_session()` | `emily_core/infrastructure/database/session.py` | 上下文管理器 | 需要跨表事务时使用 |
| `ALLOWED_TRANSITIONS` | `emily_core/services/plan_task_service.py` | `dict[str\|None, list[str]]` | 参照其模式定义状态流转规则 |
| `AuthCheckResult` | `emily_core/services/plan_task_commands.py` | dataclass | 参照其模式定义结果 DTO |

### 架构决策

1. **状态机引擎独立为模块**：`emily_core/services/node_state_machine.py` 作为纯计算引擎，不依赖数据库——接受数据、返回结果。Service 层负责调用引擎 + 读写 DB。分离计算和 I/O，便于单元测试。
2. **三态模型处理阻塞**：按需求文档 §2.1——阻塞通过向 `node_dependencies` 新增一个人工依赖项（权重 999）实现。状态机会因"前置条件满足度不足 1.0"而自动从 IN_PROGRESS 回退到 CONDITIONS_NOT_MET。不新增 BLOCKED 状态。
3. **进度用 float 计算、String 存储**：与 Phase 1-1 约定的 `progress VARCHAR` 列一致。Service 层内部用 float 计算，写入时转为字符串。
4. **递归重算上限 3 层**：祖先进度重算最多追溯 3 层，与节点嵌套深度约束一致。通过 BFS 而非递归调用防止栈溢出。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| 状态流转规则 | `emily-core/emily_core/services/plan_task_service.py` 中 `ALLOWED_TRANSITIONS` | `dict[str \| None, list[str]]` + 终态空列表 |
| Command DTO | `emily-core/emily_core/services/plan_task_commands.py` | `@dataclass` + 所有字段有默认值 + 类型注解 |
| Service 类 | `emily-core/emily_core/services/plan_task_service.py` 中 `PlanTaskService` | 构造函数接受可选 Repo 注入 + 所有方法 `async` + `asyncio.to_thread` 包裹 Repo |
| Application 类 | `emily-core/emily_core/application/plan_task_app.py` 中 `PlanTaskApplication` | `async def` 返回 `{"success": bool, "reply": str, ...}` |
| 异常类 | `emily-core/emily_core/repositories/plan_task_repo.py` 中 `InvalidStateTransitionError` | `class XxxError(ValueError):` |

---

## Phase 1-2: 状态机引擎 + 服务层

**前置检查**（必须全部通过才进入此阶段）：

```powershell
docker exec emily-core python -c "from emily_core.repositories.node_repo import ProjectNodeRepo, NodeDependencyRepo, NodeDeliverableRepo, NodeEventRepo; print('Phase 1-1 repos OK')"
```
→ 预期输出：`Phase 1-1 repos OK`

**交付物**：可计算三态流转的状态机引擎 + 可调用的 Service 层 API（创建节点、更新成果、触发流转、检测循环依赖）

---

### Step 2.1: 创建 Command DTO 模块

**目标**：定义服务层所有公共方法的入参数据结构。

**操作**：

1. 新建文件 `emily-core/emily_core/services/node_commands.py`
2. 写入以下内容：

```python
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
```

**验证**：

```powershell
docker exec emily-core python -c "from emily_core.services.node_commands import CreateNodeCommand, CreateDeliverableCommand, AddDependencyCommand, NodeOperationResult, CycleCheckResult, StateTransitionResult; print('Commands imported OK')"
```
→ 预期输出：`Commands imported OK`

---

### Step 2.2: 创建状态机引擎模块

**目标**：实现纯计算的状态流转引擎（无数据库依赖），负责前置条件满足度计算、成果完成度计算、状态判定。

**操作**：

1. 新建文件 `emily-core/emily_core/services/node_state_machine.py`
2. 写入以下内容：

```python
"""全景节点图 V2 状态机引擎 —— 纯计算逻辑，不依赖数据库。

职责：
  - 前置条件满足度计算（文件级依赖：Σ(文件就绪 × 权重)）
  - 成果完成度计算（Σ(必需成果完成度) / 必需成果总数）
  - 三态流转判定（CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED）
  - 父子进度加权汇总
  - 循环依赖检测（BFS）

基于需求文档 §2.1（三态模型）和 §4.1（状态自动流转计算）。
"""

from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger("emily.node_state_machine")


# ══════════════════════════════════════════════════════════════════════════════
# 状态常量
# ══════════════════════════════════════════════════════════════════════════════

CONDITIONS_NOT_MET = "CONDITIONS_NOT_MET"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"

VALID_STATUSES = frozenset({CONDITIONS_NOT_MET, IN_PROGRESS, COMPLETED})

# 三态流转规则（仅两跳合法）
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    CONDITIONS_NOT_MET: frozenset({IN_PROGRESS}),
    IN_PROGRESS: frozenset({COMPLETED, CONDITIONS_NOT_MET}),  # 阻塞时回退
    COMPLETED: frozenset(),  # 终态
}


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构（引擎用简单类型，不依赖 ORM）
# ══════════════════════════════════════════════════════════════════════════════

class NodeSnapshot:
    """节点快照 —— 引擎计算的输入单元。"""
    __slots__ = ("node_id", "status", "progress", "parent_node_id",
                 "dependencies", "deliverables", "children")

    def __init__(self, node_id: str, status: str = CONDITIONS_NOT_MET,
                 progress: float = 0.0, parent_node_id: str = ""):
        self.node_id = node_id
        self.status = status
        self.progress = progress
        self.parent_node_id = parent_node_id
        self.dependencies: list[DependencySnapshot] = []
        self.deliverables: list[DeliverableSnapshot] = []
        self.children: list[ChildSnapshot] = []


class DependencySnapshot:
    """依赖快照。"""
    __slots__ = ("depends_on_deliverable_id", "depends_on_node_id", "weight", "dependency_type")
    def __init__(self, depends_on_deliverable_id: str, depends_on_node_id: str,
                 weight: float = 1.0, dependency_type: str = "DELIVERABLE"):
        self.depends_on_deliverable_id = depends_on_deliverable_id
        self.depends_on_node_id = depends_on_node_id
        self.weight = weight
        self.dependency_type = dependency_type


class DeliverableSnapshot:
    """成果快照。"""
    __slots__ = ("deliverable_id", "target_amount", "current_amount", "is_required", "file_id")
    def __init__(self, deliverable_id: str, target_amount: float, current_amount: float,
                 is_required: bool = True, file_id: str = ""):
        self.deliverable_id = deliverable_id
        self.target_amount = target_amount
        self.current_amount = current_amount
        self.is_required = is_required
        self.file_id = file_id


class ChildSnapshot:
    """子节点快照（仅含状态和进度，不展开嵌套）。"""
    __slots__ = ("node_id", "status", "progress", "child_weight")
    def __init__(self, node_id: str, status: str, progress: float, child_weight: float = 1.0):
        self.node_id = node_id
        self.status = status
        self.progress = progress
        self.child_weight = child_weight


# ══════════════════════════════════════════════════════════════════════════════
# 核心计算函数
# ══════════════════════════════════════════════════════════════════════════════

def calc_dependency_satisfaction(dependencies: list[DependencySnapshot],
                                  deliverable_file_status: dict[str, bool]) -> float:
    """计算前置条件满足度。

    公式：Σ(依赖的成果文件是否已上传完成 × 权重)

    Args:
        dependencies: 节点的前置依赖列表
        deliverable_file_status: {deliverable_id: is_file_uploaded_complete}

    Returns:
        满足度 (0.0 ~ ∞)，>= 1.0 表示满足
    """
    if not dependencies:
        return 1.0  # 无依赖 = 视为满足

    total = 0.0
    for dep in dependencies:
        file_ready = deliverable_file_status.get(dep.depends_on_deliverable_id, False)
        if file_ready:
            total += dep.weight
    return total


def calc_deliverable_completion(deliverables: list[DeliverableSnapshot]) -> float:
    """计算整体成果完成度。

    单个成果完成度 = min(current_amount / max(target_amount, 0.001), 1.0)
    整体成果完成度 = Σ(必需成果的完成度) / max(必需成果总数, 1)

    Returns:
        完成度 (0.0-1.0)
    """
    required = [d for d in deliverables if d.is_required]
    if not required:
        return 1.0  # 无必需成果 = 视为已完成

    total_ratio = 0.0
    for d in required:
        target = max(d.target_amount, 0.001)
        current = min(d.current_amount, target)
        total_ratio += current / target

    return total_ratio / len(required)


def determine_node_status(dependencies: list[DependencySnapshot],
                          deliverables: list[DeliverableSnapshot],
                          deliverable_file_status: dict[str, bool],
                          children: list[ChildSnapshot]) -> str:
    """判定节点应处于的状态。

    规则：
    - 无子节点：由自身前置条件 + 成果完成度决定
    - 有子节点：由所有子节点集体决定
      - 所有子节点 CONDITIONS_NOT_MET → CONDITIONS_NOT_MET
      - 至少一个子节点 IN_PROGRESS → IN_PROGRESS
      - 所有必需子节点 COMPLETED → COMPLETED
    """
    if children:
        # 父节点模式：由子节点集体决定
        return _determine_parent_status(children)

    # 普通节点模式
    dep_satisfaction = calc_dependency_satisfaction(dependencies, deliverable_file_status)
    if dep_satisfaction < 1.0:
        return CONDITIONS_NOT_MET

    completion = calc_deliverable_completion(deliverables)
    if completion >= 1.0:
        return COMPLETED

    return IN_PROGRESS


def _determine_parent_status(children: list[ChildSnapshot]) -> str:
    """根据子节点集体状态判定父节点状态。"""
    if not children:
        return CONDITIONS_NOT_MET

    statuses = [c.status for c in children]

    if all(s == CONDITIONS_NOT_MET for s in statuses):
        return CONDITIONS_NOT_MET

    if all(s == COMPLETED for s in statuses):
        return COMPLETED

    return IN_PROGRESS


def calc_parent_progress(children: list[ChildSnapshot]) -> float:
    """计算父节点进度（子节点加权平均）。

    公式：Σ(子节点进度 × 子节点权重) / max(Σ(子节点权重), 0.0001)
    """
    if not children:
        return 0.0

    weighted_sum = sum(c.progress * c.child_weight for c in children)
    total_weight = max(sum(c.child_weight for c in children), 0.0001)

    return min(weighted_sum / total_weight, 100.0)


# ══════════════════════════════════════════════════════════════════════════════
# 循环依赖检测
# ══════════════════════════════════════════════════════════════════════════════

def detect_cycle(node_id: str, depends_on_deliverable_id: str,
                 deliverable_to_node: dict[str, str],
                 node_dependencies: dict[str, list[str]],
                 max_depth: int = 100) -> tuple[bool, list[str]]:
    """BFS 检测添加依赖是否会导致循环。

    从依赖的上游节点出发，BFS 遍历其下游是否包含 node_id 自身。
    如果包含，说明添加此依赖会形成循环。

    Args:
        node_id: 要添加依赖的下游节点
        depends_on_deliverable_id: 要依赖的成果ID
        deliverable_to_node: {deliverable_id: node_id} 成果→节点映射
        node_dependencies: {node_id: [depends_on_node_id]} 节点→其依赖的上游节点列表
        max_depth: 最大搜索深度

    Returns:
        (has_cycle: bool, cycle_path: list[node_id])
    """
    # 找到成果所属的上游节点
    upstream_node = deliverable_to_node.get(depends_on_deliverable_id)
    if upstream_node is None:
        return False, []

    # 特殊情况：上游节点就是自己 → 自依赖 → 循环
    if upstream_node == node_id:
        return True, [node_id, node_id]

    # 特殊情况：上游节点是 node_id 的祖先（父子层级）
    # 不在引擎层检测，由 Service 层额外校验

    # BFS：从 node_id 出发，沿依赖链向下游追踪
    # 如果追踪到 upstream_node，则 node_id → ... → upstream_node → node_id 形成环
    visited = {node_id}
    queue = deque([node_id])
    parent_map: dict[str, str | None] = {node_id: None}

    for _ in range(max_depth):
        if not queue:
            break
        current = queue.popleft()
        # 获取当前节点的下游节点（即哪些节点依赖了 current 的成果）
        downstream_nodes = node_dependencies.get(current, [])
        for downstream in downstream_nodes:
            if downstream == node_id:
                continue  # 跳过自己
            if downstream == upstream_node:
                # 找到循环：node_id → ... → downstream(=upstream_node) → node_id
                path = _reconstruct_path(parent_map, current, node_id, upstream_node)
                return True, path
            if downstream not in visited:
                visited.add(downstream)
                parent_map[downstream] = current
                queue.append(downstream)

    return False, []


def _reconstruct_path(parent_map: dict[str, str | None], end_node: str,
                      start_node: str, upstream_node: str) -> list[str]:
    """重建循环路径。"""
    path = []
    current = end_node
    while current is not None and current != start_node:
        path.append(current)
        current = parent_map.get(current)
    path.append(start_node)
    path.reverse()
    path.append(upstream_node)  # 补上环的闭合
    return path


def check_parent_child_cycle(parent_node_id: str, child_node_id: str,
                              parent_child_map: dict[str, str]) -> bool:
    """检查父子关系不会形成循环。

    子节点不能是父节点的祖先（任何层级）。

    Args:
        parent_node_id: 新父节点
        child_node_id: 子节点
        parent_child_map: {node_id: parent_node_id} 所有节点的父子映射

    Returns:
        True 如果会形成循环
    """
    # 向上追溯 child_node_id 的祖先链
    current = parent_node_id
    visited = set()
    while current:
        if current == child_node_id:
            return True  # parent_node_id 是 child_node_id 的后代 → 循环
        if current in visited:
            return True  # 已存在循环
        visited.add(current)
        current = parent_child_map.get(current, "")
    return False
```

**验证**：

```powershell
# 验证导入 + 单元测试
docker exec emily-core python -c "
from emily_core.services.node_state_machine import (
    CONDITIONS_NOT_MET, IN_PROGRESS, COMPLETED,
    NodeSnapshot, DependencySnapshot, DeliverableSnapshot, ChildSnapshot,
    calc_dependency_satisfaction, calc_deliverable_completion,
    determine_node_status, calc_parent_progress,
    detect_cycle, check_parent_child_cycle,
)

# 测试1：无依赖无成果 → 条件不足（默认）
node = NodeSnapshot('TEST-001')
status = determine_node_status([], [], {}, [])
assert status == CONDITIONS_NOT_MET, f'T1 failed: {status}'
print('[OK] T1: empty node -> CONDITIONS_NOT_MET')

# 测试2：无依赖 + 必需成果100%完成 → COMPLETED
deps = []
delivs = [DeliverableSnapshot('D1', target_amount=10.0, current_amount=10.0, is_required=True)]
status = determine_node_status(deps, delivs, {}, [])
assert status == COMPLETED, f'T2 failed: {status}'
print('[OK] T2: deliverables complete -> COMPLETED')

# 测试3：前置文件上传 → IN_PROGRESS
deps = [DependencySnapshot('D1', 'UPSTREAM-001', weight=1.0)]
delivs = [DeliverableSnapshot('D2', target_amount=1.0, current_amount=0.5, is_required=True)]
status = determine_node_status(deps, delivs, {'D1': True}, [])  # 文件已上传
assert status == IN_PROGRESS, f'T3 failed: {status}'
print('[OK] T3: file ready + partial deliverable -> IN_PROGRESS')

# 测试4：阻塞场景——权重999的依赖未满足 → CONDITIONS_NOT_MET
blocking_deps = [DependencySnapshot('D_BLOCK', 'UPSTREAM-001', weight=999.0)]
status = determine_node_status(blocking_deps, delivs, {'D_BLOCK': False}, [])
assert status == CONDITIONS_NOT_MET, f'T4 failed: {status}'
print('[OK] T4: blocking dependency -> CONDITIONS_NOT_MET (back from IN_PROGRESS)')

# 测试5：父子进度汇总
children = [
    ChildSnapshot('C1', COMPLETED, progress=100.0, child_weight=0.4),
    ChildSnapshot('C2', IN_PROGRESS, progress=50.0, child_weight=0.3),
    ChildSnapshot('C3', CONDITIONS_NOT_MET, progress=0.0, child_weight=0.3),
]
parent_progress = calc_parent_progress(children)
expected = (100*0.4 + 50*0.3 + 0*0.3) / 1.0  # = 55.0
assert abs(parent_progress - expected) < 0.01, f'T5 failed: {parent_progress} != {expected}'
parent_status = determine_node_status([], [], {}, children)
assert parent_status == IN_PROGRESS, f'T5 status failed: {parent_status}'
print(f'[OK] T5: parent progress={parent_progress}, status={parent_status}')

# 测试6：循环依赖检测
deliverable_to_node = {'D1': 'NODE-A', 'D2': 'NODE-B', 'D3': 'NODE-C'}
node_deps = {
    'NODE-A': ['NODE-B'],  # A 依赖 B 的成果
    'NODE-B': ['NODE-C'],  # B 依赖 C 的成果
}
# 添加 C 依赖 A → 应检测到循环 NODE-C → NODE-A → NODE-B → NODE-C
has_cycle, path = detect_cycle('NODE-C', 'D1', deliverable_to_node, node_deps)
assert has_cycle, f'T6 failed: should have cycle'
print(f'[OK] T6: cycle detected path={path}')

# 测试7：无循环的情况
has_cycle, path = detect_cycle('NODE-D', 'D1', deliverable_to_node, {})
assert not has_cycle, f'T7 failed: should not have cycle'
print('[OK] T7: no false cycle detection')

print('=== 状态机引擎全部测试通过 ===')
"
```
→ 预期输出：7 行 `[OK]` + `=== 状态机引擎全部测试通过 ===`

**失败处理**：如果断言失败，检查引擎计算逻辑。如果 import 失败，检查模块文件路径和类名。

---

### Step 2.3: 创建 NodeStateMachineService（服务层核心）

**目标**：实现服务层，协调 Repository 调用 + 状态机引擎计算 + 事件记录。这是 Phase 1-2 的核心产出。

**操作**：

1. 新建文件 `emily-core/emily_core/services/node_service.py`
2. 写入以下内容：

```python
"""全景节点图 V2 Service 层 —— 核心业务逻辑。

职责：
  - 节点/成果/依赖的 CRUD 编排
  - 调用状态机引擎 + 写入 DB
  - 循环依赖检测前置（BFS）
  - 父子进度重算（递归 ≤3 层）
  - 事件记录（状态流转、操作审计）

基于需求文档 §4.1–§4.5。参照模式：plan_task_service.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .node_commands import (
    CreateNodeCommand,
    UpdateNodeCommand,
    DiscardNodeCommand,
    CreateDeliverableCommand,
    UpdateDeliverableProgressCommand,
    AddDependencyCommand,
    RemoveDependencyCommand,
    MountChildCommand,
    UnmountChildCommand,
    NodeOperationResult,
    CycleCheckResult,
    StateTransitionResult,
)
from .node_state_machine import (
    CONDITIONS_NOT_MET,
    IN_PROGRESS,
    COMPLETED,
    NodeSnapshot,
    DependencySnapshot,
    DeliverableSnapshot,
    ChildSnapshot,
    calc_dependency_satisfaction,
    calc_deliverable_completion,
    determine_node_status,
    calc_parent_progress,
    detect_cycle,
    check_parent_child_cycle,
)
from ..repositories.node_repo import (
    ProjectNodeRepo,
    NodeDependencyRepo,
    NodeDeliverableRepo,
    NodeEventRepo,
    _parse_decimal,
    _to_decimal_str,
)
from ..infrastructure.database.models import _new_id

if TYPE_CHECKING:
    from ..infrastructure.database.models import (
        ProjectNode,
        NodeDependency,
        NodeDeliverable,
    )

logger = logging.getLogger("emily.node_service")

BEIJING_TZ = timezone(timedelta(hours=8))

# 子节点数量上限
MAX_CHILDREN_PER_PARENT = 100
# 最大递归深度
MAX_ANCESTOR_RECALC_DEPTH = 3


# ══════════════════════════════════════════════════════════════════════════════
# NodeService
# ══════════════════════════════════════════════════════════════════════════════

class NodeService:
    """全景节点图核心业务 Service。"""

    def __init__(
        self,
        node_repo: ProjectNodeRepo | None = None,
        dependency_repo: NodeDependencyRepo | None = None,
        deliverable_repo: NodeDeliverableRepo | None = None,
        event_repo: NodeEventRepo | None = None,
    ):
        self._node_repo = node_repo or ProjectNodeRepo()
        self._dep_repo = dependency_repo or NodeDependencyRepo()
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()
        self._event_repo = event_repo or NodeEventRepo()

    # ── 辅助方法 ──

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _record_event(self, node_id: str, event_type: str,
                      old_value: str = "", new_value: str = "",
                      operator_id: str = "", remark: str = "") -> None:
        """记录事件（同步写入，fire-and-forget）。"""
        try:
            self._event_repo.create(
                event_id=_new_id("EVT"),
                node_id=node_id,
                event_type=event_type,
                old_value=old_value,
                new_value=new_value,
                operator_id=operator_id,
                remark=remark,
            )
        except Exception:
            logger.exception("Failed to record event for node %s", node_id)

    # ── 节点 CRUD ──

    async def create_node(self, cmd: CreateNodeCommand) -> NodeOperationResult:
        """创建节点。"""
        child_weight_str = _to_decimal_str(cmd.child_weight, precision=4)

        node = await asyncio.to_thread(
            self._node_repo.create,
            project_id=cmd.project_id,
            node_id=cmd.node_id,
            node_name=cmd.node_name,
            owner_dept_id=cmd.owner_dept_id,
            related_company_id=cmd.related_company_id,
            deadline=cmd.deadline,
            creator_id=cmd.creator_id,
            parent_node_id=cmd.parent_node_id,
            stage_id=cmd.stage_id,
            child_weight=child_weight_str,
            remark=cmd.remark,
            land_parcel_id=cmd.land_parcel_id,
            startup_doc_id=cmd.startup_doc_id,
            sort_order=cmd.sort_order,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_created",
            new_value=json.dumps({"node_name": cmd.node_name, "project_id": cmd.project_id}),
            operator_id=cmd.creator_id,
            remark="节点创建",
        )

        logger.info("Node created: %s (project=%s)", cmd.node_id, cmd.project_id)
        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            message=f"节点「{cmd.node_name}」创建成功",
        )

    async def update_node(self, cmd: UpdateNodeCommand) -> NodeOperationResult:
        """更新节点字段。"""
        updates = {}
        if cmd.node_name is not None:
            updates["node_name"] = cmd.node_name
        if cmd.deadline is not None:
            updates["deadline"] = cmd.deadline
        if cmd.owner_dept_id is not None:
            updates["owner_dept_id"] = cmd.owner_dept_id
        if cmd.related_company_id is not None:
            updates["related_company_id"] = cmd.related_company_id
        if cmd.remark is not None:
            updates["remark"] = cmd.remark
        if cmd.stage_id is not None:
            updates["stage_id"] = cmd.stage_id
        if cmd.sort_order is not None:
            updates["sort_order"] = cmd.sort_order
        if cmd.land_parcel_id is not None:
            updates["land_parcel_id"] = cmd.land_parcel_id
        if cmd.startup_doc_id is not None:
            updates["startup_doc_id"] = cmd.startup_doc_id

        if not updates:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="无更新字段")

        old_node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if old_node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        node = await asyncio.to_thread(self._node_repo.update_fields, cmd.node_id, **updates)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="更新失败")

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_updated",
            old_value=json.dumps({"node_name": old_node.node_name, "deadline": old_node.deadline}),
            new_value=json.dumps(updates),
            operator_id=cmd.operator_id,
            remark="节点字段更新",
        )

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            message="节点更新成功",
        )

    async def discard_node(self, cmd: DiscardNodeCommand) -> NodeOperationResult:
        """废弃节点。已完成或未完成的子节点存在时阻止。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        # 检查子节点：已完成的不可废弃
        children = await asyncio.to_thread(self._node_repo.find_by_parent, cmd.node_id)
        for child in children:
            if child.status == COMPLETED:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"子节点「{child.node_id}」已完成，不可废弃父节点",
                )

        await asyncio.to_thread(self._node_repo.discard, cmd.node_id)

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_discarded",
            old_value=json.dumps({"status": node.status}),
            operator_id=cmd.operator_id,
            remark="节点废弃",
        )

        return NodeOperationResult(success=True, node_id=cmd.node_id, message="节点已废弃")

    # ── 成果管理 ──

    async def create_deliverable(self, cmd: CreateDeliverableCommand) -> NodeOperationResult:
        """为节点新增成果。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        seq = await asyncio.to_thread(self._deliv_repo.get_next_seq, cmd.node_id)
        deliverable_id = self._deliv_repo.generate_deliverable_id(cmd.node_id, seq)

        await asyncio.to_thread(
            self._deliv_repo.create,
            deliverable_id=deliverable_id,
            node_id=cmd.node_id,
            deliverable_name=cmd.deliverable_name,
            target_amount=_to_decimal_str(cmd.target_amount, precision=2),
            unit=cmd.unit,
            is_required=cmd.is_required,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="deliverable_updated",
            new_value=json.dumps({"deliverable_id": deliverable_id, "name": cmd.deliverable_name}),
            operator_id=cmd.operator_id,
            remark=f"新增成果：{cmd.deliverable_name}",
        )

        # 新增成果可能改变完成度，触发状态重算
        await self._recalc_node_status(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message=f"成果「{cmd.deliverable_name}」创建成功",
        )

    async def update_deliverable_progress(self, cmd: UpdateDeliverableProgressCommand) -> NodeOperationResult:
        """更新成果进度——核心入口，触发状态流转。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(
                success=False, node_id="",
                message=f"成果 {cmd.deliverable_id} 不存在",
            )

        old_amount = deliv.current_amount
        amount_str = _to_decimal_str(cmd.current_amount, precision=2)

        await asyncio.to_thread(
            self._deliv_repo.update_progress,
            cmd.deliverable_id,
            amount_str,
            cmd.file_id,
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_updated",
            old_value=json.dumps({"current_amount": old_amount}),
            new_value=json.dumps({"current_amount": amount_str, "file_id": cmd.file_id}),
            operator_id=cmd.operator_id,
            remark=f"成果进度更新：{old_amount} → {amount_str}",
        )

        # 关键：成果进度更新 → 触发状态重算
        result = await self._recalc_node_status(deliv.node_id)
        return result

    # ── 依赖管理 ──

    async def add_dependency(self, cmd: AddDependencyCommand) -> NodeOperationResult:
        """添加依赖——含循环检测前置。"""
        # 1. 查上游成果所属节点
        dep_deliv = await asyncio.to_thread(
            self._deliv_repo.get_by_deliverable_id, cmd.depends_on_deliverable_id,
        )
        if dep_deliv is None:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"成果 {cmd.depends_on_deliverable_id} 不存在",
            )

        upstream_node_id = dep_deliv.node_id

        # 2. 禁止自己依赖自己
        if upstream_node_id == cmd.node_id:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="节点不能依赖自己的成果",
                error_code="40001",
            )

        # 3. 禁止子节点依赖父节点（任何层级）
        if await self._is_ancestor_dependency(cmd.node_id, upstream_node_id):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="子节点不能依赖父节点（任何层级）",
                error_code="40001",
            )

        # 4. BFS 循环检测
        cycle_result = await self._check_cycle(cmd.node_id, cmd.depends_on_deliverable_id)
        if cycle_result.has_cycle:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"循环依赖：{' → '.join(cycle_result.cycle_path)}",
                error_code="40001",
            )

        # 5. 检查重复
        if await asyncio.to_thread(
            self._dep_repo.exists, cmd.node_id, cmd.depends_on_deliverable_id,
        ):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="该依赖关系已存在",
            )

        # 6. 创建依赖
        weight_str = _to_decimal_str(cmd.weight, precision=4)
        await asyncio.to_thread(
            self._dep_repo.create,
            node_id=cmd.node_id,
            depends_on_deliverable_id=cmd.depends_on_deliverable_id,
            depends_on_node_id=upstream_node_id,
            weight=weight_str,
            dependency_type=cmd.dependency_type,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="dependency_added",
            new_value=json.dumps({
                "depends_on_deliverable_id": cmd.depends_on_deliverable_id,
                "depends_on_node_id": upstream_node_id,
                "weight": weight_str,
            }),
            operator_id=cmd.operator_id,
            remark=f"新增依赖：{cmd.depends_on_deliverable_id} (权重{weight_str})",
        )

        if cmd.weight >= 999.0:
            self._record_event(
                node_id=cmd.node_id,
                event_type="BLOCKING_CONDITION_ADDED",
                new_value=json.dumps({"deliverable_id": cmd.depends_on_deliverable_id}),
                operator_id=cmd.operator_id,
                remark="人工阻塞条件",
            )

        # 7. 依赖变更 → 重新计算状态
        await self._recalc_node_status(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message="依赖添加成功",
        )

    async def remove_dependency(self, cmd: RemoveDependencyCommand) -> NodeOperationResult:
        """移除依赖。"""
        dep = await asyncio.to_thread(self._dep_repo.get_by_id, cmd.dependency_id)
        if dep is None:
            return NodeOperationResult(success=False, message="依赖不存在")

        node_id = dep.node_id
        is_blocking = _parse_decimal(dep.weight) >= 999.0

        await asyncio.to_thread(self._dep_repo.delete, cmd.dependency_id)

        self._record_event(
            node_id=node_id,
            event_type="dependency_removed",
            old_value=json.dumps({"depends_on_deliverable_id": dep.depends_on_deliverable_id}),
            operator_id=cmd.operator_id,
            remark="移除依赖",
        )

        if is_blocking:
            self._record_event(
                node_id=node_id,
                event_type="BLOCKING_CONDITION_REMOVED",
                operator_id=cmd.operator_id,
                remark="解除阻塞条件",
            )

        # 依赖移除 → 重新计算状态
        await self._recalc_node_status(node_id)

        return NodeOperationResult(success=True, node_id=node_id, message="依赖已移除")

    # ── 子节点管理 ──

    async def mount_child(self, cmd: MountChildCommand) -> NodeOperationResult:
        """挂载子节点。"""
        # 1. 数量上限检查
        count = await asyncio.to_thread(self._node_repo.count_children, cmd.parent_node_id)
        if count >= MAX_CHILDREN_PER_PARENT:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message=f"子节点数量已达上限（{MAX_CHILDREN_PER_PARENT}）",
                error_code="40002",
            )

        # 2. 深度检查：追溯到根，最多2级（挂载后最多3级）
        ancestors = await asyncio.to_thread(
            self._node_repo.get_ancestor_chain, cmd.parent_node_id, max_depth=2,
        )
        if len(ancestors) >= 2:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message="嵌套深度已达上限（3层），无法继续挂载子节点",
            )

        # 3. 循环检查：parent 不能是 child 的后代
        all_parents = {cmd.parent_node_id}
        for a in ancestors:
            all_parents.add(a.node_id)
        child_ancestors = await asyncio.to_thread(
            self._node_repo.get_ancestor_chain, cmd.child_node_id, max_depth=3,
        )
        for ca in child_ancestors:
            if ca.node_id in all_parents:
                return NodeOperationResult(
                    success=False, node_id=cmd.parent_node_id,
                    message="不能将祖先节点挂载为子节点",
                    error_code="40001",
                )

        # 4. 更新子节点
        weight_str = _to_decimal_str(cmd.child_weight, precision=4)
        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.child_node_id,
            parent_node_id=cmd.parent_node_id,
            child_weight=weight_str,
        )

        self._record_event(
            node_id=cmd.child_node_id,
            event_type="child_node_mounted",
            new_value=json.dumps({"parent_node_id": cmd.parent_node_id}),
            operator_id=cmd.operator_id,
            remark=f"挂载到父节点 {cmd.parent_node_id}",
        )

        # 更新父节点进度
        await self._recalc_node_status(cmd.parent_node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.child_node_id,
            message=f"子节点已挂载到 {cmd.parent_node_id}",
        )

    async def unmount_child(self, cmd: UnmountChildCommand) -> NodeOperationResult:
        """移除子节点关联。"""
        child = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.child_node_id)
        if child is None or child.parent_node_id != cmd.parent_node_id:
            return NodeOperationResult(
                success=False, node_id=cmd.child_node_id,
                message="父子关系不匹配",
            )

        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.child_node_id,
            parent_node_id="",
            child_weight="1.0000",
        )

        self._record_event(
            node_id=cmd.child_node_id,
            event_type="child_node_unmounted",
            old_value=json.dumps({"parent_node_id": cmd.parent_node_id}),
            operator_id=cmd.operator_id,
            remark=f"从父节点 {cmd.parent_node_id} 移除",
        )

        await self._recalc_node_status(cmd.parent_node_id)

        return NodeOperationResult(
            success=True, node_id=cmd.child_node_id, message="子节点已移除",
        )

    # ── 查询方法 ──

    async def get_node_detail(self, node_id: str) -> dict | None:
        """查询节点详情（含子节点、成果、依赖）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        children = await asyncio.to_thread(self._node_repo.find_by_parent, node_id)
        delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, node_id)
        deps = await asyncio.to_thread(self._dep_repo.find_by_node, node_id)

        return {
            "node_id": node.node_id,
            "node_name": node.node_name,
            "project_id": node.project_id,
            "status": node.status,
            "progress": _parse_decimal(node.progress),
            "deadline": node.deadline,
            "owner_dept_id": node.owner_dept_id,
            "related_company_id": node.related_company_id,
            "parent_node_id": node.parent_node_id,
            "stage_id": node.stage_id,
            "remark": node.remark,
            "is_discarded": node.is_discarded,
            "created_at": node.created_at,
            "children": [
                {
                    "node_id": c.node_id,
                    "node_name": c.node_name,
                    "status": c.status,
                    "progress": _parse_decimal(c.progress),
                }
                for c in children
            ],
            "deliverables": [
                {
                    "deliverable_id": d.deliverable_id,
                    "deliverable_name": d.deliverable_name,
                    "target_amount": _parse_decimal(d.target_amount),
                    "current_amount": _parse_decimal(d.current_amount),
                    "unit": d.unit,
                    "is_required": d.is_required,
                    "file_id": d.file_id,
                    "completed_at": d.completed_at,
                }
                for d in delivs
            ],
            "dependencies": [
                {
                    "id": d.id,
                    "depends_on_deliverable_id": d.depends_on_deliverable_id,
                    "depends_on_node_id": d.depends_on_node_id,
                    "weight": _parse_decimal(d.weight),
                    "dependency_type": d.dependency_type,
                }
                for d in deps
            ],
        }

    # ── 状态重算核心 ──

    async def _recalc_node_status(self, node_id: str) -> NodeOperationResult:
        """重新计算节点状态（文件上传/成果更新/依赖变更时触发）。

        流程：
        1. 加载节点快照（含子节点、成果、依赖）
        2. 构建 deliverable_file_status 映射
        3. 调用引擎 determine_node_status
        4. 如有变更，写入 DB + 记录事件
        5. 递归更新祖先节点进度（最多 3 层）
        """
        snap = await self._build_snapshot(node_id)
        if snap is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")

        # 构建 deliverable_file_status
        file_status = {}
        for dep in snap.dependencies:
            # 检查依赖的成果文件是否已上传完成
            dep_deliv = await asyncio.to_thread(
                self._deliv_repo.get_by_deliverable_id, dep.depends_on_deliverable_id,
            )
            if dep_deliv:
                current = _parse_decimal(dep_deliv.current_amount)
                target = max(_parse_decimal(dep_deliv.target_amount), 0.001)
                file_status[dep.depends_on_deliverable_id] = (current >= target)

        old_status = snap.status
        old_progress = snap.progress

        # 调用引擎
        new_status = determine_node_status(
            snap.dependencies, snap.deliverables, file_status, snap.children,
        )

        # 进度计算
        if snap.children:
            new_progress = calc_parent_progress(snap.children)
        else:
            new_progress = calc_deliverable_completion(snap.deliverables) * 100.0

        # 检查是否需要更新
        status_changed = (new_status != old_status)
        progress_changed = abs(new_progress - old_progress) > 0.001

        if not status_changed and not progress_changed:
            return NodeOperationResult(
                success=True, node_id=node_id, status=old_status, progress=old_progress,
                message="状态无变化",
            )

        # 写入 DB
        await asyncio.to_thread(self._node_repo.update_progress, node_id, new_progress)
        if status_changed:
            await asyncio.to_thread(self._node_repo.update_status, node_id, new_status)

            # 记录状态变更事件
            transition_result = StateTransitionResult(
                node_id=node_id,
                old_status=old_status,
                new_status=new_status,
                old_progress=old_progress,
                new_progress=new_progress,
                should_transition=True,
                reason="自动流转",
            )
            self._record_event(
                node_id=node_id,
                event_type="status_changed",
                old_value=json.dumps({"status": old_status}),
                new_value=json.dumps({"status": new_status, "progress": new_progress}),
                remark="状态自动流转",
            )
            if new_status == COMPLETED:
                self._record_event(
                    node_id=node_id,
                    event_type="auto_triggered",
                    remark="节点已完成",
                )

        # 递归更新祖先（最多 3 层）
        affected = []
        current_id = node_id
        for depth in range(MAX_ANCESTOR_RECALC_DEPTH):
            current = await asyncio.to_thread(self._node_repo.get_by_node_id, current_id)
            if current is None or not current.parent_node_id:
                break
            parent_id = current.parent_node_id
            await self._recalc_parent_progress(parent_id)
            affected.append(parent_id)
            current_id = parent_id

        logger.info(
            "Node %s recalc: status %s->%s, progress %.2f->%.2f, ancestors=%s",
            node_id, old_status, new_status, old_progress, new_progress, affected,
        )

        return NodeOperationResult(
            success=True,
            node_id=node_id,
            status=new_status,
            progress=new_progress,
            message=f"状态重算完成：{old_status} → {new_status}",
            affected_downstream=affected,
        )

    async def _recalc_parent_progress(self, parent_node_id: str) -> None:
        """重算父节点进度（不递归，仅当前层）。"""
        children = await asyncio.to_thread(self._node_repo.find_by_parent, parent_node_id)
        if not children:
            return

        child_snapshots = [
            ChildSnapshot(
                node_id=c.node_id,
                status=c.status,
                progress=_parse_decimal(c.progress),
                child_weight=_parse_decimal(c.child_weight),
            )
            for c in children
        ]

        new_progress = calc_parent_progress(child_snapshots)
        new_status = determine_node_status([], [], {}, child_snapshots)

        node = await asyncio.to_thread(self._node_repo.get_by_node_id, parent_node_id)
        if node:
            old_status = node.status
            await asyncio.to_thread(self._node_repo.update_progress, parent_node_id, new_progress)
            if new_status != old_status:
                await asyncio.to_thread(self._node_repo.update_status, parent_node_id, new_status)

    async def _build_snapshot(self, node_id: str) -> NodeSnapshot | None:
        """构建节点快照（供引擎计算）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        snap = NodeSnapshot(
            node_id=node.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            parent_node_id=node.parent_node_id,
        )

        # 加载依赖
        deps = await asyncio.to_thread(self._dep_repo.find_by_node, node_id)
        snap.dependencies = [
            DependencySnapshot(
                depends_on_deliverable_id=d.depends_on_deliverable_id,
                depends_on_node_id=d.depends_on_node_id,
                weight=_parse_decimal(d.weight),
                dependency_type=d.dependency_type,
            )
            for d in deps
        ]

        # 加载成果
        delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, node_id)
        snap.deliverables = [
            DeliverableSnapshot(
                deliverable_id=d.deliverable_id,
                target_amount=_parse_decimal(d.target_amount),
                current_amount=_parse_decimal(d.current_amount),
                is_required=d.is_required,
                file_id=d.file_id,
            )
            for d in delivs
        ]

        # 加载子节点
        children = await asyncio.to_thread(self._node_repo.find_by_parent, node_id)
        snap.children = [
            ChildSnapshot(
                node_id=c.node_id,
                status=c.status,
                progress=_parse_decimal(c.progress),
                child_weight=_parse_decimal(c.child_weight),
            )
            for c in children
        ]

        return snap

    # ── 循环检测辅助 ──

    async def _check_cycle(self, node_id: str, depends_on_deliverable_id: str) -> CycleCheckResult:
        """BFS 循环依赖检测。"""
        # 构建依赖图
        deliverable_to_node: dict[str, str] = {}
        # 查询所有节点的依赖（简化实现——生产环境应加缓存）
        # 这里只构建必要的映射

        dep_deliv = await asyncio.to_thread(
            self._deliv_repo.get_by_deliverable_id, depends_on_deliverable_id,
        )
        if dep_deliv is None:
            return CycleCheckResult(has_cycle=False)

        upstream_node = dep_deliv.node_id
        if upstream_node == node_id:
            return CycleCheckResult(has_cycle=True, cycle_path=[node_id, node_id],
                                   message="节点不能依赖自己的成果")

        # 构建 deliverable → node 映射（项目范围内）
        node_obj = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node_obj is None:
            return CycleCheckResult(has_cycle=False)

        # 查询项目的所有节点和依赖
        all_nodes = await asyncio.to_thread(self._node_repo.find_by_project, node_obj.project_id)
        all_node_ids = [n.node_id for n in all_nodes]

        # 构建 {deliverable_id: node_id}
        for nid in all_node_ids:
            delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, nid)
            for d in delivs:
                deliverable_to_node[d.deliverable_id] = d.node_id

        # 构建 {node_id: [upstream_node_id]} （node 依赖了哪些上游节点的成果）
        node_deps: dict[str, list[str]] = {}
        for nid in all_node_ids:
            deps = await asyncio.to_thread(self._dep_repo.find_by_node, nid)
            upstream_ids = list(set(d.depends_on_node_id for d in deps))
            node_deps[nid] = upstream_ids

        has_cycle, path = detect_cycle(
            node_id, depends_on_deliverable_id, deliverable_to_node, node_deps,
        )

        return CycleCheckResult(
            has_cycle=has_cycle,
            cycle_path=path,
            message=" → ".join(path) if has_cycle else "",
        )

    async def _is_ancestor_dependency(self, node_id: str, upstream_node_id: str) -> bool:
        """检查 upstream_node_id 是否是 node_id 的祖先（父子层级）。"""
        ancestors = await asyncio.to_thread(self._node_repo.get_ancestor_chain, node_id, max_depth=3)
        for a in ancestors:
            if a.node_id == upstream_node_id:
                return True
        return False
```

**验证**：

```powershell
# 验证 NodeService 可 import + 创建节点端到端
docker exec emily-core python -c "
import asyncio
from emily_core.services.node_commands import CreateNodeCommand
from emily_core.services.node_service import NodeService

async def test():
    svc = NodeService()
    cmd = CreateNodeCommand(
        project_id='svc-test',
        node_id='SVC-001',
        node_name='Service测试节点',
        deadline='2026-12-31T18:00:00+08:00',
        creator_id='test-user',
        stage_id=1,
    )
    result = await svc.create_node(cmd)
    assert result.success, f'Create failed: {result.message}'
    print(f'[OK] Create: {result.message}, status={result.status}')

    # 查询详情
    detail = await svc.get_node_detail('SVC-001')
    assert detail is not None
    assert detail['status'] == 'CONDITIONS_NOT_MET'
    print(f'[OK] Detail: status={detail[\"status\"]}, progress={detail[\"progress\"]}')

    # 清理
    from emily_core.repositories.node_repo import ProjectNodeRepo
    ProjectNodeRepo().discard('SVC-001')
    print('[OK] Cleanup done')

asyncio.run(test())
print('=== NodeService 验证通过 ===')
"
```
→ 预期输出：`[OK] Create`, `[OK] Detail`, `[OK] Cleanup`, `=== NodeService 验证通过 ===`

**失败处理**：如果 assert 失败，检查 Service 方法逻辑。如果 ImportError，检查模块文件路径。

---

### Step 2.4: 创建 NodeApplication（薄编排层）

**目标**：Application 层编排 Service 调用，生成面向 API 层或工具层的统一响应格式。

**操作**：

1. 新建文件 `emily-core/emily_core/application/node_app.py`
2. 写入以下内容：

```python
"""全景节点图 V2 Application 层 —— 编排 Service 调用 + 生成回复。

参照模式：plan_task_app.py。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.node_service import NodeService
    from ..services.node_commands import (
        CreateNodeCommand,
        UpdateNodeCommand,
        DiscardNodeCommand,
        CreateDeliverableCommand,
        UpdateDeliverableProgressCommand,
        AddDependencyCommand,
        RemoveDependencyCommand,
        MountChildCommand,
        UnmountChildCommand,
    )

logger = logging.getLogger("emily.node_app")


class NodeApplication:
    """全景节点图 Application —— 编排 Service 调用并生成统一响应。"""

    def __init__(self, service: "NodeService"):
        self._service = service

    async def create_node(self, cmd: "CreateNodeCommand") -> dict:
        """创建节点。"""
        result = await self._service.create_node(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "status": result.status,
            "progress": result.progress,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def update_node(self, cmd: "UpdateNodeCommand") -> dict:
        """更新节点字段。"""
        result = await self._service.update_node(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "status": result.status,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def discard_node(self, cmd: "DiscardNodeCommand") -> dict:
        """废弃节点。"""
        result = await self._service.discard_node(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def create_deliverable(self, cmd: "CreateDeliverableCommand") -> dict:
        """新增成果。"""
        result = await self._service.create_deliverable(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def update_deliverable_progress(self, cmd: "UpdateDeliverableProgressCommand") -> dict:
        """更新成果进度——触发状态流转。"""
        result = await self._service.update_deliverable_progress(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "status": result.status,
            "progress": result.progress,
            "reply": result.message,
            "affected_ancestors": result.affected_downstream,
            "error_code": result.error_code,
        }

    async def add_dependency(self, cmd: "AddDependencyCommand") -> dict:
        """添加依赖——含循环检测。"""
        result = await self._service.add_dependency(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def remove_dependency(self, cmd: "RemoveDependencyCommand") -> dict:
        """移除依赖。"""
        result = await self._service.remove_dependency(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def mount_child(self, cmd: "MountChildCommand") -> dict:
        """挂载子节点。"""
        result = await self._service.mount_child(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def unmount_child(self, cmd: "UnmountChildCommand") -> dict:
        """移除子节点。"""
        result = await self._service.unmount_child(cmd)
        return {
            "success": result.success,
            "node_id": result.node_id,
            "reply": result.message,
            "error_code": result.error_code,
        }

    async def get_node_detail(self, node_id: str) -> dict:
        """查询节点详情。"""
        detail = await self._service.get_node_detail(node_id)
        if detail is None:
            return {"success": False, "reply": f"节点 {node_id} 不存在"}
        return {"success": True, "data": detail, "reply": f"节点「{detail['node_name']}」查询成功"}
```

**验证**：

```powershell
docker exec emily-core python -c "from emily_core.application.node_app import NodeApplication; print('NodeApplication import OK')"
```
→ 预期输出：`NodeApplication import OK`

---

### Phase 1-2 最终验证

完成本阶段所有步骤后，运行端到端验证（状态机全链路）：

```powershell
docker exec emily-core python -c "
import asyncio
import json
from emily_core.services.node_commands import (
    CreateNodeCommand, CreateDeliverableCommand,
    UpdateDeliverableProgressCommand, AddDependencyCommand,
    MountChildCommand,
)
from emily_core.services.node_service import NodeService
from emily_core.repositories.node_repo import (
    ProjectNodeRepo, NodeDependencyRepo, NodeDeliverableRepo, NodeEventRepo,
)
from emily_core.infrastructure.database.session import get_session
from emily_core.infrastructure.database.models import (
    ProjectNode, NodeDependency, NodeDeliverable, NodeAccessibleFile, NodeEvent,
)

async def e2e():
    svc = NodeService()

    # ── 场景：两个节点，B依赖A的成果 ──
    # 1. 创建上游节点 A
    r1 = await svc.create_node(CreateNodeCommand(
        project_id='e2e-sm', node_id='E2E-A', node_name='上游节点A',
        deadline='2026-12-31T18:00:00+08:00', creator_id='u1', stage_id=1,
    ))
    assert r1.success, f'A create failed: {r1.message}'
    assert r1.status == 'CONDITIONS_NOT_MET'
    print(f'[OK] A created, status={r1.status}')

    # 2. 为A创建成果
    r2 = await svc.create_deliverable(CreateDeliverableCommand(
        node_id='E2E-A', deliverable_name='成果A1', target_amount=1.0, unit='份',
    ))
    assert r2.success
    print(f'[OK] A deliverable created: {r2.message}')

    # A 没有依赖 → 成果进度 0 → 仍为 CONDITIONS_NOT_MET (成果未完成)
    detail_a = await svc.get_node_detail('E2E-A')
    # 没有依赖 = 前置满足，但成果0% = 还在进行中
    print(f'[CHECK] A status after deliverable add: {detail_a[\"status\"]}')

    # 3. 更新A的成果进度到100%
    deliv_a = detail_a['deliverables'][0]
    r3 = await svc.update_deliverable_progress(UpdateDeliverableProgressCommand(
        deliverable_id=deliv_a['deliverable_id'], current_amount=1.0,
    ))
    assert r3.success
    detail_a2 = await svc.get_node_detail('E2E-A')
    print(f'[OK] A after 100% deliverable: status={detail_a2[\"status\"]}, progress={detail_a2[\"progress\"]}')

    # 4. 创建下游节点 B
    r4 = await svc.create_node(CreateNodeCommand(
        project_id='e2e-sm', node_id='E2E-B', node_name='下游节点B',
        deadline='2026-12-31T18:00:00+08:00', creator_id='u1', stage_id=1,
    ))
    assert r4.success
    print(f'[OK] B created, status={r4.status}')

    # 5. B添加依赖 → 指向A的成果
    r5 = await svc.add_dependency(AddDependencyCommand(
        node_id='E2E-B',
        depends_on_deliverable_id=deliv_a['deliverable_id'],
        weight=1.0,
    ))
    assert r5.success
    print(f'[OK] Dependency added: {r5.message}')

    # B 依赖的A成果已完成 → B应从 CONDITIONS_NOT_MET → IN_PROGRESS
    detail_b = await svc.get_node_detail('E2E-B')
    print(f'[OK] B after dependency: status={detail_b[\"status\"]}')

    # ── 场景：父子节点 ──
    # 6. 创建父节点 + 2个子节点
    r6 = await svc.create_node(CreateNodeCommand(
        project_id='e2e-sm', node_id='E2E-PARENT', node_name='父节点',
        deadline='2026-12-31T18:00:00+08:00', creator_id='u1', stage_id=1,
    ))
    assert r6.success

    r7 = await svc.create_node(CreateNodeCommand(
        project_id='e2e-sm', node_id='E2E-CHILD1', node_name='子节点1',
        deadline='2026-12-31T18:00:00+08:00', creator_id='u1', stage_id=1,
    ))
    r8 = await svc.create_node(CreateNodeCommand(
        project_id='e2e-sm', node_id='E2E-CHILD2', node_name='子节点2',
        deadline='2026-12-31T18:00:00+08:00', creator_id='u1', stage_id=1,
    ))

    # 7. 挂载子节点
    r9 = await svc.mount_child(MountChildCommand(
        parent_node_id='E2E-PARENT', child_node_id='E2E-CHILD1', child_weight=0.5,
    ))
    r10 = await svc.mount_child(MountChildCommand(
        parent_node_id='E2E-PARENT', child_node_id='E2E-CHILD2', child_weight=0.5,
    ))
    parent_detail = await svc.get_node_detail('E2E-PARENT')
    print(f'[OK] Parent with children: status={parent_detail[\"status\"]}, children_count={len(parent_detail[\"children\"])}')

    # ── 清理 ──
    cleanup_ids = ['E2E-A', 'E2E-B', 'E2E-PARENT', 'E2E-CHILD1', 'E2E-CHILD2']
    with get_session() as s:
        s.query(NodeEvent).filter(NodeEvent.node_id.in_(cleanup_ids)).delete(synchronize_session=False)
        s.query(NodeDependency).filter(NodeDependency.node_id.in_(cleanup_ids)).delete(synchronize_session=False)
        s.query(NodeDeliverable).filter(NodeDeliverable.node_id.in_(cleanup_ids)).delete(synchronize_session=False)
        s.query(ProjectNode).filter(ProjectNode.node_id.in_(cleanup_ids)).delete(synchronize_session=False)
        s.commit()
    print('[OK] Cleanup done')
    print('=== Phase 1-2 全部端到端验证通过 ===')

asyncio.run(e2e())
"
```
→ 预期输出：`=== Phase 1-2 全部端到端验证通过 ===`

全部通过后进入 Phase 1-3。

---

## 阶段反思指令

完成本阶段后，执行：

1. **检查产物**：列出新建文件路径
   - `emily-core/emily_core/services/node_commands.py`
   - `emily-core/emily_core/services/node_state_machine.py`
   - `emily-core/emily_core/services/node_service.py`
   - `emily-core/emily_core/application/node_app.py`

2. **检查偏差**：是否有步骤与计划不符？记录差异

3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应 Phase，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
