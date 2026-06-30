"""全景节点图 V2 Repository 层 —— 5 张表的 CRUD 操作。

包含：ProjectNodeRepo / NodeDependencyRepo / NodeDeliverableRepo /
      NodeAccessibleFileRepo / NodeEventRepo

基于需求文档 §3.2–§3.6。参照模式：plan_task_repo.py。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from ..infrastructure.database.models import (
    ProjectNode,
    NodeDependency,
    NodeDeliverable,
    NodeAccessibleFile,
    NodeEvent,
)
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.node_repo")

BEIJING_TZ = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _to_decimal_str(value: float, precision: int = 4) -> str:
    """将 float 转为固定精度的字符串（用于 DECIMAL 列存储）。"""
    if precision == 2:
        return f"{value:.2f}"
    return f"{value:.4f}"


def _parse_decimal(value: str) -> float:
    """将 DECIMAL 字符串解析为 float。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# ProjectNodeRepo
# ══════════════════════════════════════════════════════════════════════════════

class ProjectNodeRepo:
    """节点主表 Repository。"""

    @staticmethod
    def create(**kwargs) -> ProjectNode:
        """创建节点。

        必填参数：project_id, node_id, node_name, creator_id, deadline
        可选参数：owner_dept_id, related_company_id, parent_node_id, stage_id,
                  child_weight, remark, land_parcel_id, startup_doc_id, sort_order
        """
        with get_session() as session:
            node = ProjectNode(**kwargs)
            session.add(node)
            session.flush()
            logger.info("ProjectNode created: %s (project=%s)", node.node_id, node.project_id)
            return node

    @staticmethod
    def get_by_id(node_uuid: str) -> ProjectNode | None:
        """按数据库主键 ID 查询。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(ProjectNode.id == node_uuid, ProjectNode.is_discarded == False)
                .first()
            )

    @staticmethod
    def get_by_node_id(node_id: str, project_id: str | None = None) -> ProjectNode | None:
        """按业务编号 node_id 查询，可选项目过滤。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            return q.first()

    @staticmethod
    def find_by_project(project_id: str, status: str | None = None, limit: int = 200) -> list[ProjectNode]:
        """查询项目下所有节点（可选按状态过滤）。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.project_id == project_id, ProjectNode.is_discarded == False)
            )
            if status:
                q = q.filter(ProjectNode.status == status)
            return q.order_by(ProjectNode.sort_order, ProjectNode.created_at).limit(limit).all()

    @staticmethod
    def find_by_parent(parent_node_id: str) -> list[ProjectNode]:
        """查询某父节点的所有子节点。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.parent_node_id == parent_node_id,
                    ProjectNode.is_discarded == False,
                )
                .order_by(ProjectNode.sort_order)
                .all()
            )

    @staticmethod
    def find_by_stage(project_id: str, stage_id: int) -> list[ProjectNode]:
        """查询某阶段的所有根节点（parent_node_id 为空）。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.stage_id == stage_id,
                    ProjectNode.parent_node_id == "",
                    ProjectNode.is_discarded == False,
                )
                .order_by(ProjectNode.sort_order)
                .all()
            )

    @staticmethod
    def find_by_owner(owner_dept_id: str, project_id: str | None = None) -> list[ProjectNode]:
        """按主责条线查询节点。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.owner_dept_id == owner_dept_id, ProjectNode.is_discarded == False)
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            return q.order_by(ProjectNode.created_at.desc()).limit(200).all()

    @staticmethod
    def update_fields(node_id: str, **kwargs) -> ProjectNode | None:
        """更新节点字段。自动设置 updated_at。

        可更新字段：node_name, deadline, owner_dept_id, related_company_id,
                    remark, stage_id, sort_order, land_parcel_id, startup_doc_id
        """
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            for key, value in kwargs.items():
                if hasattr(node, key):
                    setattr(node, key, value)
            node.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("ProjectNode updated: %s fields=%s", node_id, list(kwargs.keys()))
            return node

    @staticmethod
    def update_status(node_id: str, new_status: str) -> ProjectNode | None:
        """更新节点状态（状态机专用）。"""
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            node.status = new_status
            node.updated_at = datetime.now(timezone.utc).isoformat()
            if new_status == "COMPLETED":
                node.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("ProjectNode status: %s -> %s", node_id, new_status)
            return node

    @staticmethod
    def update_progress(node_id: str, progress: float) -> ProjectNode | None:
        """更新节点进度（百分比 0.00-100.00）。"""
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            node.progress = _to_decimal_str(progress, precision=2)
            node.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return node

    @staticmethod
    def discard(node_id: str) -> ProjectNode | None:
        """废弃节点（软删除，不物理删除）。"""
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            node.is_discarded = True
            node.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("ProjectNode discarded: %s", node_id)
            return node

    @staticmethod
    def count_children(parent_node_id: str) -> int:
        """统计子节点数量（用于上限检查）。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.parent_node_id == parent_node_id,
                    ProjectNode.is_discarded == False,
                )
                .count()
            )

    @staticmethod
    def get_ancestor_chain(node_id: str, max_depth: int = 3) -> list[ProjectNode]:
        """向上追溯祖先链（用于递归进度重算）。最多 3 层。"""
        ancestors = []
        current_id = node_id
        for _ in range(max_depth):
            with get_session() as session:
                node = (
                    session.query(ProjectNode)
                    .filter(
                        ProjectNode.node_id == current_id,
                        ProjectNode.is_discarded == False,
                    )
                    .first()
                )
            if node is None or not node.parent_node_id:
                break
            parent = ProjectNodeRepo.get_by_node_id(node.parent_node_id)
            if parent is None:
                break
            ancestors.append(parent)
            current_id = parent.node_id
        return ancestors


# ══════════════════════════════════════════════════════════════════════════════
# NodeDependencyRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeDependencyRepo:
    """前置依赖表 Repository。"""

    @staticmethod
    def create(**kwargs) -> NodeDependency:
        """创建依赖记录。

        必填：node_id, depends_on_deliverable_id, depends_on_node_id
        可选：dependency_type (默认 DELIVERABLE), weight (默认 1.0000)
        """
        with get_session() as session:
            dep = NodeDependency(**kwargs)
            session.add(dep)
            session.flush()
            logger.info(
                "NodeDependency created: %s depends on %s",
                dep.node_id, dep.depends_on_deliverable_id,
            )
            return dep

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeDependency]:
        """查询节点的所有前置依赖。"""
        with get_session() as session:
            return (
                session.query(NodeDependency)
                .filter(NodeDependency.node_id == node_id)
                .all()
            )

    @staticmethod
    def find_downstream(depends_on_node_id: str) -> list[NodeDependency]:
        """反向查询：哪些节点依赖了某上游节点的成果。"""
        with get_session() as session:
            return (
                session.query(NodeDependency)
                .filter(NodeDependency.depends_on_node_id == depends_on_node_id)
                .all()
            )

    @staticmethod
    def get_by_id(dep_id: str) -> NodeDependency | None:
        """按主键查询。"""
        with get_session() as session:
            return session.query(NodeDependency).filter(NodeDependency.id == dep_id).first()

    @staticmethod
    def delete(dep_id: str) -> bool:
        """删除依赖记录（物理删除，因为依赖是精确关系不是业务数据）。"""
        with get_session() as session:
            dep = session.query(NodeDependency).filter(NodeDependency.id == dep_id).first()
            if dep is None:
                return False
            session.delete(dep)
            session.commit()
            logger.info("NodeDependency deleted: %s", dep_id)
            return True

    @staticmethod
    def exists(node_id: str, depends_on_deliverable_id: str) -> bool:
        """检查依赖是否已存在（唯一约束检查）。"""
        with get_session() as session:
            return (
                session.query(NodeDependency)
                .filter(
                    NodeDependency.node_id == node_id,
                    NodeDependency.depends_on_deliverable_id == depends_on_deliverable_id,
                )
                .first()
                is not None
            )


# ══════════════════════════════════════════════════════════════════════════════
# NodeDeliverableRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeDeliverableRepo:
    """产出成果表 Repository。"""

    @staticmethod
    def generate_deliverable_id(node_id: str, seq: int) -> str:
        """生成成果编号：{node_id}-DELV-{seq:03d}。"""
        return f"{node_id}-DELV-{seq:03d}"

    @staticmethod
    def get_next_seq(node_id: str) -> int:
        """获取某节点下一个成果序号。"""
        with get_session() as session:
            existing = (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.node_id == node_id)
                .all()
            )
            return len(existing) + 1

    @staticmethod
    def create(**kwargs) -> NodeDeliverable:
        """创建成果记录。

        必填：deliverable_id, node_id, deliverable_name, target_amount, unit
        可选：current_amount (默认 0.00), is_required (默认 True), file_id
        """
        with get_session() as session:
            deliv = NodeDeliverable(**kwargs)
            session.add(deliv)
            session.flush()
            logger.info("NodeDeliverable created: %s for node %s", deliv.deliverable_id, deliv.node_id)
            return deliv

    @staticmethod
    def get_by_deliverable_id(deliverable_id: str) -> NodeDeliverable | None:
        """按业务编号查询。"""
        with get_session() as session:
            return (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.deliverable_id == deliverable_id)
                .first()
            )

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeDeliverable]:
        """查询节点的所有成果。"""
        with get_session() as session:
            return (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.node_id == node_id)
                .all()
            )

    @staticmethod
    def update_progress(deliverable_id: str, current_amount: str, file_id: str = "") -> NodeDeliverable | None:
        """更新成果当前量和关联文件。"""
        with get_session() as session:
            deliv = (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.deliverable_id == deliverable_id)
                .first()
            )
            if deliv is None:
                return None
            deliv.current_amount = current_amount
            if file_id:
                deliv.file_id = file_id
            # 检查是否达成目标量
            if _parse_decimal(current_amount) >= _parse_decimal(deliv.target_amount):
                deliv.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("NodeDeliverable progress: %s -> %s", deliverable_id, current_amount)
            return deliv

    @staticmethod
    def get_completion_ratio(node_id: str) -> float:
        """计算节点必需成果的完成度比例（0.0-1.0）。"""
        with get_session() as session:
            deliverables = (
                session.query(NodeDeliverable)
                .filter(
                    NodeDeliverable.node_id == node_id,
                    NodeDeliverable.is_required == True,
                )
                .all()
            )
            if not deliverables:
                return 1.0  # 无必需成果 = 视为已完成

            total_ratio = 0.0
            for d in deliverables:
                target = max(_parse_decimal(d.target_amount), 0.001)
                current = min(_parse_decimal(d.current_amount), target)
                total_ratio += current / target

            return total_ratio / len(deliverables)


# ══════════════════════════════════════════════════════════════════════════════
# NodeAccessibleFileRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeAccessibleFileRepo:
    """节点可见文件中间表 Repository。"""

    @staticmethod
    def create(**kwargs) -> NodeAccessibleFile:
        """添加节点可见文件。

        必填：node_id, file_id, added_by
        """
        with get_session() as session:
            naf = NodeAccessibleFile(**kwargs)
            session.add(naf)
            session.flush()
            logger.info("NodeAccessibleFile added: node=%s file=%s", naf.node_id, naf.file_id)
            return naf

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeAccessibleFile]:
        """查询节点可访问的所有文件。"""
        with get_session() as session:
            return (
                session.query(NodeAccessibleFile)
                .filter(NodeAccessibleFile.node_id == node_id)
                .all()
            )

    @staticmethod
    def find_by_file(file_id: str) -> list[NodeAccessibleFile]:
        """反向查询：某文件可被哪些节点访问。"""
        with get_session() as session:
            return (
                session.query(NodeAccessibleFile)
                .filter(NodeAccessibleFile.file_id == file_id)
                .all()
            )

    @staticmethod
    def remove(node_id: str, file_id: str) -> bool:
        """移除节点可见文件。"""
        with get_session() as session:
            naf = (
                session.query(NodeAccessibleFile)
                .filter(
                    NodeAccessibleFile.node_id == node_id,
                    NodeAccessibleFile.file_id == file_id,
                )
                .first()
            )
            if naf is None:
                return False
            session.delete(naf)
            session.commit()
            logger.info("NodeAccessibleFile removed: node=%s file=%s", node_id, file_id)
            return True

    @staticmethod
    def batch_add(node_id: str, file_ids: list[str], added_by: str) -> int:
        """批量添加节点可见文件（同一事务）。"""
        count = 0
        with get_session() as session:
            for file_id in file_ids:
                # 跳过已存在的
                existing = (
                    session.query(NodeAccessibleFile)
                    .filter(
                        NodeAccessibleFile.node_id == node_id,
                        NodeAccessibleFile.file_id == file_id,
                    )
                    .first()
                )
                if existing:
                    continue
                naf = NodeAccessibleFile(
                    node_id=node_id,
                    file_id=file_id,
                    added_by=added_by,
                )
                session.add(naf)
                count += 1
            session.commit()
            logger.info("NodeAccessibleFile batch_add: node=%s count=%d", node_id, count)
            return count

    @staticmethod
    def exists(node_id: str, file_id: str) -> bool:
        """检查关联是否已存在。"""
        with get_session() as session:
            return (
                session.query(NodeAccessibleFile)
                .filter(
                    NodeAccessibleFile.node_id == node_id,
                    NodeAccessibleFile.file_id == file_id,
                )
                .first()
                is not None
            )


# ══════════════════════════════════════════════════════════════════════════════
# NodeEventRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeEventRepo:
    """事件总线持久化 Repository —— 只增不改（immutable）。"""

    @staticmethod
    def create(**kwargs) -> NodeEvent:
        """记录事件。

        必填：event_id, node_id, event_type
        可选：old_value, new_value, operator_id, remark
        """
        with get_session() as session:
            event = NodeEvent(**kwargs)
            session.add(event)
            session.flush()
            logger.info("NodeEvent created: %s type=%s node=%s", event.event_id, event.event_type, event.node_id)
            return event

    @staticmethod
    def find_by_node(node_id: str, event_type: str | None = None, limit: int = 100) -> list[NodeEvent]:
        """查询节点事件日志（按时间倒序）。"""
        with get_session() as session:
            q = session.query(NodeEvent).filter(NodeEvent.node_id == node_id)
            if event_type:
                q = q.filter(NodeEvent.event_type == event_type)
            return q.order_by(NodeEvent.created_at.desc()).limit(limit).all()

    @staticmethod
    def find_by_project(project_id: str, limit: int = 200) -> list[NodeEvent]:
        """查询项目下所有节点事件（JOIN project_nodes）。"""
        with get_session() as session:
            return (
                session.query(NodeEvent)
                .join(ProjectNode, NodeEvent.node_id == ProjectNode.node_id)
                .filter(ProjectNode.project_id == project_id)
                .order_by(NodeEvent.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_by_operator(operator_id: str, limit: int = 100) -> list[NodeEvent]:
        """查询操作人的所有事件。"""
        with get_session() as session:
            return (
                session.query(NodeEvent)
                .filter(NodeEvent.operator_id == operator_id)
                .order_by(NodeEvent.created_at.desc())
                .limit(limit)
                .all()
            )
