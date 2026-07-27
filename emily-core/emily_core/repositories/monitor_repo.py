"""监控数据 Repository —— 只读查询（节点/文件/人员/消息）。

参照模式：repositories/node_repo.py（@staticmethod + with get_session() + sync）。
所有方法为 @staticmethod + sync，Service 层用 asyncio.to_thread() 包裹。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    ProjectNode,
    File,
    User,
    CompanyInfo,
    Message,
    Conversation,
)

logger = logging.getLogger("emily.repo.monitor")


# ══════════════════════════════════════════════════════════════════════════════
# 全景节点业务字段白名单（需求 V2 §4.3）
# ══════════════════════════════════════════════════════════════════════════════

NODE_BUSINESS_FIELDS = [
    "project_id", "node_id", "node_name", "owner_dept_id",
    "related_company_id", "deadline", "remark", "status",
]


class MonitorRepository:
    """监控只读查询。"""

    # ── 全景节点 ──

    @staticmethod
    def list_nodes(
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询全景节点列表（仅业务字段，排除 is_discarded）。

        Args:
            project_id: 按项目筛选（为空则全部）
            limit: 分页大小
            offset: 偏移量

        Returns:
            字典列表，每个字典仅包含 NODE_BUSINESS_FIELDS 中的字段。
        """
        with get_session() as session:
            q = session.query(ProjectNode).filter(
                ProjectNode.is_discarded == False  # noqa: E712
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            q = q.order_by(ProjectNode.created_at.desc())
            q = q.limit(limit).offset(offset)
            rows = q.all()
            result = []
            for row in rows:
                item = {}
                for field in NODE_BUSINESS_FIELDS:
                    val = getattr(row, field, None)
                    item[field] = val
                result.append(item)
            return result

    @staticmethod
    def get_node_detail(node_id: str) -> Optional[dict]:
        """查询单节点完整业务字段（按业务编号 node_id 查询）。

        Args:
            node_id: 节点业务编号（如 SG-JG-01-2026）

        Returns:
            业务字段字典，未找到返回 None。
        """
        with get_session() as session:
            row = (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.node_id == node_id,
                    ProjectNode.is_discarded == False,  # noqa: E712
                )
                .first()
            )
            if row is None:
                return None
            item = {}
            for field in NODE_BUSINESS_FIELDS:
                val = getattr(row, field, None)
                item[field] = val
            return item

    # ── 管控文件 ──

    @staticmethod
    def list_files(
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询管控文件列表（仅最新版本，排除已删除）。

        Returns:
            字典列表，含 file_no/filename/file_type/version/uploaded_by_name/
            created_at/confidentiality/is_latest。
        """
        with get_session() as session:
            q = session.query(File).filter(
                File.is_deleted == False,  # noqa: E712
                File.is_latest == True,     # noqa: E712
            )
            if project_id:
                q = q.filter(File.project_id == project_id)
            q = q.order_by(File.created_at.desc())
            q = q.limit(limit).offset(offset)
            rows = q.all()
            result = []
            for row in rows:
                # 关联查询上传者姓名
                uploader_name = ""
                if row.uploaded_by:
                    user = session.query(User).filter(User.id == row.uploaded_by).first()
                    if user:
                        uploader_name = user.username or ""
                result.append({
                    "file_no": row.file_no,
                    "filename": row.filename,
                    "file_type": row.file_type or "",
                    "version": row.version or "V1.0",
                    "uploaded_by_name": uploader_name,
                    "created_at": row.created_at,
                    "confidentiality": row.confidentiality or 0,
                    "is_latest": row.is_latest,
                })
            return result

    # ── 人员列表 ──

    @staticmethod
    def list_users(
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询人员列表（活跃用户，排除已删除）。

        company 字段是单 FK 存 company_info.id，需关联查名称。

        Returns:
            字典列表，含 id/username/company_name/level。
        """
        with get_session() as session:
            q = session.query(User).filter(
                User.is_deleted == False,  # noqa: E712
                User.status == "active",
            )
            q = q.order_by(User.level.desc(), User.username)
            q = q.limit(limit).offset(offset)
            rows = q.all()

            # 批量加载 company_info 名称映射
            all_companies = session.query(CompanyInfo).filter(
                CompanyInfo.is_deleted == False  # noqa: E712
            ).all()
            company_map = {c.id: c.company_name for c in all_companies}

            result = []
            for row in rows:
                # company 是单 FK，直接映射
                company_name = company_map.get(row.company, "") if row.company else ""

                result.append({
                    "id": row.id,
                    "username": row.username or "",
                    "company_name": company_name,
                    "level": row.level or 0,
                })
            return result

    # ── 会话最近消息 ──

    @staticmethod
    def list_recent_messages(
        conversation_id: str,
        limit: int = 5,
    ) -> list[dict]:
        """查询指定会话的最近 N 条消息摘要。

        Args:
            conversation_id: IM 会话 ID（业务 ID，非 UUID）
            limit: 返回条数

        Returns:
            字典列表，含 sender_name/direction/content_summary/created_at。
        """
        with get_session() as session:
            # 先查 Conversation UUID
            conv = session.query(Conversation).filter(
                Conversation.conversation_id == conversation_id,
            ).first()
            if conv is None:
                return []

            messages = (
                session.query(Message)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )
            result = []
            for msg in reversed(messages):  # 按时间正序返回
                content = msg.content or ""
                result.append({
                    "sender_name": msg.sender_name or "",
                    "direction": msg.direction or "",
                    "content_summary": content[:80] + ("..." if len(content) > 80 else ""),
                    "created_at": msg.created_at,
                })
            return result
