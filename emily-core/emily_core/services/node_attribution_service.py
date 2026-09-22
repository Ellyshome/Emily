"""node_attribution_service.py —— 三档归属判定与完成反推（US-13 / US-14）。

**只出建议，不擅自改图**（R2 / AC-US-13.5）：
  · 判定与建议全过程不新建节点、不改父子关系、不改状态
  · 落临时节点是"建议被接受后"的动作，由调用方（归档编排器）显式执行

三档（确定性判据，无 LLM 挑选）：
  档 A  命中某里程碑下**已有任务预定的内容** → 反馈目标节点，建议在该节点录入；不建节点、无需模板
  档 B  未命中已有任务、但有**里程碑级归属** → 过门禁：命中落该里程碑下临时任务；未命中只出提案
  档 C  **无里程碑级归属** → 过门禁：命中落临时里程碑层并标「无归属」；未命中只出提案

依据（AC-US-13.4）：命中的规则条目（三书，复用既有 rule_book_loader）/ 命中的模板编号及其条目 /
来源文件。冲突（AC-US-13.6）：资料口径与规则口径相互矛盾时显性输出交人裁决，不静默二选一。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..repositories.node_repo import ProjectNodeRepo, NodeDeliverableRepo
from .node_template_gate import NodeTemplateGate, GateResult

logger = logging.getLogger("emily.node_attribution_service")

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"


@dataclass
class Evidence:
    kind: str          # rule / template / deliverable / file
    ref: str           # 条目 / 编号 / 文件名
    detail: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail}


@dataclass
class AttributionResult:
    """判定结论（含依据与冲突，只读产物）。"""

    tier: str = TIER_C
    target_node_id: str = ""
    target_node_name: str = ""
    milestone_id: str = ""
    milestone_name: str = ""
    gate: Optional[dict] = None
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    needs_confirm: bool = True
    reason: str = ""
    suggested_action: str = ""

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "tier_label": {"A": "档A 命中已有任务", "B": "档B 有里程碑级归属",
                           "C": "档C 无里程碑级归属"}.get(self.tier, self.tier),
            "target_node_id": self.target_node_id,
            "target_node_name": self.target_node_name,
            "milestone_id": self.milestone_id,
            "milestone_name": self.milestone_name,
            "gate": self.gate,
            "evidence": [e.to_dict() for e in self.evidence],
            "conflicts": self.conflicts,
            "needs_confirm": self.needs_confirm,
            "reason": self.reason,
            "suggested_action": self.suggested_action,
        }


@dataclass
class InferenceResult:
    """文件 → 节点完成反推结果（只提示 + 询问批准，不自动改状态）。"""

    hit: bool = False
    node_id: str = ""
    node_name: str = ""
    deliverable_id: str = ""
    deliverable_name: str = ""
    requires_approval: bool = True
    approver_hint: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "hit": self.hit,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "deliverable_id": self.deliverable_id,
            "deliverable_name": self.deliverable_name,
            "requires_approval": self.requires_approval,
            "approver_hint": self.approver_hint,
            "reason": self.reason,
        }


# 成果名通用尾词（与 node_assembly_service 同族口径）
_DELIV_SUFFIXES = ("文件", "文本", "报告", "汇总", "记录", "清单", "说明书", "材料", "文档")


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _name_variants(name: str) -> list[str]:
    """名称比对变体：原名 + 去括号 + 去通用尾词。

    节点名常带括号补充（如「施工图设计完成（含审查合格证）」），资料里通常只写主体；
    成果名常带通用尾词（如「立项批复文件」），资料里常省写。变体只做**字符串归整**，
    不做语义近似、不做相似度打分。
    """
    out = {_squash(name)}
    out.add(_squash(re.sub(r"[（(][^）)]*[）)]", "", name)))
    base = _squash(name)
    for suf in _DELIV_SUFFIXES:
        if base.endswith(suf) and len(base) - len(suf) >= 4:
            out.add(base[: -len(suf)])
    return [x for x in out if x]


class NodeAttributionService:
    """三档归属判定（只读）。"""

    def __init__(self, gate: Optional[NodeTemplateGate] = None,
                 node_repo: Optional[ProjectNodeRepo] = None,
                 deliverable_repo: Optional[NodeDeliverableRepo] = None):
        self.gate = gate or NodeTemplateGate()
        self._node_repo = node_repo or ProjectNodeRepo()
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()

    # ── 判定 ──

    def judge(self, *, project_id: str, payload: dict, actor_id: str = "") -> AttributionResult:
        """给出三档判定结论 + 依据。**只读**：不建节点、不改图、不改状态。"""
        payload = payload or {}
        title = str(payload.get("title") or payload.get("summary") or "")
        filename = str(payload.get("filename") or "")
        haystack = _squash(f"{title} {filename} {payload.get('content_summary') or ''}")
        res = AttributionResult(needs_confirm=True)

        if not project_id:
            res.reason = "缺少项目上下文，无法判定归属"
            res.suggested_action = "补全项目后重新判定，或由人工指定归属"
            return res

        nodes = self._node_repo.find_by_project(project_id, limit=2000)
        business = [n for n in nodes
                    if (getattr(n, "node_role", "") or "BUSINESS") == "BUSINESS"]
        milestones = [n for n in business if getattr(n, "node_type", "") == "MILESTONE"]

        # 档 A：命中某里程碑下已有任务预定的内容（成果名匹配既有成果）
        a = self._match_existing_deliverable(project_id, haystack)
        if a is not None:
            node, deliv_name = a
            res.tier = TIER_A
            res.target_node_id = node.get("node_id", "")
            res.target_node_name = node.get("node_name", "")
            res.evidence.append(Evidence("deliverable", deliv_name,
                                         "与项目内既有成果名匹配"))
            res.evidence.append(Evidence("file", filename or title, "来源文件"))
            res.reason = f"命中已有任务「{node.get('node_name')}」的成果「{deliv_name}」"
            res.suggested_action = "建议在该节点录入信息；**不新建节点、无需模板**"
            # 冲突显性（AC-US-13.6）：内容点名了其它节点但判定指向本节点 → 交人裁决
            conflict = self._detect_conflict(business, haystack, res)
            if conflict:
                res.conflicts.append(conflict)
            res.needs_confirm = True
            return res

        # 档 B：有里程碑级归属（里程碑名称出现在内容中）
        hit_ms = self._match_milestone(milestones, haystack)
        gate_res: GateResult = self.gate.evaluate(
            project_id=project_id, hints={"filename": filename, "summary": title})

        if hit_ms is not None:
            res.tier = TIER_B
            res.milestone_id = getattr(hit_ms, "node_id", "")
            res.milestone_name = getattr(hit_ms, "node_name", "")
            res.evidence.append(Evidence("milestone", res.milestone_name,
                                         "内容中出现该里程碑名称，判为里程碑级归属"))
        else:
            res.tier = TIER_C
            res.evidence.append(Evidence("milestone", "(无)",
                                         "内容中未出现任何里程碑名称，判为无里程碑级归属"))

        res.gate = gate_res.to_dict()
        res.evidence.append(Evidence("file", filename or title, "来源文件"))
        if gate_res.matched:
            res.evidence.append(Evidence("template", gate_res.ref_id, gate_res.reason))

        # 规则依据（复用既有三书，不新建知识源）
        rule_ev = self._rule_evidence()
        if rule_ev:
            res.evidence.append(rule_ev)

        # 冲突显性（AC-US-13.6）：内容显式指向的节点与判定结果不一致
        conflict = self._detect_conflict(business, haystack, res)
        if conflict:
            res.conflicts.append(conflict)
            res.needs_confirm = True

        if not gate_res.matched:
            res.reason = (f"档{res.tier}：{gate_res.reason}；"
                          f"不建任何节点，仅出「缺模板」提案")
            res.suggested_action = "补模板或按人的要求走人工建节点路径"
        elif res.tier == TIER_B:
            res.reason = f"档B：模板命中 {gate_res.ref_id}，可落「{res.milestone_name}」下的临时任务"
            res.suggested_action = f"按模板 {gate_res.ref_id} 装配后落临时任务（认领/挂载后迁正）"
        else:
            res.reason = f"档C：模板命中 {gate_res.ref_id}，可落临时里程碑层（标「无归属」）"
            res.suggested_action = f"按模板 {gate_res.ref_id} 装配后落临时节点等待认领"
        res.needs_confirm = True
        return res

    # ── 依据与冲突 ──

    @staticmethod
    def _rule_evidence() -> Optional[Evidence]:
        """规则依据：复用既有规则书（三书之一），不新建知识源。"""
        try:
            from .rule_book_loader import RuleBookLoader

            text = ""
            loader = RuleBookLoader()
            for attr in ("load", "read", "content"):
                v = getattr(loader, attr, None)
                if callable(v):
                    r = v()
                    text = r if isinstance(r, str) else (getattr(r, "text", "") or "")
                    if text:
                        break
                elif isinstance(v, str) and v:
                    text = v
                    break
            if not text:
                return None
            first = re.split(r"[\n。]", text.strip())[0][:60]
            return Evidence("rule", "规则书", first or "规则书已加载")
        except Exception as e:
            logger.debug("rule evidence unavailable: %s", e)
            return None

    @staticmethod
    def _detect_conflict(business_nodes: list, haystack: str, res: AttributionResult) -> dict:
        """内容显式点到某个节点，但判定结论指向别处 → 显性冲突交人裁决。"""
        if not haystack:
            return {}
        named = [n for n in business_nodes
                 if getattr(n, "node_name", "") and _squash(n.node_name) in haystack]
        if not named:
            return {}
        named_ids = {getattr(n, "node_id", "") for n in named}
        decided = res.target_node_id or res.milestone_id
        if decided and decided not in named_ids:
            return {
                "kind": "content_vs_judgement",
                "content_points_to": sorted(named_ids),
                "judgement_points_to": decided,
                "note": "资料口径与判定结果不一致，需人裁决（不静默二选一）",
            }
        return {}

    # ── 匹配工具（确定性，无 LLM）──

    def _match_existing_deliverable(self, project_id: str, haystack: str):
        if not haystack:
            return None
        pool = self._deliv_repo.find_by_project(project_id)
        best = None
        best_len = 0
        for it in pool:
            name = _squash(it.get("deliverable_name", ""))
            if len(name) < 4:
                continue
            if not any(v in haystack for v in _name_variants(name)):
                continue
            if len(name) > best_len:
                best, best_len = it, len(name)
        return (best, best.get("deliverable_name", "")) if best else None

    @staticmethod
    def _match_milestone(milestones: list, haystack: str):
        if not haystack:
            return None
        best = None
        best_len = 0
        for m in milestones:
            name = _squash(getattr(m, "node_name", ""))
            if len(name) < 4:
                continue
            if not any(v in haystack for v in _name_variants(name)):
                continue
            if len(name) > best_len:
                best, best_len = m, len(name)
        return best


class NodeCompletionInference:
    """文件 → 节点完成反推（US-14）：**只提示 + 询问批准，不自动改状态**。"""

    def __init__(self, deliverable_repo: Optional[NodeDeliverableRepo] = None,
                 node_repo: Optional[ProjectNodeRepo] = None):
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()
        self._node_repo = node_repo or ProjectNodeRepo()

    def suggest(self, *, project_id: str, file_id: str = "",
                filename: str = "", summary: str = "", actor_id: str = "") -> InferenceResult:
        """判断该文件是否像某节点的必需成果，给出提示与批准人线索。

        Returns:
            InferenceResult（`requires_approval=True`；调用方**不得**自行改状态）
        """
        out = InferenceResult()
        if not project_id:
            out.reason = "缺少项目上下文"
            return out

        haystack = _squash(f"{filename} {summary}")
        if not haystack:
            out.reason = "文件无可用识别信息（文件名/摘要为空）"
            return out

        pool = self._deliv_repo.find_by_project(project_id)
        best = None
        best_len = 0
        for it in pool:
            name = _squash(it.get("deliverable_name", ""))
            if len(name) < 4 or name not in haystack:
                continue
            if len(name) > best_len:
                best, best_len = it, len(name)
        if best is None:
            out.reason = "未匹配到项目内任何成果名（不提示）"
            return out

        node = self._node_repo.get_by_node_id(best.get("node_id", ""))
        out.hit = True
        out.node_id = best.get("node_id", "")
        out.node_name = best.get("node_name", "")
        out.deliverable_id = best.get("deliverable_id", "")
        out.deliverable_name = best.get("deliverable_name", "")
        out.approver_hint = (
            "节点责任人 / L5+ / 节点参与单位人员（与成果提交同口径）")
        out.reason = (f"该文件像是「{out.node_name}」的成果「{out.deliverable_name}」"
                      f"（节点 {out.node_id}）")
        if node is not None and (node.status or "") == "COMPLETED":
            out.reason += "；该节点已完结"
        logger.info("Completion inference: file=%s -> node=%s deliverable=%s",
                    file_id, out.node_id, out.deliverable_id)
        return out
