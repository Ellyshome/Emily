"""SessionState —— Session 会话状态机（蓝图 §4.4）。

    CREATED → ACTIVE → WAITING_CONFIRM → ACTIVE → ARCHIVING → CLOSED

注：蓝图明确指出"状态机细节尚未完善，后期需根据使用需求逐步补充丰富"。
此处为基础骨架，子状态/过渡条件/异常恢复路径待后续完善。
"""

from __future__ import annotations

from enum import Enum


class SessionState(Enum):
    """Session 状态枚举（蓝图 §4.4）。"""

    CREATED = "CREATED"                  # Session 创建，最小化灌注
    ACTIVE = "ACTIVE"                    # 正常工作状态
    WAITING_CONFIRM = "WAITING_CONFIRM"  # 等待用户确认（WorkItem 交互）
    ARCHIVING = "ARCHIVING"              # 注销归档中
    CLOSED = "CLOSED"                    # 已关闭，等待安全销毁


# 合法状态转换表（基础骨架）
TRANSITIONS: dict[SessionState, list[SessionState]] = {
    SessionState.CREATED:         [SessionState.ACTIVE],
    SessionState.ACTIVE:          [
        SessionState.ACTIVE,            # 继续接收消息
        SessionState.WAITING_CONFIRM,
        SessionState.ARCHIVING,
    ],
    SessionState.WAITING_CONFIRM: [SessionState.ACTIVE, SessionState.ARCHIVING],
    SessionState.ARCHIVING:       [SessionState.CLOSED],
    SessionState.CLOSED:          [],   # 终态
}

TERMINAL_STATES = frozenset({SessionState.CLOSED})
