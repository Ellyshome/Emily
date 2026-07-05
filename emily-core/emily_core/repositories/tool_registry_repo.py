"""ToolRegistryRepo —— API 注册表持久化操作。

提供 tool_registry 表的 CRUD 与查询能力。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database import get_session
from ..infrastructure.database.models import ToolRegistryModel

logger = logging.getLogger("emily.repo.tool_registry")


class ToolRegistryRepo:
    """API 注册表 Repository。"""

    @staticmethod
    def upsert(
        api_id: str,
        signature: str = "{}",
        display_name: str = "",
        category: str = "base",
        permission_flag: str = "all",
        handler_module: str = "",
    ) -> bool:
        """注册或更新 API 元数据。"""
        try:
            now = _now_iso()
            with get_session() as session:
                existing = session.query(ToolRegistryModel).filter(
                    ToolRegistryModel.id == api_id
                ).first()

                if existing:
                    existing.signature = signature
                    existing.display_name = display_name
                    existing.category = category
                    existing.permission_flag = permission_flag
                    existing.handler_module = handler_module
                    existing.updated_at = now
                else:
                    row = ToolRegistryModel(
                        id=api_id,
                        signature=signature,
                        display_name=display_name,
                        category=category,
                        permission_flag=permission_flag,
                        handler_module=handler_module,
                        is_active=True,
                        registered_at=now,
                        updated_at=now,
                    )
                    session.add(row)

                session.commit()
                return True
        except Exception as e:
            logger.error("ToolRegistryRepo.upsert(%s) failed: %s", api_id, e)
            return False

    @staticmethod
    def get_available(
        permission_level: int = 0,
        sop_allow: Optional[list[str]] = None,
    ) -> list[dict]:
        """查询当前用户可用的 API 列表。

        权限过滤规则：
          - category=base     → 全部用户可用
          - category=business → 检查 permission_flag vs 6 级权限
          - category=project  → 仅 L5-L6 管理员可用
        """
        try:
            with get_session() as session:
                query = session.query(ToolRegistryModel).filter(
                    ToolRegistryModel.is_active == True
                )
                rows = query.all()

                available: list[dict] = []
                for row in rows:
                    # base 类别全部可用
                    if row.category == "base":
                        available.append(_row_to_dict(row))
                        continue

                    # project 类别仅管理员
                    if row.category == "project":
                        if permission_level >= 5:
                            available.append(_row_to_dict(row))
                        continue

                    # business 类别按权限过滤
                    if row.permission_flag == "all":
                        available.append(_row_to_dict(row))
                    elif row.permission_flag == "admin" and permission_level >= 5:
                        available.append(_row_to_dict(row))
                    elif row.permission_flag == "write" and permission_level >= 3:
                        available.append(_row_to_dict(row))
                    # 其余保持不变（包括 readable 等自定义权限）

                return available
        except Exception as e:
            logger.error("ToolRegistryRepo.get_available failed: %s", e)
            return []

    @staticmethod
    def get_all_active() -> list[dict]:
        """获取全部活跃 API。"""
        try:
            with get_session() as session:
                rows = session.query(ToolRegistryModel).filter(
                    ToolRegistryModel.is_active == True
                ).all()
                return [_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("ToolRegistryRepo.get_all_active failed: %s", e)
            return []

    @staticmethod
    def deactivate(api_id: str) -> bool:
        """停用 API（软删除）。"""
        try:
            with get_session() as session:
                row = session.query(ToolRegistryModel).filter(
                    ToolRegistryModel.id == api_id
                ).first()
                if row:
                    row.is_active = False
                    row.updated_at = _now_iso()
                    session.commit()
                    return True
                return False
        except Exception as e:
            logger.error("ToolRegistryRepo.deactivate(%s) failed: %s", api_id, e)
            return False

    @staticmethod
    def get_all() -> list[dict]:
        """获取全部 API（含停用，供 register_api.py --list 使用）。"""
        try:
            with get_session() as session:
                rows = session.query(ToolRegistryModel).all()
                return [_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("ToolRegistryRepo.get_all failed: %s", e)
            return []


def _row_to_dict(row: ToolRegistryModel) -> dict:
    return {
        "api_id": row.id,
        "display_name": row.display_name,
        "signature": row.signature,
        "category": row.category,
        "permission_flag": row.permission_flag,
        "handler_module": row.handler_module,
        "is_active": row.is_active,
        "registered_at": row.registered_at,
        "updated_at": row.updated_at,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
