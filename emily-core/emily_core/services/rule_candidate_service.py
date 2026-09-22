"""rule_candidate_service.py —— 规律沉淀：从历史记录提炼规则候选（US-20）。

口径（AC-US-20.1 / 20.2 / 20.3）：
  · 只从**往期项目节点图与人工改节点记录**中提炼，每条候选**必须附支撑样本**
  · **禁止**无依据的"经验总结"——无样本的候选不得产出
  · 候选只是候选：**经人确认后才升级为标准**并落入三书；未确认的仅停留为候选
  · 确认后的标准被能力侧复用（体现在判定依据中，接线点 = 三书读取）

样本来源（既有留痕，不新建数据源）：
  · `node_events`（人工改节点记录：重命名 / 改截止 / 迁正 / 停用…）
  · `project_nodes`（往期项目节点图的实际结构）
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import NodeEvent, ProjectNode

logger = logging.getLogger("emily.rule_candidate_service")

BEIJING_TZ = timezone(timedelta(hours=8))

# 可提炼的改节点事件类型 → 候选规则模板（**须有样本才产出**）
_RULE_TEMPLATES = {
    "node_renamed": "节点名称口径：历史人工改名的节点集中在「{sample}」这类命名上",
    "node_promoted": "收容迁正口径：临时节点集中迁往「{sample}」类归属位置",
    "node_disabled": "停用口径：被停用的节点集中在「{sample}」类节点上",
    "child_node_mounted": "挂载口径：人工挂载集中在「{sample}」类父子关系上",
    "status_changed": "状态流转口径：状态变更集中在「{sample}」类节点上",
}

MIN_SAMPLES = 2   # 低于此样本数不产出候选（禁止无依据总结）


@dataclass
class RuleCandidate:
    candidate_id: str
    rule_text: str
    event_type: str
    samples: list[dict] = field(default_factory=list)
    status: str = "candidate"   # candidate / confirmed
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "rule_text": self.rule_text,
            "event_type": self.event_type,
            "samples": self.samples,
            "sample_count": len(self.samples),
            "status": self.status,
            "created_at": self.created_at,
            "disclaimer": "候选尚未升级为标准；经人确认后才落入三书。禁止无样本的「经验总结」。",
        }


class RuleCandidateService:
    """规则候选提炼（必须有支撑样本）。"""

    def __init__(self, min_samples: int = MIN_SAMPLES):
        self.min_samples = min_samples

    def propose_candidates(self, project_id: str = "", limit: int = 500,
                           event_type: str = "") -> list[RuleCandidate]:
        """从节点改动作留痕中提炼候选（每条带样本；不足样本数不产出）。"""
        with get_session() as session:
            q = session.query(NodeEvent)
            if project_id:
                q = q.filter(NodeEvent.node_id.in_(
                    session.query(ProjectNode.node_id).filter(
                        ProjectNode.project_id == project_id)))
            if event_type:
                q = q.filter(NodeEvent.event_type == event_type)
            events = q.order_by(NodeEvent.created_at.desc()).limit(limit).all()

            node_ids = {e.node_id for e in events}
            node_names = {
                n.node_id: (n.node_name or "")
                for n in session.query(ProjectNode).filter(
                    ProjectNode.node_id.in_(node_ids)).all()
            } if node_ids else {}

        groups: dict[str, list[dict]] = {}
        for e in events:
            et = e.event_type or ""
            if et not in _RULE_TEMPLATES:
                continue
            groups.setdefault(et, []).append({
                "node_id": e.node_id,
                "node_name": node_names.get(e.node_id, ""),
                "operator_id": e.operator_id or "",
                "at": e.created_at or "",
                "old_value": e.old_value or "",
                "new_value": e.new_value or "",
                "remark": e.remark or "",
            })

        out: list[RuleCandidate] = []
        now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        for et, samples in sorted(groups.items()):
            # 禁止无依据的把关：样本数不足 → 不产出
            if len(samples) < self.min_samples:
                logger.debug("规则候选跳过（样本不足 %d<%d）：%s", len(samples), self.min_samples, et)
                continue
            top = Counter(s["node_name"] for s in samples if s["node_name"]).most_common(1)
            focus = top[0][0] if top else f"{len(samples)} 个节点"
            out.append(RuleCandidate(
                candidate_id=f"RC-{et}-{abs(hash(et)) % 10000:04d}",
                rule_text=_RULE_TEMPLATES[et].format(sample=focus),
                event_type=et,
                samples=samples,
                created_at=now,
            ))
        return out

    @staticmethod
    def render_markdown(candidates: list[RuleCandidate]) -> str:
        """人可读清单（供人工确认；不写三书）。"""
        lines = ["# 规则候选（待人工确认）", ""]
        if not candidates:
            lines.append("（本次无满足样本要求的候选——候选必须有支撑样本，禁止无依据总结）")
            return "\n".join(lines)
        for c in candidates:
            lines.append(f"## {c.candidate_id}（{c.event_type}，样本 {len(c.samples)} 条）")
            lines.append("")
            lines.append(f"- 候选规则：{c.rule_text}")
            lines.append("- 支撑样本：")
            for s in c.samples[:10]:
                lines.append(f"  - {s['at'][:19]}｜{s['node_name']}（{s['node_id']}）"
                             f"｜操作人 {s['operator_id'] or '-'}｜{s['remark']}")
            lines.append("")
        lines.append("> 确认后方可升级为标准并落入三书；未确认的仅停留为候选。")
        return "\n".join(lines)
