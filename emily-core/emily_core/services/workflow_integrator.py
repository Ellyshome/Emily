"""工作流集成模块 —— 计划任务与工作流系统的集成层。

职责：
  - 计划任务确认后启动后续工作流
  - 关联工作流实例 ID 回写到计划任务
  - 工作流状态变更回调（反向更新计划任务状态）
  - 启动失败自动重试（下次调度 tick 重试）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .plan_task_service import PlanTaskService

logger = logging.getLogger("emily.workflow_integrator")


class WorkflowIntegrator:
    """计划任务与工作流系统的集成层。

    采用"确认后启动工作流"模式：
      计划任务确认 → 启动后续一般工作流 → 工作流实例 ID 关联到计划任务
    """

    def __init__(
        self,
        workflow_client=None,       # 现有工作流系统的 API 客户端
        plan_task_service: "PlanTaskService | None" = None,
    ):
        self._workflow_client = workflow_client
        self._plan_task_service = plan_task_service

    async def start_workflow_after_confirmation(
        self,
        plan_task_instance_id: str,
    ) -> str:
        """计划任务确认后，启动后续工作流。

        在 Service.confirm_task() 之后调用。
        返回：工作流实例 ID（失败时返回空字符串）
        """
        if self._plan_task_service is None:
            logger.warning("WorkflowIntegrator: plan_task_service not set, skipping")
            return ""

        if self._workflow_client is None:
            logger.debug("WorkflowIntegrator: no workflow_client, skipping")
            return ""

        try:
            # 获取计划任务实例
            instance = await self._plan_task_service.get_by_id(plan_task_instance_id)
            if instance is None:
                logger.warning("WorkflowIntegrator: instance %s not found", plan_task_instance_id)
                return ""

            # 获取关联模板
            template_id = getattr(instance, "template_id", "")
            if not template_id:
                logger.debug("WorkflowIntegrator: instance %s has no template, skipping",
                            getattr(instance, "instance_no", instance.id))
                return ""

            template = await self._plan_task_service.get_template(template_id)
            if template is None:
                logger.debug("WorkflowIntegrator: template %s not found, skipping", template_id)
                return ""

            workflow_def_key = getattr(template, "workflow_definition_key", "")
            if not workflow_def_key:
                # 无关联工作流，不需要启动
                return ""

            # 获取成果快照
            deliverables = await self._plan_task_service.get_instance_deliverables(
                plan_task_instance_id
            )

            # 启动工作流实例
            workflow_instance_id = await self._workflow_client.start_instance(
                definition_key=workflow_def_key,
                context={
                    "plan_task_instance_id": instance.id,
                    "plan_task_instance_no": getattr(instance, "instance_no", ""),
                    "title": getattr(instance, "title", ""),
                    "initiator_id": getattr(instance, "initiator_id", ""),
                    "executor_id": getattr(instance, "executor_id", ""),
                    "project_id": getattr(instance, "project_id", ""),
                    "phase_code": getattr(instance, "phase_code", ""),
                    "deliverables": [
                        {
                            "type": d.type,
                            "content": (d.content or "")[:500],
                            "file_url": d.file_url,
                            "submitted_by": d.submitted_by,
                        }
                        for d in (deliverables or [])
                    ],
                },
            )

            # 回写工作流实例 ID 到计划任务
            await self._plan_task_service.update_workflow_instance_id(
                plan_task_instance_id, workflow_instance_id
            )

            logger.info(
                "WorkflowIntegrator: started workflow '%s' for task %s → workflow_id=%s",
                workflow_def_key,
                getattr(instance, "instance_no", instance.id),
                workflow_instance_id,
            )
            return workflow_instance_id

        except Exception as e:
            logger.error(
                "WorkflowIntegrator: failed to start workflow for task %s: %s",
                plan_task_instance_id, e,
                exc_info=True,
            )
            # 不抛异常，下次调度 tick 自动重试
            return ""

    async def on_workflow_status_updated(
        self,
        workflow_instance_id: str,
        status: str,
        context: dict | None = None,
    ) -> None:
        """工作流状态变更回调（反向更新计划任务）。

        当前实现为预留接口，后续可根据工作流状态：
          - 工作流完成 → 可能触发计划任务归档
          - 工作流异常 → 通知计划任务发起人
        """
        logger.debug(
            "WorkflowIntegrator callback: workflow=%s status=%s context=%s",
            workflow_instance_id, status, context or {},
        )
        # 预留：根据工作流状态反向更新计划任务
        # 例如：工作流完成后自动触发计划任务归档
        pass

    async def retry_pending_workflow_starts(self, limit: int = 50) -> int:
        """重试所有未成功启动工作流的计划任务（由调度机定期调用）。

        扫描 status=CONFIRMED 且 workflow_instance_id 为空的实例，重新尝试启动工作流。

        Returns:
            成功重试的数量
        """
        if self._workflow_client is None:
            return 0

        retried = 0
        try:
            # 查询已确认但未关联工作流的实例
            confirmed = await self._plan_task_service.find_by_status("CONFIRMED", limit=limit)
            for inst in confirmed:
                if getattr(inst, "workflow_instance_id", ""):
                    continue  # 已有工作流关联，跳过

                wf_id = await self.start_workflow_after_confirmation(inst.id)
                if wf_id:
                    retried += 1

        except Exception as e:
            logger.error("WorkflowIntegrator retry failed: %s", e, exc_info=True)

        if retried:
            logger.info("WorkflowIntegrator: retried %d pending workflow starts", retried)
        return retried
