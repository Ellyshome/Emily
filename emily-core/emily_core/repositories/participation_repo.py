"""ParticipationRepo —— "企业参与节点" 关系的唯一查询单点。

业务口径（本项目权威定义）：
    人员不携带"项目归属"字段。一个人参与哪些项目，由其
    「所属企业 → 企业在节点上的参与记录 → 节点所属项目」推导：

        users.company  →  node_participant_companies.company_id
                       →  node_participant_companies.node_id
                       →  project_nodes.project_id

    同理，"用户可见的全景节点" = 其所属企业参与过的节点（真实 node_id）。

本模块是上述推导的唯一实现处（禁止在别处硬编码第二套口径）。
参照模式：emily_core/repositories/node_repo.py（@staticmethod + get_session）。
"""

from __future__ import annotations

import logging
from typing import Optional

from ..infrastructure.database.models import (
    CompanyInfo, NodeParticipantCompany, Project, ProjectNode, User,
)
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.participation_repo")


class ParticipationRepo:
    """企业—节点 参与关系查询。"""

    @staticmethod
    def node_ids_of_company(company_id: Optional[str]) -> list[str]:
        """企业参与过的节点编号（真实 node_id）。"""
        if not company_id:
            return []
        try:
            with get_session() as session:
                rows = (
                    session.query(NodeParticipantCompany.node_id)
                    .filter(NodeParticipantCompany.company_id == company_id)
                    .distinct()
                    .all()
                )
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error("node_ids_of_company failed company=%s: %s", company_id, e)
            return []

    @staticmethod
    def project_ids_of_company(company_id: Optional[str]) -> list[str]:
        """企业参与节点所归属的项目 ID 列表（去重，排除已废弃节点）。"""
        if not company_id:
            return []
        try:
            with get_session() as session:
                rows = (
                    session.query(ProjectNode.project_id)
                    .join(NodeParticipantCompany, NodeParticipantCompany.node_id == ProjectNode.node_id)
                    .filter(
                        NodeParticipantCompany.company_id == company_id,
                        ProjectNode.is_discarded == False,
                    )
                    .distinct()
                    .all()
                )
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error("project_ids_of_company failed company=%s: %s", company_id, e)
            return []

    @staticmethod
    def all_node_ids_of_projects(project_ids: Optional[list[str]]) -> list[str]:
        """项目下全部节点编号（供管理单位"全项目可见"口径使用）。"""
        pids = [p for p in (project_ids or []) if p]
        if not pids:
            return []
        try:
            with get_session() as session:
                rows = (
                    session.query(ProjectNode.node_id)
                    .filter(
                        ProjectNode.project_id.in_(pids),
                        ProjectNode.is_discarded == False,
                    )
                    .distinct()
                    .all()
                )
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error("all_node_ids_of_projects failed projects=%s: %s", pids, e)
            return []

    @staticmethod
    def projects_with_unregistered_nodes() -> list[dict]:
        """存在"未登记参与单位"节点的项目（启动自检告警用）。

        归属口径下，节点未登记参与单位 ⇒ 该节点对所有非管理单位用户不可见；
        若整项目都未登记 ⇒ 所有人都看不到该项目态势（且不报错）。
        此处按项目汇总"未登记参与单位的节点数"，用于暴露这种静默失效。
        """
        try:
            from sqlalchemy import func
            with get_session() as session:
                sub = (
                    session.query(NodeParticipantCompany.node_id)
                    .filter(NodeParticipantCompany.node_id == ProjectNode.node_id)
                    .exists()
                )
                rows = (
                    session.query(Project.id, Project.name, func.count(ProjectNode.node_id))
                    .join(ProjectNode, ProjectNode.project_id == Project.id)
                    .filter(
                        Project.is_deleted == False,
                        ProjectNode.is_discarded == False,
                        ~sub,
                    )
                    .group_by(Project.id, Project.name)
                    .all()
                )
            return [
                {"project_id": r[0], "project_name": r[1] or "", "unregistered_nodes": int(r[2] or 0)}
                for r in rows
            ]
        except Exception as e:
            logger.warning("projects_with_unregistered_nodes failed: %s", e)
            return []

    @staticmethod
    def company_ids_of_projects(project_ids: Optional[list[str]]) -> list[str]:
        """项目下所有参与单位 ID（经节点参与记录反查）。"""
        pids = [p for p in (project_ids or []) if p]
        if not pids:
            return []
        try:
            with get_session() as session:
                rows = (
                    session.query(NodeParticipantCompany.company_id)
                    .join(ProjectNode, ProjectNode.node_id == NodeParticipantCompany.node_id)
                    .filter(
                        ProjectNode.project_id.in_(pids),
                        ProjectNode.is_discarded == False,
                    )
                    .distinct()
                    .all()
                )
            return [r[0] for r in rows if r[0]]
        except Exception as e:
            logger.error("company_ids_of_projects failed projects=%s: %s", pids, e)
            return []

    @staticmethod
    def users_of_projects(project_ids: Optional[list[str]]) -> list[User]:
        """项目下的人员（= 参与单位所属的人员，去重、排除已删除）。"""
        pids = [p for p in (project_ids or []) if p]
        if not pids:
            return []
        try:
            with get_session() as session:
                return (
                    session.query(User)
                    .join(CompanyInfo, CompanyInfo.id == User.company)
                    .join(NodeParticipantCompany, NodeParticipantCompany.company_id == CompanyInfo.id)
                    .join(ProjectNode, ProjectNode.node_id == NodeParticipantCompany.node_id)
                    .filter(
                        ProjectNode.project_id.in_(pids),
                        ProjectNode.is_discarded == False,
                        User.is_deleted == False,
                    )
                    .distinct()
                    .all()
                )
        except Exception as e:
            logger.error("users_of_projects failed projects=%s: %s", pids, e)
            return []
