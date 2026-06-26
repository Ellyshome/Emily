"""权限层级模型 —— 6 级树形继承（需求 §2）。

业务线分叉：
  参建线: L1 访客 → L2 参建执行 → L3 参建管理
  建设线: L1 访客 → L4 建设主管 → L5 管理员 → L6 系统管理员

⚠ 关键：L4 继承 L1（非 L3），建设线与参建线在 L1 后分叉。这是树形而非线性。

鉴权语义：用户持有级别 X 时，自动拥有其继承链上所有级别的权限。
  can_access(user_level, required_level) = required_level in effective_levels(user_level)

跨线访问（如 L5 需访问 L2 资源）通过 SOPPermissionBinding 绑定多权限组，
或临时/永久授权形式解决，不破坏树形继承语义。
"""
from __future__ import annotations

from enum import Enum


class PermissionLevel(Enum):
    """6 级权限分组（需求 §2.1）。值域 1-6，User.permission_level 字段直接存数值。"""

    GUEST = 1              # L1 访客：所有接入用户自动获得
    PARTICIPANT_EXEC = 2   # L2 参建执行：非建设单位人员基础权限
    PARTICIPANT_MGR = 3    # L3 参建管理：参建单位管理人员
    OWNER_SUPERVISOR = 4   # L4 建设主管：建设单位专业主管
    ADMIN = 5              # L5 管理员：全局管理员
    SYS_ADMIN = 6          # L6 系统管理员：最高系统权限


# 继承链（含自身）—— 树形：每个级别持有自身 + 所有祖先级别的权限
# 参建线: 1 ← 2 ← 3
# 建设线: 1 ← 4 ← 5 ← 6
INHERITANCE_CHAIN: dict[int, frozenset[int]] = {
    1: frozenset({1}),
    2: frozenset({2, 1}),           # 参建执行 ⊃ 访客
    3: frozenset({3, 2, 1}),        # 参建管理 ⊃ 参建执行 ⊃ 访客
    4: frozenset({4, 1}),           # 建设主管 ⊃ 访客（非参建线）
    5: frozenset({5, 4, 1}),        # 管理员 ⊃ 建设主管 ⊃ 访客
    6: frozenset({6, 5, 4, 1}),     # 系统管理员 ⊃ 管理员 ⊃ 建设主管 ⊃ 访客
}

# 级别中文名（用于审计日志 / 用户提示）
LEVEL_NAME: dict[int, str] = {
    1: "访客",
    2: "参建执行",
    3: "参建管理",
    4: "建设主管",
    5: "管理员",
    6: "系统管理员",
}


def effective_levels(user_level: int) -> frozenset[int]:
    """用户经继承后实际持有的全部级别集合（含自身）。

    未知级别（如 0、负数）降级为 {L1}，确保 fail-safe 为最低权限。
    """
    return INHERITANCE_CHAIN.get(user_level, INHERITANCE_CHAIN[PermissionLevel.GUEST.value])


def can_access(user_level: int, required_level: int) -> bool:
    """树形继承鉴权：所需级别在用户继承链内即放行。

    Args:
        user_level: 用户当前 permission_level（1-6）
        required_level: 资源/SOP 要求的最低 permission_level（1-6）

    Returns:
        True 若用户经继承持有 required_level 权限

    Examples:
        >>> can_access(3, 2)   # L3 参建管理 访问 L2 资源
        True
        >>> can_access(4, 2)   # L4 建设主管 访问 L2 资源（跨线，不继承）
        False
        >>> can_access(5, 2)   # L5 管理员 访问 L2 资源（建设线不含参建线）
        False
        >>> can_access(6, 4)   # L6 系统管理员 访问 L4 资源
        True
    """
    return required_level in effective_levels(user_level)


def is_admin(user_level: int) -> bool:
    """是否管理员及以上（L5+）。用于永久授权、强制撤销等高权限操作判定。"""
    return user_level >= PermissionLevel.ADMIN.value


def is_sys_admin(user_level: int) -> bool:
    """是否系统管理员（L6）。用于系统配置、权限规则定义等最高权限判定。"""
    return user_level >= PermissionLevel.SYS_ADMIN.value
