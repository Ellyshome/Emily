"""ProjectAgent — project-level autonomous agent.

Operates at the *project* scope (above SessionAgent / WorkItemAgent),
performing three duties in a background tick loop:

  1. State machine active maintenance  (stale detection, deadline watching)
  2. Health checks                      (Phase 2+)
  3. AI automated operations            (Phase 3+)

Phase 1 implements the skeleton + stale detection (rule-based, no LLM).
Follows the same _loop() → _tick() → advisory-lock pattern as PlanTaskScheduler.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .maintenance.stale_detector import StaleDetector
from .project_agent_config import ProjectAgentConfig

if TYPE_CHECKING:
    from ..repositories.sm_node_repo import SMNodeRepository
    from ..outbound_bus import OutboundEventBus

logger = logging.getLogger("emily.project_agent")

# Advisory lock key for multi-process mutual exclusion
_LOCK_KEY = "project_agent:global_tick"


class ProjectAgent:
    """Project-level autonomous agent — background tick loop.

    Lifecycle: CREATED → STARTING → ACTIVE → STOPPING → STOPPED

    When a tick fails non-fatally the agent enters DEGRADED mode
    (logs + continues) rather than crashing the whole loop.
    """

    def __init__(
        self,
        config: ProjectAgentConfig,
        node_repo: "SMNodeRepository",
        outbound_bus: "OutboundEventBus",
    ):
        self._config = config
        self._running = False
        self._task: asyncio.Task | None = None

        # Phase 1: stale detector
        self._stale_detector = StaleDetector(
            node_repo=node_repo,
            outbound_bus=outbound_bus,
            stale_threshold_days=config.stale_threshold_days,
            deadline_warn_days=config.deadline_warn_days,
            alert_cooldown_hours=config.alert_cooldown_hours,
        )

        # Phase 2+: health checker (not yet wired)
        # Phase 3+: ops runner (not yet wired)

        # Phase 3: ops scheduler (injected by EmilyCore._init_ops_module)
        self._ops_scheduler = None  # OpsScheduler | None

    # ── Lifecycle ──

    async def start(self) -> None:
        """Start the background tick loop.

        Safe to call when already running (no-op).
        """
        if not self._config.enabled:
            logger.info("ProjectAgent: disabled by config")
            return
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "ProjectAgent: started (tick=%ds, stale_threshold=%dd, deadline_warn=%dd)",
            self._config.tick_seconds,
            self._config.stale_threshold_days,
            self._config.deadline_warn_days,
        )

    async def stop(self) -> None:
        """Gracefully stop the background loop.

        Awaits the current tick to finish, then cancels the task.
        """
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ProjectAgent: stopped")

    # ── Main loop ──

    async def _loop(self) -> None:
        """Main loop: tick, sleep, repeat."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "ProjectAgent tick failed (degraded — will retry): %s",
                    e, exc_info=True,
                )
            await asyncio.sleep(self._config.tick_seconds)

    async def _tick(self) -> None:
        """Single tick cycle, protected by PostgreSQL advisory lock.

        Uses the same get_session_raw / advisory-lock pattern as
        PlanTaskScheduler to ensure only one process runs the tick
        in multi-instance deployments.
        """
        from ..infrastructure.database.session import get_session_raw
        from sqlalchemy import text

        lock_session = get_session_raw()
        try:
            acquired = lock_session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": _LOCK_KEY},
            ).scalar()
            if not acquired:
                return  # another process is already ticking

            try:
                self._do_tick()
            finally:
                try:
                    lock_session.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:key))"),
                        {"key": _LOCK_KEY},
                    )
                except Exception as e:
                    logger.warning("Failed to release ProjectAgent advisory lock: %s", e)
        finally:
            lock_session.close()

    def _do_tick(self) -> None:
        """Execute one tick worth of checks (synchronous, called inside advisory lock).

        Each check is wrapped in its own try/except — one check failing
        does not block the others (DEGRADED mode).
        """
        # ── Phase 1: Stale detection ──
        result = self._stale_detector.run()
        if result.errors:
            for err in result.errors:
                logger.warning("ProjectAgent tick: %s", err)
        if result.total_findings > 0:
            logger.info(
                "ProjectAgent tick: %d stale nodes, %d milestone warnings",
                len(result.stale_nodes), len(result.milestone_warnings),
            )

        # Phase 3: 运维调度（OpsScheduler 嵌入 ProjectAgent tick）
        if self._ops_scheduler:
            from uuid import uuid4
            tick_id = str(uuid4())
            try:
                self._ops_scheduler.run_tick(tick_id, self._tick_count)
            except Exception as e:
                logger.error("Ops tick failed (degraded): %s", e)

    # ── Health / status ──

    def set_ops_scheduler(self, scheduler) -> None:
        """由 EmilyCore._init_ops_module() 调用，注入 OpsScheduler 实例。"""
        self._ops_scheduler = scheduler

    def status(self) -> dict:
        """Return a summary dict for monitoring / health endpoints."""
        result = {
            "enabled": self._config.enabled,
            "running": self._running,
            "tick_seconds": self._config.tick_seconds,
            "stale_threshold_days": self._config.stale_threshold_days,
            "phases": ["stale_detector"],  # Phase 2/3 append here
        }
        if self._ops_scheduler:
            result["ops"] = self._ops_scheduler.status()
        return result
