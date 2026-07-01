"""Guardian 相关数据结构。"""

from __future__ import annotations

from enum import Enum


class GuardianVerdict(Enum):
    """守护审核标记。"""
    PASS = "pass"
    FLAG = "flag"
    REJECT = "reject"  # 保留，当前不使用
