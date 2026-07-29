# emily-core/emily_core/skill/registry.py
"""SkillRegistry —— 降级为 SOP .md 索引器（L3 agent loop 迁移后）。

扫描 emily-data/sops/*.md，按 sop_id 索引。保留 dump_as_text/get_by_sop_id 等接口
供 SessionAgent 意图识别消费。不再解析 Skill YAML（已删除）。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("emily.skill.registry")


def _extract_sop_type(sop_id: str) -> str:
    parts = sop_id.split("-")
    for p in parts[1:]:
        if p in ("REC", "FILE", "QRY", "FLOW", "SYS"):
            return p
    return "UNKNOWN"


@dataclass
class SopDoc:
    """SOP .md 索引项。"""
    sop_id: str
    display_name: str
    file_path: str
    instructions: str = ""  # 首行摘要


@dataclass
class SkillRegistryStatus:
    total_files: int = 0
    successfully_parsed: int = 0
    failed_parsed: int = 0
    failed_files: list[str] = field(default_factory=list)
    is_ready: bool = False
    last_reload_at: str = ""


class SkillRegistry:
    """SOP .md 索引器（原 Skill YAML 注册表降级）。"""

    def __init__(self, skill_directory: str):
        # skill_directory 仍指向 skills/ 目录（兼容），但实际扫描 sops/
        self.skill_directory = Path(skill_directory)
        self._sop_dir: Path | None = None
        self._lock = threading.RLock()
        self._registry: dict[str, SopDoc] = {}
        self._is_ready = False

    def _resolve_sop_dir(self) -> Path:
        if self._sop_dir is not None:
            return self._sop_dir
        # sops/ 与 skills/ 同级
        candidates = [
            self.skill_directory.parent / "sops",
            self.skill_directory.parent.parent / "emily-data" / "sops",
            Path("/app/sops"),
        ]
        for c in candidates:
            if c.exists():
                self._sop_dir = c
                return c
        self._sop_dir = self.skill_directory.parent / "sops"
        return self._sop_dir

    def load(self) -> SkillRegistryStatus:
        with self._lock:
            return self._scan()

    def reload(self) -> SkillRegistryStatus:
        return self.load()

    def _scan(self) -> SkillRegistryStatus:
        now = datetime.now(timezone.utc).isoformat()
        sop_dir = self._resolve_sop_dir()
        new_reg: dict[str, SopDoc] = {}
        failed_files: list[str] = []
        if not sop_dir.exists():
            logger.warning("SOP dir not found: %s", sop_dir)
            self._registry = new_reg
            self._is_ready = False
            return SkillRegistryStatus(last_reload_at=now)
        ok = 0
        for p in sorted(sop_dir.glob("SOP-*.md")):
            try:
                sop_id = p.stem  # 如 SOP-002-REC-event_record
                text = p.read_text(encoding="utf-8")
                display_name = self._extract_display_name(text, sop_id)
                first_line = self._extract_first_instruction(text)
                new_reg[sop_id] = SopDoc(
                    sop_id=sop_id, display_name=display_name,
                    file_path=str(p), instructions=first_line,
                )
                ok += 1
            except Exception as e:
                failed_files.append(p.name)
                logger.error("SOP parse failed: %s — %s", p.name, e)
        self._registry = new_reg
        self._is_ready = ok > 0
        logger.info("SkillRegistry(SOP indexer) loaded: %d docs from %s", ok, sop_dir)
        return SkillRegistryStatus(total_files=ok, successfully_parsed=ok,
                                   failed_parsed=len(failed_files),
                                   failed_files=failed_files,
                                   is_ready=ok > 0, last_reload_at=now)

    @staticmethod
    def _extract_display_name(text: str, sop_id: str) -> str:
        # 从首行 # 标题提取
        for line in text.splitlines()[:3]:
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("# ").strip() or sop_id
        return sop_id

    @staticmethod
    def _extract_first_instruction(text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(">") and not line.startswith("|"):
                return line[:100]
        return ""

    # ── 查询接口（SessionAgent 依赖，签名不变）──

    def get_by_sop_id(self, sop_id: str) -> SopDoc | None:
        with self._lock:
            return self._registry.get(sop_id)

    def has_skill(self, sop_id: str) -> bool:
        with self._lock:
            return sop_id in self._registry

    def list_sop_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._registry.keys())

    def list_skills(self) -> list[SopDoc]:
        with self._lock:
            return list(self._registry.values())

    def dump_as_text(self) -> str:
        """以类型树格式导出（供 SessionAgent 意图识别消费）。保留原输出结构。"""
        with self._lock:
            docs = list(self._registry.values())
        if not docs:
            return "（暂无已加载的业务流程/SOP）"
        grouped: dict[str, list[SopDoc]] = {}
        for d in docs:
            grouped.setdefault(_extract_sop_type(d.sop_id), []).append(d)
        TYPE_DESC = {
            "REC": "记录与录入（事件/任务/会议等）",
            "FILE": "文件管理（归档/查询/分享）",
            "QRY": "数据查询（项目/进度/人员等）",
            "FLOW": "深度调查（跨维度分析/审计）",
            "SYS": "系统管理（确认/取消/设置等）",
        }
        lines = ["## 一、业务类型树（先看这里，确定消息属于哪个类型）", ""]
        for sop_type, type_docs in grouped.items():
            names = "、".join(d.display_name for d in type_docs)
            ids = "、".join(d.sop_id for d in type_docs)
            lines.append(f"**{sop_type}** — {TYPE_DESC.get(sop_type, sop_type)}")
            lines.append(f"  包含流程: {names}")
            lines.append(f"  编号: {ids}")
            lines.append("")
        lines += ["---", "", "## 二、各类型流程清单（锁定类型后精匹配）", ""]
        for sop_type, type_docs in grouped.items():
            lines.append(f"### {sop_type} — {TYPE_DESC.get(sop_type, sop_type)}")
            lines.append("")
            for d in type_docs:
                lines.append(f"**[{d.sop_id}] {d.display_name}**")
                if d.instructions:
                    lines.append(f"  说明: {d.instructions}")
                lines.append("")
            lines += ["---", ""]
        return "\n".join(lines)
