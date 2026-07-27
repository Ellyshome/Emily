"""全景节点图 V2 状态机引擎 —— 纯计算逻辑，不依赖数据库。

职责：
  - 前置条件满足度计算（文件级依赖：Σ(文件就绪 × 权重)）
  - 成果完成度计算（Σ(必需成果完成度) / 必需成果总数）
  - 三态流转判定（CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED）
  - 循环依赖检测（BFS）

基于需求文档 §2.1（四态模型：未启用→条件不足→进行中→已完成）和 §4.1（状态自动流转计算）。
"""

from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger("emily.node_state_machine")


# ══════════════════════════════════════════════════════════════════════════════
# 状态常量
# ══════════════════════════════════════════════════════════════════════════════

NOT_ACTIVATED = "NOT_ACTIVATED"
CONDITIONS_NOT_MET = "CONDITIONS_NOT_MET"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"

VALID_STATUSES = frozenset({NOT_ACTIVATED, CONDITIONS_NOT_MET, IN_PROGRESS, COMPLETED})

# 四态流转规则
# NOT_ACTIVATED → CONDITIONS_NOT_MET：部门负责人审批通过
# CONDITIONS_NOT_MET → IN_PROGRESS：条件满足
# IN_PROGRESS → COMPLETED：成果 100% 完成
# IN_PROGRESS → CONDITIONS_NOT_MET：阻塞时回退
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    NOT_ACTIVATED: frozenset({CONDITIONS_NOT_MET}),   # 审批通过后激活
    CONDITIONS_NOT_MET: frozenset({IN_PROGRESS}),
    IN_PROGRESS: frozenset({COMPLETED, CONDITIONS_NOT_MET}),  # 阻塞时回退
    COMPLETED: frozenset(),  # 终态
}


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构（引擎用简单类型，不依赖 ORM）
# ══════════════════════════════════════════════════════════════════════════════

class NodeSnapshot:
    """节点快照 —— 引擎计算的输入单元。"""
    __slots__ = ("node_id", "status", "dependencies", "deliverables", "children")

    def __init__(self, node_id: str, status: str = NOT_ACTIVATED):
        self.node_id = node_id
        self.status = status
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
    __slots__ = ("node_id", "status", "progress")
    def __init__(self, node_id: str, status: str, progress: float):
        self.node_id = node_id
        self.status = status
        self.progress = progress


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

    # 无任何成果定义 → 节点尚未定义工作，保留为"条件不足"
    if not deliverables:
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

    # BFS：从 upstream_node 出发，沿 depends_on_node_id 链向上游追溯
    # 如果追踪到 node_id，则 upstream_node 依赖 node_id 的成果，
    # 而 node_id 又要依赖 upstream_node 的成果 → 循环
    visited = {upstream_node}
    queue = deque([upstream_node])
    parent_map: dict[str, str | None] = {upstream_node: None}

    for _ in range(max_depth):
        if not queue:
            break
        current = queue.popleft()
        # current 依赖了哪些上游节点？沿依赖链继续追溯
        current_upstreams = node_dependencies.get(current, [])
        for next_upstream in current_upstreams:
            if next_upstream == node_id:
                # 找到循环：node_id → upstream_node → ... → node_id
                path = _reconstruct_path(parent_map, current, upstream_node, node_id)
                return True, path
            if next_upstream not in visited:
                visited.add(next_upstream)
                parent_map[next_upstream] = current
                queue.append(next_upstream)

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
