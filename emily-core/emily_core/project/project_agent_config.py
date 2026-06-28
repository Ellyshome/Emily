"""ProjectAgentConfig — runtime configuration for the project-level agent.

Pulled from the global Config's project_agent_* fields. Holds only the
values the agent needs at runtime; the global Config remains the single
source of truth for defaults.
"""

from dataclasses import dataclass


@dataclass
class ProjectAgentConfig:
    """Runtime configuration for ProjectAgent.

    All defaults here are non-authoritative — the authoritative defaults
    live in config.py's Config dataclass. This dataclass exists so the
    ProjectAgent doesn't need a full Config reference.
    """

    enabled: bool = True
    """Master switch for the project agent."""

    tick_seconds: int = 300
    """Tick loop interval in seconds (default 5 min)."""

    stale_threshold_days: int = 14
    """Days before a non-terminal node is considered 'stale'."""

    deadline_warn_days: int = 7
    """Days before planned_end_date to start warning about milestones."""

    alert_cooldown_hours: int = 24
    """Minimum hours between repeated alerts for the same node+issue."""

    ops_enabled: bool = True
    """运维模块是否启用。False 时 OpsScheduler 不注入到 ProjectAgent。"""

    @classmethod
    def from_config(cls, config) -> "ProjectAgentConfig":
        """Build from the global Config dataclass."""
        return cls(
            enabled=getattr(config, "project_agent_enabled", True),
            tick_seconds=getattr(config, "project_agent_tick_seconds", 300),
            stale_threshold_days=getattr(config, "project_agent_stale_threshold_days", 14),
            deadline_warn_days=getattr(config, "project_agent_deadline_warn_days", 7),
            alert_cooldown_hours=getattr(config, "project_agent_alert_cooldown_hours", 24),
        )
