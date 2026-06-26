"""State machine active-maintenance sub-module.

Phase 1:  stale_detector  — stuck-node detection + milestone deadline warnings
Phase 2+: dependency_validator, deadline_watcher (reserved)
"""

from .stale_detector import StaleDetector, StaleDetectionResult, StaleNode, MilestoneWarning

__all__ = [
    "StaleDetector",
    "StaleDetectionResult",
    "StaleNode",
    "MilestoneWarning",
]
