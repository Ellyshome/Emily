"""adapters.session —— Session 池管理层（蓝图 §3.4）。

Emily Core 容器内的消息入口层：Session 池维护、入站消息路由、Session 生命周期管理。
"""

from .session_pool import SessionPoolManager
from .session_factory import SessionFactory
from .session_config import SessionConfig

__all__ = [
    "SessionPoolManager",
    "SessionFactory",
    "SessionConfig",
]
