"""SchedulerEngine —— 调度引擎核心。参照模式：plan_task_scheduler.py + bus.py。

职责：
  - tick 循环 + Advisory Lock 分布式调度
  - 扫描 ACTIVE 作业，匹配到期的执行
  - 触发 before/after/on_error hooks
  - 通过 JobHandlerRegistry 调用对应 handler
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass

from .handler_registry import JobHandlerRegistry, JobResult
from .hook_registry import SchedulerHookRegistry, HookDecision

logger = logging.getLogger("emily.scheduler.engine")


@dataclass
class SchedulerContext:
    """调度执行上下文（类似 BusContext）。"""
    job_id: str = ""
    job_no: str = ""
    action_type: str = ""
    action_params: dict = None
    execution_id: str = ""
    execution_no: str = ""
    period_key: str = ""
    result: JobResult = None

    def __post_init__(self):
        if self.action_params is None:
            self.action_params = {}


class SchedulerEngine:
    """调度引擎 —— tick 循环 + Advisory Lock + Hook 触发。

    参照模式：
      - PlanTaskScheduler._loop + _tick（Advisory Lock + tick）
      - PipelineBUS.run（before/after hooks）
    """

    def __init__(
        self,
        service,
        handler_registry: JobHandlerRegistry,
        hook_registry: SchedulerHookRegistry,
        config,
        outbound_bus=None,
    ):
        self._service = service
        self._handlers = handler_registry
        self._hooks = hook_registry
        self._config = config
        self._outbound = outbound_bus
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        """启动调度循环。"""
        if not getattr(self._config, 'scheduler_enabled', True):
            logger.info("SchedulerEngine: disabled by config")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        tick = getattr(self._config, 'scheduler_tick_seconds', 60)
        logger.info("SchedulerEngine: started (tick=%ds)", tick)

    async def stop(self):
        """停止调度循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SchedulerEngine: stopped")

    async def _loop(self):
        """主循环。"""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduler tick failed: %s", e, exc_info=True)
            tick_seconds = getattr(self._config, 'scheduler_tick_seconds', 60)
            await asyncio.sleep(tick_seconds)

    async def _tick(self):
        """单次调度循环（Advisory Lock 保护）。"""
        from ..infrastructure.database.session import get_session_raw
        from sqlalchemy import text

        lock_key = "scheduler_engine:global_tick"
        lock_session = get_session_raw()
        try:
            acquired = lock_session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": lock_key},
            ).scalar()
            if not acquired:
                return

            try:
                # 扫描所有 ACTIVE 作业
                active_jobs = await self._service.list_active_jobs()
                now = datetime.now(timezone.utc)

                for job in active_jobs:
                    if self._is_due(job, now):
                        await self._execute_job(job)

            finally:
                try:
                    lock_session.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:key))"),
                        {"key": lock_key},
                    )
                except Exception:
                    pass
        finally:
            lock_session.close()

    def _is_due(self, job, now: datetime) -> bool:
        """判断作业是否到期执行。"""
        next_exec = getattr(job, 'next_execution_at', '')
        if not next_exec:
            return True  # 无下次执行时间，立即执行
        try:
            next_dt = datetime.fromisoformat(next_exec.replace("Z", "+00:00"))
            return now >= next_dt
        except (ValueError, AttributeError):
            return True

    async def _execute_job(self, job):
        """执行单个作业：创建执行记录 → hooks → handler → hooks。"""
        period_key = self._calc_period_key(job)
        ctx = SchedulerContext(
            job_id=job.id,
            job_no=job.job_no,
            action_type=job.action_type,
            action_params=json.loads(getattr(job, 'action_params', '{}') or '{}'),
            period_key=period_key,
        )

        # 创建执行记录
        exec_result = await self._service.create_execution(job.id, period_key)
        if exec_result.success:
            ctx.execution_no = exec_result.execution_no

        # ① before:execute hooks
        if not await self._fire_before_hooks(ctx):
            logger.info("Job %s blocked by before:execute hook", job.job_no)
            return

        # ② 执行 handler
        handler = self._handlers.get(job.action_type)
        if handler is None:
            logger.error("No handler for action_type=%s", job.action_type)
            return

        try:
            result = await handler.execute(ctx.action_params)
            ctx.result = result
        except Exception as e:
            logger.error("Handler %s failed: %s", job.action_type, e, exc_info=True)
            ctx.result = JobResult(success=False, summary=str(e))
            # ④ on_error:execute hooks
            await self._fire_error_hooks(ctx, e)

        # ③ after:execute hooks
        await self._fire_after_hooks(ctx)

        # ── 进化日志：调度器作业日志 ──
        try:
            from ..infrastructure.logging.scheduler_logger import SchedulerJobLogger
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            SchedulerJobLogger.log_sync(
                job_id=job.id,
                action_type=job.action_type,
                params_json=getattr(job, 'action_params', '{}') or '{}',
                success=ctx.result.success if ctx.result else False,
                summary=(ctx.result.summary or "")[:500] if ctx.result else "",
                elapsed_ms=0,
                error_detail="" if (ctx.result and ctx.result.success) else (ctx.result.summary if ctx.result else ""),
                started_at="",
                completed_at=now,
            )
        except Exception as e:
            logger.warning("Scheduler job log write failed: %s", e)

    async def trigger_job(self, job_id: str):
        """手动触发作业（不检查调度时间）。"""
        job = await self._service.get_job(job_id)
        if job is None:
            raise ValueError(f"作业 {job_id} 不存在")
        await self._execute_job(job)

    # ── Hook 触发（对齐 PipelineBUS） ──

    async def _fire_before_hooks(self, ctx: SchedulerContext) -> bool:
        """触发 before:execute hooks。返回 False 表示被阻断。"""
        for hook in self._hooks.get_enabled("before:execute"):
            try:
                result = await hook.execute(ctx)
                if result.is_blocked:
                    logger.info("Blocked by hook '%s': %s", hook.name, result.message)
                    return False
            except Exception as e:
                logger.error("Before hook '%s' failed: %s", hook.name, e)
                return False  # before hook 异常视为阻断
        return True

    async def _fire_after_hooks(self, ctx: SchedulerContext) -> None:
        """触发 after:execute hooks。fire-and-forget。"""
        for hook in self._hooks.get_enabled("after:execute"):
            try:
                await hook.execute(ctx)
            except Exception as e:
                logger.warning("After hook '%s' failed: %s", hook.name, e)

    async def _fire_error_hooks(self, ctx: SchedulerContext, error: Exception) -> None:
        """触发 on_error:execute hooks。"""
        for hook in self._hooks.get_enabled("on_error:execute"):
            try:
                await hook.execute(ctx)
            except Exception as e:
                logger.error("Error hook '%s' also failed: %s", hook.name, e)

    @staticmethod
    def _calc_period_key(job) -> str:
        """根据作业类型计算当前周期标识。"""
        now = datetime.now(timezone.utc)
        job_type = getattr(job, 'job_type', 'ONCE')
        cron = getattr(job, 'cron_expression', '').lower()
        if job_type == "WEEKLY" or (job_type == "CRON" and "week" in cron):
            return now.strftime("%Y-W%W")
        elif job_type == "MONTHLY" or (job_type == "CRON" and "month" in cron):
            return now.strftime("%Y-M%m")
        return now.strftime("%Y-%m-%d")
