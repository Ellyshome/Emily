"""计划任务 Repository 层 —— 四张表的 CRUD 操作。

包含：PlanTaskTemplateRepo / PlanTaskInstanceRepo / PlanTaskLogRepo / PlanTaskDeliverableRepo
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from ..infrastructure.database.models import (
    PlanTaskTemplate,
    PlanTaskInstance,
    PlanTaskLog,
    PlanTaskDeliverable,
)
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.plan_task_repo")

BEIJING_TZ = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

# ── 异常类（ValueError 子类，向后兼容 except ValueError；5.6）──

class InvalidStateTransitionError(ValueError):
    """非法的状态流转。"""


class ArchivedTaskError(ValueError):
    """归档/取消的终态任务不可修改。"""


class TaskNotFoundError(ValueError):
    """任务实例不存在。"""


# ── 时区归一化辅助（统一为 UTC ISO8601，naive 视为北京时间；5.4）──

def _to_utc_iso(value: str) -> str:
    """将 ISO8601 时间字符串归一化为 UTC ISO8601（naive 视为北京时间）。解析失败原样返回。"""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def _parse_iso_utc(value: str):
    """解析 ISO8601 为 UTC datetime（naive 视为北京时间）。失败返回 None。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PlanTaskTemplateRepo
# ══════════════════════════════════════════════════════════════════════════════

class PlanTaskTemplateRepo:
    """任务模板 Repository。"""

    @staticmethod
    def generate_template_no() -> str:
        """生成模板编号 TPL-YYYYMMDD-NNNN。"""
        date_part = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        with get_session() as session:
            latest = (
                session.query(PlanTaskTemplate.template_no)
                .filter(PlanTaskTemplate.template_no.like(f"TPL-{date_part}-%"))
                .order_by(PlanTaskTemplate.template_no.desc())
                .first()
            )
            if latest:
                seq = int(latest[0].rsplit("-", 1)[-1]) + 1
            else:
                seq = 1
            return f"TPL-{date_part}-{seq:04d}"

    @staticmethod
    def create(**kwargs) -> PlanTaskTemplate:
        """创建任务模板。"""
        with get_session() as session:
            template = PlanTaskTemplate(**kwargs)
            session.add(template)
            session.flush()
            logger.info("PlanTaskTemplate created: %s", template.template_no)
            return template

    @staticmethod
    def get_by_id(template_id: str) -> PlanTaskTemplate | None:
        """按 ID 查询模板。"""
        with get_session() as session:
            return (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.id == template_id, PlanTaskTemplate.is_deleted == False)
                .first()
            )

    @staticmethod
    def get_by_template_no(template_no: str) -> PlanTaskTemplate | None:
        """按模板编号查询。"""
        with get_session() as session:
            return (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.template_no == template_no, PlanTaskTemplate.is_deleted == False)
                .first()
            )

    @staticmethod
    def get_active_cycle_templates() -> list[PlanTaskTemplate]:
        """获取所有 ACTIVE 的循环模板（task_type != ONCE）。"""
        with get_session() as session:
            return (
                session.query(PlanTaskTemplate)
                .filter(
                    PlanTaskTemplate.status == "ACTIVE",
                    PlanTaskTemplate.task_type != "ONCE",
                    PlanTaskTemplate.is_deleted == False,
                )
                .all()
            )

    @staticmethod
    def get_active_templates() -> list[PlanTaskTemplate]:
        """获取所有 ACTIVE 模板。"""
        with get_session() as session:
            return (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.status == "ACTIVE", PlanTaskTemplate.is_deleted == False)
                .all()
            )

    @staticmethod
    def update_status(template_id: str, status: str) -> PlanTaskTemplate | None:
        """更新模板状态。"""
        with get_session() as session:
            template = (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.id == template_id, PlanTaskTemplate.is_deleted == False)
                .first()
            )
            if template is None:
                return None
            template.status = status
            template.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("PlanTaskTemplate %s status: %s", template_id, status)
            return template

    @staticmethod
    def mark_template_llm_failed(template_id: str) -> None:
        """标记模板 LLM 推算截止时间失败（§2.4）。"""
        with get_session() as session:
            template = (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.id == template_id)
                .first()
            )
            if template:
                template.llm_calculation_failed = True
                template.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()

    @staticmethod
    def clear_template_llm_failed(template_id: str) -> None:
        """清除模板 LLM 失败标记（推算成功时自愈，§2.4）。"""
        with get_session() as session:
            template = (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.id == template_id)
                .first()
            )
            if template and template.llm_calculation_failed:
                template.llm_calculation_failed = False
                template.llm_failure_notified = False
                template.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()

    @staticmethod
    def mark_template_llm_failure_notified(template_id: str) -> None:
        """标记模板 LLM 失败已通知发起人。"""
        with get_session() as session:
            template = (
                session.query(PlanTaskTemplate)
                .filter(PlanTaskTemplate.id == template_id)
                .first()
            )
            if template:
                template.llm_failure_notified = True
                template.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()

    @staticmethod
    def find_templates_llm_failed_pending_notification(limit: int = 50) -> list[PlanTaskTemplate]:
        """查询 LLM 推算失败且尚未通知发起人的模板。"""
        with get_session() as session:
            return (
                session.query(PlanTaskTemplate)
                .filter(
                    PlanTaskTemplate.llm_calculation_failed == True,
                    PlanTaskTemplate.llm_failure_notified == False,
                    PlanTaskTemplate.is_deleted == False,
                )
                .order_by(PlanTaskTemplate.updated_at.asc())
                .limit(limit)
                .all()
            )


# ══════════════════════════════════════════════════════════════════════════════
# PlanTaskInstanceRepo
# ══════════════════════════════════════════════════════════════════════════════

class PlanTaskInstanceRepo:
    """任务实例 Repository。"""

    # 终态集合：不可再做任何修改
    ARCHIVABLE_STATUSES = frozenset({"ARCHIVED", "CANCELLED"})

    @staticmethod
    def generate_instance_no() -> str:
        """生成实例编号 PTI-YYYYMMDD-NNNN。"""
        date_part = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        with get_session() as session:
            latest = (
                session.query(PlanTaskInstance.instance_no)
                .filter(PlanTaskInstance.instance_no.like(f"PTI-{date_part}-%"))
                .order_by(PlanTaskInstance.instance_no.desc())
                .first()
            )
            if latest:
                seq = int(latest[0].rsplit("-", 1)[-1]) + 1
            else:
                seq = 1
            return f"PTI-{date_part}-{seq:04d}"

    @staticmethod
    def create(session=None, **kwargs) -> PlanTaskInstance:
        """创建任务实例。

        幂等检查：若 template_id + period_key 已有活跃实例则返回已有实例。
        传入 session 时复用该 session（由调用方管理提交）；否则自建 session。
        """
        template_id = kwargs.get("template_id", "")
        period_key = kwargs.get("period_key", "")

        def _impl(sess):
            # 幂等检查
            if template_id and period_key:
                existing = (
                    sess.query(PlanTaskInstance)
                    .filter(
                        PlanTaskInstance.template_id == template_id,
                        PlanTaskInstance.period_key == period_key,
                        PlanTaskInstance.status.notin_(["CANCELLED"]),
                        PlanTaskInstance.is_deleted == False,
                    )
                    .first()
                )
                if existing:
                    logger.info(
                        "PlanTaskInstance already exists for template=%s period=%s → %s",
                        template_id, period_key, existing.instance_no,
                    )
                    return existing

            instance = PlanTaskInstance(**kwargs)
            sess.add(instance)
            sess.flush()
            logger.info("PlanTaskInstance created: %s", instance.instance_no)
            return instance

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_by_id(instance_id: str) -> PlanTaskInstance | None:
        """按 ID 查询实例。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.id == instance_id, PlanTaskInstance.is_deleted == False)
                .first()
            )

    @staticmethod
    def get_by_instance_no(instance_no: str) -> PlanTaskInstance | None:
        """按实例编号查询。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.instance_no == instance_no, PlanTaskInstance.is_deleted == False)
                .first()
            )

    @staticmethod
    def get_by_period_key(template_id: str, period_key: str) -> PlanTaskInstance | None:
        """幂等查询：按 template_id + period_key 查找非取消实例。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(
                    PlanTaskInstance.template_id == template_id,
                    PlanTaskInstance.period_key == period_key,
                    PlanTaskInstance.status != "CANCELLED",
                    PlanTaskInstance.is_deleted == False,
                )
                .first()
            )

    @staticmethod
    def find_by_status(status: str, limit: int = 100) -> list[PlanTaskInstance]:
        """按状态查询实例列表。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.status == status, PlanTaskInstance.is_deleted == False)
                .order_by(PlanTaskInstance.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_overdue(now_iso: str, limit: int = 100) -> list[PlanTaskInstance]:
        """查询超时且仍在 WAITING 状态的任务。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(
                    PlanTaskInstance.status == "WAITING",
                    PlanTaskInstance.deadline_at < now_iso,
                    PlanTaskInstance.deadline_at.isnot(None),
                    PlanTaskInstance.is_deleted == False,
                )
                .order_by(PlanTaskInstance.deadline_at.asc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_long_overdue(now_iso: str, overdue_days: int, limit: int = 50) -> list[PlanTaskInstance]:
        """查询超期 N 天以上、未升级过的 WAITING 任务（自动升级用，§5.3）。"""
        try:
            threshold_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(days=overdue_days)
            threshold = threshold_dt.isoformat()
        except Exception:
            return []
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(
                    PlanTaskInstance.status == "WAITING",
                    PlanTaskInstance.deadline_at < threshold,
                    PlanTaskInstance.deadline_at.isnot(None),
                    PlanTaskInstance.escalated_at.is_(None),
                    PlanTaskInstance.is_deleted == False,
                )
                .order_by(PlanTaskInstance.deadline_at.asc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_near_deadline(now_iso: str, before_minutes: int = 60, limit: int = 100) -> list[PlanTaskInstance]:
        """查询临近截止（N 分钟内）且尚未提醒的任务。"""
        from datetime import datetime as dt, timedelta

        now = dt.fromisoformat(now_iso)
        window_start = (now + timedelta(minutes=before_minutes)).isoformat()

        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(
                    PlanTaskInstance.status == "WAITING",
                    PlanTaskInstance.deadline_at <= window_start,
                    PlanTaskInstance.deadline_at > now_iso,
                    PlanTaskInstance.reminded_at.is_(None),
                    PlanTaskInstance.is_deleted == False,
                )
                .order_by(PlanTaskInstance.deadline_at.asc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_by_executor(executor_id: str, status: str | None = None, limit: int = 100) -> list[PlanTaskInstance]:
        """按执行人查询任务列表。"""
        with get_session() as session:
            q = (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.executor_id == executor_id, PlanTaskInstance.is_deleted == False)
            )
            if status:
                q = q.filter(PlanTaskInstance.status == status)
            return q.order_by(PlanTaskInstance.created_at.desc()).limit(limit).all()

    @staticmethod
    def find_by_initiator(initiator_id: str, status: str | None = None, limit: int = 100) -> list[PlanTaskInstance]:
        """按发起人查询任务列表。"""
        with get_session() as session:
            q = (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.initiator_id == initiator_id, PlanTaskInstance.is_deleted == False)
            )
            if status:
                q = q.filter(PlanTaskInstance.status == status)
            return q.order_by(PlanTaskInstance.created_at.desc()).limit(limit).all()

    @staticmethod
    def find_match_candidates(
        project_id: str,
        phase_code: str,
        executor_id: str,
        time_window_days: int = 7,
        limit: int = 20,
    ) -> list[PlanTaskInstance]:
        """计划外事件匹配候选：同项目 + 同阶段 + 同执行人 + 时间窗口内的 WAITING 任务。"""
        from datetime import datetime as dt, timedelta

        now = dt.now(timezone.utc)
        window_start = (now - timedelta(days=time_window_days)).isoformat()

        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .filter(
                    PlanTaskInstance.status == "WAITING",
                    PlanTaskInstance.executor_id == executor_id,
                    PlanTaskInstance.project_id == project_id,
                    PlanTaskInstance.phase_code == phase_code,
                    PlanTaskInstance.deadline_at >= window_start,
                    PlanTaskInstance.is_deleted == False,
                )
                .order_by(PlanTaskInstance.deadline_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_recent_archived_cycle_tasks(limit: int = 50) -> list[PlanTaskInstance]:
        """查询最近归档的循环模板实例（用于触发下一轮生成）。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .join(PlanTaskTemplate, PlanTaskInstance.template_id == PlanTaskTemplate.id)
                .filter(
                    PlanTaskInstance.status == "ARCHIVED",
                    PlanTaskTemplate.task_type != "ONCE",
                    PlanTaskTemplate.status == "ACTIVE",
                    PlanTaskInstance.is_deleted == False,
                )
                .order_by(PlanTaskInstance.archived_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_overdue_cycle_instances(now_iso: str, limit: int = 50) -> list[PlanTaskInstance]:
        """查询已过期但未归档/未取消的循环任务实例（容错补齐用）。"""
        with get_session() as session:
            return (
                session.query(PlanTaskInstance)
                .join(PlanTaskTemplate, PlanTaskInstance.template_id == PlanTaskTemplate.id)
                .filter(
                    PlanTaskInstance.status.notin_(["ARCHIVED", "CANCELLED"]),
                    PlanTaskInstance.deadline_at < now_iso,
                    PlanTaskTemplate.task_type != "ONCE",
                    PlanTaskTemplate.status == "ACTIVE",
                    PlanTaskInstance.is_deleted == False,
                )
                .order_by(PlanTaskInstance.deadline_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def update_status(instance_id: str, new_status: str, session=None, **kwargs) -> PlanTaskInstance | None:
        """更新实例状态 + 相关时间戳。

        强制约束：若当前状态为 ARCHIVED 或 CANCELLED（终态），抛出 ValueError。
        传入 session 时复用该 session（由调用方管理提交）；否则自建 session。
        """

        def _impl(sess):
            instance = (
                sess.query(PlanTaskInstance)
                .filter(PlanTaskInstance.id == instance_id, PlanTaskInstance.is_deleted == False)
                .first()
            )
            if instance is None:
                return None

            if instance.status in ("ARCHIVED", "CANCELLED"):
                raise ArchivedTaskError(
                    f"PlanTaskInstance {instance_id} 已处于终态 '{instance.status}'，不可修改"
                )

            from_status = instance.status
            instance.status = new_status
            instance.updated_at = datetime.now(timezone.utc).isoformat()

            # 按状态设置时间戳
            if new_status == "SUBMITTED" and "submitted_at" not in kwargs:
                instance.submitted_at = datetime.now(timezone.utc).isoformat()
            elif new_status == "CONFIRMED" and "confirmed_at" not in kwargs:
                instance.confirmed_at = datetime.now(timezone.utc).isoformat()
            elif new_status == "ARCHIVED" and "archived_at" not in kwargs:
                instance.archived_at = datetime.now(timezone.utc).isoformat()

            # 按 kwargs 覆盖时间戳和额外字段
            for key, value in kwargs.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)

            logger.info("PlanTaskInstance %s: %s → %s", instance_id, from_status, new_status)
            return instance

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def set_reminded_at(instance_id: str, reminded_at: str) -> None:
        """更新最后提醒时间。"""
        with get_session() as session:
            instance = (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.id == instance_id)
                .first()
            )
            if instance:
                instance.reminded_at = reminded_at
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()

    @staticmethod
    def count_by_status() -> dict[str, int]:
        """按状态统计实例数量。"""
        with get_session() as session:
            from sqlalchemy import func
            rows = (
                session.query(PlanTaskInstance.status, func.count(PlanTaskInstance.id))
                .filter(PlanTaskInstance.is_deleted == False)
                .group_by(PlanTaskInstance.status)
                .all()
            )
            return {status: count for status, count in rows}

    @staticmethod
    def update_workflow_instance_id(instance_id: str, workflow_instance_id: str) -> None:
        """回写工作流实例 ID 到计划任务。"""
        with get_session() as session:
            instance = (
                session.query(PlanTaskInstance)
                .filter(PlanTaskInstance.id == instance_id)
                .first()
            )
            if instance:
                instance.workflow_instance_id = workflow_instance_id
                instance.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# PlanTaskLogRepo
# ══════════════════════════════════════════════════════════════════════════════

class PlanTaskLogRepo:
    """状态变更日志 Repository。"""

    @staticmethod
    def create(
        instance_id: str,
        from_status: str | None,
        to_status: str,
        operator_id: str = "",
        reason: str = "",
        snapshot: dict | None = None,
        session=None,
    ) -> PlanTaskLog:
        """创建状态变更日志。传入 session 时复用（由调用方管理提交）。"""

        def _impl(sess):
            log = PlanTaskLog(
                instance_id=instance_id,
                from_status=from_status,
                to_status=to_status,
                operator_id=operator_id or "",
                reason=reason,
                snapshot=json.dumps(snapshot or {}, ensure_ascii=False),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            sess.add(log)
            sess.flush()
            return log

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def find_by_instance(instance_id: str, limit: int = 100) -> list[PlanTaskLog]:
        """查询实例的状态变更日志（按时间正序）。"""
        with get_session() as session:
            return (
                session.query(PlanTaskLog)
                .filter(PlanTaskLog.instance_id == instance_id)
                .order_by(PlanTaskLog.created_at.asc())
                .limit(limit)
                .all()
            )


# ══════════════════════════════════════════════════════════════════════════════
# PlanTaskDeliverableRepo
# ══════════════════════════════════════════════════════════════════════════════

class PlanTaskDeliverableRepo:
    """任务成果 Repository。"""

    @staticmethod
    def create(session=None, **kwargs) -> PlanTaskDeliverable:
        """创建成果记录。传入 session 时复用（由调用方管理提交）。"""

        def _impl(sess):
            deliverable = PlanTaskDeliverable(
                created_at=datetime.now(timezone.utc).isoformat(),
                **kwargs,
            )
            sess.add(deliverable)
            sess.flush()
            logger.info("PlanTaskDeliverable created for instance=%s", kwargs.get("instance_id", "?"))
            return deliverable

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def find_by_instance(instance_id: str, limit: int = 100) -> list[PlanTaskDeliverable]:
        """查询实例的所有成果。"""
        with get_session() as session:
            return (
                session.query(PlanTaskDeliverable)
                .filter(PlanTaskDeliverable.instance_id == instance_id)
                .order_by(PlanTaskDeliverable.submitted_at.desc())
                .limit(limit)
                .all()
            )
