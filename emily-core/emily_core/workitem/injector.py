"""KnowledgeInjector —— 增量灌注引擎（蓝图 §5.3）。

WorkItem-Agent 是全局单例，维护"当前已灌注的知识集合"。新 WorkItem 到达时，
计算该 WorkItem 所需资源（SOP / 工具 / DB 表）与当前上下文的差集，仅加载缺失部分，
最小化上下文污染。

    被注入知识 = WI 需求 - 现有上下文

Phase B 实现（蓝图 §12.2）：
  · 加载 SOP 全文（通过 SOPLoader）
  · 加载工具参数 Schema（通过 ToolRegistry）
  · 加载数据库表结构摘要（预定义映射 → Phase C 升级为 ORM inspect）
  · Token 预算控制（总上下文 ≤ 32K tokens 估算）
  · WorkItem 完成时基本回收
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("emily.injector")


@dataclass
class InjectionResult:
    """单次增量灌注的结果（差集）。"""

    new_sops: set[str] = field(default_factory=set)
    new_tools: set[str] = field(default_factory=set)
    new_tables: set[str] = field(default_factory=set)

    @property
    def is_empty(self) -> bool:
        """本次是否无新增灌注（全部命中现有上下文）。"""
        return not (self.new_sops or self.new_tools or self.new_tables)


# Phase B: 数据库表结构摘要（Phase C 升级为 ORM inspect 反射）
_KNOWN_TABLES: dict[str, str] = {
    "events": "events(id, event_no, title, event_type, category, event_date, location, description, payload, project_id, status, created_at)",
    "tasks": "tasks(id, task_no, title, description, status, priority, assignee, due_date, project_id, created_at)",
    "meetings": "meetings(id, meeting_no, title, meeting_type, meeting_date, location, attendees, conclusion, action_items, project_id)",
    "files": "files(id, file_no, file_name, file_type, file_path, file_size, version, confidentiality, project_id)",
    "projects": "projects(id, name, code, lifecycle_stage, city, address, is_deleted)",
    "users": "users(id, name, role, grouping, position, company)",
    "messages": "messages(id, conversation_id, sender_id, direction, content, msg_type, created_at)",
}


class KnowledgeInjector:
    """Phase B: 增量灌注引擎 —— 维护全局已灌注知识集合，按需加载缺失项。

    新增能力：
      · 加载 SOP 全文（通过 SOPLoader）
      · 加载工具参数 Schema（通过 ToolRegistry）
      · Token 预算控制（总上下文 ≤ 32K tokens 估算）
    """

    def __init__(
        self,
        sop_intent_registry=None,   # SOPIntentRegistry: 获取 SOP spec
        sop_loader=None,            # SOPLoader: 加载 SOP 全文
        tool_registry=None,         # ToolRegistry: 获取工具定义
        max_sop_text_chars: int = 8000,
        max_context_tokens_est: int = 32000,
    ):
        # WorkItem-Agent 当前上下文中已灌注的知识集合
        self._loaded_sops: set[str] = set()
        self._loaded_tools: set[str] = set()
        self._loaded_tables: set[str] = set()

        # 实际内容存储
        self._sop_texts: dict[str, str] = {}       # sop_id → SOP 全文
        self._tool_defs: dict[str, dict] = {}       # tool_name → 工具定义
        self._table_schemas: dict[str, str] = {}    # table_name → schema 摘要

        # 依赖
        self._sop_intent_registry = sop_intent_registry
        self._sop_loader = sop_loader
        self._tool_registry = tool_registry

        # 限制
        self._max_sop_text_chars = max_sop_text_chars
        self._max_context_tokens_est = max_context_tokens_est

    def analyze(self, work_item) -> InjectionResult:
        """分析 WorkItem 所需资源，与现有上下文求差集，加载缺失部分。

        Args:
            work_item: 待执行的 WorkItem（读取其 sop_id / required_tools / required_tables）。

        Returns:
            InjectionResult: 本次需新增灌注的 SOP / 工具 / 表。
        """
        required_sops = {work_item.sop_id} if work_item.sop_id else set()
        required_tools = set(getattr(work_item, "required_tools", set()) or set())
        required_tables = set(getattr(work_item, "required_tables", set()) or set())

        new_sops = required_sops - self._loaded_sops
        new_tools = required_tools - self._loaded_tools
        new_tables = required_tables - self._loaded_tables

        # 1. 加载 SOP 全文
        for sop_id in new_sops:
            text = self._load_sop_text(sop_id)
            if text:
                self._sop_texts[sop_id] = text
                self._loaded_sops.add(sop_id)
            else:
                # 加载失败也标记为已知，避免重复尝试
                self._loaded_sops.add(sop_id)

        # 2. 加载工具定义
        for tool_name in new_tools:
            tool_def = self._load_tool_def(tool_name)
            if tool_def:
                self._tool_defs[tool_name] = tool_def
                self._loaded_tools.add(tool_name)

        # 3. 加载表 schema
        for table_name in new_tables:
            schema = self._load_table_schema(table_name)
            if schema:
                self._table_schemas[table_name] = schema
                self._loaded_tables.add(table_name)

        # 写入 WorkItem 注入记录
        work_item.injected_sops |= new_sops
        work_item.injected_tools |= new_tools
        work_item.injected_tables |= new_tables

        # Token 预算检查
        self._enforce_token_budget()

        if not new_sops and not new_tools and not new_tables:
            logger.debug("Injector: WI %s — all knowledge already loaded", work_item.id)
        else:
            logger.info(
                "Injector: WI %s — +%d sop, +%d tool, +%d table (loaded: %d/%d/%d)",
                work_item.id,
                len(new_sops), len(new_tools), len(new_tables),
                len(self._loaded_sops), len(self._loaded_tools), len(self._loaded_tables),
            )

        return InjectionResult(
            new_sops=new_sops,
            new_tools=new_tools,
            new_tables=new_tables,
        )

    def release(self, work_item) -> None:
        """WorkItem 完成后回收其独占的知识（保留高频复用基础上下文）。

        Phase B: 仅从 loaded 集合中移除该 WI 注入的项。
        完整引用计数 / LRU 回收策略属 Phase C。
        """
        removed = set()
        for sop_id in work_item.injected_sops:
            if sop_id in self._loaded_sops:
                self._loaded_sops.discard(sop_id)
                self._sop_texts.pop(sop_id, None)
                removed.add(sop_id)
        for tool_name in work_item.injected_tools:
            if tool_name in self._loaded_tools:
                self._loaded_tools.discard(tool_name)
                self._tool_defs.pop(tool_name, None)
                removed.add(tool_name)
        for table_name in work_item.injected_tables:
            if table_name in self._loaded_tables:
                self._loaded_tables.discard(table_name)
                self._table_schemas.pop(table_name, None)
                removed.add(table_name)
        if removed:
            logger.debug("Injector: WI %s released %d items", work_item.id, len(removed))

    # ── Phase B: 上下文查询 ──

    def get_context_text(self) -> str:
        """构建注入的上下文字符串，供 WorkItemAgent 在构造 LLM prompt 时使用。"""
        parts = []
        if self._sop_texts:
            for sop_id, text in self._sop_texts.items():
                parts.append(f"--- SOP: {sop_id} ---\n{text}")
        if self._tool_defs:
            parts.append("--- 可用工具 ---")
            for name, defn in self._tool_defs.items():
                parts.append(f"- {name}: {defn.get('description', '')}")
        if self._table_schemas:
            parts.append("--- 可用数据表 ---")
            for name, schema in self._table_schemas.items():
                parts.append(f"- {name}: {schema}")
        return "\n\n".join(parts)

    def loaded_summary(self) -> dict:
        """当前已灌注知识摘要（调试用）。"""
        return {
            "sops": sorted(self._loaded_sops),
            "tools": sorted(self._loaded_tools),
            "tables": sorted(self._loaded_tables),
            "sop_texts_count": len(self._sop_texts),
            "tool_defs_count": len(self._tool_defs),
            "table_schemas_count": len(self._table_schemas),
        }

    # ── 内部 ──

    def _load_sop_text(self, sop_id: str) -> str | None:
        """从 SOPLoader 加载 SOP 完整文本。"""
        if self._sop_loader is None:
            return None
        try:
            text = self._sop_loader.load_full_text(sop_id)
            if text and len(text) > self._max_sop_text_chars:
                text = text[:self._max_sop_text_chars] + "\n\n... (内容已截断)"
            return text
        except Exception as e:
            logger.warning("Failed to load SOP text for %s: %s", sop_id, e)
            return None

    def _load_tool_def(self, tool_name: str) -> dict | None:
        """从 ToolRegistry 获取工具定义。"""
        if self._tool_registry is None:
            return None
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return None
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }

    @staticmethod
    def _load_table_schema(table_name: str) -> str | None:
        """获取数据库表 schema 摘要。"""
        # Phase B: 预定义映射；Phase C 升级为 SQLAlchemy inspect 反射
        return _KNOWN_TABLES.get(table_name)

    def _enforce_token_budget(self) -> None:
        """Token 预算控制：总字符数超限时 LRU 回收最旧的 SOP。"""
        total_chars = (
            sum(len(v) for v in self._sop_texts.values()) +
            sum(len(str(v)) for v in self._tool_defs.values()) +
            sum(len(v) for v in self._table_schemas.values())
        )
        est_tokens = total_chars // 3  # 粗略估算：中英文混合 ~3 char/token

        if est_tokens > self._max_context_tokens_est:
            logger.warning(
                "KnowledgeInjector: estimated %d tokens exceeds budget %d, trimming...",
                est_tokens, self._max_context_tokens_est,
            )
            # LRU 回收：清理最早加载的 SOP
            while est_tokens > self._max_context_tokens_est and self._sop_texts:
                oldest = next(iter(self._sop_texts))
                del self._sop_texts[oldest]
                self._loaded_sops.discard(oldest)
                total_chars = (
                    sum(len(v) for v in self._sop_texts.values()) +
                    sum(len(str(v)) for v in self._tool_defs.values()) +
                    sum(len(v) for v in self._table_schemas.values())
                )
                est_tokens = total_chars // 3
