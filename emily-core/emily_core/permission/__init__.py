"""权限管理子系统 —— RBAC + ABAC 混合模型（6 级树形继承）。

模块组成：
  - level.py: PermissionLevel 枚举 + INHERITANCE_CHAIN 树形继承 + can_access()
  - code_compiler.py: PermissionCodeCompiler 权限编码解析/匹配
  - auth_engine.py: PermissionAuthEngine 三维树形鉴权
  - row_security.py: SQLAlchemy before_execute 行级安全拦截器 + PermissionAuditLogRepository
  - cache.py: PermissionCache 两级缓存（L1 矩阵 + L2 用户白名单）

实施计划见 需求文件/权限管理系统/权限管理系统-实施计划.md
"""
from .level import (
    INHERITANCE_CHAIN,
    LEVEL_NAME,
    PermissionLevel,
    can_access,
    effective_levels,
    is_admin,
    is_sys_admin,
)
from .code_compiler import (
    SECURITY_LEVELS,
    SECURITY_LEVEL_NAME,
    CompiledCode,
    can_view_security_level,
    code_matches_any,
    compile_code,
)
from .auth_engine import (
    AccessCheckResult,
    PermissionAuthEngine,
)
from .cache import (
    PermissionCache,
    PermissionMatrix,
)
from .row_security import (
    PermissionAuditLogRepository,
    get_current_permission_snapshot,
    register_row_security_listener,
    restore_auth_injection,
    set_current_permission_snapshot,
    skip_auth_injection,
)

__all__ = [
    # level
    "PermissionLevel",
    "INHERITANCE_CHAIN",
    "LEVEL_NAME",
    "effective_levels",
    "can_access",
    "is_admin",
    "is_sys_admin",
    # code_compiler
    "SECURITY_LEVELS",
    "SECURITY_LEVEL_NAME",
    "CompiledCode",
    "compile_code",
    "code_matches_any",
    "can_view_security_level",
    # auth_engine
    "AccessCheckResult",
    "PermissionAuthEngine",
    # cache
    "PermissionCache",
    "PermissionMatrix",
    # row_security
    "PermissionAuditLogRepository",
    "set_current_permission_snapshot",
    "get_current_permission_snapshot",
    "skip_auth_injection",
    "restore_auth_injection",
    "register_row_security_listener",
]
