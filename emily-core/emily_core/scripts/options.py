"""options — Web 控制台参数的动态候选值（取自真实运行环境）。

"来自实际环境"的参数（用户 / 项目 / 节点）不做自由文本输入——手抄 UUID 既易错
也无法校验存在性。前端按 params.options_source 拉取本模块的候选值渲染为下拉。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.scripts.options")

# 数据源 → 中文名（报错信息与前端提示复用）
SOURCES: dict[str, str] = {
    "users": "用户",
    "projects": "项目",
    "nodes": "节点",
}


def list_options(source: str, query: str = "", limit: int = 300) -> list[dict]:
    """返回候选值列表 [{value, label, note}]。

    Args:
        source: users / projects / nodes。
        query: 关键字过滤，label / note / value 任一包含即命中。
        limit: 返回上限。

    Raises:
        ValueError: 未知数据源。
    """
    fetchers = {"users": _users, "projects": _projects, "nodes": _nodes}
    if source not in fetchers:
        raise ValueError(f"未知的选项数据源：{source}（可选：{', '.join(fetchers)}）")

    options = fetchers[source]()

    if query:
        needle = query.strip().lower()
        options = [
            o for o in options
            if needle in o["label"].lower()
            or needle in (o["note"] or "").lower()
            or needle in o["value"].lower()
        ]
    return options[:limit]


def _users() -> list[dict]:
    """活跃用户（users 表），note 带所属公司名便于重名区分。"""
    from emily_core.infrastructure.database.models import CompanyInfo, User
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(
                User.id.label("user_id"),
                User.username.label("username"),
                User.level.label("level"),
                CompanyInfo.company_name.label("company_name"),
            )
            .outerjoin(CompanyInfo, CompanyInfo.id == User.company)
            .filter(User.status == "active", User.is_deleted.isnot(True))
            .order_by(User.username)
            .all()
        )
    return [
        {"value": r.user_id, "label": r.username, "level": int(r.level or 0), "note": r.company_name or ""}
        for r in rows
    ]


def _projects() -> list[dict]:
    """未删除项目（projects 表）。"""
    from emily_core.infrastructure.database.models import Project
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(Project.id, Project.code, Project.name)
            .filter(Project.is_deleted.isnot(True))
            .order_by(Project.name)
            .all()
        )
    return [{"value": r.id, "label": r.name, "note": r.code or ""} for r in rows]


def _nodes() -> list[dict]:
    """未废弃节点（project_nodes 表），label 带所属项目便于区分重名。"""
    from emily_core.infrastructure.database.models import Project, ProjectNode
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(
                ProjectNode.node_id.label("node_id"),
                ProjectNode.node_name.label("node_name"),
                ProjectNode.project_id.label("project_id"),
                Project.name.label("project_name"),
            )
            .outerjoin(Project, Project.id == ProjectNode.project_id)
            .filter(ProjectNode.is_discarded.isnot(True))
            .order_by(Project.name, ProjectNode.node_id)
            .all()
        )
    return [
        {"value": r.node_id, "label": r.node_name, "note": r.project_name or r.project_id}
        for r in rows
    ]
