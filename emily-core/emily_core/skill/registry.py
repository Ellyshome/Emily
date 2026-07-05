# emily-core/emily_core/skill/registry.py
"""SkillRegistry —— Skill 注册表（load/reload/查询）。

参照 SOPIntentRegistry 的 load/reload/原子替换模式。
扫描 emily-data/skills/ 目录，解析全部 .skill.yaml 文件。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .definition import SkillDefinition
from .parser import parse_skill_file, SkillParseError

logger = logging.getLogger("emily.skill.registry")


def _extract_sop_type(sop_id: str) -> str:
    """从 sop_id 推导 SOP 类型（如 SOP-002-REC → REC）。

    兼容两种格式：
      - SOP-002-REC-event_record → REC
      - SOP-002-REC → REC
      - 无类型后缀 → UNKNOWN
    """
    parts = sop_id.split("-")
    for p in parts[1:]:  # 跳过 "SOP"
        if p in ("REC", "FILE", "QRY", "FLOW", "SYS"):
            return p
    return "UNKNOWN"


@dataclass
class SkillRegistryStatus:
    """SkillRegistry 运行状态快照。"""
    total_files: int = 0
    successfully_parsed: int = 0
    failed_parsed: int = 0
    failed_files: list[str] = field(default_factory=list)
    is_ready: bool = False
    last_reload_at: str = ""


class SkillRegistry:
    """Skill 注册表 —— 扫描目录 + 按 sop_id 查询 + 热重载。"""

    _SKILL_FILE_PATTERN = "*.skill.yaml"

    def __init__(self, skill_directory: str):
        self.skill_directory = Path(skill_directory)
        self._lock = threading.RLock()
        self._registry: dict[str, SkillDefinition] = {}   # sop_id → SkillDefinition
        self._by_skill_id: dict[str, SkillDefinition] = {}  # skill_id → SkillDefinition
        self._is_ready: bool = False

    # ── 加载 / 重载 ──

    def load(self) -> SkillRegistryStatus:
        """首次加载。"""
        with self._lock:
            new_registry, new_by_skill_id, status = self._scan_and_parse()
            self._registry = new_registry
            self._by_skill_id = new_by_skill_id
            self._is_ready = status.successfully_parsed > 0
            logger.info(
                "SkillRegistry loaded: %d skills, %d ok, %d failed",
                status.total_files, status.successfully_parsed, status.failed_parsed,
            )
            return status

    def reload(self) -> SkillRegistryStatus:
        """热重载（原子替换）。"""
        with self._lock:
            new_registry, new_by_skill_id, status = self._scan_and_parse()
            if status.successfully_parsed == 0 and len(self._registry) > 0:
                logger.warning("SkillRegistry reload: all failed, keeping old registry")
                return self._get_status()
            self._registry = new_registry
            self._by_skill_id = new_by_skill_id
            self._is_ready = status.successfully_parsed > 0
            logger.info(
                "SkillRegistry reloaded: %d ok, %d failed",
                status.successfully_parsed, status.failed_parsed,
            )
            return status

    # ── 查询 ──

    def get_by_sop_id(self, sop_id: str) -> SkillDefinition | None:
        """按 SOP 编号查询 Skill。"""
        with self._lock:
            return self._registry.get(sop_id)

    def get_by_skill_id(self, skill_id: str) -> SkillDefinition | None:
        """按 Skill ID 查询。"""
        with self._lock:
            return self._by_skill_id.get(skill_id)

    def has_skill(self, sop_id: str) -> bool:
        """判断某 SOP 是否有对应 Skill。"""
        with self._lock:
            return sop_id in self._registry

    def list_sop_ids(self) -> list[str]:
        """列出所有已注册 Skill 对应的 sop_id。"""
        with self._lock:
            return sorted(self._registry.keys())

    def list_skills(self) -> list[SkillDefinition]:
        """列出所有已注册 Skill 定义。"""
        with self._lock:
            return list(self._registry.values())

    # ── 目录输出（供 LLM 意图识别消费）──

    def dump_as_text(self) -> str:
        """将全部 Skill 以类型树格式导出为纯文本（供 LLM 消费）。

        替代 SOPIntentRegistry.dump_as_text()，成为意图识别 prompt 的唯一数据源。
        输出结构与 SOPIntentRegistry 兼容，确保 session.md prompt 模板无需修改。
        """
        with self._lock:
            skills = list(self._registry.values())

        if not skills:
            return "（暂无已加载的业务流程/Skill）"

        # 按 sop_type 分组（从 sop_id 推导类型）
        grouped: dict[str, list[SkillDefinition]] = {}
        for skill in skills:
            sop_type = _extract_sop_type(skill.sop_id)
            grouped.setdefault(sop_type, []).append(skill)

        # 类型描述映射
        TYPE_DESC = {
            "REC": "记录与录入（事件/任务/会议等）",
            "FILE": "文件管理（归档/查询/分享）",
            "QRY": "数据查询（项目/进度/人员等）",
            "FLOW": "深度调查（跨维度分析/审计）",
            "SYS": "系统管理（确认/取消/设置等）",
        }

        # 类型级兜底策略
        FALLBACK_BY_TYPE = {
            "REC": "这是记录/录入类请求。若以上 REC 流程均不匹配，使用 record_event / record_task 原子工具自由推理录入。",
            "FILE": "这是文件管理类请求。若以上 FILE 流程均不匹配，使用 file_storage 原子工具处理。",
            "QRY": "这是数据查询类请求。若以上 QRY 流程均不匹配，使用 query_data 工具查询，query_type 根据用户意图选择 event/task/meeting/file/message/summary。",
            "FLOW": "这是深度调查类请求。若以上 FLOW 流程均不匹配，使用系统内置的守护调查Agent执行跨维度分析。",
            "SYS": "这是系统管理类请求。若以上 SYS 流程均不匹配，但仍需系统功能，使用对应原子工具自由推理。",
        }

        lines: list[str] = []

        # ── 第一部分：类型树总览 ──
        lines.append("## 一、业务类型树（先看这里，确定消息属于哪个类型）")
        lines.append("")
        lines.append("请先将用户消息归类到以下类型之一，再在该类型下精匹配具体流程：")
        lines.append("")

        for sop_type, type_skills in grouped.items():
            names = "、".join(s.display_name for s in type_skills)
            sop_ids = "、".join(s.sop_id for s in type_skills)
            desc = TYPE_DESC.get(sop_type, sop_type)
            lines.append(f"**{sop_type}** — {desc}")
            lines.append(f"  包含流程: {names}")
            lines.append(f"  编号: {sop_ids}")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ── 第二部分：各类型详细规则 ──
        lines.append("## 二、各类型详细匹配规则（锁定类型后精匹配）")
        lines.append("")

        for sop_type, type_skills in grouped.items():
            desc = TYPE_DESC.get(sop_type, sop_type)
            lines.append(f"### {sop_type} — {desc}")
            lines.append("")

            for skill in type_skills:
                # 工具列表
                tool_names = ", ".join(t.name for t in skill.tools) if skill.tools else "（无工具声明）"
                lines.append(f"**[{skill.sop_id}] {skill.display_name}** | 工具: {tool_names}")

                # 执行步骤摘要
                if skill.steps:
                    step_descs = " → ".join(
                        f"{s.id}({s.tool_name})" for s in skill.steps[:5]
                    )
                    lines.append(f"  步骤: {step_descs}")
                else:
                    lines.append("  步骤: （无预定义步骤）")

                # instructions 首行摘要
                if skill.instructions:
                    first_line = skill.instructions.strip().split("\n")[0][:80]
                    lines.append(f"  说明: {first_line}")

                lines.append("")

            # 类型级兜底
            fallback = FALLBACK_BY_TYPE.get(sop_type, "")
            if fallback:
                lines.append(f"> **{sop_type} 类型兜底**: {fallback}")
                lines.append("")

            lines.append("---")

        return "\n".join(lines)

    # ── 内部 ──

    def _scan_and_parse(self) -> tuple[dict[str, SkillDefinition], dict[str, SkillDefinition], SkillRegistryStatus]:
        """扫描目录 + 解析。"""
        new_registry: dict[str, SkillDefinition] = {}
        new_by_skill_id: dict[str, SkillDefinition] = {}
        now = datetime.now(timezone.utc).isoformat()

        if not self.skill_directory.exists():
            logger.warning("Skill directory not found: %s", self.skill_directory)
            status = SkillRegistryStatus(last_reload_at=now)
            return new_registry, new_by_skill_id, status

        skill_files = sorted(self.skill_directory.glob(self._SKILL_FILE_PATTERN))
        ok_count = 0
        failed_count = 0
        failed_files: list[str] = []

        for file_path in skill_files:
            try:
                skill = parse_skill_file(file_path)
                new_registry[skill.sop_id] = skill
                new_by_skill_id[skill.skill_id] = skill
                ok_count += 1
            except (SkillParseError, Exception) as e:
                failed_count += 1
                failed_files.append(file_path.name)
                logger.error("Skill parse failed: %s — %s", file_path.name, e)

        status = SkillRegistryStatus(
            total_files=ok_count + failed_count,
            successfully_parsed=ok_count,
            failed_parsed=failed_count,
            failed_files=failed_files,
            is_ready=ok_count > 0,
            last_reload_at=now,
        )
        return new_registry, new_by_skill_id, status

    def _get_status(self) -> SkillRegistryStatus:
        """返回当前状态快照。"""
        with self._lock:
            ok = sum(1 for _ in self._registry.values())
            return SkillRegistryStatus(
                total_files=ok,
                successfully_parsed=ok,
                is_ready=self._is_ready,
            )
