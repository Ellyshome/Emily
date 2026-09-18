"""权限审计日志仓储 —— 记录授权（GRANT）/ 回收（REVOKE）/ 越权拒绝（ACCESS_DENIED）。

> **历史说明（2026-09-18，决策 Q9）**：本模块原含一个 SQLAlchemy `do_orm_execute`
> 行级安全拦截器（按 `company_id` 向 SELECT 注入"本单位 + 合作单位"过滤）。
> 排查确认它**自建立起从未生效**（缺口 G-13）：白名单声明的
> `events/tasks/files/messages.company_id` 四列在库中并不存在，
> `ORMExecuteState` 亦无 `select_statement` 属性（SQLAlchemy 2.0），
> 且权限快照的注入入口从未被调用 —— 注入必然抛错并被 fail-open 分支吞掉。
> 按"设计尽量简洁、确有需要再加"的原则，已整段删除（含 thread-local 上下文、
> 启动自检与注册调用），不留"看似有行级安全"的残迹。
>
> **行可见性的现归属**：由服务层承担 —— `VisibleFileSetResolver`（文件可见集）、
> `ParticipationRepo`（企业参与节点 → 节点/项目范围）、`query_service` 的
> 项目/参与关系过滤、能力层 fail-closed 门禁。若将来确需行级统一兜底，
> 须先补归属列（或改用 `project_id` 推导）并单独立项。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.permission.row_security")


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
