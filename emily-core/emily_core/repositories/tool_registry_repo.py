"""ToolRegistryRepo —— API 注册表持久化操作。

提供 tool_registry 表的 CRUD 与查询能力。
"""

from __future__ import annotations

import json
import logging

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
        exposure_mode: str = "meta",
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
                    existing.exposure_mode = exposure_mode
                    existing.handler_module = handler_module
                    existing.updated_at = now
                else:
                    row = ToolRegistryModel(
                        id=api_id,
                        signature=signature,
                        display_name=display_name,
                        category=category,
                        permission_flag=permission_flag,
                        exposure_mode=exposure_mode,
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
    def get_available(level: int = 0) -> list[dict]:
        """查询当前用户按**级别**可用的 API 列表。

        权限过滤规则：
          - category=base     → 全部用户可用
          - category=business → 检查 permission_flag vs 6 级权限
              all      → 全部用户（含 L1 访客）
              read_l4  → L4 条线负责人及以上（线性 >=4；用于"模板库读取"等 L4 门槛的只读能力）
              write_l2 → L2 参建执行及以上（线性 >=2；用于"一线可上传"的追加型写工具）
              write    → L3 参建管理及以上
              admin    → L5 管理员及以上
          - category=project  → 仅 L5-L6 管理员可用

        注意：本方法**不做 SOP 授权判定**。已授权 SOP 所声明工具的放行，由调用方
        （session/fetchers/fetch_available_tools.py）在拿到本结果后并集处理——
        层级职责不同：本仓库只管 tool_registry 表自身，不感知 SOP 文档。
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
                        if level >= 5:
                            available.append(_row_to_dict(row))
                        continue

                    # business 类别按权限过滤
                    if row.permission_flag == "all":
                        available.append(_row_to_dict(row))
                    elif row.permission_flag == "admin" and level >= 5:
                        available.append(_row_to_dict(row))
                    elif row.permission_flag == "read_l4" and level >= 4:
                        available.append(_row_to_dict(row))
                    elif row.permission_flag == "write_l2" and level >= 2:
                        available.append(_row_to_dict(row))
                    elif row.permission_flag == "write" and level >= 3:
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
        "exposure_mode": row.exposure_mode,
        "handler_module": row.handler_module,
        "is_active": row.is_active,
        "registered_at": row.registered_at,
        "updated_at": row.updated_at,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
