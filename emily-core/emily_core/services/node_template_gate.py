"""node_template_gate.py —— 模板锚定门禁与「缺模板」提案（US-12）。

**门禁是需求核心，不得降级**（PRD §4.4-2 / AC-US-12.2）：
  · 匹配强度 = 必须命中模板库中**已存在的模板编号**（ref_id 字面量）
  · 不做类别近似、不做同专业派生、不做相似度打分、**不用 LLM 软匹配**
  · 未命中 → **不创建任何节点**（含临时节点），只产出一条「缺模板」提案

判定为**纯函数**（无 LLM、无写库）：输入文件元信息与显式提示，输出结论 + 匹配尝试依据。

作用域（AC-US-12.1）：门禁**仅**作用于「文件上传触发的节点自动补建」；
人工建节点（控制台 / 对话 `create_node`）与整树装配不经过本门禁。

故障口径（AC-US-12.5）：模板库缺失 / 索引损坏 / 检索异常 → 一律按「未命中」处理，
且文件归档与人工建节点主链路照常成功（调用方 fail-open）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .node_template_loader import NodeTemplateLoader, TemplateUnavailable

logger = logging.getLogger("emily.node_template_gate")


@dataclass
class GateResult:
    """门禁结论（含匹配尝试依据，供提案与留痕使用）。"""

    matched: bool = False
    ref_id: str = ""
    attempts: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "ref_id": self.ref_id,
            "attempts": self.attempts,
            "reason": self.reason,
        }


class NodeTemplateGate:
    """模板锚定门禁（纯判定）。"""

    def __init__(self, loader: Optional[NodeTemplateLoader] = None):
        self.loader = loader or NodeTemplateLoader()

    # ── 门禁判定 ──

    def evaluate(self, *, file_id: str = "", project_id: str = "",
                 hints: Optional[dict] = None) -> GateResult:
        """判定该文件是否命中模板库已有模板编号。

        Args:
            file_id / project_id: 来源文件与项目（记入依据）
            hints: 可选提示，支持
                · ref_id     —— 显式指定的模板编号（用户指定 / 上游已知）
                · filename   —— 文件名（用于**编号字面量**命中）
                · summary    —— 文件摘要（同上）

        Returns:
            GateResult；未命中时 `attempts` 记录"按什么键去找、是否命中"，供提案引用。
        """
        hints = hints or {}
        attempts: list[dict] = []

        available, why = self.loader.is_available()
        if not available:
            attempts.append({"key": "template_library", "matched": False, "note": why})
            return GateResult(matched=False, attempts=attempts,
                              reason=f"模板库不可用，按未命中处理（{why}）")

        try:
            known = {t.ref_id for t in self.loader.list_templates()}
        except TemplateUnavailable as e:
            attempts.append({"key": "template_library", "matched": False, "note": e.reason})
            return GateResult(matched=False, attempts=attempts,
                              reason=f"模板库检索异常，按未命中处理（{e.reason}）")

        # 候选来源（**严格编号命中**，不引入近似/派生/语义匹配）
        candidates: list[tuple[str, str]] = []
        explicit = str(hints.get("ref_id") or "").strip()
        if explicit:
            candidates.append(("explicit_ref_id", explicit))
        haystack = f"{hints.get('filename') or ''} {hints.get('summary') or ''}"
        if haystack.strip():
            for ref in sorted(known):
                if ref and ref in haystack:
                    candidates.append(("literal_in_file_meta", ref))

        if not candidates:
            attempts.append({
                "key": "template_ref_id", "matched": False,
                "note": "文件中未出现任何模板编号，且未显式指定模板（不做近似/派生/语义降级匹配）",
            })
            return GateResult(matched=False, attempts=attempts,
                              reason="未命中模板库已有模板编号（无候选）")

        for source, ref in candidates:
            hit = ref in known
            attempts.append({"key": "template_ref_id", "candidate": ref,
                             "source": source, "matched": hit,
                             "note": "模板库存在该编号" if hit else "模板库无该编号"})
            if hit:
                return GateResult(matched=True, ref_id=ref, attempts=attempts,
                                  reason=f"命中模板 {ref}（来源：{source}）")

        return GateResult(matched=False, attempts=attempts,
                          reason=f"候选模板编号均不在模板库中（{len(candidates)} 个）")

    # ── 缺模板提案 ──

    def propose_missing(self, result: GateResult, *, file_id: str = "",
                        file_no: str = "", project_id: str = "") -> dict:
        """产出「缺模板」提案（承载于既有待解决问题清单）。

        提案写明"这份文件指向的节点在模板库无匹配"，附来源文件与匹配尝试依据（AC-US-12.4）。
        """
        basis = "；".join(
            f"{a.get('key')}={a.get('candidate') or a.get('note')}"
            f"（{'命中' if a.get('matched') else '未命中'}）"
            for a in (result.attempts or [])
        ) or "无"
        try:
            from .pending_issues import PendingIssuesService

            issue_id = PendingIssuesService().add(
                raised_by="Emily（模板锚定门禁）",
                source=f"文件归档触发节点补建：{file_no or file_id or '(未知文件)'}",
                description=(
                    "这份文件指向的节点在模板库无匹配，未创建任何节点。\n"
                    f"- 来源文件：{file_no or file_id}（project={project_id or '(未知)'}）\n"
                    f"- 匹配尝试依据：{basis}\n"
                    f"- 结论：{result.reason}"
                ),
                suggestion=(
                    "① 由人工在模板库补齐对应模板单元后重试；"
                    "② 或按人的要求走人工建节点路径（人工建节点不受本门禁限制）。"
                    "系统不自动制作模板。"
                ),
            )
            logger.info("Missing-template proposal raised: %s (file=%s)", issue_id, file_id)
            return {"ok": True, "proposal_id": issue_id}
        except Exception as e:
            logger.warning("缺模板提案写入失败 file=%s: %s", file_id, e)
            return {"ok": False, "reason": str(e)}
