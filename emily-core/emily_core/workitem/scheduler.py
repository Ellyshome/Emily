"""SessionScheduler —— WorkItem 调度器（蓝图 §5.4）。

每个 Session 一个 Scheduler，负责该 Session 下所有 WorkItem 的调度：
  · 管理 WorkItem 创建/排队/优先级
  · 将 WorkItem 分配到公共 Pipeline BUS 执行
  · 监测 WorkItem 执行状态
  · 处理 WorkItem 挂起/恢复/终止

架构关系（蓝图 §5.1）：Session-Scheduler → Pipeline BUS（公共总线）→ WorkItem。
Pipeline BUS 是系统级公共总线（全局单例），不属于单个 WorkItem 私有；
Scheduler 是每 Session 一个，只管本 Session 的 WorkItem 排队与分配。
"""

from __future__ import annotations

import logging

from .workitem import WorkItem
from .workitem_state import WorkItemState
from .pipeline.bus import PipelineBUS
from .pipeline.context import BusContext

logger = logging.getLogger("emily.scheduler")


class SessionScheduler:
    """Session 级 WorkItem 调度器。

    权限架构 v1.2：
    - 保存对 SessionContext 的引用，在创建 BusContext 时注入
    - WorkItemAgent 通过 BusContext 只读方法获取权限信息
    - 不直接将权限数据传递给 WorkItemAgent，避免上下文污染
    """

    def __init__(self, session_id: str, bus: PipelineBUS, session_context=None):
        self.session_id = session_id
        self._bus = bus                       # 公共 Pipeline BUS（全局共享）
        self._session_context = session_context  # SessionContext（只读访问）
        self._queue: list[WorkItem] = []      # 待执行队列（按 priority 排序）
        self._active: dict[str, WorkItem] = {}  # 执行中
        self._done: list[WorkItem] = []       # 已完成

    def enqueue(self, work_item: WorkItem) -> None:
        """将 WorkItem 加入队列（按 priority 排序，0 最高）。"""
        work_item.session_id = self.session_id
        self._queue.append(work_item)
        self._queue.sort(key=lambda wi: wi.priority)
        logger.debug(
            "Scheduler[%s] enqueued %s (priority=%d, queue=%d)",
            self.session_id, work_item.id, work_item.priority, len(self._queue),
        )

    async def run_next(self) -> WorkItem | None:
        """取出下一个 WorkItem，分配到公共 Pipeline BUS 执行。

        Returns:
            WorkItem: 执行完毕的 WorkItem；队列空时返回 None。
        """
        if not self._queue:
            return None
        wi = self._queue.pop(0)
        return await self._run_one(wi)

    async def run_all(self) -> list[WorkItem]:
        """顺序执行队列中所有 WorkItem。

        Returns:
            list[WorkItem]: 全部执行完毕的 WorkItem（按执行顺序）。
        """
        results: list[WorkItem] = []
        while self._queue:
            wi = self._queue.pop(0)
            results.append(await self._run_one(wi))
        return results

    async def _run_one(self, wi: WorkItem, message=None) -> WorkItem:
        """在公共 BUS 上执行单个 WorkItem，驱动其状态机。"""
        self._active[wi.id] = wi
        try:
            # CREATED → PLANNING（KnowledgeInjector 增量灌注在 BUS node1 内触发）
            wi.transition_to(WorkItemState.PLANNING)

            # 权限架构 v1.2：将 SessionContext 注入 BusContext（只读）
            # WorkItemAgent 通过 context.get_permissions() 等方法访问权限信息
            context = BusContext(
                work_item=wi,
                message=message,
                user_id=wi.user_id,
                is_admin=wi.is_admin,
                _session_context=self._session_context,  # 私有字段，仅初始化时设置
            )

            # PLANNING → EXECUTING（节点内部经过 node1/node2 规划 + node3 执行）
            wi.transition_to(WorkItemState.EXECUTING)
            await self._bus.run(context)

            if context.should_abort:
                wi.transition_to(WorkItemState.FAILED)
                wi.error_message = wi.error_message or context.abort_reason
                logger.warning("Scheduler[%s] WI %s FAILED: %s",
                               self.session_id, wi.id, wi.error_message)
            else:
                wi.transition_to(WorkItemState.DONE)
                logger.info("Scheduler[%s] WI %s DONE", self.session_id, wi.id)
        except Exception as e:
            logger.error("Scheduler[%s] WI %s crashed: %s",
                         self.session_id, wi.id, e, exc_info=True)
            if not wi.is_terminal:
                try:
                    wi.transition_to(WorkItemState.FAILED)
                except ValueError:
                    wi.state = WorkItemState.FAILED
            wi.error_message = str(e)
        finally:
            self._active.pop(wi.id, None)
            self._done.append(wi)
        return wi

    @property
    def has_pending(self) -> bool:
        """是否还有排队中的 WorkItem。"""
        return bool(self._queue)

    @property
    def active_count(self) -> int:
        """执行中的 WorkItem 数量。"""
        return len(self._active)
