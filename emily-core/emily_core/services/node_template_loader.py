"""node_template_loader.py —— 全景节点模板库只读读取。

职责边界：
  · 只读：读 index.yaml → 读 {ref_id}/template.yaml → 返回清单/附件/对象声明原文
  · 不写盘、不落库、不调 LLM（幂等，可反复调用）
  · 模板库缺失 / 索引损坏 → 抛 TemplateUnavailable（明确失败），由调用方决定降级

承载形态（人工维护，见 emily-data/node_templates/README.md）：
    node_templates/index.yaml              索引（脚本生成）
    node_templates/{ref_id}/template.yaml  结构化清单入口
    node_templates/{ref_id}/<附件>          任意格式，仅登记不解析

参照模式：services/rule_book_loader.py（资产加载）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..infrastructure.paths import resolve_data_path

logger = logging.getLogger("emily.node_template_loader")

MANIFEST_NAME = "template.yaml"
_DEV_RELATIVE = "emily-data/node_templates"
_CONTAINER_PATH = "/app/data/node_templates"


class TemplateUnavailable(RuntimeError):
    """模板库不可用（目录缺失 / 索引缺失或损坏）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── DTO ──


@dataclass
class Attachment:
    """模板附件（仅登记，不解析内容）。"""

    name: str
    size: int = 0
    type: str = ""


@dataclass
class TemplateDeliverable:
    """模板声明的成果条目。"""

    name: str
    target_amount: float = 1.0
    unit: str = "份"
    is_required: bool = True
    typical_filenames: str = ""


@dataclass
class TemplateSummary:
    """索引条目（清单视图）。"""

    ref_id: str
    node_name: str
    node_type: str
    stage_id: int = 0
    summary: str = ""
    attachment_count: int = 0


@dataclass
class TemplateDetail:
    """模板详情：清单 + 成果 + 对象声明原文 + 前置条件 + 附件清单。"""

    ref_id: str
    node_name: str
    node_type: str
    stage_id: int = 0
    summary: str = ""
    deliverables: list[TemplateDeliverable] = field(default_factory=list)
    declarations_raw: dict = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── 服务 ──


class NodeTemplateLoader:
    """模板库只读读取器。"""

    def __init__(self, templates_dir: str = ""):
        self.templates_dir = Path(
            templates_dir
            or resolve_data_path("", _CONTAINER_PATH, _DEV_RELATIVE)
        )

    # ── 可用性 ──

    def is_available(self) -> tuple[bool, str]:
        """模板库是否可用于检索。返回 (可用, 原因)。"""
        if not self.templates_dir.exists():
            return False, f"模板目录不存在: {self.templates_dir}"
        index_path = self.templates_dir / "index.yaml"
        if not index_path.exists():
            return False, f"索引文件缺失: {index_path}"
        try:
            data = self._read_index()
        except TemplateUnavailable as e:
            return False, e.reason
        if not data.get("templates"):
            return False, "索引为空（无任何模板条目）"
        return True, ""

    def _read_index(self) -> dict:
        index_path = self.templates_dir / "index.yaml"
        try:
            import yaml
            data = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        except TemplateUnavailable:
            raise
        except Exception as e:
            raise TemplateUnavailable(f"索引文件损坏，无法解析: {index_path}（{e}）") from e
        if not isinstance(data, dict):
            raise TemplateUnavailable(f"索引格式异常（顶层非映射）: {index_path}")
        return data

    # ── 清单 ──

    def list_templates(self, node_type: str = "", keyword: str = "") -> list[TemplateSummary]:
        """列出模板清单，可按类型 / 关键词过滤。

        Raises:
            TemplateUnavailable: 模板库缺失或索引损坏（明确失败，不以空列表冒充成功）
        """
        data = self._read_index()
        entries = data.get("templates") or []
        if not entries:
            raise TemplateUnavailable("索引无模板条目")

        nt = (node_type or "").strip().upper()
        kw = (keyword or "").strip()
        out: list[TemplateSummary] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if nt and str(e.get("node_type", "")).upper() != nt:
                continue
            if kw:
                haystack = " ".join(str(e.get(k, "")) for k in
                                    ("ref_id", "node_name", "summary"))
                if kw not in haystack:
                    continue
            out.append(TemplateSummary(
                ref_id=str(e.get("ref_id", "")),
                node_name=str(e.get("node_name", "")),
                node_type=str(e.get("node_type", "TASK")),
                stage_id=int(e.get("stage_id", 0) or 0),
                summary=str(e.get("summary", "")),
                attachment_count=len(e.get("attachments") or []),
            ))
        return out

    # ── 详情 ──

    def get_template(self, ref_id: str) -> Optional[TemplateDetail]:
        """读取指定模板的清单 + 对象声明原文 + 附件清单。未命中返回 None。"""
        ref_id = (ref_id or "").strip()
        if not ref_id:
            return None

        data = self._read_index()
        entry = next(
            (e for e in (data.get("templates") or [])
             if isinstance(e, dict) and str(e.get("ref_id", "")) == ref_id),
            None,
        )
        if entry is None:
            return None

        manifest = self._manifest_path(entry, ref_id)
        if manifest is None or not manifest.exists():
            # 索引有登记但清单文件缺失 → 仍返回索引可见的骨架（附件不影响主链路）
            logger.warning("模板清单文件缺失: %s（ref_id=%s）", manifest, ref_id)
            return TemplateDetail(
                ref_id=ref_id,
                node_name=str(entry.get("node_name", "")),
                node_type=str(entry.get("node_type", "TASK")),
                stage_id=int(entry.get("stage_id", 0) or 0),
                summary=str(entry.get("summary", "")),
                attachments=self._attachments_from_index(entry),
                warnings=["清单文件缺失，成果与对象声明不可读（附件不影响主链路）"],
            )

        warnings: list[str] = []
        raw = self._load_manifest(manifest, warnings)
        return TemplateDetail(
            ref_id=str(raw.get("ref_id", ref_id)),
            node_name=str(raw.get("node_name", entry.get("node_name", ""))),
            node_type=str(raw.get("node_type", entry.get("node_type", "TASK"))),
            stage_id=int(raw.get("stage_id", entry.get("stage_id", 0)) or 0),
            summary=str(raw.get("summary", entry.get("summary", ""))),
            deliverables=self._parse_deliverables(raw),
            declarations_raw=raw.get("declarations") or {},
            preconditions=[str(x) for x in (raw.get("preconditions") or [])],
            attachments=self._attachments_from_index(entry) or self._scan_attachments(manifest.parent),
            warnings=warnings,
        )

    # ── 索引一致性（只读巡检；供 self_check 使用）──

    def check_consistency(self) -> dict:
        """索引与模板单元目录的一致性巡检（不写盘）。

        Returns:
            {"ok": bool, "template_count": int, "added": [...], "removed": [...],
             "missing_manifest": [...], "error": str}
        """
        if not self.templates_dir.exists():
            return {"ok": False, "error": f"模板目录不存在: {self.templates_dir}", "template_count": 0}

        # 目录真值：含清单入口的子目录即一个模板单元
        on_disk: set[str] = set()
        missing: list[str] = []
        for d in sorted(p for p in self.templates_dir.iterdir() if p.is_dir()):
            if not (d / MANIFEST_NAME).exists():
                continue
            on_disk.add(d.name)

        try:
            data = self._read_index()
        except TemplateUnavailable as e:
            return {"ok": False, "error": e.reason, "template_count": len(on_disk)}

        indexed = {str(e.get("ref_id", "")) for e in (data.get("templates") or []) if isinstance(e, dict)}
        for ref in sorted(indexed):
            if not (self.templates_dir / ref / MANIFEST_NAME).exists():
                missing.append(ref)

        added = sorted(on_disk - indexed)
        removed = sorted(indexed - on_disk)
        return {
            "ok": not (added or removed or missing),
            "template_count": len(on_disk),
            "added": added,
            "removed": removed,
            "missing_manifest": missing,
            "error": "",
        }

    # ── 内部 ──

    def _manifest_path(self, entry: dict, ref_id: str) -> Optional[Path]:
        """清单入口路径：优先用索引的 entry 字段，回退 {ref_id}/template.yaml。"""
        rel = str(entry.get("entry", "") or "").strip()
        if rel:
            return self.templates_dir / rel
        return self.templates_dir / ref_id / MANIFEST_NAME

    def _load_manifest(self, path: Path, warnings: list[str]) -> dict:
        try:
            import yaml
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("模板清单读取失败: %s（%s）", path, e)
            warnings.append(f"清单文件解析失败: {e}")
            return {}

    @staticmethod
    def _parse_deliverables(raw: dict) -> list[TemplateDeliverable]:
        out: list[TemplateDeliverable] = []
        for d in (raw.get("deliverables") or []):
            if not isinstance(d, dict):
                continue
            name = str(d.get("name") or d.get("deliverable_name") or "").strip()
            if not name:
                continue
            try:
                target = float(d.get("target_amount", d.get("target", 1.0)) or 1.0)
            except (TypeError, ValueError):
                target = 1.0
            out.append(TemplateDeliverable(
                name=name,
                target_amount=target,
                unit=str(d.get("unit") or "份"),
                is_required=bool(d.get("is_required", True)),
                typical_filenames=str(d.get("typical_filenames", "") or ""),
            ))
        return out

    @staticmethod
    def _attachments_from_index(entry: dict) -> list[Attachment]:
        out: list[Attachment] = []
        for a in (entry.get("attachments") or []):
            if not isinstance(a, dict):
                continue
            out.append(Attachment(
                name=str(a.get("name", "")),
                size=int(a.get("size", 0) or 0),
                type=str(a.get("type", "")),
            ))
        return out

    @staticmethod
    def _scan_attachments(unit_dir: Path) -> list[Attachment]:
        """索引未登记附件时的目录回退（附件缺失不影响模板可读）。"""
        out: list[Attachment] = []
        try:
            for f in sorted(unit_dir.iterdir()):
                if f.is_file() and f.name != MANIFEST_NAME:
                    out.append(Attachment(name=f.name, size=f.stat().st_size,
                                          type=(f.suffix.lstrip(".").lower() or "unknown")))
        except OSError:
            return []
        return out


def build_loader() -> NodeTemplateLoader:
    """工厂：供工具与编排层按需取用（无状态，可重复构造）。"""
    return NodeTemplateLoader()
