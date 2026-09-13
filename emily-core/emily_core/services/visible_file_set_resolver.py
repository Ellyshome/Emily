"""VisibleFileSetResolver — 可见文件范围解析器（M2）。

给定 user_id 及其权限上下文（company_id / info_level），实时计算可见文件集合：

    可见文件 = ① 上传者自有 ∪ ② 公开文件 ∪ ③ 节点可见 ∩ 密级 ∪ ④ 显式授权 ∪ ⑥ 系统管理员兜底

作为 RAG 检索的前置过滤器，返回 SQLAlchemy Select（file_id 子查询），
由 KnowledgeChunkRepo.search_dense 以 `doc_id IN (子查询)` 在排序前过滤。

「可见性是关系而非属性」：不依赖 session_accessible_files 快照，实时计算避免过期。
"""

from __future__ import annotations

import logging
from typing import Union

from sqlalchemy import and_, or_, select
from sqlalchemy.sql import Select

from ..infrastructure.database.models import (
    File,
    NodeAccessibleFile,
    NodeParticipantCompany,
    SessionAccessibleFile,
)
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.service.visible_file_set")

# 与 sync_for_user / PermissionService._derive_info_level 的密级映射保持一致
# 密级简化为 3 级：0公开 / 1内部 / 2机密（白名单制，不再有 3 绝密）
_INFO_LEVEL_MAP = {"public": 0, "internal": 1, "confidential": 2}


class VisibleFileSetResolver:
    """可见文件范围唯一权威来源。

    resolve_visible_file_ids 返回 Select（惰性，不执行），
    resolve_visible_file_list 物化为 list（小集合/调试用）。
    """

    def resolve_visible_file_ids(
        self,
        user_id: str,
        *,
        company_id: str,
        info_level: str,
        explicit_ok: bool = True,
    ) -> Select:
        """实时计算 ①∪②∪③∪④，返回 file_id 集合的 SQLAlchemy Select。"""
        return _build_visible_select(user_id, company_id, info_level, explicit_ok)

    def resolve_visible_file_list(self, user_id: str, **ctx) -> list[str]:
        """与 resolve_visible_file_ids 同源，仅物化为 list。"""
        stmt = _build_visible_select(
            user_id,
            ctx.get("company_id", ""),
            ctx.get("info_level", "public"),
            ctx.get("explicit_ok", True),
        )
        with get_session() as session:
            rows = session.execute(stmt).scalars().all()
        return [str(r) for r in rows]


def _build_visible_select(
    user_id: str,
    company_id: str,
    info_level: str,
    explicit_ok: bool,
) -> Select:
    """构造可见文件 id 的 Select。

    条件按 File 表直接拼 or_，避免跨表 union 的 CompoundSelect 兼容性问题。
    """
    max_conf = _INFO_LEVEL_MAP.get(info_level, 0)
    # 机密(2) 为白名单制：节点/项目自动可见只到内部(1)，机密不因同节点自动放行
    node_conf_cap = min(max_conf, 1)

    conds: list = [
        # ① 上传者自有集（自传永可见，不受密级约束）
        and_(File.uploaded_by == user_id, File.is_deleted == False),
    ]

    # 企业参与节点（规则 ②/③ 共用）
    participant_nodes = select(NodeParticipantCompany.node_id).where(
        NodeParticipantCompany.company_id == company_id,
    ) if company_id else None

    # ② 公开文件集（confidentiality = 0）：全员可见，不走节点判定
    conds.append(and_(
        File.confidentiality == 0,
        File.is_deleted == False,
    ))

    # ③ 节点可见集 = 企业参与节点上经 node_accessible_files 显式绑定的文件 ∩ 密级约束
    #    （D1: 节点来源 = node_participant_companies；文件必须显式挂载到节点，无全项目放行）
    if participant_nodes is not None:
        node_file_ids = select(NodeAccessibleFile.file_id).where(
            NodeAccessibleFile.node_id.in_(participant_nodes),
        )
        conds.append(and_(
            File.id.in_(node_file_ids),
            File.confidentiality <= node_conf_cap,
            File.is_deleted == False,
        ))

    # ⑥ 系统管理员(L6) 兜底：全局原生可见 公开/内部/机密（白名单之外的管理员通道）
    if max_conf >= 2:
        conds.append(and_(
            File.confidentiality <= 2,
            File.is_deleted == False,
        ))

    # ④ 显式授权（session_accessible_files.access_type = explicit）
    if explicit_ok:
        explicit_ids = select(SessionAccessibleFile.file_id).where(
            SessionAccessibleFile.user_id == user_id,
            SessionAccessibleFile.access_type == "explicit",
        )
        conds.append(and_(
            File.id.in_(explicit_ids),
            File.is_deleted == False,
        ))

    return select(File.id).where(or_(*conds))
