"""RouteDecision —— 接管决策对象。"""

from dataclasses import dataclass


@dataclass
class RouteDecision:
    """DomainTakeoverService 的输出，描述是否接管当前消息。

    仅基于 @机器人 等规则判断是否接管，不涉及意图分类。
    意图分类已由 MasterAgent + 决策树导航统一处理。
    """

    takeover: bool
    """是否接管"""

    mode: str = "collaborate"
    """接管模式: observe / collaborate / managed"""

    intent: str | None = None
    """意图类型（预留字段，当前由 MasterAgent 决策树处理）"""

    confidence: float = 0.0
    """置信度"""

    handler: str | None = None
    """处理器名称（预留字段）"""

    should_reply: bool = True
    """是否需要回复。观察模式下为 False"""

    reason: str = ""
    """决策原因，用于日志追踪"""
