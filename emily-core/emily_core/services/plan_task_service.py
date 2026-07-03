"""计划任务 Service 层 —— 核心业务逻辑。

职责：
  - 任务模板管理（创建、激活）
  - 任务实例生命周期管理（创建、提交、审核、退回、归档、取消）
  - 五态状态机（含 ANOMALY_PENDING_REVIEW 异常复核）驱动
  - 鉴权：发起人→执行人权限层级检查，异常标记
  - 计划外事件匹配
  - 监控统计数据查询
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .plan_task_commands import (
    CreateTemplateCommand,
    CreateInstanceCommand,
    CreateInstanceFromTemplateCommand,
    SubmitDeliverableCommand,
    ReviewTaskCommand,
    ReviewAnomalyCommand,
    EscalateTaskCommand,
    AuthCheckResult,
    PeriodCalculationResult,
    MatchResult,
)
from ..repositories.plan_task_repo import (
    PlanTaskTemplateRepo,
    PlanTaskInstanceRepo,
    PlanTaskLogRepo,
    PlanTaskDeliverableRepo,
    InvalidStateTransitionError,
    ArchivedTaskError,
    TaskNotFoundError,
    ComplianceChainError,
    _to_utc_iso,
)
from ..repositories.user_repo import UserRepository
from ..infrastructure.database.session import get_session

if TYPE_CHECKING:
    from ..infrastructure.database.models import (
        PlanTaskTemplate,
        PlanTaskInstance,
        PlanTaskLog,
        PlanTaskDeliverable,
    )

logger = logging.getLogger("emily.plan_task_service")


# ══════════════════════════════════════════════════════════════════════════════
# 状态流转规则
# ══════════════════════════════════════════════════════════════════════════════

ALLOWED_TRANSITIONS: dict[str | None, list[str]] = {
    None:                       ["WAITING", "ANOMALY_PENDING_REVIEW"],
    "WAITING":                  ["SUBMITTED", "CANCELLED", "ANOMALY_PENDING_REVIEW"],
    "ANOMALY_PENDING_REVIEW":   ["WAITING", "CANCELLED"],
    "SUBMITTED":                ["CONFIRMED", "RETURNED", "CANCELLED"],
    "RETURNED":                 ["SUBMITTED", "CANCELLED"],
    "CONFIRMED":                ["ARCHIVED", "CANCELLED"],
    "ARCHIVED":                 [],
    "CANCELLED":                [],
}

# 从 None（新实例）到哪个状态的反向查找
_CREATE_TARGET_STATUSES = ALLOWED_TRANSITIONS[None]


# ══════════════════════════════════════════════════════════════════════════════
# PlanTaskService
# ══════════════════════════════════════════════════════════════════════════════

class PlanTaskService:
    """计划任务核心业务 Service。"""

    def __init__(
        self,
        template_repo: PlanTaskTemplateRepo | None = None,
        instance_repo: PlanTaskInstanceRepo | None = None,
        log_repo: PlanTaskLogRepo | None = None,
        deliverable_repo: PlanTaskDeliverableRepo | None = None,
        user_repo: UserRepository | None = None,
    ):
        self._template_repo = template_repo or PlanTaskTemplateRepo()
        self._instance_repo = instance_repo or PlanTaskInstanceRepo()
        self._log_repo = log_repo or PlanTaskLogRepo()
        self._deliverable_repo = deliverable_repo or PlanTaskDeliverableRepo()
        self._user_repo = user_repo or UserRepository()

    # ── 辅助方法 ──

    @staticmethod
    def _validate_transition(current_status: str | None, target_status: str) -> None:
        """校验状态流转是否合法。"""
        allowed = ALLOWED_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise InvalidStateTransitionError(
                f"非法状态流转：'{current_status}' → '{target_status}'，"
                f"允许的目标状态：{allowed or '（终态，不可变更）'}"
            )

    @staticmethod
    def _snapshot(instance) -> dict:
        """生成实例关键字段的快照（供日志使用）。"""
        return {
            "title": getattr(instance, "title", ""),
            "status": getattr(instance, "status", ""),
            "executor_id": getattr(instance, "executor_id", ""),
            "initiator_id": getattr(instance, "initiator_id", ""),
            "deadline_at": getattr(instance, "deadline_at", ""),
            "project_id": getattr(instance, "project_id", ""),
            "phase_code": getattr(instance, "phase_code", ""),
        }

    # ── 任务模板管理 ──

    async def create_template(self, cmd: CreateTemplateCommand):
        """创建任务模板。"""
        template_no = PlanTaskTemplateRepo.generate_template_no()
        template = PlanTaskTemplateRepo.create(
            template_no=template_no,
            name=cmd.name,
            description=cmd.description,
            initiator_id=cmd.initiator_id,
            executor_id=cmd.executor_id,
            project_id=cmd.project_id,
            task_type=cmd.task_type or "ONCE",
            deadline_rule=cmd.deadline_rule,
            verification_standard=cmd.verification_standard or "{}",
            workflow_definition_key=cmd.workflow_definition_key,
            status="DRAFT",
            creator_id=cmd.creator_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Template created: %s (%s) type=%s", template_no, cmd.name, cmd.task_type)
        return template

    async def activate_template(self, template_id: str):
        """激活模板（DRAFT → ACTIVE）。"""
        template = PlanTaskTemplateRepo.get_by_id(template_id)
        if template is None:
            raise ValueError(f"模板不存在: {template_id}")
        if template.status != "DRAFT":
            raise ValueError(f"只有 DRAFT 状态的模板可以激活，当前状态: {template.status}")
        return PlanTaskTemplateRepo.update_status(template_id, "ACTIVE")

    async def deactivate_template(self, template_id: str):
        """停用模板（ACTIVE → INACTIVE）。停用后不再生成新的循环实例。"""
        template = PlanTaskTemplateRepo.get_by_id(template_id)
        if template is None:
            raise ValueError(f"模板不存在: {template_id}")
        return PlanTaskTemplateRepo.update_status(template_id, "INACTIVE")

    async def get_template(self, template_id: str):
        """查询模板详情。"""
        return await asyncio.to_thread(PlanTaskTemplateRepo.get_by_id, template_id)

    async def get_active_cycle_templates(self):
        """获取所有 ACTIVE 的循环模板。"""
        return await asyncio.to_thread(PlanTaskTemplateRepo.get_active_cycle_templates)

    # ── 任务实例生命周期 ──

    async def create_instance(self, cmd: CreateInstanceCommand):
        """创建任务实例，含鉴权检查和幂等检查。

        鉴权逻辑：
          - 正常：发起人 permission_level >= 执行人 → 状态 = WAITING
          - 异常：发起人 permission_level < 执行人 → 状态 = ANOMALY_PENDING_REVIEW
            （如业务执行者向主管下达任务，需上级复核）

        合规链校验（SOP-009 §0）：
          - 若指定了 node_id，校验节点存在且状态为 IN_PROGRESS
        """
        # ── 合规链校验：若指定了 node_id，检查节点状态 ──
        if cmd.node_id:
            node_check = await self._check_node_valid(cmd.node_id, cmd.project_id)
            if not node_check["valid"]:
                raise ComplianceChainError(
                    reason=node_check["reason"],
                    guidance=node_check["guidance"],
                )

        # 鉴权检查
        auth_result = await self._authorize_task_creation(
            initiator_id=cmd.initiator_id,
            executor_id=cmd.executor_id,
        )
        if not auth_result.allowed:
            raise ValueError(f"鉴权失败: {auth_result.reason}")

        target_status = auth_result.target_status

        instance_no = PlanTaskInstanceRepo.generate_instance_no()
        # 实例创建 + 日志在同一事务（4.6）
        with get_session() as session:
            instance = PlanTaskInstanceRepo.create(
                session=session,
                instance_no=instance_no,
                template_id=cmd.template_id,
                title=cmd.title,
                description=cmd.description,
                initiator_id=cmd.initiator_id,
                executor_id=cmd.executor_id,
                project_id=cmd.project_id,
                phase_code=cmd.phase_code,
                node_id=cmd.node_id,
                deadline_at=_to_utc_iso(cmd.deadline_at),
                verification_standard=cmd.verification_standard or "{}",
                period_key=cmd.period_key,
                status=target_status,
                anomaly_reason=auth_result.reason if auth_result.anomaly else "",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            # 写状态变更日志
            PlanTaskLogRepo.create(
                instance_id=instance.id,
                from_status=None,
                to_status=target_status,
                operator_id=cmd.initiator_id,
                reason=auth_result.reason if auth_result.anomaly else "任务创建",
                snapshot=self._snapshot(instance),
                session=session,
            )

        logger.info(
            "Instance created: %s status=%s anomaly=%s",
            instance_no, target_status, auth_result.anomaly,
        )
        return instance, auth_result

    async def create_instance_from_template(
        self, cmd: CreateInstanceFromTemplateCommand
    ):
        """从模板创建实例（调度机内部使用，跳过鉴权）。"""
        instance_no = PlanTaskInstanceRepo.generate_instance_no()
        # 实例创建 + 日志在同一事务（4.6）
        with get_session() as session:
            instance = PlanTaskInstanceRepo.create(
                session=session,
                instance_no=instance_no,
                template_id=cmd.template_id,
                title=cmd.title,
                description=cmd.description,
                initiator_id=cmd.initiator_id,
                executor_id=cmd.executor_id,
                project_id=cmd.project_id,
                node_id=cmd.node_id,
                deadline_at=_to_utc_iso(cmd.deadline_at),
                verification_standard=cmd.verification_standard or "{}",
                period_key=cmd.period_key,
                status="WAITING",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            PlanTaskLogRepo.create(
                instance_id=instance.id,
                from_status=None,
                to_status="WAITING",
                operator_id="scheduler",
                reason=f"循环任务自动生成 (template={cmd.template_no}, period={cmd.period_key})",
                snapshot=self._snapshot(instance),
                session=session,
            )

        logger.info("Instance auto-created from template: %s", instance_no)
        return instance

    async def submit_deliverable(self, cmd: SubmitDeliverableCommand):
        """提交成果（WAITING → SUBMITTED）。

        前置合规校验（成果上报合规链 §0）：
          1. 任务实例必须存在
          2. 任务必须关联执行中的全景节点
          任一缺失则返回阻断信息，引导用户补充任务布置或节点启动。
        """
        instance = self._instance_repo.get_by_id(cmd.instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {cmd.instance_id}")

        self._validate_transition(instance.status, "SUBMITTED")

        # ── 合规链校验：任务 → 节点 ──
        compliance_result = await self._validate_compliance_chain(
            instance, is_acceptance_check=cmd.is_acceptance_check,
        )
        if not compliance_result["valid"]:
            raise ComplianceChainError(
                reason=compliance_result["reason"],
                guidance=compliance_result["guidance"],
            )

        # ── 虚拟节点路由：完工确认报告无实体节点时自动归入 VIRTUAL-NODE ──
        routed_to_virtual = compliance_result.get("routed_to_virtual", False)
        if routed_to_virtual:
            instance = self._instance_repo.update_node_id(
                cmd.instance_id, compliance_result["virtual_node_id"],
            )

        # 成果 + 状态变更 + 日志在同一事务（4.6）
        with get_session() as session:
            self._deliverable_repo.create(
                session=session,
                instance_id=cmd.instance_id,
                type=cmd.type or "TEXT",
                content=cmd.content,
                file_url=cmd.file_url,
                file_name=cmd.file_name,
                submitted_by=cmd.submitted_by,
                is_acceptance_check=cmd.is_acceptance_check,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
            instance = self._instance_repo.update_status(
                cmd.instance_id,
                "SUBMITTED",
                session=session,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
            PlanTaskLogRepo.create(
                instance_id=cmd.instance_id,
                from_status="WAITING",
                to_status="SUBMITTED",
                operator_id=cmd.submitted_by,
                reason="执行者提交成果",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    async def confirm_task(self, cmd: ReviewTaskCommand):
        """发起人确认成果（SUBMITTED → CONFIRMED）。"""
        instance = self._instance_repo.get_by_id(cmd.instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {cmd.instance_id}")

        self._validate_transition(instance.status, "CONFIRMED")

        with get_session() as session:
            instance = self._instance_repo.update_status(
                cmd.instance_id,
                "CONFIRMED",
                session=session,
                confirmed_at=datetime.now(timezone.utc).isoformat(),
            )
            PlanTaskLogRepo.create(
                instance_id=cmd.instance_id,
                from_status="SUBMITTED",
                to_status="CONFIRMED",
                operator_id=cmd.operator_id,
                reason=cmd.reason or "发起人确认成果",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    async def return_task(self, cmd: ReviewTaskCommand):
        """发起人退回成果（SUBMITTED → RETURNED）。"""
        instance = self._instance_repo.get_by_id(cmd.instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {cmd.instance_id}")

        self._validate_transition(instance.status, "RETURNED")

        with get_session() as session:
            instance = self._instance_repo.update_status(
                cmd.instance_id, "RETURNED", session=session,
            )
            PlanTaskLogRepo.create(
                instance_id=cmd.instance_id,
                from_status="SUBMITTED",
                to_status="RETURNED",
                operator_id=cmd.operator_id,
                reason=cmd.reason or "发起人退回修改",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    async def resubmit_deliverable(self, cmd: SubmitDeliverableCommand):
        """执行者修改后重新提交（RETURNED → SUBMITTED）。"""
        instance = self._instance_repo.get_by_id(cmd.instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {cmd.instance_id}")

        self._validate_transition(instance.status, "SUBMITTED")

        # 新成果 + 状态变更 + 日志在同一事务（4.6）
        with get_session() as session:
            self._deliverable_repo.create(
                session=session,
                instance_id=cmd.instance_id,
                type=cmd.type or "TEXT",
                content=cmd.content,
                file_url=cmd.file_url,
                file_name=cmd.file_name,
                submitted_by=cmd.submitted_by,
                is_acceptance_check=cmd.is_acceptance_check,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
            instance = self._instance_repo.update_status(
                cmd.instance_id,
                "SUBMITTED",
                session=session,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
            PlanTaskLogRepo.create(
                instance_id=cmd.instance_id,
                from_status="RETURNED",
                to_status="SUBMITTED",
                operator_id=cmd.submitted_by,
                reason="执行者修改后重新提交",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    async def archive_task(self, instance_id: str):
        """归档已确认任务（CONFIRMED → ARCHIVED）。"""
        instance = self._instance_repo.get_by_id(instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {instance_id}")

        self._validate_transition(instance.status, "ARCHIVED")

        with get_session() as session:
            instance = self._instance_repo.update_status(
                instance_id,
                "ARCHIVED",
                session=session,
                archived_at=datetime.now(timezone.utc).isoformat(),
            )
            PlanTaskLogRepo.create(
                instance_id=instance_id,
                from_status="CONFIRMED",
                to_status="ARCHIVED",
                operator_id="scheduler",
                reason="调度机自动归档",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    async def cancel_instance(self, instance_id: str, reason: str = "", operator_id: str = ""):
        """取消任务（* → CANCELLED）。"""
        instance = self._instance_repo.get_by_id(instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {instance_id}")

        from_status = instance.status  # 更新前捕获（修正审计日志 from_status 丢失，5.1）
        self._validate_transition(instance.status, "CANCELLED")

        with get_session() as session:
            instance = self._instance_repo.update_status(
                instance_id, "CANCELLED", session=session,
            )
            PlanTaskLogRepo.create(
                instance_id=instance_id,
                from_status=from_status,
                to_status="CANCELLED",
                operator_id=operator_id,
                reason=reason or "任务取消",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    async def mark_unscheduled(self, instance_id: str, reason: str = ""):
        """标记为计划外事件（不流转状态，仅设标记）。"""
        instance = self._instance_repo.get_by_id(instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {instance_id}")

        current_status = instance.status
        with get_session() as session:
            instance = self._instance_repo.update_status(
                instance_id,
                current_status,  # 保持当前状态
                session=session,
                is_unscheduled=True,
                anomaly_reason=reason,
            )
            PlanTaskLogRepo.create(
                instance_id=instance_id,
                from_status=current_status,
                to_status=current_status,
                operator_id="system",
                reason=f"标记为计划外事件: {reason}",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    # ── 异常复核（P2）──

    async def review_anomaly_task(self, cmd: ReviewAnomalyCommand):
        """上级复核反向下达的异常任务。

        approve → ANOMALY_PENDING_REVIEW → WAITING（确认下发）
        reject  → ANOMALY_PENDING_REVIEW → CANCELLED（终止退回）
        """
        instance = self._instance_repo.get_by_id(cmd.instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {cmd.instance_id}")

        if instance.status != "ANOMALY_PENDING_REVIEW":
            raise ValueError(f"只有 ANOMALY_PENDING_REVIEW 状态可复核，当前: {instance.status}")

        if cmd.action == "approve":
            target = "WAITING"
            reason = cmd.reason or "上级复核通过，确认下发"
        elif cmd.action == "reject":
            target = "CANCELLED"
            reason = cmd.reason or "上级复核驳回，终止任务"
        else:
            raise ValueError(f"不支持的复核动作: {cmd.action}")

        self._validate_transition(instance.status, target)
        with get_session() as session:
            instance = self._instance_repo.update_status(
                cmd.instance_id, target, session=session,
            )
            PlanTaskLogRepo.create(
                instance_id=cmd.instance_id,
                from_status="ANOMALY_PENDING_REVIEW",
                to_status=target,
                operator_id=cmd.reviewer_id,
                reason=reason,
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    # ── 执行人升级（P2）──

    async def escalate_to_supervisor(self, cmd: EscalateTaskCommand):
        """执行人离职/失能，升级给顺位上级。

        不流转状态，仅变更 executor_id 并记录升级信息。
        """
        instance = self._instance_repo.get_by_id(cmd.instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {cmd.instance_id}")

        if not instance.executor_id:
            raise ValueError("任务未指定执行人，无法升级")

        # 获取执行人的直接上级
        executor = await asyncio.to_thread(self._user_repo.get_by_id, instance.executor_id)
        if executor is None:
            raise ValueError(f"执行人不存在: {instance.executor_id}")

        supervisor_id = getattr(executor, "supervisor_id", "") if hasattr(executor, "supervisor_id") else ""
        if not supervisor_id:
            raise ValueError(f"执行人 {instance.executor_id} 无直接上级，无法升级")

        original_executor = instance.executor_id
        current_status = instance.status

        with get_session() as session:
            instance = self._instance_repo.update_status(
                cmd.instance_id,
                current_status,  # 保持当前状态
                session=session,
                executor_id=supervisor_id,
                original_executor_id=original_executor,
                escalation_reason=cmd.reason,
                escalated_at=datetime.now(timezone.utc).isoformat(),
            )
            PlanTaskLogRepo.create(
                instance_id=cmd.instance_id,
                from_status=current_status,
                to_status=current_status,
                operator_id="scheduler",
                reason=f"执行人升级: {original_executor} → {supervisor_id}，原因: {cmd.reason}",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    # ── 人工设定截止时间 ──

    async def set_deadline_manually(
        self, instance_id: str, deadline_at: str, operator_id: str = ""
    ):
        """人工设定截止时间（LLM 推算失败时的手动介入）。

        执行后自动清除 llm_calculation_failed 标记，
        若实例状态不是终态则恢复为 WAITING（若当前是新建/异常状态）。
        """
        instance = self._instance_repo.get_by_id(instance_id)
        if instance is None:
            raise TaskNotFoundError(f"任务实例不存在: {instance_id}")

        current_status = instance.status
        update_kwargs = {
            "deadline_at": _to_utc_iso(deadline_at),
            "llm_calculation_failed": False,
            "llm_failure_notified": False,
        }
        # 如果当前不在正常状态流转中且非终态，则恢复为 WAITING
        if current_status not in ("ARCHIVED", "CANCELLED") and not current_status.startswith("SUBMITTED"):
            target_status = "WAITING"
        else:
            target_status = current_status

        with get_session() as session:
            instance = self._instance_repo.update_status(
                instance_id,
                target_status,
                session=session,
                **update_kwargs,
            )
            PlanTaskLogRepo.create(
                instance_id=instance_id,
                from_status=current_status,
                to_status=instance.status,
                operator_id=operator_id or "initiator",
                reason=f"人工设定截止时间: {deadline_at}",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance

    # ── 计划外事件匹配 ──

    async def match_or_create_unscheduled(
        self,
        user_id: str,
        upload_context: dict,
        deliverable: SubmitDeliverableCommand,
    ):
        """上传成果时尝试匹配挂起的计划任务，匹配失败则创建计划外实例。

        Args:
            user_id: 上传者（执行人）ID
            upload_context: 包含 project_id, phase_code, keywords 等
            deliverable: 提交的成果数据

        Returns:
            (instance, MatchResult): 匹配到的实例（或新建的计划外实例）和匹配结果
        """
        project_id = upload_context.get("project_id", "")
        phase_code = upload_context.get("phase_code", "")

        # 1. 精确匹配：同项目 + 同阶段 + 同执行人 + 时间窗口
        candidates = self._instance_repo.find_match_candidates(
            project_id=project_id,
            phase_code=phase_code,
            executor_id=user_id,
            time_window_days=7,
        )

        if candidates:
            # 有精确匹配 → 直接使用第一个
            matched = candidates[0]
            deliverable.instance_id = matched.id
            await self.submit_deliverable(deliverable)
            return matched, MatchResult(
                matched=True,
                instance_id=matched.id,
                instance_no=matched.instance_no,
                confidence="exact",
            )

        # 2. 无精确匹配 → 创建计划外实例（创建+日志+成果+提交+日志同事务，4.6）
        instance_no = self._instance_repo.generate_instance_no()
        with get_session() as session:
            instance = self._instance_repo.create(
                session=session,
                instance_no=instance_no,
                title=deliverable.content[:100] if deliverable.content else "计划外任务",
                description=deliverable.content or "",
                initiator_id=user_id,  # 上传者自建
                executor_id=user_id,    # 上传者自己执行
                project_id=project_id,
                phase_code=phase_code,
                status="WAITING",
                is_unscheduled=True,
                anomaly_reason="上传成果时未匹配到挂起的计划任务",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            PlanTaskLogRepo.create(
                instance_id=instance.id,
                from_status=None,
                to_status="WAITING",
                operator_id=user_id,
                reason="计划外事件：上传成果时未匹配到挂起的计划任务",
                snapshot=self._snapshot(instance),
                session=session,
            )

            # 保存成果
            self._deliverable_repo.create(
                session=session,
                instance_id=instance.id,
                type=deliverable.type or "TEXT",
                content=deliverable.content,
                file_url=deliverable.file_url,
                file_name=deliverable.file_name,
                submitted_by=user_id,
                is_acceptance_check=getattr(deliverable, "is_acceptance_check", False),
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )

            # 自动提交
            instance = self._instance_repo.update_status(
                instance.id,
                "SUBMITTED",
                session=session,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )

            PlanTaskLogRepo.create(
                instance_id=instance.id,
                from_status="WAITING",
                to_status="SUBMITTED",
                operator_id=user_id,
                reason="计划外事件自动提交",
                snapshot=self._snapshot(instance),
                session=session,
            )

        return instance, MatchResult(
            matched=False,
            instance_id=instance.id,
            instance_no=instance_no,
            confidence="none",
        )

    # ── 鉴权逻辑 ──

    async def _authorize_task_creation(
        self, initiator_id: str, executor_id: str
    ) -> AuthCheckResult:
        """检查发起人是否有权限向执行人下达任务。

        正常流程：发起人 permission_level >= 执行人 permission_level → WAITING
        异常场景：发起人 permission_level < 执行人 permission_level
          → ANOMALY_PENDING_REVIEW（如业务执行者向主管下达任务，需上级复核）
        """
        if not initiator_id or not executor_id:
            # 缺少信息时默认放行
            return AuthCheckResult(allowed=True, anomaly=False, target_status="WAITING")

        initiator = await asyncio.to_thread(self._user_repo.get_by_id, initiator_id)
        executor = await asyncio.to_thread(self._user_repo.get_by_id, executor_id)

        if initiator is None or executor is None:
            return AuthCheckResult(allowed=True, anomaly=False, target_status="WAITING")

        initiator_level = getattr(initiator, "permission_level", 0) or 0
        executor_level = getattr(executor, "permission_level", 0) or 0

        if initiator_level < executor_level:
            supervisor_id = getattr(initiator, "supervisor_id", "") if hasattr(initiator, "supervisor_id") else ""
            return AuthCheckResult(
                allowed=True,
                anomaly=True,
                target_status="ANOMALY_PENDING_REVIEW",
                reason=(
                    f"发起人权限等级（{initiator_level}）低于执行人（{executor_level}），"
                    f"任务已标记为异常，需上级复核后下发"
                ),
                supervisor_id=supervisor_id,
            )

        return AuthCheckResult(allowed=True, anomaly=False, target_status="WAITING")

    # ── 合规链校验（SOP-009 §0）──

    async def _check_node_valid(self, node_id: str, project_id: str = "") -> dict:
        """校验全景节点是否存在且处于 IN_PROGRESS 状态（task creation 时用）。

        Returns:
            {"valid": bool, "reason": str, "guidance": str}
        """
        from ..repositories.node_repo import NodeRepository

        node = await asyncio.to_thread(
            NodeRepository.get_by_node_id, node_id,
            project_id=project_id if project_id else None,
        )
        if node is None:
            return {
                "valid": False,
                "reason": f"全景节点 {node_id} 不存在",
                "guidance": "请联系项目负责人确认节点编号，或通过 create_node 创建对应全景节点后再布置任务。",
            }
        node_status = getattr(node, "status", "")
        if node_status not in ("IN_PROGRESS",):
            return {
                "valid": False,
                "reason": f"全景节点 {node_id} 当前状态为「{node_status}」，非「IN_PROGRESS」，无法挂载任务",
                "guidance": "请联系部门负责人审批并启动该全景节点后，再布置任务。",
            }
        return {"valid": True, "reason": "", "guidance": ""}

    async def _validate_compliance_chain(self, instance, is_acceptance_check: bool = False) -> dict:
        """校验成果上报合规链：任务 → 执行中的全景节点。

        规则：
          1. 若任务实例有 node_id → 直接校验该节点状态
          2. 若任务来自模板，且模板有 required_node_ids → 校验模板关联节点
          3. 若均无：
             a. 完工确认报告（is_acceptance_check=True）→ 自动路由到虚拟节点
             b. 普通成果 → 阻断，引导用户补充节点关联

        Returns:
            {"valid": bool, "reason": str, "guidance": str,
             "routed_to_virtual": bool, "virtual_node_id": str}
        """
        from ..repositories.node_repo import NodeRepository

        VIRTUAL_NODE_ID = "VIRTUAL-NODE"

        # 1. 检查实例直连的 node_id
        instance_node_id = getattr(instance, "node_id", "") or ""
        if instance_node_id:
            node = await asyncio.to_thread(
                NodeRepository.get_by_node_id, instance_node_id,
                project_id=getattr(instance, "project_id", None),
            )
            if node is None:
                return {
                    "valid": False,
                    "reason": f"任务关联的全景节点 {instance_node_id} 不存在",
                    "guidance": "请联系项目负责人确认节点编号是否正确，或通过 create_node 创建对应全景节点。",
                }
            node_status = getattr(node, "status", "")
            if node_status not in ("IN_PROGRESS",):
                return {
                    "valid": False,
                    "reason": f"全景节点 {instance_node_id} 当前状态为「{node_status}」，非「IN_PROGRESS」",
                    "guidance": "请联系部门负责人审批并启动该全景节点（状态流转至 IN_PROGRESS），再提交成果。",
                }
            return {"valid": True, "reason": "", "guidance": ""}

        # 2. 检查模板的 required_node_ids
        template_id = getattr(instance, "template_id", None)
        if template_id:
            template = await asyncio.to_thread(
                PlanTaskTemplateRepo.get_by_id, template_id
            )
            if template:
                try:
                    required_ids = json.loads(getattr(template, "required_node_ids", "[]") or "[]")
                except (json.JSONDecodeError, TypeError):
                    required_ids = []
                if required_ids:
                    # 取第一个节点校验
                    first_node_id = required_ids[0]
                    node = await asyncio.to_thread(
                        NodeRepository.get_by_node_id, first_node_id,
                        project_id=getattr(instance, "project_id", None),
                    )
                    if node is None:
                        return {
                            "valid": False,
                            "reason": f"任务模板关联的全景节点 {first_node_id} 不存在",
                            "guidance": "请联系项目负责人确认节点是否存在，或重新指定有效的全景节点。",
                        }
                    node_status = getattr(node, "status", "")
                    if node_status not in ("IN_PROGRESS",):
                        return {
                            "valid": False,
                            "reason": f"全景节点 {first_node_id} 当前状态为「{node_status}」，非「IN_PROGRESS」",
                            "guidance": "请联系部门负责人审批并启动该全景节点（状态流转至 IN_PROGRESS），再提交成果。",
                        }
                    return {"valid": True, "reason": "", "guidance": ""}

        # 3. 无节点关联 → 完工确认报告走虚拟节点，普通成果阻断
        if is_acceptance_check:
            # 完工确认报告自动归入虚拟节点，等待二次分配
            return {
                "valid": True,
                "reason": "",
                "guidance": (
                    "当前完工确认报告暂无对应实体节点，已自动归入虚拟节点（VIRTUAL-NODE）。\n"
                    "管理员将定期处理虚拟节点中的数据，在实体节点建立后执行二次分配。"
                ),
                "routed_to_virtual": True,
                "virtual_node_id": VIRTUAL_NODE_ID,
            }

        return {
            "valid": False,
            "reason": "当前任务未关联任何全景节点，不符合成果上报合规要求",
            "guidance": (
                "员工产出的工作成果必须来源于已分配的任务，而任务必须挂载于执行中的全景节点。\n"
                "请先联系有权限的项目负责人，通过 record_plan_task 布置任务时指定关联的全景节点，\n"
                "并确保该节点已通过审批处于「IN_PROGRESS」状态，再重新提交成果。"
            ),
        }

    # ── 查询方法 ──
    # 注：Repository 为同步实现，这里用 asyncio.to_thread 包裹，
    # 避免阻塞 asyncio 事件循环（调度循环与消息处理共享同一循环）。

    async def get_by_id(self, instance_id: str):
        """查询实例详情。"""
        return await asyncio.to_thread(self._instance_repo.get_by_id, instance_id)

    async def get_by_period_key(self, template_id: str, period_key: str):
        """幂等查询。"""
        return await asyncio.to_thread(
            self._instance_repo.get_by_period_key, template_id, period_key
        )

    async def find_by_status(self, status: str, limit: int = 100):
        """按状态查询实例列表。"""
        return await asyncio.to_thread(self._instance_repo.find_by_status, status, limit)

    async def find_overdue(self, now_iso: str, limit: int = 100):
        """查询超时任务。"""
        return await asyncio.to_thread(self._instance_repo.find_overdue, now_iso, limit)

    async def find_long_overdue(self, now_iso: str, overdue_days: int, limit: int = 50):
        """查询超期 N 天以上的 WAITING 任务（自动升级用，§5.3）。"""
        return await asyncio.to_thread(
            self._instance_repo.find_long_overdue, now_iso, overdue_days, limit
        )

    async def find_near_deadline(self, now_iso: str, before_minutes: int = 60, limit: int = 100):
        """查询临近截止任务。"""
        return await asyncio.to_thread(
            self._instance_repo.find_near_deadline, now_iso, before_minutes, limit
        )

    async def find_templates_llm_failed_pending_notification(self, limit: int = 50):
        """查询 LLM 推算失败且尚未通知的模板（§2.4，模板级）。"""
        return await asyncio.to_thread(
            PlanTaskTemplateRepo.find_templates_llm_failed_pending_notification, limit
        )

    async def mark_template_llm_failed(self, template_id: str) -> None:
        """标记模板 LLM 推算失败。"""
        await asyncio.to_thread(PlanTaskTemplateRepo.mark_template_llm_failed, template_id)

    async def clear_template_llm_failed(self, template_id: str) -> None:
        """清除模板 LLM 失败标记（推算成功时自愈）。"""
        await asyncio.to_thread(PlanTaskTemplateRepo.clear_template_llm_failed, template_id)

    async def mark_template_llm_failure_notified(self, template_id: str) -> None:
        """标记模板 LLM 失败已通知发起人。"""
        await asyncio.to_thread(PlanTaskTemplateRepo.mark_template_llm_failure_notified, template_id)

    async def find_recent_archived_cycle_tasks(self, limit: int = 50):
        """查询最近归档的循环任务实例。"""
        return await asyncio.to_thread(
            self._instance_repo.find_recent_archived_cycle_tasks, limit
        )

    async def find_overdue_cycle_instances(self, now_iso: str, limit: int = 50):
        """查询已过期但未归档/取消的循环任务（容错补齐）。"""
        return await asyncio.to_thread(
            self._instance_repo.find_overdue_cycle_instances, now_iso, limit
        )

    async def count_by_status(self) -> dict[str, int]:
        """按状态统计。"""
        return await asyncio.to_thread(self._instance_repo.count_by_status)

    async def get_instance_logs(self, instance_id: str, limit: int = 100):
        """查询实例的状态变更日志。"""
        return await asyncio.to_thread(PlanTaskLogRepo.find_by_instance, instance_id, limit)

    async def get_instance_deliverables(self, instance_id: str, limit: int = 100):
        """查询实例的成果列表。"""
        return await asyncio.to_thread(
            PlanTaskDeliverableRepo.find_by_instance, instance_id, limit
        )

    async def set_reminded_at(self, instance_id: str, reminded_at: str):
        """更新最后提醒时间。"""
        await asyncio.to_thread(self._instance_repo.set_reminded_at, instance_id, reminded_at)

    async def update_workflow_instance_id(self, instance_id: str, workflow_instance_id: str):
        """回写工作流实例 ID。"""
        await asyncio.to_thread(
            self._instance_repo.update_workflow_instance_id, instance_id, workflow_instance_id
        )

    async def find_by_executor(self, executor_id: str, status: str | None = None, limit: int = 100):
        """按执行人查询任务。"""
        return await asyncio.to_thread(
            self._instance_repo.find_by_executor, executor_id, status, limit
        )

    async def find_by_initiator(self, initiator_id: str, status: str | None = None, limit: int = 100):
        """按发起人查询任务。"""
        return await asyncio.to_thread(
            self._instance_repo.find_by_initiator, initiator_id, status, limit
        )
