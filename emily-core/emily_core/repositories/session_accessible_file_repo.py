"""SessionAccessibleFileRepo —— 可见文件持久化操作。

提供 session_accessible_files 表的同步、查询、搜索能力。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..infrastructure.database import get_session
from ..infrastructure.database.models import SessionAccessibleFile, File, ProjectNode, NodeAccessibleFile
from sqlalchemy import or_, and_

logger = logging.getLogger("emily.repo.accessible_file")


class SessionAccessibleFileRepo:
    """Session 可见文件 Repository。"""

    @staticmethod
    def sync_for_user(
        user_id: str,
        project_ids: list[str],
        info_level: str = "public",
        authorized_node_ids: Optional[list[str]] = None,
    ) -> int:
        """批量同步用户可见文件。

        三步：
          1. 清除该用户旧的 project_scope + node_linked 记录
          2. 按 project_ids + confidentiality ≤ info_level 查询 → 批量写入
          3. 按 authorized_node_ids → node_accessible_files 关联查询 → upsert
        （explicit 记录不清除）
        """
        authorized_node_ids = authorized_node_ids or []
        info_level_map = {"public": 0, "internal": 1, "confidential": 2, "secret": 3}
        max_conf = info_level_map.get(info_level, 0)
        total = 0

        try:
            now = _now_iso()
            with get_session() as session:
                # 1. 清除旧的 project_scope + node_linked
                session.query(SessionAccessibleFile).filter(
                    SessionAccessibleFile.user_id == user_id,
                    SessionAccessibleFile.access_type.in_(["project_scope", "node_linked"]),
                ).delete(synchronize_session=False)

                # 2. project_scope 批量写入
                if project_ids:
                    files = session.query(File).filter(
                        File.project_id.in_(project_ids),
                        File.is_deleted == False,
                        File.confidentiality <= max_conf,
                    ).all()

                    for f in files:
                        saf = SessionAccessibleFile(
                            user_id=user_id,
                            file_id=f.id,
                            access_type="project_scope",
                            granted_by="system",
                            granted_at=now,
                        )
                        session.add(saf)
                        total += 1

                # 3. node_linked upsert
                if authorized_node_ids:
                    node_file_links = session.query(NodeAccessibleFile).filter(
                        NodeAccessibleFile.node_id.in_(authorized_node_ids)
                    ).all()

                    for link in node_file_links:
                        # 检查是否已由 project_scope 覆盖
                        existing = session.query(SessionAccessibleFile).filter(
                            SessionAccessibleFile.user_id == user_id,
                            SessionAccessibleFile.file_id == link.file_id,
                        ).first()

                        if not existing:
                            saf = SessionAccessibleFile(
                                user_id=user_id,
                                file_id=link.file_id,
                                access_type="node_linked",
                                granted_by=link.added_by or "system",
                                granted_at=link.added_at or now,
                            )
                            session.add(saf)
                            total += 1

                session.commit()
                logger.info("sync_for_user(%s): %d files synced", user_id, total)
                return total
        except Exception as e:
            logger.error("sync_for_user(%s) failed: %s", user_id, e)
            return total

    @staticmethod
    def get_file_summary(user_id: str) -> dict:
        """获取用户可见文件摘要（count + type 分布）。"""
        try:
            with get_session() as session:
                rows = session.query(
                    SessionAccessibleFile.file_id,
                ).filter(
                    SessionAccessibleFile.user_id == user_id,
                ).all()

                file_ids = [r.file_id for r in rows]
                if not file_ids:
                    return {"count": 0, "by_type": {}, "files": []}

                files = session.query(File).filter(
                    File.id.in_(file_ids),
                    File.is_deleted == False,
                ).all()

                by_type: dict[str, int] = {}
                files_list: list[dict] = []
                for f in files:
                    ft = f.file_type or "其他"
                    by_type[ft] = by_type.get(ft, 0) + 1
                    files_list.append({
                        "file_id": f.id,
                        "filename": f.filename,
                        "file_type": f.file_type or "",
                        "project_id": f.project_id or "",
                    })

                return {"count": len(files), "by_type": by_type, "files": files_list}
        except Exception as e:
            logger.error("get_file_summary(%s) failed: %s", user_id, e)
            return {"count": 0, "by_type": {}, "files": []}

    @staticmethod
    def search(user_id: str, keyword: str, top_k: int = 5) -> list[dict]:
        """关键词搜索用户可见文件。

        匹配策略：
          1. 将 keyword 按空格/中文分词拆分
          2. 在 files.filename 和 files.file_type 中 ILIKE 匹配
          3. 按匹配得分排序（文件名匹配权重 > file_type 匹配权重）
        """
        keywords = [k.strip() for k in keyword.replace(" ", " ").split(" ") if k.strip()]
        if not keywords:
            return []

        try:
            with get_session() as session:
                accessible = session.query(SessionAccessibleFile.file_id).filter(
                    SessionAccessibleFile.user_id == user_id
                ).subquery()

                query = session.query(File).filter(
                    File.id.in_(accessible),
                    File.is_deleted == False,
                )

                # 逐关键词 ILIKE
                conditions = []
                for kw in keywords:
                    conditions.append(File.filename.ilike(f"%{kw}%"))
                    conditions.append(File.file_type.ilike(f"%{kw}%"))

                files = query.filter(or_(*conditions)).limit(top_k * 2).all()

                scored = []
                for f in files:
                    score = 0
                    for kw in keywords:
                        if kw.lower() in (f.filename or "").lower():
                            score += 10
                        if kw.lower() in (f.file_type or "").lower():
                            score += 3
                    scored.append({
                        "file_id": f.id,
                        "filename": f.filename or "",
                        "file_type": f.file_type or "",
                        "description": f.file_type or "",
                        "score": score,
                    })

                scored.sort(key=lambda x: x["score"], reverse=True)
                return scored[:top_k]
        except Exception as e:
            logger.error("search(%s, %s) failed: %s", user_id, keyword, e)
            return []

    @staticmethod
    def add_explicit_grant(user_id: str, file_id: str, granted_by: str = "admin") -> bool:
        """显式授予用户文件访问权限。"""
        try:
            now = _now_iso()
            with get_session() as session:
                existing = session.query(SessionAccessibleFile).filter(
                    SessionAccessibleFile.user_id == user_id,
                    SessionAccessibleFile.file_id == file_id,
                ).first()

                if existing:
                    existing.access_type = "explicit"
                    existing.granted_by = granted_by
                    existing.granted_at = now
                    existing.expires_at = ""
                else:
                    saf = SessionAccessibleFile(
                        user_id=user_id,
                        file_id=file_id,
                        access_type="explicit",
                        granted_by=granted_by,
                        granted_at=now,
                    )
                    session.add(saf)

                session.commit()
                return True
        except Exception as e:
            logger.error("add_explicit_grant(%s, %s) failed: %s", user_id, file_id, e)
            return False

    @staticmethod
    def revoke(user_id: str, file_id: str) -> bool:
        """撤销用户文件访问权限。"""
        try:
            with get_session() as session:
                deleted = session.query(SessionAccessibleFile).filter(
                    SessionAccessibleFile.user_id == user_id,
                    SessionAccessibleFile.file_id == file_id,
                ).delete()
                session.commit()
                return deleted > 0
        except Exception as e:
            logger.error("revoke(%s, %s) failed: %s", user_id, file_id, e)
            return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
