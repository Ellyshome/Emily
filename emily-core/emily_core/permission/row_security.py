"""行级安全拦截器 —— SQLAlchemy before_execute 事件监听（设计文档 §5.3）。

自动注入 company_id 过滤条件，实现行级数据隔离：
  - 用户只能查询自身公司 + partner 公司的数据
  - 可过滤表白名单：events/tasks/files/messages（含 company_id 归属列的表）
  - JOIN/UNION/子查询仅处理 leftmost 主表（安全范围）
  - 无法安全注入的复杂查询：fail-open + WARNING 日志

实现要点（设计文档 §5.3.1）：
  - Thread-local 标记位 _skip_auth_injection 防重复注入
  - 可过滤表清单白名单声明
  - 仅处理 SELECT 语句
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.sql import select

logger = logging.getLogger("emily.permission.row_security")

# ══════════════════════════════════════════════════════════════════════════════
# 可过滤表白名单（仅对含 company_id 归属列的表注入过滤）
# ══════════════════════════════════════════════════════════════════════════════

_FILTERABLE_TABLES: dict[str, str] = {
    # 表名 → company_id 列名
    "events": "company_id",
    "tasks": "company_id",
    "files": "company_id",
    "messages": "company_id",
}

# ══════════════════════════════════════════════════════════════════════════════
# Thread-local 上下文
# ══════════════════════════════════════════════════════════════════════════════

_local = threading.local()


def set_current_permission_snapshot(snapshot) -> None:
    """设置当前线程的权限快照（Session 层调用）。"""
    _local.permission_snapshot = snapshot


def get_current_permission_snapshot():
    """获取当前线程的权限快照。"""
    return getattr(_local, "permission_snapshot", None)


def skip_auth_injection() -> None:
    """标记当前线程跳过行级安全注入（系统内部查询时调用）。"""
    _local.skip_auth = True


def restore_auth_injection() -> None:
    """恢复当前线程的行级安全注入。"""
    _local.skip_auth = False


def _is_skip() -> bool:
    """检查是否应跳过注入。"""
    return getattr(_local, "skip_auth", False)


# ══════════════════════════════════════════════════════════════════════════════
# 行级安全拦截器
# ══════════════════════════════════════════════════════════════════════════════

_auth_listener_registered = False


def register_row_security_listener() -> None:
    """注册 SQLAlchemy before_execute 行级安全拦截器。

    全局只注册一次。应在应用启动时调用。
    使用 SQLAlchemy 2.0 的 SessionEvents.before_execute 事件。
    """
    global _auth_listener_registered
    if _auth_listener_registered:
        return
    _auth_listener_registered = True

    @event.listens_for(Session, "do_orm_execute")
    def _inject_company_filter(orm_execute_state):
        """do_orm_execute 钩子：对 SELECT 查询自动注入 company_id 过滤。

        SQLAlchemy 2.0 推荐使用 do_orm_execute 替代 before_execute，
        可以安全地修改 ORM 查询的 WHERE 条件。
        """
        # 跳过标记
        if _is_skip():
            return

        # 仅处理 SELECT
        if not orm_execute_state.is_select:
            return

        # 获取权限快照
        perms = get_current_permission_snapshot()
        if perms is None:
            return

        # 构建允许的 company_id 列表
        allowed_ids = _build_allowed_company_ids(perms)
        if not allowed_ids:
            return

        # 尝试注入过滤条件
        try:
            _try_inject_orm_filter(orm_execute_state, allowed_ids)
        except Exception as e:
            # fail-open：注入失败时记录 WARNING 但不阻断
            logger.warning(
                "Row security injection failed, fail-open: %s",
                e,
            )


def _build_allowed_company_ids(perms) -> list[str]:
    """从权限快照构建允许访问的 company_id 列表。"""
    ids: list[str] = []
    if perms.company_id:
        ids.append(perms.company_id)
    if perms.partner_ids:
        ids.extend(pid for pid in perms.partner_ids if pid and pid not in ids)

    # L5+ 管理员可看所有公司数据（不注入过滤）
    from .level import is_admin
    if is_admin(perms.level):
        return []

    return ids


def _try_inject_orm_filter(orm_execute_state, allowed_ids: list[str]):
    """对 ORM 查询注入 company_id IN (...) 过滤条件。

    通过修改 orm_execute_state.session 对应查询的 WHERE 条件实现。
    仅对白名单中的表注入。
    """
    from sqlalchemy import and_

    # 遍历查询涉及的实体（映射类）
    for entity in orm_execute_state.select_statement.column_slices:
        # 获取实体对应的表
        table = _get_entity_table(entity)
        if table is None:
            continue
        table_name = table.name if hasattr(table, 'name') else str(table)
        if table_name not in _FILTERABLE_TABLES:
            continue

        company_col_name = _FILTERABLE_TABLES[table_name]
        company_col = _find_table_column(table, company_col_name)
        if company_col is None:
            continue

        # 注入 company_id IN (...) 条件
        condition = company_col.in_(allowed_ids)
        existing_where = orm_execute_state.select_statement.whereclause
        if existing_where is not None:
            orm_execute_state.select_statement = orm_execute_state.select_statement.where(
                and_(existing_where, condition)
            )
        else:
            orm_execute_state.select_statement = orm_execute_state.select_statement.where(condition)


def _get_entity_table(entity):
    """从 ORM 查询实体提取底层 Table 对象。"""
    # Mapper entity
    if hasattr(entity, 'mapper'):
        mapper = entity.mapper
        if hasattr(mapper, 'local_table'):
            return mapper.local_table
    # Column property
    if hasattr(entity, '__clause_element__'):
        el = entity.__clause_element__()
        if hasattr(el, 'table'):
            return el.table
    # Direct table
    if hasattr(entity, 'table'):
        return entity.table
    return None


def _find_table_column(table, col_name: str) -> Optional:
    """在 Table 对象中查找指定名称的列。"""
    if hasattr(table, 'columns') and hasattr(table.columns, col_name):
        return table.columns[col_name]
    if hasattr(table, 'c') and hasattr(table.c, col_name):
        return table.c[col_name]
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 审计日志 Repository（轻量，auth_engine 使用）
# ══════════════════════════════════════════════════════════════════════════════


class PermissionAuditLogRepository:
    """权限审计日志数据访问 —— 仅 INSERT（需求 §8.2）。

    遵循项目约定：纯 @staticmethod，可选 session 参数。
    """

    @staticmethod
    def log_access_denied(grantee_id: str, perm_code: str, *,
                          reason: str = "", session=None) -> None:
        """写入 ACCESS_DENIED 审计记录。"""
        from ..infrastructure.database.models import PermissionAuditLog, _utc_now
        from ..infrastructure.database.session import get_session as _get_session
        from typing import Optional as _Opt
        from sqlalchemy.orm import Session as _Session

        def _impl(sess: _Session):
            log = PermissionAuditLog(
                event_time=_utc_now(),
                grantor_id="",
                grantee_id=grantee_id,
                perm_code=perm_code,
                grant_type="",
                operation_type="ACCESS_DENIED",
                remark=reason,
            )
            sess.add(log)
            sess.flush()

        if session is not None:
            _impl(session)
        else:
            with _get_session() as sess:
                _impl(sess)

    @staticmethod
    def log_grant(grantor_id: str, grantee_id: str, perm_code: str,
                  grant_type: str, *, duration: int = None,
                  session_id: str = "", remark: str = "", session=None) -> None:
        """写入 GRANT 审计记录。"""
        from ..infrastructure.database.models import PermissionAuditLog, _utc_now
        from ..infrastructure.database.session import get_session as _get_session
        from sqlalchemy.orm import Session as _Session

        def _impl(sess: _Session):
            log = PermissionAuditLog(
                event_time=_utc_now(),
                grantor_id=grantor_id,
                grantee_id=grantee_id,
                perm_code=perm_code,
                grant_type=grant_type,
                duration=duration,
                session_id=session_id,
                operation_type="GRANT",
                remark=remark,
            )
            sess.add(log)
            sess.flush()

        if session is not None:
            _impl(session)
        else:
            with _get_session() as sess:
                _impl(sess)

    @staticmethod
    def log_revoke(grantor_id: str, grantee_id: str, perm_code: str, *,
                   remark: str = "", session=None) -> None:
        """写入 REVOKE 审计记录。"""
        from ..infrastructure.database.models import PermissionAuditLog, _utc_now
        from ..infrastructure.database.session import get_session as _get_session
        from sqlalchemy.orm import Session as _Session

        def _impl(sess: _Session):
            log = PermissionAuditLog(
                event_time=_utc_now(),
                grantor_id=grantor_id,
                grantee_id=grantee_id,
                perm_code=perm_code,
                operation_type="REVOKE",
                remark=remark,
            )
            sess.add(log)
            sess.flush()

        if session is not None:
            _impl(session)
        else:
            with _get_session() as sess:
                _impl(sess)

    @staticmethod
    def query_logs(grantee_id: str = "", operation_type: str = "",
                   limit: int = 100, *, session=None) -> list:
        """查询审计日志。"""
        from ..infrastructure.database.models import PermissionAuditLog
        from ..infrastructure.database.session import get_session as _get_session
        from sqlalchemy.orm import Session as _Session

        def _impl(sess: _Session):
            q = sess.query(PermissionAuditLog)
            if grantee_id:
                q = q.filter(PermissionAuditLog.grantee_id == grantee_id)
            if operation_type:
                q = q.filter(PermissionAuditLog.operation_type == operation_type)
            q = q.order_by(PermissionAuditLog.log_id.desc())
            return q.limit(limit).all()

        if session is not None:
            return _impl(session)
        with _get_session() as sess:
            return _impl(sess)
