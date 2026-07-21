"""鉴权引擎接口。

定义鉴权决策的数据结构（鉴权由 workitem_agent.authorize() 自包含）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
