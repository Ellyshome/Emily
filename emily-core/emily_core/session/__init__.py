"""session —— Session 层（蓝图 §4）。

Session 层是每条用户会话的主脑。以 SessionAgent 为核心，负责自然语言交互、
意图识别 + WorkItem 拆分、多 WorkItem 编排、出站审核、注销归档。
"""

from .session_agent import SessionAgent
from .session_state import SessionState, TRANSITIONS, TERMINAL_STATES
from .session_context import SessionContext
from .focus_lock import FocusLock
from .confirm_queue import ConfirmQueue

__all__ = [
    "SessionAgent",
    "SessionState",
    "TRANSITIONS",
    "TERMINAL_STATES",
    "SessionContext",
    "FocusLock",
    "ConfirmQueue",
]
