"""SOPIntentRegistry —— SOP 意图注册表（纯加载机 + 目录格式化器）。

职责边界：
  - SOPIntentRegistry: 解析 SOP §1(权限) + §2(意图识别标准) → 索引 → 格式化为文本
  - Orchestrator (MasterAgent): LLM 语义匹配 → 编排 → 回复
  - Specialist (BusinessFlowAgent): 加载 SOP §1-§7 全文 → 按 SOP 逐步执行

设计原则：
  P1 启动即加载  P2 结构化解析  P3 LLM语义匹配  P4 权限前置
  P5 动态可重载  P6 容错降级    P7 原子替换      P8 可观测

注意：SOPIntentRegistry 不做意图匹配 —— 匹配 100% 由 LLM 在 Orchestrator 侧完成。
"""

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("emily.agent.intent_registry")


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构定义
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SOPIntentSpec:
    """从单个 SOP 文件解析出的全部路由相关字段（不可变）。"""

    # ---- 第1章：业务流版本信息 ----
    sop_id: str                     # "SOP-001-REC"
    sop_version: str                # "v1.3"
    sop_file_path: str              # 源文件绝对路径
    sop_file_name: str              # 源文件名
    display_name: str               # 业务名称，如 "会议纪要录入"
    sop_type: str                   # "REC" / "FILE" / "FLOW" / "QRY" / "SYS"

    # ---- 权限控制 ----
    allow_roles: tuple = ("all",)   # 从第1章表格"权限控制"行解析，缺失降级为 ("all",)

    # ---- 第2.1节：触发语义特征 ----
    trigger_keywords: tuple = ()    # 触发关键词
    deny_conditions: tuple = ()     # 否定条件

    # ---- 第2.2节：示例对话 ----
    positive_examples: tuple = ()   # 正面示例
    negative_examples: tuple = ()   # 反面示例

    # ---- 元信息 ----
    parse_status: str = "ok"        # "ok" | "partial" | "failed"
    parse_errors: tuple = ()        # 解析问题列表
    parsed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RegistryStatus:
    """SOPIntentRegistry 的当前运行状态快照。"""
    total_sop_files: int = 0
    successfully_parsed: int = 0
    partially_parsed: int = 0
    failed_parsed: int = 0
    failed_sop_files: list = field(default_factory=list)
    last_reload_at: str = ""
    sop_directory: str = ""
    is_ready: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# SOPIntentRegistry 主类
# ══════════════════════════════════════════════════════════════════════════════


class SOPIntentRegistry:
    """SOP 意图注册表 —— 纯加载机 + 目录格式化器。

    用法示例：
        registry = SOPIntentRegistry(sop_directory=".../SOPrepository")
        registry.load()

        # Orchestrator 获取目录文本，注入 LLM system prompt
        catalog_text = registry.dump_as_text()

        # 新增 SOP 文件后热重载
        registry.reload()
    """

    # SOP 文件命名规范: SOP-XXX-TYPE-name.md
    _SOP_FILE_PATTERN = re.compile(r"^SOP-\d{3}-[A-Z]+.*\.md$")
    # SOP ID 提取: SOP-XXX-TYPE
    _SOP_ID_PATTERN = re.compile(r"(SOP-\d{3}-[A-Z]+)")
    # 跳过规范文件
    _SKIP_PREFIXES = ("SOP-000-BASE", "SOP-000-")

    def __init__(self, sop_directory: str):
        self.sop_directory = Path(sop_directory)
        self._lock = threading.RLock()
        self._registry: dict[str, SOPIntentSpec] = {}
        self._last_reload_at: str = ""
        self._is_ready: bool = False

    # ═══════════════════════════════════════════════════════════════
    # 公开 API — 加载与重载
    # ═══════════════════════════════════════════════════════════════

    def load(self) -> RegistryStatus:
        """首次加载。优先从 .sop_index.json 加载，失败则完整解析并持久化。"""
        with self._lock:
            # 优先从持久化索引加载
            cached = self._try_load_index()
            if cached is not None:
                self._registry = cached
                ok = sum(1 for s in cached.values() if s.parse_status == "ok")
                partial = sum(1 for s in cached.values() if s.parse_status == "partial")
                failed = sum(1 for s in cached.values() if s.parse_status == "failed")
                now = datetime.now(timezone.utc).isoformat()
                status = RegistryStatus(
                    total_sop_files=len(cached),
                    successfully_parsed=ok,
                    partially_parsed=partial,
                    failed_parsed=failed,
                    last_reload_at=now,
                    sop_directory=str(self.sop_directory),
                    is_ready=ok > 0,
                )
                self._last_reload_at = now
                self._is_ready = ok > 0
                logger.info(
                    "SOPIntentRegistry loaded from index: %d SOP(s), %d ok, %d partial, %d failed",
                    status.total_sop_files,
                    status.successfully_parsed,
                    status.partially_parsed,
                    status.failed_parsed,
                )
                return status

            # 索引不可用 → 完整解析
            new_registry, status = self._scan_and_parse()
            self._registry = new_registry
            self._last_reload_at = status.last_reload_at
            self._is_ready = status.successfully_parsed > 0
            self._save_index()
            logger.info(
                "SOPIntentRegistry loaded: %d SOP(s), %d ok, %d partial, %d failed",
                status.total_sop_files,
                status.successfully_parsed,
                status.partially_parsed,
                status.failed_parsed,
            )
            return status

    def reload(self) -> RegistryStatus:
        """运行时重新扫描目录（原子替换策略，仅解析文件一次）。"""
        with self._lock:
            old_registry = self._registry
            new_registry, status = self._scan_and_parse()

            if status.successfully_parsed == 0:
                # 全部失败 → 保留旧索引
                self._registry = old_registry
                logger.warning(
                    "SOPIntentRegistry reload: all %d files failed, keeping old index (%d entries)",
                    status.total_sop_files,
                    len(old_registry),
                )
            else:
                # 原子 swap（解析结果直接使用，不重复解析）
                self._registry = new_registry
                self._last_reload_at = status.last_reload_at
                self._is_ready = True
                self._save_index()
                logger.info(
                    "SOPIntentRegistry reloaded: %d ok, %d partial, %d failed",
                    status.successfully_parsed,
                    status.partially_parsed,
                    status.failed_parsed,
                )

            return status

    # ═══════════════════════════════════════════════════════════════
    # 公开 API — 目录输出（供 LLM 消费）
    # ═══════════════════════════════════════════════════════════════

    def dump_as_text(self) -> str:
        """将全部 SOP 以类型树引导格式导出为纯文本（供 LLM 消费）。

        输出结构：
          一、业务类型树（总览 → LLM 先看这里锁定类型）
          二、各类型详细匹配规则（锁定后精匹配）

        向后兼容：接口签名不变，内部委托给 dump_type_tree()。
        """
        return self.dump_type_tree()

    def dump_type_tree(self) -> str:
        """以类型树引导格式输出全部 SOP 的意图规则（核心方法）。

        供 LLM 分层推理：
          第一步：看「类型树总览」锁定类型
          第二步：看该类型的详细规则做精匹配
        """
        with self._lock:
            specs = [s for s in self._registry.values() if s.parse_status != "failed"]

        if not specs:
            return "（暂无已加载的业务流程）"

        grouped = self._group_by_type(specs)

        # ── 第一部分：类型树总览 ──
        lines = []
        lines.append("## 一、业务类型树（先看这里，确定消息属于哪个类型）")
        lines.append("")
        lines.append("请先将用户消息归类到以下 5 个类型之一，再在该类型下精匹配具体流程：")
        lines.append("")

        for sop_type, type_specs in grouped.items():
            names = "、".join(s.display_name for s in type_specs)
            sop_ids = "、".join(s.sop_id for s in type_specs)
            desc = self._type_description(sop_type)
            lines.append(f"**{sop_type}** — {desc}")
            lines.append(f"  包含流程: {names}")
            lines.append(f"  编号: {sop_ids}")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ── 第二部分：各类型详细规则 ──
        lines.append("## 二、各类型详细匹配规则（锁定类型后精匹配）")
        lines.append("")

        # 类型级兜底策略
        FALLBACK_BY_TYPE = {
            "REC": "这是记录/录入类请求。若以上 REC 流程均不匹配，使用 record_event / record_task 原子工具自由推理录入。",
            "FILE": "这是文件管理类请求。若以上 FILE 流程均不匹配，使用 file_storage 原子工具处理。",
            "QRY": "这是数据查询类请求。若以上 QRY 流程均不匹配，使用 query_data 工具查询，query_type 根据用户意图选择 event/task/meeting/file/message/summary。",
            "FLOW": "这是深度调查类请求。若以上 FLOW 流程均不匹配，使用系统内置的守护调查Agent执行跨维度分析。",
            "SYS": "这是系统管理类请求。若以上 SYS 流程均不匹配，但仍需系统功能，使用对应原子工具自由推理。",
        }

        for sop_type, type_specs in grouped.items():
            desc = self._type_description(sop_type)
            lines.append(f"### {sop_type} — {desc}")
            lines.append("")

            for spec in type_specs:
                # 精简头部
                roles_str = ", ".join(spec.allow_roles)
                lines.append(f"**[{spec.sop_id}] {spec.display_name}** | 权限: {roles_str}")

                # 触发条件（压缩为一行，最多 3 条）
                if spec.trigger_keywords:
                    triggers = "；".join(spec.trigger_keywords[:3])
                    lines.append(f"  触发: {triggers}")
                else:
                    lines.append("  触发: （未定义）")

                # 否定条件（只保留前 2 条）
                if spec.deny_conditions:
                    denies = "；".join(spec.deny_conditions[:2])
                    lines.append(f"  否定: {denies}")

                # 正例（最多 1 条）
                if spec.positive_examples:
                    lines.append(f"  正例: {spec.positive_examples[0]}")

                # 反例（最多 1 条）
                if spec.negative_examples:
                    lines.append(f"  反例: {spec.negative_examples[0]}")

                if spec.parse_status == "partial":
                    lines.append(f"  ⚠ 解析警告")

                lines.append("")

            # 类型级兜底
            fallback = FALLBACK_BY_TYPE.get(sop_type, "")
            if fallback:
                lines.append(f"> **{sop_type} 类型兜底**: {fallback}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def list_loaded_sops(self) -> list[str]:
        """返回所有已加载的 SOP 编号列表（按编号排序）。"""
        with self._lock:
            return sorted(self._registry.keys())

    def get_spec(self, sop_id: str) -> SOPIntentSpec | None:
        """获取指定 SOP 的完整解析数据（用于权限检查等）。"""
        with self._lock:
            return self._registry.get(sop_id)

    # ═══════════════════════════════════════════════════════════════
    # 公开 API — 监控
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> RegistryStatus:
        """返回当前运行状态快照。"""
        with self._lock:
            ok = sum(1 for s in self._registry.values() if s.parse_status == "ok")
            partial = sum(1 for s in self._registry.values() if s.parse_status == "partial")
            failed = sum(1 for s in self._registry.values() if s.parse_status == "failed")
            failed_files = [
                s.sop_file_name
                for s in self._registry.values()
                if s.parse_status == "failed"
            ]
            return RegistryStatus(
                total_sop_files=len(self._registry),
                successfully_parsed=ok,
                partially_parsed=partial,
                failed_parsed=failed,
                failed_sop_files=failed_files,
                last_reload_at=self._last_reload_at,
                sop_directory=str(self.sop_directory),
                is_ready=self._is_ready,
            )

    # ═══════════════════════════════════════════════════════════════
    # 内部实现 — 类型树辅助方法
    # ═══════════════════════════════════════════════════════════════

    @property
    def _index_path(self) -> Path:
        """持久化索引文件路径。"""
        return self.sop_directory / ".sop_index.json"

    @staticmethod
    def _type_description(sop_type: str) -> str:
        """类型 → 一句话定义。"""
        DESCRIPTIONS = {
            "REC": "记录类 —— 将信息录入系统（会议纪要、事件、任务、长期记忆）",
            "FILE": "文件类 —— 文件归档与管理",
            "QRY": "查询类 —— 从系统查询已有数据",
            "FLOW": "流程类 —— 跨维度深度调查与分析",
            "SYS": "系统类 —— 系统维护与管理功能",
        }
        return DESCRIPTIONS.get(sop_type, f"{sop_type} 类")

    @staticmethod
    def _group_by_type(specs: list) -> dict[str, list]:
        """按 sop_type 分组，保持类型顺序：REC → FILE → QRY → FLOW → SYS。"""
        type_order = ["REC", "FILE", "QRY", "FLOW", "SYS"]
        groups: dict[str, list] = {}
        for spec in specs:
            t = spec.sop_type
            if t not in groups:
                groups[t] = []
            groups[t].append(spec)
        ordered: dict[str, list] = {}
        for t in type_order:
            if t in groups:
                ordered[t] = sorted(groups[t], key=lambda s: s.sop_id)
        for t in sorted(groups.keys()):
            if t not in ordered:
                ordered[t] = sorted(groups[t], key=lambda s: s.sop_id)
        return ordered

    # ═══════════════════════════════════════════════════════════════
    # 内部实现 — 索引持久化
    # ═══════════════════════════════════════════════════════════════

    def _try_load_index(self) -> dict[str, SOPIntentSpec] | None:
        """尝试从 .sop_index.json 加载索引。

        返回 None 表示索引不存在、已过期或损坏，需要走完整解析。
        过期判定：源 .md 文件最新 mtime > 索引记录的 mtime。
        """
        index_path = self._index_path
        if not index_path.exists():
            logger.debug("SOP index not found: %s", index_path)
            return None

        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("SOP index corrupted: %s", e)
            return None

        # 过期检查
        recorded_mtime = index.get("_meta", {}).get("source_files_mtime_latest", "")
        if not recorded_mtime:
            return None

        try:
            index_mtime = datetime.fromisoformat(recorded_mtime).timestamp()
        except (ValueError, OSError):
            return None

        actual_latest = max(
            (f.stat().st_mtime for f in self.sop_directory.glob("SOP-*.md")),
            default=0,
        )
        if actual_latest > index_mtime:
            logger.info("SOP index stale (source changed), will rebuild")
            return None

        # 反序列化为 SOPIntentSpec
        registry: dict[str, SOPIntentSpec] = {}
        for sop_id, data in index.get("sops", {}).items():
            spec = SOPIntentSpec(
                sop_id=data.get("sop_id", sop_id),
                sop_version=data.get("version", "v0.0"),
                sop_file_path=str(self.sop_directory / data.get("file_name", "")),
                sop_file_name=data.get("file_name", ""),
                display_name=data.get("display_name", sop_id),
                sop_type=data.get("sop_type", "UNK"),
                allow_roles=tuple(data.get("allow_roles", ["all"])),
                trigger_keywords=tuple(data.get("trigger_keywords", [])),
                deny_conditions=tuple(data.get("deny_conditions", [])),
                positive_examples=tuple(data.get("positive_examples", [])),
                negative_examples=tuple(data.get("negative_examples", [])),
                parse_status=data.get("parse_status", "ok"),
            )
            registry[spec.sop_id] = spec

        logger.info("SOP index loaded from disk: %d sops (skipped md parsing)", len(registry))
        return registry

    def _save_index(self) -> None:
        """将当前内存索引序列化为 .sop_index.json。

        索引内容：
        - _meta: 构建元信息（版本、时间、源文件数、最新 mtime）
        - type_tree: 按 sop_type 分组的类型树（含 fallback_guidance）
        - sops: 每条 SOP 的路由字段（§1+§2，不含 §3-§7）
        """
        with self._lock:
            specs = [s for s in self._registry.values() if s.parse_status != "failed"]

        if not specs:
            return

        grouped = self._group_by_type(specs)

        # 类型级兜底策略
        FALLBACK_BY_TYPE = {
            "REC": {
                "fallback_tools": ["record_event", "record_task"],
                "fallback_guidance": "这是记录/录入类请求。若以上 REC 流程均不匹配，使用 record_event / record_task 原子工具自由推理录入。",
            },
            "FILE": {
                "fallback_tools": ["file_storage"],
                "fallback_guidance": "这是文件管理类请求。若以上 FILE 流程均不匹配，使用 file_storage 原子工具处理。",
            },
            "QRY": {
                "fallback_tools": ["query_data"],
                "fallback_guidance": "这是数据查询类请求。若以上 QRY 流程均不匹配，使用 query_data 工具查询。",
            },
            "FLOW": {
                "fallback_tools": [],
                "fallback_guidance": "这是深度调查类请求。若以上 FLOW 流程均不匹配，使用系统内置的守护调查Agent执行跨维度分析。",
            },
            "SYS": {
                "fallback_tools": [],
                "fallback_guidance": "这是系统管理类请求。若以上 SYS 流程均不匹配，但仍需系统功能，使用对应原子工具自由推理。",
            },
        }

        type_tree = {}
        for sop_type, type_specs in grouped.items():
            fb = FALLBACK_BY_TYPE.get(sop_type, {})
            type_tree[sop_type] = {
                "description": self._type_description(sop_type),
                "fallback_tools": fb.get("fallback_tools", []),
                "fallback_guidance": fb.get("fallback_guidance", ""),
                "sops": [s.sop_id for s in type_specs],
            }

        sops = {}
        for spec in specs:
            sops[spec.sop_id] = {
                "sop_id": spec.sop_id,
                "display_name": spec.display_name,
                "sop_type": spec.sop_type,
                "version": spec.sop_version,
                "file_name": spec.sop_file_name,
                "allow_roles": list(spec.allow_roles),
                "trigger_keywords": list(spec.trigger_keywords),
                "deny_conditions": list(spec.deny_conditions),
                "positive_examples": list(spec.positive_examples),
                "negative_examples": list(spec.negative_examples),
                "parse_status": spec.parse_status,
            }

        # 计算源文件最新 mtime
        latest_mtime = max(
            (f.stat().st_mtime for f in self.sop_directory.glob("SOP-*.md")),
            default=0,
        )
        latest_mtime_str = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()

        ok = sum(1 for s in specs if s.parse_status == "ok")
        partial = sum(1 for s in specs if s.parse_status == "partial")
        failed = sum(1 for s in self._registry.values() if s.parse_status == "failed")

        index = {
            "_meta": {
                "version": "1",
                "built_at": datetime.now(timezone.utc).isoformat(),
                "source_dir": str(self.sop_directory.name),
                "source_files_count": len(specs),
                "source_files_mtime_latest": latest_mtime_str,
                "parse_result": {
                    "ok": ok,
                    "partial": partial,
                    "failed": failed,
                },
            },
            "type_tree": type_tree,
            "sops": sops,
        }

        self._index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("SOP index saved: %s (%d sops, %d types)",
                     self._index_path, len(specs), len(type_tree))

    def _scan_and_parse(self) -> tuple[dict[str, SOPIntentSpec], RegistryStatus]:
        """扫描目录 + 解析所有 SOP 文件 → 返回 (新索引, 状态)。

        注意：此方法不修改 self._registry，调用方负责决定是否 swap。
        """
        new_registry: dict[str, SOPIntentSpec] = {}
        now = datetime.now(timezone.utc).isoformat()

        if not self.sop_directory.exists():
            logger.error("SOP directory not found: %s", self.sop_directory)
            status = RegistryStatus(
                total_sop_files=0,
                last_reload_at=now,
                sop_directory=str(self.sop_directory),
                is_ready=False,
            )
            return new_registry, status

        sop_files = sorted(self.sop_directory.glob("SOP-*.md"))
        ok_count = 0
        partial_count = 0
        failed_count = 0
        failed_files: list[str] = []

        for file_path in sop_files:
            file_name = file_path.name

            # 跳过非 SOP 文件（不匹配命名规范）
            if not self._SOP_FILE_PATTERN.match(file_name):
                continue

            # 跳过规范文件
            if any(file_name.startswith(prefix) for prefix in self._SKIP_PREFIXES):
                logger.debug("Skipping base standard file: %s", file_name)
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
                spec = self._parse_markdown(content, str(file_path), file_name)
                new_registry[spec.sop_id] = spec

                if spec.parse_status == "ok":
                    ok_count += 1
                elif spec.parse_status == "partial":
                    partial_count += 1
                    logger.warning(
                        "SOP %s partially parsed: %s",
                        file_name,
                        ", ".join(spec.parse_errors) if spec.parse_errors else "unknown",
                    )
                else:
                    failed_count += 1
                    failed_files.append(file_name)
                    logger.error(
                        "SOP %s failed to parse: %s",
                        file_name,
                        ", ".join(spec.parse_errors) if spec.parse_errors else "unknown",
                    )

            except Exception as e:
                failed_count += 1
                failed_files.append(file_name)
                logger.error("SOP %s parse exception: %s", file_name, e, exc_info=True)
                # 创建一个 failed spec 记录
                sop_id = self._extract_sop_id_from_filename(file_name)
                new_registry[sop_id] = SOPIntentSpec(
                    sop_id=sop_id,
                    sop_version="v0.0",
                    sop_file_path=str(file_path),
                    sop_file_name=file_name,
                    display_name=file_name,
                    sop_type="UNK",
                    parse_status="failed",
                    parse_errors=(str(e),),
                )

        total = ok_count + partial_count + failed_count
        status = RegistryStatus(
            total_sop_files=total,
            successfully_parsed=ok_count,
            partially_parsed=partial_count,
            failed_parsed=failed_count,
            failed_sop_files=failed_files,
            last_reload_at=now,
            sop_directory=str(self.sop_directory),
            is_ready=ok_count > 0,
        )
        return new_registry, status

    def _parse_markdown(
        self, content: str, file_path: str, file_name: str
    ) -> SOPIntentSpec:
        """核心解析函数。使用 mistune 块级 AST 解析 SOP Markdown。

        mistune 未安装或解析异常时自动降级到旧正则解析器。
        """
        try:
            from .sop_parser import parse_sop_markdown

            result = parse_sop_markdown(content, file_path, file_name)

            errors: list[str] = []
            parse_status = "ok"

            sop_id = result.get('sop_id', '')
            if not sop_id:
                parse_status = "failed"
                errors.append("无法提取 sop_id")
                sop_id = self._extract_sop_id_from_filename(file_name)

            # sop_type 兜底
            sop_type = result.get('sop_type', '') or self._extract_sop_type_from_id_or_name(
                sop_id or file_name
            )

            # display_name 兜底
            display_name = result.get('display_name', '')
            if not display_name:
                display_name = self._extract_display_name_from_filename(file_name)

            # allow_roles 兜底
            allow_roles = result.get('allow_roles', ())
            role_error = None
            if not allow_roles:
                allow_roles = ("all",)
                role_error = "权限字段缺失，已降级默认 all"
            if role_error:
                errors.append(role_error)

            trigger_keywords = result.get('trigger_keywords', ())
            deny_conditions = result.get('deny_conditions', ())
            positive_examples = result.get('positive_examples', ())
            negative_examples = result.get('negative_examples', ())

            if parse_status != "failed":
                if not trigger_keywords:
                    parse_status = "partial"
                    errors.append("未找到触发关键词（§2.1 必须条件为空）")

            return SOPIntentSpec(
                sop_id=sop_id,
                sop_version=result.get('version', '') or "v0.0",
                sop_file_path=file_path,
                sop_file_name=file_name,
                display_name=display_name,
                sop_type=sop_type,
                allow_roles=allow_roles,
                trigger_keywords=trigger_keywords,
                deny_conditions=deny_conditions,
                positive_examples=positive_examples,
                negative_examples=negative_examples,
                parse_status=parse_status,
                parse_errors=tuple(errors),
            )
        except Exception as e:
            logger.warning(
                "mistune 解析 SOP %s 失败（%s），降级到旧正则解析器",
                file_name, e,
            )
            return self._parse_markdown_fallback(content, file_path, file_name)

    @staticmethod
    def _extract_display_name_from_filename(file_name: str) -> str:
        """从文件名提取显示名称（fallback）。"""
        name = file_name.replace(".md", "")
        parts = name.split("-", 3)
        if len(parts) >= 4:
            return parts[3].replace("_", " ")
        return name

    @staticmethod
    def _extract_sop_type_from_id_or_name(raw: str) -> str:
        """从 sop_id 或文件名提取业务类型。"""
        match = re.search(r"SOP-\d{3}-(\w+)", raw)
        if match:
            return match.group(1)
        parts = raw.replace(".md", "").split("-")
        if len(parts) >= 3:
            return parts[2]
        return "UNK"

    def _parse_markdown_fallback(
        self, content: str, file_path: str, file_name: str
    ) -> SOPIntentSpec:
        """旧正则解析器（冷备份）。纯文本正则 + 逐行状态机（零第三方依赖）。"""
        errors: list[str] = []
        parse_status = "ok"

        # ── 提取 sop_id ──
        sop_id = self._extract_sop_id(content, file_name)
        if not sop_id:
            parse_status = "failed"
            errors.append("无法提取 sop_id")

        # ── 提取 sop_type ──
        sop_type = self._extract_sop_type(content, sop_id or file_name)

        # ── 提取 display_name ──
        display_name = self._extract_display_name(content, file_name)

        # ── 提取 version ──
        version = self._extract_version(content)

        # ── 提取 allow_roles ──
        allow_roles, role_error = self._extract_allow_roles(content)
        if role_error:
            errors.append(role_error)

        # ── 提取 §2.1 触发条件 ──
        trigger_keywords, deny_conditions = self._extract_section_2_1(content)

        # ── 提取 §2.2 示例 ──
        positive_examples, negative_examples = self._extract_section_2_2(content)

        # ── 判定 parse_status ──
        if parse_status != "failed":
            if not trigger_keywords:
                parse_status = "partial"
                errors.append("未找到触发关键词（§2.1 必须条件为空）")

        return SOPIntentSpec(
            sop_id=sop_id or self._extract_sop_id_from_filename(file_name),
            sop_version=version,
            sop_file_path=file_path,
            sop_file_name=file_name,
            display_name=display_name,
            sop_type=sop_type,
            allow_roles=allow_roles,
            trigger_keywords=trigger_keywords,
            deny_conditions=deny_conditions,
            positive_examples=positive_examples,
            negative_examples=negative_examples,
            parse_status=parse_status,
            parse_errors=tuple(errors),
        )

    # ── 各字段解析辅助方法 ──

    def _extract_sop_id(self, content: str, file_name: str) -> str:
        """从第1章表格或文件名提取 sop_id。"""
        # 首选：第1章表格"业务流编号"行
        match = re.search(
            r"业务流编号\s*\|\s*(SOP-\d{3}-[A-Z]+)",
            content,
        )
        if match:
            return match.group(1)

        # 备选：从文件名提取
        return self._extract_sop_id_from_filename(file_name)

    @staticmethod
    def _extract_sop_id_from_filename(file_name: str) -> str:
        """从文件名提取 SOP ID。"""
        match = re.search(r"(SOP-\d{3}-[A-Z]+)", file_name)
        if match:
            return match.group(1)
        # 最后兜底：用文件名去掉 .md
        name = file_name.replace(".md", "")
        return name

    @staticmethod
    def _extract_sop_type(content: str, sop_id_or_name: str) -> str:
        """提取业务类型缩写。"""
        # 首选：从第1章表格"业务类型"行
        match = re.search(r"业务类型\s*\|\s*(\w+)", content)
        if match:
            return match.group(1)

        # 备选：从 sop_id 解析（SOP-XXX-TYPE → TYPE）
        match = re.search(r"SOP-\d{3}-(\w+)", sop_id_or_name)
        if match:
            return match.group(1)

        # 备选：从文件名字段提取
        parts = sop_id_or_name.replace(".md", "").split("-")
        if len(parts) >= 3:
            return parts[2]

        return "UNK"

    @staticmethod
    def _extract_display_name(content: str, file_name: str) -> str:
        """从一级标题提取显示名称。"""
        match = re.search(r"^#\s+(.+?)(?:\s*—\s*业务流服务手册)?\s*$", content, re.MULTILINE)
        if match:
            name = match.group(1).strip()
            # 去掉末尾的 "— 业务流服务手册" 如果有的话
            name = re.sub(r"\s*—\s*业务流服务手册\s*$", "", name)
            return name

        # 备选：从文件名提取
        name = file_name.replace(".md", "")
        # SOP-XXX-TYPE-name → name
        parts = name.split("-", 3)
        if len(parts) >= 4:
            return parts[3].replace("_", " ")
        return name

    @staticmethod
    def _extract_version(content: str) -> str:
        """从第1章表格提取版本号。"""
        match = re.search(r"版本\s*\|\s*(v?\d+\.\d+)", content)
        if match:
            return match.group(1)
        return "v0.0"

    @staticmethod
    def _extract_allow_roles(content: str) -> tuple[tuple, str | None]:
        """提取权限控制字段。返回 (roles_tuple, error_or_none)。"""
        # 首选：第1章表格"权限控制"行
        match = re.search(r"权限控制\s*\|\s*(.+?)(?:\n|\|)", content)
        if match:
            roles_text = match.group(1).strip()
            # 提取反引号中的角色名，去重并保持顺序
            roles = list(dict.fromkeys(re.findall(r"`(\w+)`", roles_text)))
            if roles:
                return tuple(roles), None

        # 备选：从头部"允许用户角色"行提取
        match = re.search(r"允许用户角色[：:]\s*(.+?)(?:\n|$)", content)
        if match:
            roles_text = match.group(1).strip()
            roles = list(dict.fromkeys(re.findall(r"`(\w+)`", roles_text)))
            if roles:
                return tuple(roles), None

        # 默认降级
        return ("all",), "权限字段缺失，已降级默认 all"

    @staticmethod
    def _extract_section_2_1(content: str) -> tuple[tuple, tuple]:
        """提取 §2.1 触发语义特征：必须条件 + 否定条件。"""
        trigger_keywords: list[str] = []
        deny_conditions: list[str] = []

        # 定位 §2.1 章节
        section_match = re.search(
            r"###?\s*2\.1\s+触发此业务流的语义特征\s*\n(.*?)(?=###?\s+2\.2|##\s+3\.)",
            content,
            re.DOTALL,
        )
        if not section_match:
            # 备选：尝试定位"必须条件"
            section_match = re.search(
                r"必须条件[：:]\s*\n(.*?)(?=否定条件|###?\s+2\.2|##\s+3\.)",
                content,
                re.DOTALL,
            )
            if section_match:
                section_text = section_match.group(1)
            else:
                return (), ()
        else:
            section_text = section_match.group(1)

        # 提取「必须条件」后的 - 列表项
        must_match = re.search(
            r"\*\*必须条件[^**]*\*\*\s*[：:]?\s*\n(.*?)(?:\*\*否定条件|$)",
            section_text,
            re.DOTALL,
        )
        if must_match:
            must_text = must_match.group(1)
            items = re.findall(r"-\s+(.+?)(?=\n-|\n\n|$)", must_text, re.DOTALL)
            for item in items:
                cleaned = item.strip()
                if cleaned:
                    # 中文顿号拆分：将长列表项按顿号拆成多个关键词
                    sub_items = re.split(r"[、；;]", cleaned)
                    for sub in sub_items:
                        sub = sub.strip()
                        if sub and len(sub) > 2:
                            trigger_keywords.append(sub)

        # 提取「否定条件」后的 - 列表项
        deny_match = re.search(
            r"\*\*否定条件[^**]*\*\*\s*[：:]?\s*\n(.*?)(?=###?\s+2\.2|##\s+3\.|$)",
            section_text,
            re.DOTALL,
        )
        if deny_match:
            deny_text = deny_match.group(1)
            items = re.findall(r"-\s+(.+?)(?=\n-|\n\n|$)", deny_text, re.DOTALL)
            for item in items:
                cleaned = item.strip()
                if cleaned:
                    deny_conditions.append(cleaned)

        return tuple(trigger_keywords), tuple(deny_conditions)

    @staticmethod
    def _extract_section_2_2(content: str) -> tuple[tuple, tuple]:
        """提取 §2.2 示例对话：正面示例 + 反面示例。"""
        positive_examples: list[str] = []
        negative_examples: list[str] = []

        # 定位 §2.2 章节
        section_match = re.search(
            r"###?\s*2\.2\s+示例对话\s*\n(.*?)(?=##\s+3\.|##\s+[4-7]\.)",
            content,
            re.DOTALL,
        )
        if not section_match:
            return (), ()

        section_text = section_match.group(1)

        # 提取「应触发此业务流」后的引用块内容
        # 兼容两种格式: **应触发此业务流**： 和 **应触发此业务流：**
        pos_match = re.search(
            r"\*\*应触发此业务流[：:]?\*\*[：:]?\s*\n(.*?)(?:\*\*不应触发|###?\s|$)",
            section_text,
            re.DOTALL,
        )
        if pos_match:
            pos_text = pos_match.group(1)
            # 提取 > 「...」引用块
            quotes = re.findall(r">\s*「([^」]+)」", pos_text)
            for q in quotes:
                cleaned = q.strip()
                if cleaned:
                    positive_examples.append(cleaned)

        # 提取「不应触发此业务流」后的引用块内容
        # 兼容两种格式
        neg_match = re.search(
            r"\*\*不应触发此业务流[：:]?\*\*[：:]?\s*\n(.*?)(?=##\s+3\.|$)",
            section_text,
            re.DOTALL,
        )
        if neg_match:
            neg_text = neg_match.group(1)
            # 提取 > 「...」引用块和 → 后的说明
            quotes = re.findall(r">\s*「([^」]+)」", neg_text)
            for q in quotes:
                cleaned = q.strip()
                if cleaned:
                    negative_examples.append(cleaned)
            # 也提取 → 后面的说明
            arrows = re.findall(r"→\s*(.+?)(?=\n|$)", neg_text)
            for a in arrows:
                cleaned = a.strip()
                if cleaned and cleaned not in negative_examples:
                    negative_examples.append(cleaned)

        return tuple(positive_examples), tuple(negative_examples)
