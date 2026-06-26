"""Project-level autonomous agent package.

Exports:
    ProjectAgent      — background tick loop for state-machine maintenance
    ProjectAgentConfig — runtime configuration dataclass
    StaleDetector     — stale node / deadline detection (Phase 1)
"""

from .project_agent import ProjectAgent
from .project_agent_config import ProjectAgentConfig
from .maintenance.stale_detector import StaleDetector, StaleDetectionResult

__all__ = [
    "ProjectAgent",
    "ProjectAgentConfig",
    "StaleDetector",
    "StaleDetectionResult",
]
