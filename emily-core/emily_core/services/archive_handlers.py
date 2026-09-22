"""archive_handlers.py —— 归档后处理器实现 + 注册点（域C 主流程编排）。

流程（一份文件归档后）：
    归档收口 → ArchiveHandlerRegistry.dispatch
        └── node_build_handler
              ├── [M5] NodeTemplateGate.evaluate —— 未命中 → 只出「缺模板」提案（零建节点）
              ├── [M5] 命中 → [M6] NodeAttributionService.judge —— 三档判定（给依据、只出建议）
              ├── [M7] 档B/C 落临时节点（携带模板来源 + 按模板装配的成果清单）
              └── [M6] NodeCompletionInference.suggest —— 「像 X 节点成果」提示（不改状态）

**不阻断**（PRD §4.4-12）：本处理器整体 fail-open；归档与人工建节点主链路不受影响。
**只出建议**（R2）：档A 不建节点；档B/C 落的是**临时节点**（认领/挂载后才迁正），不落正式位置。
"""

from __future__ import annotations

import logging

from .archive_handler_registry import ArchiveHandlerRegistry

logger = logging.getLogger("emily.archive_handlers")


async def _load_file_meta(file_id: str) -> dict:
    """取文件元信息（文件名 / 编号 / 摘要），失败则返回空（不阻断）。"""
    if not file_id:
        return {}
    try:
        from ..infrastructure.database.models import File
        from ..infrastructure.database.session import get_session

        with get_session() as session:
            f = session.query(File).filter(File.id == file_id).first()
            if f is None:
                return {}
            return {
                "filename": f.filename or "",
                "file_no": f.file_no or "",
                "summary": f.content_summary or "",
                "project_id": f.project_id or "",
            }
    except Exception as e:
        logger.debug("load file meta failed file=%s: %s", file_id, e)
        return {}


async def node_build_handler(*, file_id: str, project_id: str = "",
                             actor_id: str = "", filename: str = "") -> dict:
    """文件归档触发的节点补建链路（门禁 → 判定 → 落临时节点 / 提案 / 完成反推）。"""
    from .node_attribution_service import (
        NodeAttributionService, NodeCompletionInference, TIER_A, TIER_B,
    )
    from .node_container_service import NodeContainerService
    from .node_template_gate import NodeTemplateGate
    from .node_template_loader import NodeTemplateLoader

    meta = await _load_file_meta(file_id)
    pid = project_id or meta.get("project_id", "")
    fname = filename or meta.get("filename", "")
    file_no = meta.get("file_no", "")
    summary = meta.get("summary", "")
    if not pid:
        logger.info("归档分析跳过：文件 %s 无项目归属", file_id)
        return {"skipped": "no_project"}

    gate = NodeTemplateGate()
    result = gate.evaluate(project_id=pid,
                           hints={"filename": fname, "summary": summary})

    # ── 未命中：不建任何节点（含临时节点），只出「缺模板」提案（AC-US-12.4）──
    if not result.matched:
        proposal = gate.propose_missing(result, file_id=file_id,
                                        file_no=file_no, project_id=pid)
        logger.info("门禁未命中，仅出提案：file=%s proposal=%s", file_id,
                    proposal.get("proposal_id") or proposal.get("reason"))
        return {"matched": False, "created_nodes": [], "proposal": proposal,
                "gate": result.to_dict()}

    # ── 命中：装配 + 三档判定 + 落临时节点（携带模板来源）──
    detail = NodeTemplateLoader().get_template(result.ref_id)
    deliverables = []
    if detail is not None:
        deliverables = [{
            "deliverable_name": d.name,
            "target_amount": d.target_amount,
            "unit": d.unit,
            "is_required": d.is_required,
        } for d in detail.deliverables]

    attribution = NodeAttributionService(gate=gate).judge(
        project_id=pid,
        payload={"title": fname, "filename": fname, "content_summary": summary},
        actor_id=actor_id,
    )

    created_ids: list[str] = []
    # 档A：命中已有任务 → 不建节点（AC-US-13.1）
    if attribution.tier != TIER_A:
        cs = NodeContainerService()
        parent = attribution.milestone_id if attribution.tier == TIER_B else ""
        node_name = detail.node_name if detail is not None else fname
        try:
            node_id = await cs.create_temp_task(
                project_id=pid,
                node_name=node_name,
                operator_id=actor_id,
                parent_node_id=parent,
                remark=(f"文件触发的临时节点（模板 {result.ref_id}；"
                        f"来源文件 {file_no or file_id}；认领/挂载后迁正）"),
                template_ref_id=result.ref_id,
                deliverables=deliverables,
                node_type=(detail.node_type if detail is not None else "TASK"),
            )
            created_ids.append(node_id)
            logger.info("门禁命中落临时节点：%s（模板 %s / 档%s）",
                        node_id, result.ref_id, attribution.tier)
        except Exception as e:
            # 落节点失败不阻断（归档已完成）；登记以便人工介入
            logger.warning("临时节点落库失败 file=%s: %s", file_id, e)

    # 完成反推：只提示 + 询问批准，不自动改状态（AC-US-14.2 / 14.3）
    inference = NodeCompletionInference().suggest(
        project_id=pid, file_id=file_id, filename=fname, summary=summary,
        actor_id=actor_id)

    return {
        "matched": True,
        "ref_id": result.ref_id,
        "gate": result.to_dict(),
        "attribution": attribution.to_dict(),
        "created_nodes": created_ids,
        "completion_inference": inference.to_dict() if inference.hit else None,
    }


async def plan_draft_handler(*, file_id: str, project_id: str = "",
                            actor_id: str = "", filename: str = "") -> dict:
    """计划类资料归档 → 产出整树装配草案（US-19 入口之一；M9 提供实现）。"""
    from .node_plan_draft_service import NodePlanDraftService

    meta = await _load_file_meta(file_id)
    pid = project_id or meta.get("project_id", "")
    if not pid:
        return {"skipped": "no_project"}
    try:
        draft = await NodePlanDraftService().analyze(
            project_id=pid, file_id=file_id, actor_id=actor_id,
            filename=filename or meta.get("filename", ""),
            summary=meta.get("summary", ""))
    except Exception as e:
        logger.info("整树装配草案未产出 file=%s: %s", file_id, e)
        return {"draft_id": "", "reason": str(e)}
    return {"draft_id": getattr(draft, "draft_id", ""),
            "confidence": getattr(draft, "confidence", 0.0),
            "items": len(getattr(draft, "items", []) or [])}


def register_archive_handlers() -> list[str]:
    """注册全部归档后处理器（bootstrap 调用；新增能力在此加一行）。"""
    ArchiveHandlerRegistry.register("node_template_build", node_build_handler)
    ArchiveHandlerRegistry.register("plan_draft", plan_draft_handler)
    return ArchiveHandlerRegistry.names()
