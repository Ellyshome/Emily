"""计划任务调度引擎 —— 后台 asyncio 循环。

职责：
  - 分布式锁：多实例部署时使用 PostgreSQL Advisory Lock 确保只有一个进程执行调度
  - 超时检测 + 提醒：扫描超时/临近截止的 WAITING 任务，发送通知
  - 循环任务补齐（双触发机制）：
      主触发：上一轮归档后自动生成下一轮实例
      容错触发：已过期但未归档/取消的任务，LLM 判断是否应补齐当前周期
  - LLM 失败通知：扫描推算失败的实例，通知发起人人工处理
  - 自动归档：已确认的任务自动归档
  - 自动升级：超期 N 天未处理的任务自动升级给上级（P2）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .plan_task_commands import CreateInstanceFromTemplateCommand, EscalateTaskCommand, PeriodCalculationResult

if TYPE_CHECKING:
    from ..config import Config
    from ..outbound_bus import OutboundEventBus

logger = logging.getLogger("emily.plan_task_scheduler")


class LLMCalculationError(Exception):
    """LLM 推算周期/截止时间失败（§2.4 降级：不做自动推算，通知人工处理）。"""

# ══════════════════════════════════════════════════════════════════════════════
# 消息模板
# ══════════════════════════════════════════════════════════════════════════════

_REMINDER_TEMPLATE = (
    "【提醒】你有一项任务「{title}」将在 {deadline_at} 截止，请尽快提交成果。"
)
_OVERDUE_TEMPLATE = (
    "【超时通知】任务「{title}」已于 {deadline_at} 截止，当前已超时。已通知任务负责人 {initiator_name}。"
)
_ESCALATION_TEMPLATE = (
    "【升级通知】任务「{title}」（{instance_no}）已超期 {days} 天，"
    "原执行人 {original_executor_name} 未提交成果，现升级由你处理。"
)
_LLM_FAILURE_TEMPLATE = (
    "【需要人工处理】计划任务循环模板「{template_name}」（{template_no}）"
    "无法自动推算当前周期的截止时间（deadline_rule: {deadline_rule}），"
    "请手动设定截止时间后继续执行。"
)


# ══════════════════════════════════════════════════════════════════════════════
# PlanTaskScheduler
# ══════════════════════════════════════════════════════════════════════════════

class PlanTaskScheduler:
    """计划任务调度引擎 —— 后台 asyncio 循环。

    设计原则：
      - 每次 tick 基于数据库状态判定（不依赖内存），进程重启后自动恢复
      - 分布式锁确保多进程部署时只有一个执行调度
      - 所有异常 catching，单个任务失败不影响其他任务
    """

    def __init__(
        self,
        service,                       # PlanTaskService
        config: "Config",
        outbound_bus: "OutboundEventBus",
        llm_client=None,               # LLM 客户端（用于推算截止时间）
        workflow_integrator=None,      # 工作流集成（确认后启动工作流 + 重试，§4.4）
    ):
        self._service = service
        self._config = config
        self._outbound = outbound_bus
        self._llm = llm_client
        self._workflow_integrator = workflow_integrator
        self._last_overdue_check = None  # 上次超时检测时间（节流，§5.3）
        self._task: asyncio.Task | None = None
        self._running = False

    # ── 生命周期 ──

    async def start(self):
        """启动调度循环（在 EmilyCore 初始化时调用）。"""
        if not self._config.scheduler_enabled:
            logger.info("PlanTaskScheduler: disabled by config")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("PlanTaskScheduler: started (tick=%ds)", self._config.scheduler_tick_seconds)

    async def stop(self):
        """停止调度循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PlanTaskScheduler: stopped")

    # ── 分布式锁 ──
    # Advisory Lock 在 _tick 内用独立裸 Session 持有（见 _tick 实现）。
    # 不再使用独立 helper：advisory lock 绑定数据库连接，
    # 必须跨整个 _tick 保持同一 session，否则 session 关闭即释放锁。

    # ── 主循环 ──

    async def _loop(self):
        """主循环：每 scheduler_tick_seconds 秒执行一次。"""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduler tick failed: %s", e, exc_info=True)
            await asyncio.sleep(self._config.scheduler_tick_seconds)

    async def _tick(self):
        """单次调度循环（分布式锁保护）。

        持锁机制：用一个独立裸 Session 跨整个 _tick 保持打开，在该 Session 的
        数据库连接上获取 PostgreSQL Advisory Lock，直到 _tick 结束才释放——
        确保锁在调度期间始终有效（advisory lock 绑定连接，session 提前关闭即释放）。
        """
        from ..infrastructure.database.session import get_session_raw
        from sqlalchemy import text

        lock_key = "plan_task_scheduler:global_tick"
        lock_session = get_session_raw()
        try:
            acquired = lock_session.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": lock_key},
            ).scalar()
            if not acquired:
                return  # 其他进程已在处理

            try:
                now = datetime.now(timezone.utc).isoformat()

                # 1. 超时检测 + 发送提醒
                await self._handle_overdue_tasks(now)

                # 2. 临近截止提醒
                await self._handle_near_deadline_tasks(now)

                # 3. 循环任务实例生成（主动补齐 + 归档触发 + 容错补齐）
                await self._handle_cycle_task_generation(now)

                # 4. LLM 推算失败通知发起人人工处理
                await self._notify_llm_failure_to_initiators()

                # 5. 自动归档已确认任务
                await self._auto_archive_confirmed()

                # 6. 超期 N 天未处理自动升级给上级（P2）
                await self._auto_escalate_long_overdue(now)

                # 7. 重试未启动工作流的已确认任务（工作流集成，§4.4）
                if self._workflow_integrator is not None:
                    try:
                        await self._workflow_integrator.retry_pending_workflow_starts()
                    except Exception as e:
                        logger.warning("Workflow retry failed: %s", e)

            finally:
                try:
                    lock_session.execute(
                        text("SELECT pg_advisory_unlock(hashtext(:key))"),
                        {"key": lock_key},
                    )
                except Exception as e:
                    logger.warning("Failed to release distributed lock: %s", e)
        finally:
            lock_session.close()

    # ── 超时处理 ──

    async def _handle_overdue_tasks(self, now_iso: str):
        """处理超时任务：通知执行者和发起人。"""
        # 节流：超时检测按 scheduler_overdue_check_interval 间隔执行（§5.3）
        interval = getattr(self._config, "scheduler_overdue_check_interval", 300)
        _now = datetime.now(timezone.utc)
        if self._last_overdue_check is not None and (_now - self._last_overdue_check).total_seconds() < interval:
            return
        self._last_overdue_check = _now
        try:
            overdue = await self._service.find_overdue(now_iso, limit=100)
            for inst in overdue:
                if not hasattr(inst, "executor_id") or not inst.executor_id:
                    continue

                # 通知执行者
                self._outbound.publish("reply", {
                    "user_id": inst.executor_id,
                    "content": _OVERDUE_TEMPLATE.format(
                        title=getattr(inst, "title", ""),
                        deadline_at=getattr(inst, "deadline_at", "") or "",
                        initiator_name=self._get_user_display_name(getattr(inst, "initiator_id", "")),
                    ),
                    "source": "plan_task_scheduler",
                })

                # 标记已提醒
                await self._service.set_reminded_at(inst.id, now_iso)

                logger.info("Overdue notification sent: instance=%s executor=%s",
                           getattr(inst, "instance_no", inst.id), inst.executor_id)
        except Exception as e:
            logger.error("Overdue task handling failed: %s", e, exc_info=True)

    # ── 临近截止提醒 ──

    async def _handle_near_deadline_tasks(self, now_iso: str):
        """处理临近截止的任务：发送提醒。"""
        try:
            _before = getattr(self._config, "scheduler_reminder_before_minutes", 60)
            near = await self._service.find_near_deadline(now_iso, before_minutes=_before, limit=100)
            for inst in near:
                if not hasattr(inst, "executor_id") or not inst.executor_id:
                    continue

                self._outbound.publish("reply", {
                    "user_id": inst.executor_id,
                    "content": _REMINDER_TEMPLATE.format(
                        title=getattr(inst, "title", ""),
                        deadline_at=getattr(inst, "deadline_at", "") or "",
                    ),
                    "source": "plan_task_scheduler",
                })

                await self._service.set_reminded_at(inst.id, now_iso)

                logger.info("Near-deadline reminder sent: instance=%s executor=%s",
                           getattr(inst, "instance_no", inst.id), inst.executor_id)
        except Exception as e:
            logger.error("Near-deadline reminder failed: %s", e, exc_info=True)

    # ── 循环任务生成（双触发机制）──

    async def _handle_cycle_task_generation(self, now_iso: str):
        """循环任务补齐：主动补齐 + 归档触发 + 容错补齐。"""
        try:
            # 主动补齐：扫描所有 ACTIVE 循环模板，确保当前周期实例存在
            # （计划 §4.3 核心逻辑——不依赖上一轮归档，新激活模板也能立即生成首条实例）
            active_templates = await self._service.get_active_cycle_templates()
            for template in active_templates:
                await self._ensure_template_current_cycle(template)

            # 主触发：最近归档的循环任务 → 生成下一轮
            archived = await self._service.find_recent_archived_cycle_tasks(limit=50)
            for inst in archived:
                await self._generate_next_cycle_from_archived(inst)

            # 容错触发：已过期但未归档/取消的循环任务 → 判断是否应补齐
            overdue_cycles = await self._service.find_overdue_cycle_instances(now_iso, limit=50)
            for inst in overdue_cycles:
                await self._ensure_current_cycle_exists(inst, now_iso)

        except Exception as e:
            logger.error("Cycle task generation failed: %s", e, exc_info=True)

    async def _generate_next_cycle_from_archived(self, archived_instance):
        """主触发：上一轮归档后生成下一轮实例。"""
        template_id = getattr(archived_instance, "template_id", "")
        if not template_id:
            return

        template = await self._service.get_template(template_id)
        if template is None or template.status != "ACTIVE":
            return

        try:
            # 推算下一个 period_key
            next_period = await self._calc_next_period(
                deadline_rule=template.deadline_rule,
                current_period_key=getattr(archived_instance, "period_key", ""),
                current_deadline=getattr(archived_instance, "deadline_at", ""),
            )

            # 幂等检查
            existing = await self._service.get_by_period_key(template_id, next_period.period_key)
            if existing:
                return

            # 创建下一轮实例
            await self._service.create_instance_from_template(
                CreateInstanceFromTemplateCommand(
                    template_id=template_id,
                    title=template.name,
                    description=template.description,
                    initiator_id=template.initiator_id,
                    executor_id=template.executor_id,
                    project_id=template.project_id,
                    deadline_at=next_period.deadline_at,
                    verification_standard=template.verification_standard,
                    period_key=next_period.period_key,
                    template_no=template.template_no,
                )
            )
            logger.info("Next cycle created: template=%s period=%s",
                       template.template_no, next_period.period_key)

        except LLMCalculationError as e:
            await self._service.mark_template_llm_failed(template_id)
            logger.warning("LLM next-period calc failed for template %s: %s", template_id, e)
        except Exception as e:
            logger.warning("Failed to generate next cycle from archived instance %s: %s",
                         getattr(archived_instance, "instance_no", "?"), e)

    async def _ensure_current_cycle_exists(self, overdue_instance, now_iso: str):
        """容错触发：检查已过期的循环任务是否需要补齐当前周期。

        注意：已 CANCELLED 或模板 INACTIVE 的不再补齐。
        """
        template_id = getattr(overdue_instance, "template_id", "")
        if not template_id:
            return

        template = await self._service.get_template(template_id)
        if template is None or template.status != "ACTIVE":
            return

        try:
            # 推算当前周期
            current_period = await self._calc_current_period(
                deadline_rule=template.deadline_rule,
                reference_time=datetime.now(timezone.utc),
            )

            # 幂等检查
            existing = await self._service.get_by_period_key(template_id, current_period.period_key)
            if existing:
                return

            # 创建当前周期实例
            await self._service.create_instance_from_template(
                CreateInstanceFromTemplateCommand(
                    template_id=template_id,
                    title=template.name,
                    description=template.description,
                    initiator_id=template.initiator_id,
                    executor_id=template.executor_id,
                    project_id=template.project_id,
                    deadline_at=current_period.deadline_at,
                    verification_standard=template.verification_standard,
                    period_key=current_period.period_key,
                    template_no=template.template_no,
                )
            )
            logger.info("Catch-up cycle created: template=%s period=%s",
                       template.template_no, current_period.period_key)

        except LLMCalculationError as e:
            await self._service.mark_template_llm_failed(template_id)
            logger.warning("LLM calc failed for template %s: %s",
                          getattr(template, "template_no", "?"), e)
        except Exception as e:
            logger.warning("Failed to catch-up cycle for template %s: %s", template.template_no, e)

    async def _ensure_template_current_cycle(self, template):
        """主动补齐：确保 ACTIVE 循环模板的当前周期实例存在（不依赖上一轮归档）。

        计划 §4.3 _ensure_cycle_instances_exist 的落地——新激活的循环模板
        在首次 tick 即生成当前周期实例，无需等待上一轮归档。
        LLM 推算失败时标记模板并跳过（§2.4），由 _notify_llm_failure_to_initiators 通知人工；
        推算成功时清除失败标记（自愈——下次失败可重新通知）。
        """
        if getattr(template, "task_type", "ONCE") == "ONCE":
            return
        if getattr(template, "status", "") != "ACTIVE":
            return

        try:
            current_period = await self._calc_current_period(
                deadline_rule=getattr(template, "deadline_rule", ""),
                reference_time=datetime.now(timezone.utc),
            )
        except LLMCalculationError as e:
            await self._service.mark_template_llm_failed(template.id)
            logger.warning("LLM calc failed for template %s: %s",
                          getattr(template, "template_no", "?"), e)
            return
        except Exception as e:
            logger.warning("Failed to ensure current cycle for template %s: %s",
                          getattr(template, "template_no", "?"), e)
            return

        # 推算成功：清除失败标记（自愈）
        await self._service.clear_template_llm_failed(template.id)

        try:
            # 幂等检查
            existing = await self._service.get_by_period_key(
                template.id, current_period.period_key
            )
            if existing:
                return

            await self._service.create_instance_from_template(
                CreateInstanceFromTemplateCommand(
                    template_id=template.id,
                    title=template.name,
                    description=getattr(template, "description", ""),
                    initiator_id=template.initiator_id,
                    executor_id=template.executor_id,
                    project_id=template.project_id,
                    deadline_at=current_period.deadline_at,
                    verification_standard=getattr(template, "verification_standard", "{}"),
                    period_key=current_period.period_key,
                    template_no=template.template_no,
                )
            )
            logger.info("Current cycle ensured: template=%s period=%s",
                       template.template_no, current_period.period_key)
        except Exception as e:
            logger.warning("Failed to create current cycle instance for template %s: %s",
                          getattr(template, "template_no", "?"), e)

    # ── LLM 推算 ──
    # 计划 §2.4：LLM 推算失败时不做自动兜底推算，抛 LLMCalculationError，
    # 由调用方标记模板失败并通知发起人人工处理。

    async def _calc_current_period(
        self, deadline_rule: str, reference_time: datetime
    ) -> PeriodCalculationResult:
        """调用 LLM 推算当前周期的 period_key 和 deadline_at。

        LLM 不可用或调用失败时抛 LLMCalculationError（不做兜底推算，§2.4）。
        """
        if not self._llm:
            raise LLMCalculationError("LLM 客户端未配置，无法推算周期")

        prompt = (
            f"当前任务的截止时间描述为：「{deadline_rule}」。"
            f"以参考时间 {reference_time.isoformat()} 为基准，"
            f"推算当前所在周期的信息。返回 JSON："
            f'{{"period_key": "2024-W25 或 2024-M06 格式",'
            f'"deadline_at": "ISO8601 截止时间", "cycle_type": "WEEKLY 或 MONTHLY"}}'
        )
        try:
            data = await self._llm.chat_json(prompt, "PlanTaskScheduler period calc")
        except Exception as e:
            raise LLMCalculationError(
                f"LLM 推算当前周期失败 (rule='{deadline_rule}'): {e}"
            ) from e

        if not isinstance(data, dict):
            raise LLMCalculationError(f"LLM 返回非 JSON 对象 (rule='{deadline_rule}'): {data}")
        period_key = data.get("period_key", "")
        deadline_at = data.get("deadline_at", "")
        if not period_key or not deadline_at:
            raise LLMCalculationError(
                f"LLM 返回的周期信息不完整 (rule='{deadline_rule}'): {data}"
            )
        return PeriodCalculationResult(
            period_key=period_key,
            deadline_at=deadline_at,
            cycle_type=data.get("cycle_type", ""),
        )

    async def _calc_next_period(
        self,
        deadline_rule: str,
        current_period_key: str,
        current_deadline: str,
    ) -> PeriodCalculationResult:
        """调用 LLM 推算下一个周期的 period_key 和 deadline_at。

        LLM 不可用或调用失败时抛 LLMCalculationError（不做兜底推算，§2.4）。
        """
        if not self._llm:
            raise LLMCalculationError("LLM 客户端未配置，无法推算下一周期")

        prompt = (
            f"当前任务的截止时间描述为：「{deadline_rule}」。"
            f"上一轮周期的标识是 {current_period_key}，截止时间是 {current_deadline}。"
            f"请推算下一个周期的信息。返回 JSON："
            f'{{"period_key": "2024-W26 或 2024-M07 格式",'
            f'"deadline_at": "ISO8601 截止时间", "cycle_type": "WEEKLY 或 MONTHLY"}}'
        )
        try:
            data = await self._llm.chat_json(prompt, "PlanTaskScheduler next period calc")
        except Exception as e:
            raise LLMCalculationError(
                f"LLM 推算下一周期失败 (rule='{deadline_rule}'): {e}"
            ) from e

        if not isinstance(data, dict):
            raise LLMCalculationError(f"LLM 返回非 JSON 对象 (rule='{deadline_rule}'): {data}")
        period_key = data.get("period_key", "")
        deadline_at = data.get("deadline_at", "")
        if not period_key or not deadline_at:
            raise LLMCalculationError(
                f"LLM 返回的下一周期信息不完整 (rule='{deadline_rule}'): {data}"
            )
        return PeriodCalculationResult(
            period_key=period_key,
            deadline_at=deadline_at,
            cycle_type=data.get("cycle_type", ""),
        )

    # ── LLM 失败通知 ──

    async def _notify_llm_failure_to_initiators(self):
        """扫描 LLM 推算失败且尚未通知的模板，通知发起人人工处理（§2.4）。

        失败标记在模板级别——推算针对 deadline_rule，属模板级事件。
        """
        try:
            failed_templates = await self._service.find_templates_llm_failed_pending_notification(limit=50)
            for tpl in failed_templates:
                self._outbound.publish("reply", {
                    "user_id": getattr(tpl, "initiator_id", ""),
                    "content": _LLM_FAILURE_TEMPLATE.format(
                        template_name=getattr(tpl, "name", "?"),
                        template_no=getattr(tpl, "template_no", "?"),
                        deadline_rule=getattr(tpl, "deadline_rule", "?"),
                    ),
                    "source": "plan_task_scheduler",
                })
                await self._service.mark_template_llm_failure_notified(tpl.id)
                logger.info("LLM failure notification sent: template=%s",
                           getattr(tpl, "template_no", tpl.id))

        except Exception as e:
            logger.error("LLM failure notification failed: %s", e, exc_info=True)

    # ── 自动归档 ──

    async def _auto_archive_confirmed(self):
        """自动归档已确认的任务。"""
        try:
            confirmed = await self._service.find_by_status("CONFIRMED", limit=100)
            for inst in confirmed:
                try:
                    await self._service.archive_task(inst.id)
                    logger.info("Auto-archived: instance=%s", getattr(inst, "instance_no", inst.id))
                except ValueError as e:
                    logger.debug("Skip archive instance=%s: %s", inst.id, e)
        except Exception as e:
            logger.error("Auto-archive failed: %s", e, exc_info=True)

    # ── 自动升级（P2）──

    async def _auto_escalate_long_overdue(self, now_iso: str):
        """超期 N 天未处理的任务，自动升级给顺位上级（P2，§5.3）。

        扫描 deadline_at 早于 (now - N 天) 且尚未升级过的 WAITING 任务，
        调用 escalate_to_supervisor 转派给执行人的直接上级。
        """
        try:
            days = getattr(self._config, "scheduler_escalate_after_overdue_days", 7)
            long_overdue = await self._service.find_long_overdue(now_iso, days, limit=50)
            for inst in long_overdue:
                try:
                    await self._service.escalate_to_supervisor(
                        EscalateTaskCommand(
                            instance_id=inst.id,
                            reason=f"超期 {days} 天未提交成果，自动升级给顺位上级",
                        )
                    )
                    logger.info("Auto-escalated long-overdue: instance=%s",
                               getattr(inst, "instance_no", inst.id))
                except Exception as e:
                    logger.warning("Auto-escalate failed for instance %s: %s", inst.id, e)
        except Exception as e:
            logger.error("Auto-escalate long-overdue failed: %s", e, exc_info=True)

    # ── 辅助方法 ──

    @staticmethod
    def _get_user_display_name(user_id: str) -> str:
        """获取用户显示名称（简化版，避免额外 DB 查询）。"""
        return user_id[:12] if user_id else "未知"
