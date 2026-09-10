"""CompanyResolver —— "单位引用"统一解析器。

概念口径（见 docs/Spec/项目归属与可见范围_Spec.md）：
    跨表引用一律使用 **ID**；中文单位名称/类型只是**输入侧**的可读写法，
    必须经本模块解析为 `company_info.id` 之后才可落库 / 参与权限判定。

解析顺序（唯一命中才算成功，多候选一律拒绝猜测）：
    1) `ref` 本身已是合法 `company_info.id` → 原样返回
    2) 项目参建单位范围内按 `type` 匹配 → 再按 `company_name` 精确匹配
    3) 上一步未命中时，退到全局范围重复 2)（兼容存量/种子里的中文写法）
    4) 多候选 / 无命中 → 返回 None 并告警（fail-closed，不静默取第一个）

参照模式：emily_core/repositories/participation_repo.py（@staticmethod + get_session）
"""

from __future__ import annotations

import logging
from typing import Optional

from ..infrastructure.database.models import CompanyInfo, NodeParticipantCompany, ProjectNode
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.company_resolver")


class CompanyResolver:
    """单位引用 → company_info.id 的唯一解析实现。"""

    @staticmethod
    def resolve(ref: str, project_id: str = "") -> Optional[str]:
        """把"单位引用"解析为 `company_info.id`；无法唯一确定时返回 None。"""
        ref = (ref or "").strip()
        if not ref:
            return None
        try:
            with get_session() as session:
                # 1) 已是合法 ID
                if session.query(CompanyInfo.id).filter(CompanyInfo.id == ref).first():
                    return ref

                # 2) 项目参建单位范围
                if project_id:
                    cids = [
                        r[0] for r in (
                            session.query(NodeParticipantCompany.company_id)
                            .join(ProjectNode, ProjectNode.node_id == NodeParticipantCompany.node_id)
                            .filter(
                                ProjectNode.project_id == project_id,
                                ProjectNode.is_discarded == False,
                            )
                            .distinct()
                            .all()
                        ) if r[0]
                    ]
                    if cids:
                        found = CompanyResolver._match(
                            session, ref, CompanyInfo.id.in_(cids)
                        )
                        if found:
                            return found

                # 3) 全局范围（兼容存量/种子中的中文写法）
                return CompanyResolver._match(session, ref)
        except Exception as e:
            logger.error("CompanyResolver.resolve failed ref=%s: %s", ref, e)
            return None

    @staticmethod
    def find_invalid_node_company_refs() -> list[dict]:
        """存量中 `related_company_id` 不是合法 `company_info.id` 的节点（启动自检用）。

        空值视为"未设置"，不算违规；只报"存了非 ID 内容"（如中文标签）的情况。
        """
        try:
            from ..infrastructure.database.models import ProjectNode
            with get_session() as session:
                valid = {r[0] for r in session.query(CompanyInfo.id).all()}
                rows = (
                    session.query(ProjectNode.node_id, ProjectNode.related_company_id)
                    .filter(ProjectNode.is_discarded == False)
                    .all()
                )
            return [
                {"node_id": nid, "related_company_id": ref}
                for nid, ref in rows
                if (ref or "").strip() and ref not in valid
            ]
        except Exception as e:
            logger.warning("find_invalid_node_company_refs failed: %s", e)
            return []

    @staticmethod
    def _match(session, ref: str, *filters) -> Optional[str]:
        """在给定范围内按 type → company_name 顺序匹配，唯一命中才返回 id。"""
        base = session.query(CompanyInfo)
        for f in filters:
            base = base.filter(f)
        pool = base.all()

        for matched in (
            [c for c in pool if (c.type or "") == ref],
            [c for c in pool if (c.company_name or "") == ref],
        ):
            if len(matched) == 1:
                return matched[0].id
            if len(matched) > 1:
                logger.warning("单位引用 '%s' 命中多个候选，拒绝猜测", ref)
                return None
        return None
