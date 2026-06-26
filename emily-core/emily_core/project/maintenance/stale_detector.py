"""Stale detector — find and report nodes stuck in non-terminal states.

This is the first active-maintenance check performed by ProjectAgent on
every tick. It queries sm_nodes for IN_PROGRESS / BLOCKED / DELAYED
nodes whose updated_at timestamp is older than the configured threshold,
then emits alerts through the outbound bus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...repositories.sm_node_repo import SMNodeRepository
    from ...outbound_bus import OutboundEventBus

logger = logging.getLogger("emily.project.stale_detector")

# ── Alert templates ──
_STALE_ALERT_TEMPLATE = (
    "【节点卡滞预警】\n"
    "节点「{node_name}」（{node_id}）处于「{status_label}」状态已超过 {days} 天未更新。\n"
    "负责人：{owner}\n"
    "所属阶段：阶段{stage_id}\n"
    "最后更新时间：{updated_at}\n"
    "请确认进度或更新状态。"
)

_MILESTONE_DEADLINE_TEMPLATE = (
    "【里程碑预警】\n"
    "里程碑节点「{node_name}」（{node_id}）计划截止日期为 {planned_end_date}，距今不足 {days} 天。\n"
    "当前状态：{status_label}\n"
    "负责人：{owner}\n"
    "请关注进度，确保按期完成。"
)


@dataclass
class StaleNode:
    """A single stale node finding."""
    node_id: str
    node_name: str
    status: str
    status_label: str
    stage_id: int
    owner: str
    updated_at: str
    days_stale: int
    planned_end_date: str = ""


@dataclass
class MilestoneWarning:
    """A milestone approaching its deadline."""
    node_id: str
    node_name: str
    status: str
    status_label: str
    owner: str
    planned_end_date: str
    days_remaining: int


@dataclass
class StaleDetectionResult:
    """Aggregated stale detection output for one tick."""
    stale_nodes: list[StaleNode] = field(default_factory=list)
    milestone_warnings: list[MilestoneWarning] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_findings(self) -> int:
        return len(self.stale_nodes) + len(self.milestone_warnings)


class StaleDetector:
    """Detect stale/overdue state-machine nodes and generate alerts.

    Designed to be called once per ProjectAgent tick. All checks are
    pure-SQL / rule-based: no LLM calls here — keep tick cost low.
    """

    # Human-readable labels mirroring node_state.py
    _STATUS_LABELS = {
        "NOT_STARTED": "未启动",
        "IN_PROGRESS": "进行中",
        "BLOCKED":     "已阻塞",
        "DELAYED":     "已延期",
        "COMPLETED":   "已完成",
    }

    # Non-terminal statuses we check for staleness
    _NON_TERMINAL = ["IN_PROGRESS", "BLOCKED", "DELAYED"]

    def __init__(
        self,
        node_repo: "SMNodeRepository",
        outbound_bus: "OutboundEventBus",
        stale_threshold_days: int = 14,
        deadline_warn_days: int = 7,
        alert_cooldown_hours: int = 24,
    ):
        self._node_repo = node_repo
        self._outbound = outbound_bus
        self._stale_threshold_days = stale_threshold_days
        self._deadline_warn_days = deadline_warn_days
        self._alert_cooldown_hours = alert_cooldown_hours

        # Simple in-memory cooldown: { "node_id:issue_type" → last_alert_iso }
        # Resets on process restart — acceptable for Phase 1.
        self._alert_cooldowns: dict[str, str] = {}

    # ── Public entry point ──

    def run(self) -> StaleDetectionResult:
        """Run one complete stale-detection sweep.

        Returns a StaleDetectionResult; also publishes alerts to the
        outbound bus for any issues found (with cooldown).
        """
        result = StaleDetectionResult()

        # 1. Stale node detection
        try:
            result.stale_nodes = self._detect_stale_nodes()
            for sn in result.stale_nodes:
                if self._should_alert(f"stale:{sn.node_id}"):
                    self._publish_stale_alert(sn)
                    self._mark_alerted(f"stale:{sn.node_id}")
        except Exception as e:
            msg = f"Stale node detection failed: {e}"
            logger.error(msg, exc_info=True)
            result.errors.append(msg)

        # 2. Milestone deadline warning
        try:
            result.milestone_warnings = self._detect_milestone_warnings()
            for mw in result.milestone_warnings:
                if self._should_alert(f"milestone:{mw.node_id}"):
                    self._publish_milestone_alert(mw)
                    self._mark_alerted(f"milestone:{mw.node_id}")
        except Exception as e:
            msg = f"Milestone deadline check failed: {e}"
            logger.error(msg, exc_info=True)
            result.errors.append(msg)

        return result

    # ── Detection logic ──

    def _detect_stale_nodes(self) -> list[StaleNode]:
        """Query nodes stuck in non-terminal state beyond threshold."""
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(days=self._stale_threshold_days)
        older_than = threshold.isoformat()

        stale_rows = self._node_repo.list_stale(
            statuses=list(self._NON_TERMINAL),
            older_than_iso=older_than,
        )

        result: list[StaleNode] = []
        for row in stale_rows:
            updated = row.updated_at or ""
            days = 0
            try:
                if updated:
                    updated_dt = datetime.fromisoformat(updated)
                    days = (now - updated_dt).days
            except (ValueError, TypeError):
                pass

            result.append(StaleNode(
                node_id=row.node_id,
                node_name=row.node_name,
                status=row.status,
                status_label=self._STATUS_LABELS.get(row.status, row.status),
                stage_id=row.stage_id,
                owner=row.owner or "未指定",
                updated_at=updated,
                days_stale=max(days, self._stale_threshold_days),
                planned_end_date=row.planned_end_date or "",
            ))

        if result:
            logger.info(
                "StaleDetector: found %d stale nodes (threshold=%dd)",
                len(result), self._stale_threshold_days,
            )
        return result

    def _detect_milestone_warnings(self) -> list[MilestoneWarning]:
        """Find milestones whose planned_end_date is approaching."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        rows = self._node_repo.list_milestones_near_deadline(
            now_iso=now_iso,
            warn_before_days=self._deadline_warn_days,
        )

        result: list[MilestoneWarning] = []
        for row in rows:
            days_remaining = 0
            try:
                if row.planned_end_date:
                    deadline_dt = datetime.fromisoformat(row.planned_end_date)
                    days_remaining = (deadline_dt - now).days
            except (ValueError, TypeError):
                pass

            # Only warn if status is not COMPLETED (already filtered in query,
            # but double-check) and the deadline is genuinely close.
            if row.status == "COMPLETED":
                continue

            result.append(MilestoneWarning(
                node_id=row.node_id,
                node_name=row.node_name,
                status=row.status,
                status_label=self._STATUS_LABELS.get(row.status, row.status),
                owner=row.owner or "未指定",
                planned_end_date=row.planned_end_date or "",
                days_remaining=max(days_remaining, 0),
            ))

        if result:
            logger.info(
                "StaleDetector: found %d milestones near deadline (warn=%dd)",
                len(result), self._deadline_warn_days,
            )
        return result

    # ── Alerting ──

    def _publish_stale_alert(self, sn: StaleNode) -> None:
        """Publish a stale-node alert to the outbound bus."""
        content = _STALE_ALERT_TEMPLATE.format(
            node_name=sn.node_name,
            node_id=sn.node_id,
            status_label=sn.status_label,
            days=sn.days_stale,
            owner=sn.owner,
            stage_id=sn.stage_id,
            updated_at=sn.updated_at or "未知",
        )
        self._outbound.publish("reply", {
            "content": content,
            "source": "project_agent:stale_detector",
        })
        logger.info(
            "Stale alert published: node=%s status=%s days=%d",
            sn.node_id, sn.status, sn.days_stale,
        )

    def _publish_milestone_alert(self, mw: MilestoneWarning) -> None:
        """Publish a milestone-deadline alert to the outbound bus."""
        content = _MILESTONE_DEADLINE_TEMPLATE.format(
            node_name=mw.node_name,
            node_id=mw.node_id,
            planned_end_date=mw.planned_end_date,
            days=mw.days_remaining,
            status_label=mw.status_label,
            owner=mw.owner,
        )
        self._outbound.publish("reply", {
            "content": content,
            "source": "project_agent:milestone_watcher",
        })
        logger.info(
            "Milestone alert published: node=%s deadline=%s remaining=%dd",
            mw.node_id, mw.planned_end_date, mw.days_remaining,
        )

    # ── Cooldown ──

    def _should_alert(self, key: str) -> bool:
        """Check whether enough time has passed since the last alert for key."""
        last = self._alert_cooldowns.get(key)
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
            cooldown = timedelta(hours=self._alert_cooldown_hours)
            return (datetime.now(timezone.utc) - last_dt) >= cooldown
        except (ValueError, TypeError):
            return True

    def _mark_alerted(self, key: str) -> None:
        """Record that an alert was sent for key right now."""
        self._alert_cooldowns[key] = datetime.now(timezone.utc).isoformat()
