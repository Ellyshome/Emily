"""SessionConfig —— Session 池配置（蓝图 §3.4）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionConfig:
    """Session 池行为配置。"""

    ttl_seconds: int = 600
    """Session 无新消息过期时间（默认 10 分钟，蓝图 §3.4）。"""

    task_idle_seconds: int = 180
    """任务完成后空闲超时（秒），默认 3 分钟。任务段制下补充 ttl_seconds。"""

    hard_limit_seconds: int = 1800
    """Session 硬上限（秒），默认 30 分钟。超时强制归档。"""

    max_concurrent: int = 100
    """最大并发 Session 数（蓝图 §10.4 session_max_concurrent）。"""

    max_workitems_per_session: int = 5
    """每 Session 最大 WorkItem 数（蓝图 §10.4 workitem_max_per_session）。"""

    sweep_interval_seconds: int = 60
    """后台过期扫描间隔（任务段制更敏感，从 300s 调短到 60s）。"""

    @classmethod
    def from_config(cls, config) -> "SessionConfig":
        """从全局 Config 提取 Session 相关字段。"""
        return cls(
            ttl_seconds=getattr(config, "session_ttl_seconds", 600),
            max_concurrent=getattr(config, "session_max_concurrent", 100),
            max_workitems_per_session=getattr(config, "workitem_max_per_session", 5),
        )
