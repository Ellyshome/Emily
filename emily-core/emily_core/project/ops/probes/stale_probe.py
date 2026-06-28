"""StaleProbe — 卡滞检测探针。

将现有 StaleDetector 适配为 Probe 接口，向 OpsScheduler 报告发现结果。
原有告警路径（outbound_bus 推送）保持不变，StaleProbe 仅追加 DB 持久化。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..probe_base import Probe, ProbeFinding, TickContext

if TYPE_CHECKING:
    from ...maintenance.stale_detector import StaleDetector
    from ..config import OpsConfig


class StaleProbe(Probe):
    """卡滞检测探针 - 包装 StaleDetector 为 Probe 接口。

    职责：将 StaleDetector.run() 的返回结果转换为 ProbeFinding 列表，
    写入 ops_finding 表以支持运维审计和回溯。
    原有告警推送路径（stale_detector → outbound_bus）保持不变。
    """

    def __init__(self, stale_detector: "StaleDetector", config: "OpsConfig"):
        self._detector = stale_detector
        self._config = config

    def name(self) -> str:
        return "stale_probe"

    def enabled(self) -> bool:
        return self._config.stale_probe_enabled

    def interval_seconds(self) -> int:
        return self._config.tick_interval_seconds

    def run(self, ctx: TickContext) -> list[ProbeFinding]:
        """执行卡滞检测，将 StaleDetectionResult 转换为 ProbeFinding 列表。"""
        result = self._detector.run()
        findings: list[ProbeFinding] = []

        # 卡滞节点 → ProbeFinding
        for sn in result.stale_nodes:
            findings.append(ProbeFinding(
                finding_type="STALE_NODE",
                severity="WARNING",
                target_id=sn.node_id,
                message=(
                    f"节点「{sn.node_name}」（{sn.node_id}）处于「{sn.status_label}」状态"
                    f"已超过 {sn.days_stale} 天未更新"
                ),
                metadata={
                    "node_name": sn.node_name,
                    "status": sn.status,
                    "status_label": sn.status_label,
                    "stage_id": sn.stage_id,
                    "owner": sn.owner,
                    "updated_at": sn.updated_at,
                    "days_stale": sn.days_stale,
                },
            ))

        # 里程碑预警 → ProbeFinding
        for mw in result.milestone_warnings:
            findings.append(ProbeFinding(
                finding_type="MILESTONE_WARNING",
                severity="WARNING",
                target_id=mw.node_id,
                message=(
                    f"里程碑节点「{mw.node_name}」（{mw.node_id}）计划截止日期为"
                    f" {mw.planned_end_date}，距今不足 {mw.days_remaining} 天"
                ),
                metadata={
                    "node_name": mw.node_name,
                    "status": mw.status,
                    "status_label": mw.status_label,
                    "owner": mw.owner,
                    "planned_end_date": mw.planned_end_date,
                    "days_remaining": mw.days_remaining,
                },
            ))

        return findings
