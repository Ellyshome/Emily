"""OpsConfig — 运维模块运行时配置 dataclass。

Pulled from the global Config's ops_* fields. Holds only the values the
ops module needs at runtime; the global Config remains the single source
of truth for defaults.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OpsConfig:
    """运维模块运行时配置。

    All defaults here are non-authoritative — the authoritative defaults
    live in config.py's Config dataclass. This dataclass exists so the
    ops module doesn't need a full Config reference.
    """

    enabled: bool = True
    """运维调度模块总开关"""

    tick_interval_seconds: int = 300
    """Tick 执行间隔（秒），默认 300（5分钟）"""

    stale_probe_enabled: bool = True
    """卡滞节点检测探针开关"""

    stale_threshold_days: int = 14
    """节点卡滞判定阈值（天）"""

    deadline_warn_days: int = 7
    """milestone 节点到期前 N 天开始预警"""

    alert_cooldown_hours: int = 24
    """同一节点同一问题的告警冷却时间（小时）"""

    mailbox_enabled: bool = False
    """邮箱轮询探针开关"""

    mail_imap_host: str = ""
    """IMAP 服务器地址"""

    mail_imap_port: int = 993
    """IMAP 端口（993 SSL）"""

    mail_username: str = ""
    """邮箱用户名/地址"""

    mail_password: str = ""
    """邮箱密码/授权码"""

    mail_sender_whitelist: str = ""
    """发件人白名单，逗号分隔"""

    health_probe_enabled: bool = False
    """健康度检查探针开关"""

    startup_report_enabled: bool = True
    """冷启动报告开关"""

    fallback_log_dir: str = "logs/"
    """降级备份文件目录"""

    @classmethod
    def from_global_config(cls, cfg: "Config") -> "OpsConfig":
        """从全局 Config dataclass 提取同名字段。"""
        return cls(
            enabled=getattr(cfg, "ops_enabled", True),
            tick_interval_seconds=getattr(cfg, "project_agent_tick_seconds", 300),
            stale_probe_enabled=getattr(cfg, "ops_stale_probe_enabled", True),
            stale_threshold_days=getattr(cfg, "project_agent_stale_threshold_days", 14),
            deadline_warn_days=getattr(cfg, "project_agent_deadline_warn_days", 7),
            alert_cooldown_hours=getattr(cfg, "project_agent_alert_cooldown_hours", 24),
            mailbox_enabled=getattr(cfg, "ops_mailbox_enabled", False),
            mail_imap_host=getattr(cfg, "ops_mail_imap_host", ""),
            mail_imap_port=getattr(cfg, "ops_mail_imap_port", 993),
            mail_username=getattr(cfg, "ops_mail_username", ""),
            mail_password=getattr(cfg, "ops_mail_password", ""),
            mail_sender_whitelist=getattr(cfg, "ops_mail_sender_whitelist", ""),
            health_probe_enabled=getattr(cfg, "ops_health_probe_enabled", False),
            startup_report_enabled=getattr(cfg, "ops_startup_report_enabled", True),
            fallback_log_dir=getattr(cfg, "ops_fallback_log_dir", "logs/"),
        )
