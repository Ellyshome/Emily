"""KnowledgeInjector —— 增量灌注引擎（蓝图 §5.3）。

WorkItem-Agent 是全局单例，维护"当前已灌注的知识集合"。新 WorkItem 到达时，
计算该 WorkItem 所需资源（SOP / 工具 / DB 表）与当前上下文的差集，仅加载缺失部分，
最小化上下文污染。

    被注入知识 = WI 需求 - 现有上下文

本期为占位骨架：以集合 diff 表达"增量"语义，真实的 SOP 全文加载 / schema 加载
/ 工具参数详情加载（蓝图 §4.3.1 懒加载触发表）属 Phase B/C。
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


class KnowledgeInjector:
    """增量灌注引擎 —— 维护全局已灌注知识集合，按需加载缺失项。"""

    def __init__(self):
        # WorkItem-Agent 当前上下文中已灌注的知识集合
        self._loaded_sops: set[str] = set()
        self._loaded_tools: set[str] = set()
        self._loaded_tables: set[str] = set()

    def analyze(self, work_item) -> InjectionResult:
        """分析 WorkItem 所需资源，与现有上下文求差集，仅返回缺失部分。

        Args:
            work_item: 待执行的 WorkItem（读取其 sop_id / required 资源）。

        Returns:
            InjectionResult: 本次需新增灌注的 SOP / 工具 / 表。
        """
        required_sops = {work_item.sop_id} if work_item.sop_id else set()
        # 占位：真实实现从 SOP 定义解析出所需工具/表；此处用 WorkItem 已声明的字段
        required_tools = set(getattr(work_item, "required_tools", set()) or set())
        required_tables = set(getattr(work_item, "required_tables", set()) or set())

        result = InjectionResult(
            new_sops=required_sops - self._loaded_sops,
            new_tools=required_tools - self._loaded_tools,
            new_tables=required_tables - self._loaded_tables,
        )

        # 灌注：合并入全局上下文 + 记录到 WorkItem
        self._loaded_sops |= result.new_sops
        self._loaded_tools |= result.new_tools
        self._loaded_tables |= result.new_tables
        work_item.injected_sops |= result.new_sops
        work_item.injected_tools |= result.new_tools
        work_item.injected_tables |= result.new_tables

        if result.is_empty:
            logger.debug("Injector: WI %s — all knowledge already loaded", work_item.id)
        else:
            logger.info(
                "Injector: WI %s — +%d sop, +%d tool, +%d table",
                work_item.id,
                len(result.new_sops), len(result.new_tools), len(result.new_tables),
            )
        return result

    def release(self, work_item) -> None:
        """WorkItem 完成后回收其独占的知识（保留高频复用基础上下文）。

        本期为占位：真实回收策略（引用计数 / LRU）属 Phase C。
        """
        logger.debug("Injector: WI %s released (context retained)", work_item.id)

    def loaded_summary(self) -> dict:
        """当前已灌注知识摘要（调试用）。"""
        return {
            "sops": sorted(self._loaded_sops),
            "tools": sorted(self._loaded_tools),
            "tables": sorted(self._loaded_tables),
        }
