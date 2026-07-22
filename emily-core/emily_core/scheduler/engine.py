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
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from .handler_registry import JobHandlerRegistry, JobResult
from .hook_registry import SchedulerHookRegistry, HookDecision
from .next_execution import calc_next_execution

logger = logging.getLogger("emily.scheduler.engine")

# 北京时间：种子/历史调度作业的 next_execution_at 存的是无 offset 的北京时间字符串
_BEIJING_TZ = timezone(timedelta(hours=8))


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
                except Exception as e:
                    logger.debug("advisory unlock failed: %s", e, exc_info=True)
        finally:
            lock_session.close()

    def _is_due(self, job, now: datetime) -> bool:
        """判断作业是否到期执行。

        now 为 UTC aware。next_execution_at 有两种历史存法：
        - 带 offset/Z 的 aware 字符串（_utc_now() 产出的 +00:00）→ 直接比
        - 无 offset 的 naive 北京时间字符串（db_seeds 的 2026-07-21T09:00:00）
          → 按北京时间解释后再与 UTC now 比较；否则 aware/naive 比较会抛 TypeError
        """
        next_exec = getattr(job, 'next_execution_at', '')
        if not next_exec:
            return True  # 无下次执行时间，立即执行
        try:
            next_dt = datetime.fromisoformat(next_exec.replace("Z", "+00:00"))
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=_BEIJING_TZ)
            return now >= next_dt
        except (ValueError, AttributeError):
            return True

    async def _execute_job(self, job):
        """执行单个作业：创建执行记录 → 状态流转 → hooks → handler → reschedule。

        状态流转：PENDING(创建) → RUNNING → SUCCESS/FAILED。
        reschedule：执行后重算 next_execution_at；ONCE 跑完置 INACTIVE。
        """
        period_key = self._calc_period_key(job)

        # 幂等：同周期已有 SUCCESS 执行记录则跳过。
        # reschedule 落地后 next_execution_at 推进到下个周期是主闸门；
        # 此处仅查 SUCCESS 兜底，允许同周期失败重试（PENDING/RUNNING 由 tick 串行 + advisory lock 兜底）。
        if await self._service.has_executed_in_period(job.id, period_key):
            logger.debug("Job %s already succeeded in period %s, skip", job.job_no, period_key)
            return

        ctx = SchedulerContext(
            job_id=job.id,
            job_no=job.job_no,
            action_type=job.action_type,
            action_params=json.loads(getattr(job, 'action_params', '{}') or '{}'),
            period_key=period_key,
        )

        # 创建执行记录（PENDING）→ 置 RUNNING
        exec_result = await self._service.create_execution(job.id, period_key)
        exec_id = ""
        if exec_result.success:
            ctx.execution_no = exec_result.execution_no
            exec_id = exec_result.execution_id
        if exec_id:
            await self._service.update_execution_status(exec_id, "RUNNING")

        started_iso = datetime.now(timezone.utc).isoformat()
        success = False
        summary = ""
        error_msg = ""

        # ① before:execute hooks（阻断 → 落 FAILED，不触发 after/on_error）
        if not await self._fire_before_hooks(ctx):
            logger.info("Job %s blocked by before:execute hook", job.job_no)
            error_msg = "blocked by before:execute hook"
        else:
            try:
                # ② 执行 handler（No-handler 抛错落 FAILED，状态可追溯）
                handler = self._handlers.get(job.action_type)
                if handler is None:
                    raise RuntimeError(f"No handler for action_type={job.action_type}")

                result = await handler.execute(ctx.action_params)
                ctx.result = result
                success = result.success
                summary = (result.summary or "")[:1000]
                if not success:
                    error_msg = summary

            except Exception as e:
                logger.error("Job %s failed: %s", job.action_type, e, exc_info=True)
                summary = str(e)[:1000]
                error_msg = summary
                ctx.result = JobResult(success=False, summary=summary)
                # ④ on_error:execute hooks
                await self._fire_error_hooks(ctx, e)

            # ③ after:execute hooks
            await self._fire_after_hooks(ctx)

        # 状态流转：SUCCESS / FAILED（不再停留 PENDING）
        if exec_id:
            await self._service.update_execution_status(
                exec_id,
                "SUCCESS" if success else "FAILED",
                result_summary=summary,
                error_message="" if success else error_msg,
            )

        # ── 进化日志：调度器作业日志 ──
        try:
            from ..infrastructure.logging.scheduler_logger import SchedulerJobLogger
            now_iso = datetime.now(timezone.utc).isoformat()
            SchedulerJobLogger.log_sync(
                job_id=job.id,
                action_type=job.action_type,
                params_json=getattr(job, 'action_params', '{}') or '{}',
                success=success,
                summary=summary[:500],
                elapsed_ms=0,
                error_detail="" if success else error_msg,
                started_at=started_iso,
                completed_at=now_iso,
            )
        except Exception as e:
            logger.warning("Scheduler job log write failed: %s", e)

        # ── reschedule：重算 next_execution_at（成功/失败都推进，避免 past-due 每 tick 重跑）──
        # ONCE 跑完无下次执行时间 → 置 INACTIVE。
        try:
            now_utc = datetime.now(timezone.utc)
            next_at = calc_next_execution(job, now_utc)
            await self._service.update_job_last_executed(job.id, now_utc.isoformat(), next_at)
            if not next_at and getattr(job, 'job_type', 'ONCE') == "ONCE":
                await self._service.update_job_status(job.id, "INACTIVE")
                logger.info("Job %s (ONCE) executed, set INACTIVE", job.job_no)
        except Exception as e:
            logger.warning("Job %s reschedule failed: %s", job.job_no, e)

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
