"""路由决策接口。

定义意图路由的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IntentType(Enum):
    """意图类型枚举。"""
    SOP = "sop"              # 单 SOP 命中
    COMPOUND = "compound"    # 复合意图，需拆解
    FALLBACK = "fallback"    # 未命中，走兜底
    FAST_REPLY = "fast_reply"  # 快速通道（闲聊）


@dataclass
class SubTask:
    """拆解后的子任务。

    Attributes:
        id: 子任务唯一标识
        sop_id: 匹配的 SOP 编号
        user_input: 子任务对应的用户输入片段
        depends_on: 依赖的前置子任务 ID 列表
        priority: 优先级（1=普通 0=最高）
    """
    id: str
    sop_id: str
    user_input: str = ""
    depends_on: list[str] = field(default_factory=list)
    priority: int = 1


@dataclass
class RouteDecision:
    """路由决策结果。

    Attributes:
        intent_type: 意图类型（sop/compound/fallback/fast_reply）
        sop_id: 匹配的 SOP 编号（未命中时为 None）
        confidence: 匹配置信度（high/medium/low/none）
        is_compound: 是否为复合意图
        sub_tasks: 拆解后的子任务列表（单意图时 len=1）
        fallback_reason: fallback 时的原因描述
    """
    intent_type: str = "fallback"  # 使用 IntentType 的值
    sop_id: str | None = None
    confidence: str = "none"       # "high" | "medium" | "low" | "none"
    is_compound: bool = False
    sub_tasks: list[SubTask] = field(default_factory=list)
    fallback_reason: str = ""

    # 额外元数据
    _source: str = ""
