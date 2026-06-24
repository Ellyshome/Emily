"""鉴权引擎接口。

定义鉴权决策的数据结构和 AuthEngine 接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .routing import RouteDecision


class AuthDecision(Enum):
    """鉴权决策枚举。"""
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class AuthResult:
    """鉴权结果。

    Attributes:
        decision: 鉴权决策（ALLOW/DENY）
        reason: 拒绝原因（仅 DENY 时填充）
        matched_roles: 用户匹配到的角色列表
    """
    decision: AuthDecision
    reason: str = ""
    matched_roles: list[str] = field(default_factory=list)

    # 额外元数据（用于标注来源等）
    _source: str = ""

    @property
    def is_allowed(self) -> bool:
        return self.decision == AuthDecision.ALLOW

    @property
    def is_denied(self) -> bool:
        return self.decision == AuthDecision.DENY


class AuthEngine(ABC):
    """鉴权引擎接口。

    输入: user_id + RouteDecision（含 sub_tasks + sop_ids）
    输出: AuthResult（ALLOW/DENY）

    Mock: 始终返回 ALLOW
    真实: 读取 User.perm_list/grouping → 匹配 SOP.allow_roles → 决策
    """

    @abstractmethod
    async def authorize(
        self, user_id: str, route_decision: Any
    ) -> AuthResult:
        """执行鉴权决策。

        Args:
            user_id: 用户 ID
            route_decision: RouteDecision 对象（含 intent_type/sop_id/sub_tasks）

        Returns:
            AuthResult: 鉴权结果
        """
        ...
