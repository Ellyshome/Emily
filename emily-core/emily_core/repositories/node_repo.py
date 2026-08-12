"""全景节点图 V2 Repository 层 —— 5 张表的 CRUD 操作。

包含：ProjectNodeRepo / NodeDependencyRepo / NodeDeliverableRepo /
      NodeAccessibleFileRepo / NodeEventRepo

基于需求文档 §3.2–§3.6。
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
    NodeParticipant,
    NodeParticipantCompany,
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
        """创建节点（幂等：node_id 已存在则返回已有节点，不重复写入）。

        必填参数：project_id, node_id, node_name, creator_id, deadline
        可选参数：owner_dept_id, related_company_id, remark
        """
        node_id = kwargs.get("node_id", "")
        if node_id:
            existing = ProjectNodeRepo.get_by_node_id(node_id)
            if existing is not None:
                logger.info(
                    "ProjectNode already exists (idempotent skip): %s (project=%s)",
                    existing.node_id, existing.project_id,
                )
                return existing
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
            return q.order_by(ProjectNode.created_at.desc()).limit(limit).all()

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

        可更新字段：node_name, deadline, owner_dept_id, related_company_id, remark
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
    def find_by_status(status: str, project_id: str | None = None,
                       owner_dept_id: str | None = None,
                       limit: int = 200) -> list[ProjectNode]:
        """按状态查询节点（用于查询待审批节点等）。

        Args:
            status: 节点状态（NOT_ACTIVATED / CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED）
            project_id: 可选项目过滤
            owner_dept_id: 可选主责条线过滤
            limit: 返回上限
        """
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.status == status,
                    ProjectNode.is_discarded == False,
                )
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            if owner_dept_id:
                q = q.filter(ProjectNode.owner_dept_id == owner_dept_id)
            return q.order_by(ProjectNode.created_at.desc()).limit(limit).all()

    @staticmethod
    def find_pending_approval(owner_dept_id: str | None = None,
                              project_id: str | None = None,
                              limit: int = 200) -> list[ProjectNode]:
        """查询待审批节点（status=NOT_ACTIVATED）。

        Args:
            owner_dept_id: 可选按主责条线过滤（部门负责人查看自己部门的待审批节点）
            project_id: 可选项目过滤
            limit: 返回上限
        """
        return ProjectNodeRepo.find_by_status(
            "NOT_ACTIVATED",
            project_id=project_id,
            owner_dept_id=owner_dept_id,
            limit=limit,
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

    @staticmethod
    def find_by_responsible_user(
        responsible_user_id: str,
        project_id: str | None = None,
        node_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ProjectNode]:
        """按责任人查询节点。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.responsible_user_id == responsible_user_id,
                    ProjectNode.is_discarded == False,
                )
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            if node_type:
                q = q.filter(ProjectNode.node_type == node_type)
            if status:
                q = q.filter(ProjectNode.status == status)
            return q.order_by(ProjectNode.deadline.asc()).limit(limit).all()

    @staticmethod
    def find_by_participant_user(
        user_id: str,
        project_id: str | None = None,
        node_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ProjectNode]:
        """按参与人查询节点。"""
        with get_session() as session:
            # 子查询：该用户参与的 node_id 列表
            participant_node_ids = (
                session.query(NodeParticipant.node_id)
                .filter(NodeParticipant.user_id == user_id)
                .subquery()
            )
            q = (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.node_id.in_(participant_node_ids),
                    ProjectNode.is_discarded == False,
                )
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            if node_type:
                q = q.filter(ProjectNode.node_type == node_type)
            if status:
                q = q.filter(ProjectNode.status == status)
            return q.order_by(ProjectNode.deadline.asc()).limit(limit).all()

    @staticmethod
    def find_by_node_type(
        node_type: str,
        project_id: str | None = None,
        limit: int = 200,
    ) -> list[ProjectNode]:
        """按节点类型查询。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_type == node_type, ProjectNode.is_discarded == False)
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            return q.order_by(ProjectNode.created_at.desc()).limit(limit).all()

    @staticmethod
    def find_near_deadline(before_minutes: int = 60, limit: int = 100) -> list[ProjectNode]:
        """查询即将到期的节点（deadline 在 now + before_minutes 内，非 COMPLETED）。"""
        now = datetime.now(timezone.utc)
        window_end = (now + timedelta(minutes=before_minutes)).isoformat()
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.deadline != "",
                    ProjectNode.deadline.isnot(None),
                    ProjectNode.deadline <= window_end,
                    ProjectNode.deadline > now.isoformat(),
                    ProjectNode.status != "COMPLETED",
                    ProjectNode.is_discarded == False,
                )
                .order_by(ProjectNode.deadline.asc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_overdue(limit: int = 100) -> list[ProjectNode]:
        """查询已超期的节点（deadline < now 且 status 非 COMPLETED）。"""
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.deadline != "",
                    ProjectNode.deadline.isnot(None),
                    ProjectNode.deadline < now_iso,
                    ProjectNode.status != "COMPLETED",
                    ProjectNode.is_discarded == False,
                )
                .order_by(ProjectNode.deadline.asc())
                .limit(limit)
                .all()
            )


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

    @staticmethod
    def update_file_id(deliverable_id: str, file_id: str) -> NodeDeliverable | None:
        """更新成果关联文件。"""
        with get_session() as session:
            deliv = (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.deliverable_id == deliverable_id)
                .first()
            )
            if deliv is None:
                return None
            deliv.file_id = file_id
            session.commit()
            logger.info("NodeDeliverable file_id updated: %s → %s", deliverable_id, file_id or "(cleared)")
            return deliv

    @staticmethod
    def update_submission_status(
        deliverable_id: str,
        submission_status: str,
        submitted_by: str = "",
        confirmed_by: str = "",
        return_reason: str = "",
        attachment_file_id: str = "",
    ) -> NodeDeliverable | None:
        """更新成果提交状态。"""
        with get_session() as session:
            deliv = (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.deliverable_id == deliverable_id)
                .first()
            )
            if deliv is None:
                return None
            deliv.submission_status = submission_status
            if submitted_by:
                deliv.submitted_by = submitted_by
                deliv.submitted_at = datetime.now(timezone.utc).isoformat()
            if confirmed_by:
                deliv.confirmed_by = confirmed_by
                deliv.confirmed_at = datetime.now(timezone.utc).isoformat()
            if return_reason:
                deliv.return_reason = return_reason
            if attachment_file_id:
                deliv.attachment_file_id = attachment_file_id
            session.commit()
            logger.info("NodeDeliverable submission: %s -> %s", deliverable_id, submission_status)
            return deliv

    @staticmethod
    def find_by_submission_status(
        node_id: str,
        submission_status: str,
    ) -> list[NodeDeliverable]:
        """按提交状态查询节点的成果。"""
        with get_session() as session:
            return (
                session.query(NodeDeliverable)
                .filter(
                    NodeDeliverable.node_id == node_id,
                    NodeDeliverable.submission_status == submission_status,
                )
                .all()
            )

    @staticmethod
    def get_by_node_and_name(
        node_id: str,
        deliverable_name: str,
    ) -> NodeDeliverable | None:
        """按节点ID+成果名称查找。"""
        with get_session() as session:
            return (
                session.query(NodeDeliverable)
                .filter(
                    NodeDeliverable.node_id == node_id,
                    NodeDeliverable.deliverable_name == deliverable_name,
                )
                .first()
            )

    @staticmethod
    def find_pending_by_responsible_user(
        responsible_user_id: str,
        project_id: str | None = None,
        submission_status: str = "",
        node_status: str = "IN_PROGRESS",
        limit: int = 20,
        offset: int = 0,
    ) -> list[tuple]:
        """查询责任人名下节点的待办成果（JOIN project_nodes）。

        Args:
            node_status: 节点状态过滤，默认 "IN_PROGRESS"。传 "" 则不过滤。

        Returns:
            list of (NodeDeliverable, ProjectNode) tuples
        """
        with get_session() as session:
            q = (
                session.query(NodeDeliverable, ProjectNode)
                .join(ProjectNode, NodeDeliverable.node_id == ProjectNode.node_id)
                .filter(
                    ProjectNode.responsible_user_id == responsible_user_id,
                    ProjectNode.is_discarded == False,
                )
            )
            if node_status:
                q = q.filter(ProjectNode.status == node_status)
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            if submission_status:
                q = q.filter(NodeDeliverable.submission_status == submission_status)
            return (
                q.order_by(ProjectNode.deadline.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )

    @staticmethod
    def count_pending_by_responsible_user(
        responsible_user_id: str,
        project_id: str | None = None,
        submission_status: str = "",
        node_status: str = "IN_PROGRESS",
    ) -> int:
        """统计责任人名下的待办成果数。

        Args:
            node_status: 节点状态过滤，默认 "IN_PROGRESS"。传 "" 则不过滤。
        """
        with get_session() as session:
            from sqlalchemy import func
            q = (
                session.query(func.count(NodeDeliverable.id))
                .join(ProjectNode, NodeDeliverable.node_id == ProjectNode.node_id)
                .filter(
                    ProjectNode.responsible_user_id == responsible_user_id,
                    ProjectNode.is_discarded == False,
                )
            )
            if node_status:
                q = q.filter(ProjectNode.status == node_status)
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            if submission_status:
                q = q.filter(NodeDeliverable.submission_status == submission_status)
            return q.scalar() or 0


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


# ══════════════════════════════════════════════════════════════════════════════
# NodeParticipantCompanyRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeParticipantCompanyRepo:
    """节点参与单位关联表 Repository。"""

    @staticmethod
    def add(node_id: str, company_id: str, added_by: str = "") -> NodeParticipantCompany:
        """添加节点参与单位。"""
        now_iso = datetime.now(BEIJING_TZ).isoformat()
        with get_session() as session:
            npc = NodeParticipantCompany(
                node_id=node_id,
                company_id=company_id,
                added_by=added_by,
                added_at=now_iso,
            )
            session.add(npc)
            session.flush()
            logger.info("NodeParticipantCompany added: node=%s company=%s", node_id, company_id)
            return npc

    @staticmethod
    def remove(node_id: str, company_id: str) -> bool:
        """移除节点参与单位。"""
        with get_session() as session:
            deleted = (
                session.query(NodeParticipantCompany)
                .filter(
                    NodeParticipantCompany.node_id == node_id,
                    NodeParticipantCompany.company_id == company_id,
                )
                .delete()
            )
            session.flush()
            if deleted:
                logger.info("NodeParticipantCompany removed: node=%s company=%s", node_id, company_id)
            return deleted > 0

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeParticipantCompany]:
        """查询节点的所有参与单位。"""
        with get_session() as session:
            return (
                session.query(NodeParticipantCompany)
                .filter(NodeParticipantCompany.node_id == node_id)
                .all()
            )

    @staticmethod
    def find_company_ids_by_node(node_id: str) -> list[str]:
        """查询节点的参与单位ID列表。"""
        with get_session() as session:
            rows = (
                session.query(NodeParticipantCompany.company_id)
                .filter(NodeParticipantCompany.node_id == node_id)
                .all()
            )
            return [r[0] for r in rows]

    @staticmethod
    def find_by_company(company_id: str, limit: int = 200) -> list[NodeParticipantCompany]:
        """查询某单位参与的所有节点。"""
        with get_session() as session:
            return (
                session.query(NodeParticipantCompany)
                .filter(NodeParticipantCompany.company_id == company_id)
                .limit(limit)
                .all()
            )

    @staticmethod
    def replace_all(node_id: str, company_ids: list[str], added_by: str = "") -> list[NodeParticipantCompany]:
        """全量替换节点的参与单位列表。"""
        now_iso = datetime.now(BEIJING_TZ).isoformat()
        with get_session() as session:
            # 删除旧的
            session.query(NodeParticipantCompany).filter(
                NodeParticipantCompany.node_id == node_id,
            ).delete()
            # 插入新的
            npcs = []
            for cid in company_ids:
                npc = NodeParticipantCompany(
                    node_id=node_id,
                    company_id=cid,
                    added_by=added_by,
                    added_at=now_iso,
                )
                session.add(npc)
                npcs.append(npc)
            session.flush()
            logger.info("NodeParticipantCompany replaced: node=%s count=%d", node_id, len(company_ids))
            return npcs
