"""node_plan_draft_service.py —— 计划类资料整树装配（US-19）。

流程（**草案确认后才写库**）：
    投喂计划类资料 → 类型识别（置信度）→ 抽取节点树 → 与现有节点比对
      → 产出「新增 / 更新 / 无变化」三类清单（每项附资料出处）
      → 落运行时草案（emily-data/runtime/drafts/{draft_id}.json）
      → 人工勾选确认 → 复用既有批量建树/批量更新能力写库 → 清理草案

硬口径：
  · **不因资料未提及而删除**任何现有节点或字段（AC-US-19.5）：比对是并集式的，
    未提及 = 不出现在清单，**永远不产生删除动作**
  · **幂等**（AC-US-19.9）：比对键优先节点编号，无编号用「名称 + 项目」；重复投喂不重复建/改
  · 复用既有批量能力（AC-US-19.7）：写入只走 `node_batch.create_node_tree` / `node_batch_update.batch_*`
  · 以模板库为比对参考系（AC-US-19.8）：能对应到模板的条目标注模板编号；**本入口逐条人工确认，不适用硬门禁**
  · 草案不入库（承载于运行时文件），避免为零 schema 变更原则新增表
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import ProjectNode
from ..infrastructure.paths import resolve_data_path

logger = logging.getLogger("emily.node_plan_draft_service")

BEIJING_TZ = timezone(timedelta(hours=8))

# 类型识别关键词（确定性；低置信度不进入装配）
_PLAN_KEYWORDS = ("全景计划", "进度计划", "节点清单", "里程碑", "计划节点", "总控计划")
_UNIT_RE = re.compile(r"[|｜]")

NEW, UPDATE, UNCHANGED = "NEW", "UPDATE", "UNCHANGED"

_DRAFTS_DIR_DEV = "emily-data/runtime/drafts"
_DRAFTS_DIR_CONTAINER = "/app/runtime/drafts"


def _drafts_dir() -> Path:
    p = Path(resolve_data_path("", _DRAFTS_DIR_CONTAINER, _DRAFTS_DIR_DEV))
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class DraftItem:
    """草案条目（每项附资料出处）。"""

    change_type: str = NEW
    match_key: str = ""
    node_id: str = ""
    node_name: str = ""
    node_type: str = "TASK"
    deadline: str = ""
    deliverables: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    template_ref_id: str = ""
    source_quote: str = ""

    def to_dict(self) -> dict:
        return {
            "change_type": self.change_type,
            "match_key": self.match_key,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_type": self.node_type,
            "deadline": self.deadline,
            "deliverables": self.deliverables,
            "dependencies": self.dependencies,
            "template_ref_id": self.template_ref_id,
            "source_quote": self.source_quote,
        }


@dataclass
class PlanDraft:
    """整树装配草案（暂存于运行时文件）。"""

    draft_id: str = ""
    project_id: str = ""
    source_file_id: str = ""
    source_file_no: str = ""
    confidence: float = 0.0
    created_at: str = ""
    items: list[DraftItem] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "project_id": self.project_id,
            "source_file_id": self.source_file_id,
            "source_file_no": self.source_file_no,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "items": [i.to_dict() for i in self.items],
            "counts": {
                "NEW": sum(1 for i in self.items if i.change_type == NEW),
                "UPDATE": sum(1 for i in self.items if i.change_type == UPDATE),
                "UNCHANGED": sum(1 for i in self.items if i.change_type == UNCHANGED),
            },
            "skipped": self.skipped,
            "note": self.note,
            "disclaimer": "草案**尚未落库**；确认后才写入，未采纳项丢弃。写入不会删除任何未提及的既有节点。",
        }


class NodePlanDraftService:
    """整树装配草案服务。"""

    CONFIDENCE_THRESHOLD = 0.5

    # ── 类型识别 ──

    @staticmethod
    def recognize(filename: str = "", summary: str = "") -> tuple[float, str]:
        """判定资料是否计划 / 全景类，返回 (置信度, 理由)。低置信度不进入装配。"""
        hay = f"{filename} {summary}"
        if not hay.strip():
            return 0.0, "无可用识别信息"
        hits = [k for k in _PLAN_KEYWORDS if k in hay]
        ext = (filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else "")
        score = 0.0
        why = []
        if hits:
            score += min(0.6, 0.3 * len(hits))
            why.append(f"含关键词 {'/'.join(hits)}")
        if ext in ("xlsx", "xls", "csv", "md", "txt"):
            score += 0.2
            why.append(f"表格/文本格式（{ext}）")
        if "节点" in hay or "计划" in hay:
            score += 0.2
            why.append("含「节点/计划」语义词")
        return min(score, 1.0), "；".join(why) or "未命中计划类特征"

    # ── 抽取 ──

    @staticmethod
    def extract_items(text: str) -> tuple[list[dict], list[dict]]:
        """从资料文本抽取节点条目（确定性解析：表格行 / 清单行）。

        Returns:
            (items, skipped) —— skipped 记录无法解析的行（附原因）
        """
        items: list[dict] = []
        skipped: list[dict] = []
        deliverable_col: int = -1   # 由表头定位，避免节点名含「成果」二字被误判为成果列
        name_col: int = -1
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            cells = [c.strip() for c in _UNIT_RE.split(line.strip("|｜"))] if "|" in line or "｜" in line else []
            if cells and all(set(c) <= set("-: ") for c in cells):
                continue   # 表格分隔行
            if not cells:
                # 清单行：`- 名称（截止 2026-12-31）`
                m = re.match(r"^[-*]\s*(.+?)(?:[（(]截止\s*([\d-]+)[)）])?$", line)
                if not m:
                    continue
                name = m.group(1).strip()
                if len(name) < 2:
                    continue
                items.append({"node_id": "", "node_name": name,
                              "deadline": m.group(2) or "", "source_quote": line})
                continue
            if cells and (cells[0] in ("节点名称", "名称", "node_name", "节点编号") or "名称" in cells[0]):
                # 表头行 → 记录各列位置
                for idx, c in enumerate(cells):
                    if any(k in c for k in ("成果", "交付物", "产物")):
                        deliverable_col = idx
                    if ("名称" in c or "node_name" in c) and name_col < 0:
                        name_col = idx
                continue
            node_id = cells[0] if re.match(r"^[A-Za-z0-9\-_]{3,}$", cells[0] or "") else ""
            name = cells[1] if node_id and len(cells) > 1 else cells[0]
            deadline = ""
            deliverables: list[str] = []
            for idx, c in enumerate(cells):
                if re.match(r"^\d{4}-\d{2}-\d{2}", c or ""):
                    deadline = c[:10]
                    continue
                # 仅取**表头定位到的成果列**（无表头时不推断，避免误判）
                if idx == deliverable_col and len(c) > 1:
                    deliverables = [x.strip() for x in re.split(r"[、;；,，/]", c) if len(x.strip()) >= 2]
            if not name or len(name) < 2:
                skipped.append({"line": line, "reason": "无法识别节点名称"})
                continue
            items.append({"node_id": node_id, "node_name": name,
                          "deadline": deadline, "source_quote": line,
                          "deliverables": deliverables})
        return items, skipped

    # ── 分析 ──

    async def analyze(self, *, project_id: str, file_id: str = "", actor_id: str = "",
                      filename: str = "", summary: str = "",
                      text: str = "", nodes: Optional[list[dict]] = None) -> PlanDraft:
        """产出草案（**不落库**）。

        Args:
            text: 资料正文（调用方已解析时直接给；否则由本方法从归档文件解析）
            nodes: 已抽取的条目（显式给定时跳过文本解析）

        Raises:
            ValueError: 低置信度（不进入装配）或缺少资料来源
        """
        confidence, why = self.recognize(filename, summary)
        draft = PlanDraft(
            draft_id=f"PLD-{datetime.now(BEIJING_TZ).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
            project_id=project_id,
            source_file_id=file_id,
            confidence=round(confidence, 2),
            created_at=datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
        )
        if confidence < self.CONFIDENCE_THRESHOLD:
            raise ValueError(f"类型识别置信度不足（{confidence:.2f} < {self.CONFIDENCE_THRESHOLD}）：{why}")

        # 幂等：同一来源文件已确认过 → 不重复产出（AC-US-19.9）
        if file_id:
            prior = self._find_confirmed(file_id)
            if prior:
                draft.note = f"来源文件 {file_id} 此前已确认写入（草案 {prior}），本次不重复产出"
                return draft

        if nodes is None:
            if not text:
                text = await self._read_file_text(file_id)
            nodes, skipped = self.extract_items(text)
            draft.skipped = skipped
        if not nodes:
            raise ValueError("资料中未抽取到任何节点条目（不产出草案）")

        existing = await self._load_existing(project_id)
        for raw in nodes:
            item = self._classify(raw, existing, project_id)
            draft.items.append(item)
        draft.note = f"类型识别：{why}"
        return draft

    @staticmethod
    async def _read_file_text(file_id: str) -> str:
        """从归档文件读取文本（复用既有解析服务）。"""
        if not file_id:
            return ""
        try:
            from ..infrastructure.database.models import File
            from ..services.file_parser_service import FileParserService

            with get_session() as session:
                f = session.query(File).filter(File.id == file_id).first()
                if f is None or not f.storage_path:
                    return ""
                path = f.storage_path
                name = f.filename or ""
            r = await FileParserService.parse_and_summarize(path, name)
            return getattr(r, "summary", "") or ""
        except Exception as e:
            logger.info("读取归档文件文本失败 file=%s: %s", file_id, e)
            return ""

    @staticmethod
    async def _load_existing(project_id: str) -> list[dict]:
        with get_session() as session:
            rows = session.query(ProjectNode).filter(
                ProjectNode.project_id == project_id,
                ProjectNode.is_discarded == False,
            ).all()
            return [{"node_id": r.node_id, "node_name": r.node_name or "",
                     "node_type": getattr(r, "node_type", "") or "",
                     "deadline": r.deadline or "",
                     "node_role": getattr(r, "node_role", "") or "BUSINESS"}
                    for r in rows]

    def _classify(self, raw: dict, existing: list[dict], project_id: str) -> DraftItem:
        """比对键优先节点编号，无编号时用「名称 + 项目」（AC-US-19.4）。"""
        nid = (raw.get("node_id") or "").strip()
        name = (raw.get("node_name") or "").strip()
        key = nid or f"{name}@{project_id}"
        item = DraftItem(match_key=key, node_id=nid, node_name=name,
                         deadline=raw.get("deadline", "") or "",
                         source_quote=raw.get("source_quote", ""),
                         node_type=raw.get("node_type", "TASK") or "TASK",
                         deliverables=[
                             {"deliverable_name": d, "target_amount": 1.0,
                              "unit": "份", "is_required": True}
                             for d in (raw.get("deliverables") or [])
                         ])

        match = None
        if nid:
            match = next((e for e in existing if e["node_id"] == nid), None)
        if match is None and name:
            match = next((e for e in existing if e["node_name"] == name), None)

        if match is None:
            item.change_type = NEW
            item.template_ref_id = self._template_hint(name)
        else:
            item.node_id = match["node_id"]
            changed = (
                (item.deadline and item.deadline != match["deadline"])
                or (item.node_type and item.node_type != match["node_type"])
            )
            item.change_type = UPDATE if changed else UNCHANGED
            item.template_ref_id = self._template_hint(name)
        return item

    @staticmethod
    def _template_hint(node_name: str) -> str:
        """模板参考系：名称与模板节点名一致时标注模板编号（AC-US-19.8）。"""
        try:
            from .node_template_loader import NodeTemplateLoader, TemplateUnavailable

            for t in NodeTemplateLoader().list_templates():
                if t.node_name and t.node_name in node_name:
                    return t.ref_id
        except TemplateUnavailable:
            return ""
        except Exception:
            return ""
        return ""

    # ── 草案存取 ──

    def save(self, draft: PlanDraft) -> str:
        path = _drafts_dir() / f"{draft.draft_id}.json"
        path.write_text(json.dumps(draft.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Plan draft saved: %s", path)
        return draft.draft_id

    @staticmethod
    def load(draft_id: str) -> dict:
        path = _drafts_dir() / f"{draft_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"草案不存在或已确认清理：{draft_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def delete(draft_id: str) -> None:
        path = _drafts_dir() / f"{draft_id}.json"
        if path.exists():
            path.unlink()

    # ── 确认写入 ──

    async def confirm(self, *, draft_id: str, accepted_indexes: list[int],
                      actor_id: str = "") -> dict:
        """人工勾选确认后写入（复用既有批量能力；未采纳项丢弃；**不删除任何节点**）。"""
        data = self.load(draft_id)
        items = data.get("items") or []
        accepted = [items[i] for i in (accepted_indexes or [])
                    if 0 <= i < len(items)]
        if not accepted:
            self.delete(draft_id)
            return {"ok": True, "created": 0, "updated": 0, "skipped": len(items),
                    "message": "未采纳任何条目，草案已丢弃（未写库）"}

        created = updated = 0
        failed: list[dict] = []
        blocked: list[dict] = [{
            "kind": "missing_deliverable",
            "node_id": i.get("node_id") or "",
            "node_name": i.get("node_name") or "",
            "reason": "资料未给出必需成果——按「成果必备」口径不予创建，请补齐成果后另建",
        } for i in blocked]
        project_id = data.get("project_id", "")

        new_items = [i for i in accepted if i.get("change_type") == NEW]
        upd_items = [i for i in accepted if i.get("change_type") == "UPDATE"]

        # 成果必备口径：无必需成果的节点不得创建（不得以占位成果绕过）。
        # 资料未给成果的条目**单列回报**，由人补齐后另建，而不是静默失败。
        creatable, blocked = [], []
        for i in new_items:
            req = [d for d in (i.get("deliverables") or [])
                   if d.get("is_required", True)]
            (creatable if req else blocked).append(i)
        new_items = creatable

        # 新增：复用既有批量建树能力（按项目隔离，携带操作人与来源文件身份）
        if new_items:
            try:
                from .node_batch import create_node_tree

                yaml_nodes = [{
                    "node_id": i.get("node_id") or "",
                    "node_name": i.get("node_name") or "",
                    "node_type": i.get("node_type") or "TASK",
                    "deadline": i.get("deadline") or "",
                    "deliverables": i.get("deliverables") or [],
                    "remark": f"来源资料：{data.get('source_file_no') or data.get('source_file_id')}",
                } for i in new_items]
                res = await create_node_tree(project_id, actor_id, yaml_nodes, dry_run=False)
                created = sum(1 for r in (res or []) if r.get("success"))
                failed.extend([r for r in (res or []) if not r.get("success")])
            except Exception as e:
                failed.append({"kind": "create", "reason": str(e)})

        if upd_items:
            try:
                from .node_batch_update import batch_update_nodes

                updates = [{"node_id": i.get("node_id"),
                            "deadline": i.get("deadline") or None}
                           for i in upd_items if i.get("node_id")]
                res = await batch_update_nodes(updates, operator_id=actor_id, dry_run=False)
                updated = sum(1 for r in (res or []) if r.get("success"))
                failed.extend([r for r in (res or []) if not r.get("success")])
            except Exception as e:
                failed.append({"kind": "update", "reason": str(e)})

        # 记录来源文件已确认（幂等锚点），随后清理草案
        self._mark_confirmed(data.get("source_file_id", ""), draft_id)
        self.delete(draft_id)
        msg = (f"已写入：新增 {created} / 更新 {updated}；"
               f"未采纳 {len(items) - len(accepted)} 项已丢弃（未删除任何既有节点）")
        if blocked:
            msg += f"；{len(blocked)} 项因资料未给必需成果未创建（需补齐后另建）"
        return {"ok": not failed, "created": created, "updated": updated,
                "skipped": len(items) - len(accepted), "failed": failed,
                "blocked": blocked, "message": msg}

    @staticmethod
    def _confirmed_path() -> Path:
        return _drafts_dir() / "confirmed.json"

    def _find_confirmed(self, file_id: str) -> str:
        try:
            data = json.loads(self._confirmed_path().read_text(encoding="utf-8"))
            return (data.get("files") or {}).get(file_id, "")
        except Exception:
            return ""

    def _mark_confirmed(self, file_id: str, draft_id: str) -> None:
        if not file_id:
            return
        path = self._confirmed_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
        files = data.get("files") or {}
        files[file_id] = draft_id
        data["files"] = files
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
