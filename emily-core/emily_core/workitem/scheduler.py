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

    def __init__(self, session_id: str, bus: PipelineBUS = None, session_context=None, core=None):
        self.session_id = session_id
        self._session_context = session_context
        self._core = core                     # EmilyCore 实例（取 _workitem_graph）
        self._queue: list[WorkItem] = []
        self._active: dict[str, WorkItem] = {}
        self._done: list[WorkItem] = []

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

    async def run_all_with_message(self, message, db_message_id: str = "", actor_snapshot=None) -> list[WorkItem]:
        """顺序执行队列中所有 WorkItem，携带原始入站消息（含附件等信息）。

        文件上传链路需要 message.attachments 在 BusContext 中可用，
        此方法将 message 传递到每个 WorkItem 的 BusContext 中。

        Args:
            message: 原始 StandardMessage（含 attachments）
            db_message_id: 入站消息持久化后的数据库 ID（M2 修复：供 trace 关联）
            actor_snapshot: 当前操作者权限快照（群聊多用户权限越界修复）

        Returns:
            list[WorkItem]: 全部执行完毕的 WorkItem。
        """
        self._current_actor = actor_snapshot
        results: list[WorkItem] = []
        while self._queue:
            wi = self._queue.pop(0)
            results.append(await self._run_one(wi, message=message, db_message_id=db_message_id))
        return results

    async def run_dag(
        self,
        plans: list,
        message=None,
        db_message_id: str = "",
        actor_snapshot=None,
        *,
        max_depth: int = 3,
        on_wi_done=None,
    ) -> list[WorkItem]:
        """按依赖拓扑序执行 WI 计划（层内并行，层间顺序）。

        Args:
            plans: list[WorkItemPlan]（含 wi / depends_on / dynamic / depth）
            message: 原始入站消息（透传给每个 WI 的 BusContext）
            db_message_id: 入站消息持久化 ID
            actor_snapshot: 当前操作者权限快照
            max_depth: DAG 最大深度（超出则丢弃追加计划）
            on_wi_done: 动态追加回调 async (wi, done, *, depth) -> list[WorkItemPlan]

        Returns:
            list[WorkItem]：全部 WI（含 DONE / FAILED / SKIPPED），按完成顺序
        """
        import asyncio as _asyncio

        self._current_actor = actor_snapshot
        results: list[WorkItem] = []
        done_ids: set[str] = set()
        failed_ids: set[str] = set()
        pending: list = list(plans)
        halted = False

        while pending and not halted:
            # 1) 当前可执行层（所有前置已完成）
            layer_idx = [
                i for i, p in enumerate(pending)
                if all(d in done_ids for d in (p.depends_on or []))
            ]
            if not layer_idx:
                # 依赖环 / 依赖永不满足 → 剩余全部 SKIPPED（防死循环）
                logger.warning("Scheduler[%s] run_dag: unresolvable deps, skip %d WI",
                               self.session_id, len(pending))
                for p in pending:
                    self._mark_skipped(p.wi)
                    results.append(p.wi)
                break

            layer = [pending[i] for i in layer_idx]
            keep_idx = set(layer_idx)
            pending = [p for i, p in enumerate(pending) if i not in keep_idx]

            # 2) 层内并行（create_task 拷贝上下文，隔离 set_bus_context / 日志 ContextVar）
            tasks = [
                _asyncio.create_task(
                    self._run_one(p.wi, message=message, db_message_id=db_message_id)
                )
                for p in layer
            ]
            layer_outcome = await _asyncio.gather(*tasks, return_exceptions=True)

            # 3) 汇总本层结果
            layer_wis: list[WorkItem] = []
            for p, outcome in zip(layer, layer_outcome):
                if isinstance(outcome, BaseException):
                    logger.error("Scheduler[%s] run_dag WI %s raised: %s",
                                 self.session_id, p.wi.id, outcome)
                    self._mark_failed(p.wi, str(outcome))
                    wi = p.wi
                else:
                    wi = outcome
                results.append(wi)
                layer_wis.append(wi)
                if wi.state == WorkItemState.DONE:
                    done_ids.add(wi.id)
                else:
                    failed_ids.add(wi.id)

            # 4) 后继依赖失败 → SKIPPED（不执行，不抛异常）
            remaining: list = []
            for p in pending:
                if any(d in failed_ids for d in (p.depends_on or [])):
                    self._mark_skipped(p.wi)
                    results.append(p.wi)
                    failed_ids.add(p.wi.id)
                else:
                    remaining.append(p)
            pending = remaining

            # 5) 多轮续接挂起 → 立即停止后续层（由 SessionAgent 处理续接）
            if any(w.state == WorkItemState.WAITING_FOR_INPUT for w in layer_wis):
                logger.info("Scheduler[%s] run_dag halted: WI waiting for input", self.session_id)
                halted = True
                break

            # 6) 动态追加（跨域检索编排）
            if on_wi_done is None:
                continue
            for wi in layer_wis:
                if wi.state != WorkItemState.DONE:
                    continue
                try:
                    added = await on_wi_done(wi, results, depth=getattr(wi, "depth", 0))
                except Exception as e:
                    logger.warning("Scheduler[%s] on_wi_done failed for %s: %s",
                                   self.session_id, wi.id, e)
                    continue
                for plan in (added or []):
                    if getattr(plan, "depth", 0) > max_depth:
                        logger.info("Scheduler[%s] drop dynamic plan depth=%d > max=%d",
                                    self.session_id, plan.depth, max_depth)
                        continue
                    pending.append(plan)

        return results

    @staticmethod
    def _mark_skipped(wi: WorkItem) -> None:
        """标记 WI 为 SKIPPED（终态）。"""
        try:
            wi.transition_to(WorkItemState.SKIPPED)
        except ValueError:
            wi.state = WorkItemState.SKIPPED
        wi.error_message = wi.error_message or "前置 WI 未成功，已跳过"

    @staticmethod
    def _mark_failed(wi: WorkItem, reason: str) -> None:
        """标记 WI 为 FAILED（终态）。"""
        if not wi.is_terminal:
            try:
                wi.transition_to(WorkItemState.FAILED)
            except ValueError:
                wi.state = WorkItemState.FAILED
        wi.error_message = wi.error_message or reason

    async def _run_one(self, wi: WorkItem, message=None, db_message_id: str = "") -> WorkItem:
        """在统一生命周期图上执行单个 WorkItem。

        职责瘦身：建 BusContext + 调图 + interrupt 检测/resume + 持久化。
        不做手写状态转换（由图驱动）。
        """
        self._active[wi.id] = wi
        try:
            # 多轮续接：WAITING_FOR_INPUT 直接回 EXECUTING（图用 Command(resume=...) 恢复）
            is_resuming = (wi.state == WorkItemState.WAITING_FOR_INPUT)
            if is_resuming:
                wi.transition_to(WorkItemState.EXECUTING)
            else:
                wi.transition_to(WorkItemState.PLANNING)
                wi.transition_to(WorkItemState.EXECUTING)

            context = BusContext(
                work_item=wi,
                message=message,
                user_id=wi.user_id,
                is_admin=wi.is_admin,
                db_message_id=db_message_id,
                _session_context=self._session_context,
                _actor_snapshot=getattr(self, "_current_actor", None),
            )
            wi.pipeline_run_id = context.pipeline_run_id

            archive_md_path = getattr(self, "archive_md_path", "")
            if archive_md_path:
                context.baggage["archive_md_path"] = archive_md_path

            if context.message is None:
                stored = getattr(wi, '_source_message', None)
                if stored is not None:
                    context.message = stored

            # 调图（含 interrupt 检测/resume）
            await self._run_graph(context, is_resuming=is_resuming,
                                  resume_input=getattr(wi, "additional_input", "") or "")

            # 检查是否 interrupt 挂起（WAITING_FOR_INPUT）
            if wi.state == WorkItemState.WAITING_FOR_INPUT:
                logger.info("Scheduler[%s] WI %s WAITING_FOR_INPUT: %s",
                            self.session_id, wi.id, wi.question[:60])
                return wi

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

    async def _run_graph(self, context, is_resuming: bool = False, resume_input: str = "") -> None:
        """通过统一生命周期图执行 WorkItem（含 interrupt/resume）。"""
        from langgraph.types import Command
        from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
        from emily_core.infrastructure.logging.business_event_logger import BusinessEventLogger
        from emily_core.workitem.langgraph_engine.state import (
            set_bus_context, clear_bus_context, make_initial_state,
        )

        core = getattr(self, "_core", None)
        graph = getattr(core, "_workitem_graph", None) if core else None
        if graph is None:
            raise RuntimeError("LangGraph engine not built — check EmilyCore._build_pipeline_bus()")

        set_bus_context(context)

        LLMInteractionLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
            user_id=context.user_id,
        )
        BusinessEventLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
        )

        core = getattr(self, "_core", None)
        outbound_bus = getattr(core, "outbound_bus", None) if core else None
        if outbound_bus is not None and context.message is not None:
            _cid = context.message.conversation_id or ""

            def _send_progress(text: str, _bus=outbound_bus, _cid=_cid) -> None:
                _bus.publish("progress", {"content": text, "conversation_id": _cid})

            context.baggage.setdefault("progress_sender", _send_progress)

        try:
            _cfg = getattr(core, "config", None) if core else None
            max_iter = getattr(_cfg, "agent_loop_max_iterations", 12) if _cfg else 12
            config = {"configurable": {"thread_id": context.pipeline_run_id}}

            if is_resuming and resume_input:
                # 续接：用 Command(resume=...) 把用户回复注入 interrupt 断点
                result = await graph.ainvoke(
                    Command(resume=resume_input), config=config,
                )
            else:
                state = make_initial_state(
                    pipeline_run_id=context.pipeline_run_id,
                    max_iterations=max_iter,
                )
                result = await graph.ainvoke(state, config=config)

            # 检测 interrupt 挂起
            _check_interrupt(self, context, config, graph)
        finally:
            clear_bus_context()
            LLMInteractionLogger.clear_context()
            BusinessEventLogger.clear_context()

    @property
    def has_pending(self) -> bool:
        """是否还有排队中的 WorkItem。"""
        return bool(self._queue)

    @property
    def active_count(self) -> int:
        """执行中的 WorkItem 数量。"""
        return len(self._active)


def _check_interrupt(scheduler, context, config, graph) -> None:
    """检测图是否因 interrupt 挂起（WAITING_FOR_INPUT），若是则标记 WorkItem 状态。"""
    try:
        # langgraph 1.x：get_state 读 checkpoint，interrupt 时 __interrupt__ 非空
        snap = graph.get_state(config)
        tasks = getattr(snap, "tasks", {}) or {}
        next_nodes = getattr(snap, "next", ()) or ()
        # interrupt 挂起时 next 含 tool_node（ask_user 在 tool_node 内 interrupt）
        if next_nodes or (hasattr(snap, "values") and snap.values.get("wi_state") == "executing"
                          and snap.values.get("waiting_question")):
            wi = context.work_item
            wi.state = WorkItemState.WAITING_FOR_INPUT
            wi.question = snap.values.get("waiting_question", "") or _extract_interrupt_question(snap)
            _logger = logging.getLogger("emily.scheduler")
            _logger.info("Scheduler interrupt detected: WI %s question=%s",
                        wi.id, wi.question[:60])
    except Exception as e:
        logging.getLogger("emily.scheduler").debug("interrupt check skipped: %s", e)


def _extract_interrupt_question(snap) -> str:
    """从 interrupt 快照提取问题文本。"""
    try:
        tasks = getattr(snap, "tasks", {}) or {}
        for t in tasks.values():
            interrupts = getattr(t, "interrupts", []) or []
            for intr in interrupts:
                val = getattr(intr, "value", None)
                if isinstance(val, str):
                    return val
    except Exception:
        pass
    return "请补充信息"
