"""计划任务 Application 层 —— 编排 Service 调用 + 生成回复文本。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..services.plan_task_service import PlanTaskService
    from ..services.plan_task_commands import (
        CreateTemplateCommand,
        CreateInstanceCommand,
        SubmitDeliverableCommand,
        ReviewTaskCommand,
    )

logger = logging.getLogger("emily.plan_task_app")


class PlanTaskApplication:
    """计划任务 Application —— 编排 Service 调用并生成面向用户的回复文本。"""

    def __init__(self, service: "PlanTaskService", workflow_integrator=None):
        self._service = service
        self._workflow_integrator = workflow_integrator

    # ── 创建任务 ──

    async def create_task_from_command(
        self,
        cmd: "CreateInstanceCommand",
    ) -> dict:
        """从命令创建计划任务实例。

        Returns:
            {"success": bool, "object_id": str, "instance_no": str,
             "status": str, "anomaly": bool, "reply": str}
        """
        try:
            instance, auth_result = await self._service.create_instance(cmd)

            if auth_result.anomaly:
                reply = (
                    f"✅ 计划任务「{cmd.title}」已创建（编号：{instance.instance_no}），"
                    f"但已标记为异常状态，等待上级复核。\n"
                    f"原因：{auth_result.reason}"
                )
            else:
                reply = (
                    f"✅ 计划任务「{cmd.title}」已创建（编号：{instance.instance_no}），"
                    f"状态：等待执行。截止时间：{cmd.deadline_at or '待定'}。"
                )

            return {
                "success": True,
                "object_id": instance.id,
                "instance_no": instance.instance_no,
                "status": instance.status,
                "anomaly": auth_result.anomaly,
                "reply": reply,
            }
        except ValueError as e:
            return {
                "success": False,
                "object_id": "",
                "instance_no": "",
                "status": "",
                "anomaly": False,
                "reply": f"❌ 创建失败：{e}",
            }

    async def create_template_from_command(
        self,
        cmd: "CreateTemplateCommand",
    ) -> dict:
        """创建任务模板。

        Returns:
            {"success": bool, "object_id": str, "template_no": str, "reply": str}
        """
        try:
            template = await self._service.create_template(cmd)
            return {
                "success": True,
                "object_id": template.id,
                "template_no": template.template_no,
                "reply": (
                    f"✅ 任务模板「{cmd.name}」已创建（编号：{template.template_no}），"
                    f"状态：草稿。请确认后激活模板以开始使用。"
                ),
            }
        except Exception as e:
            logger.error("Failed to create template: %s", e)
            return {
                "success": False,
                "object_id": "",
                "template_no": "",
                "reply": f"❌ 创建模板失败：{e}",
            }

    async def activate_template(self, template_id: str) -> dict:
        """激活任务模板（DRAFT → ACTIVE）。激活后调度机自动生成循环实例。"""
        try:
            template = await self._service.activate_template(template_id)
            return {
                "success": True,
                "object_id": template.id,
                "template_no": template.template_no,
                "reply": f"✅ 模板「{template.name}」已激活。",
            }
        except Exception as e:
            logger.error("Failed to activate template: %s", e)
            return {"success": False, "reply": f"❌ 激活模板失败：{e}"}

    # ── 提交成果 ──

    async def submit_task(self, cmd: "SubmitDeliverableCommand") -> dict:
        """提交计划任务成果。"""
        try:
            instance = await self._service.submit_deliverable(cmd)
            return {
                "success": True,
                "object_id": instance.id,
                "instance_no": instance.instance_no,
                "status": instance.status,
                "reply": (
                    f"✅ 成果已提交（任务编号：{instance.instance_no}），"
                    f"等待发起人确认。"
                ),
            }
        except ValueError as e:
            return {
                "success": False,
                "object_id": "",
                "instance_no": "",
                "status": "",
                "reply": f"❌ 提交失败：{e}",
            }

    # ── 审核任务 ──

    async def review_task(self, cmd: "ReviewTaskCommand") -> dict:
        """审核任务（确认或退回）。"""
        try:
            if cmd.action == "confirm":
                instance = await self._service.confirm_task(cmd)
                # 确认后触发关联工作流（best-effort，失败不影响确认结果，§4.4）
                if self._workflow_integrator is not None:
                    try:
                        await self._workflow_integrator.start_workflow_after_confirmation(instance.id)
                    except Exception as e:
                        logger.warning("start_workflow_after_confirmation failed: %s", e)
                reply = (
                    f"✅ 任务「{instance.title}」（{instance.instance_no}）已确认，"
                    f"待归档入库。"
                )
            elif cmd.action == "return":
                instance = await self._service.return_task(cmd)
                reason_text = f"原因：{cmd.reason}" if cmd.reason else ""
                reply = (
                    f"↩️ 任务「{instance.title}」（{instance.instance_no}）已退回执行者修改。"
                    f"{reason_text}"
                )
            else:
                return {
                    "success": False,
                    "reply": f"❌ 不支持的审核动作：{cmd.action}",
                }

            return {
                "success": True,
                "object_id": instance.id,
                "instance_no": instance.instance_no,
                "status": instance.status,
                "reply": reply,
            }
        except ValueError as e:
            return {
                "success": False,
                "reply": f"❌ 审核失败：{e}",
            }

    # ── 查询任务 ──

    async def query_my_tasks(
        self, user_id: str, role: str = "executor", status: str | None = None, limit: int = 20
    ) -> dict:
        """查询我的任务列表。

        Args:
            user_id: 用户 ID
            role: "executor"（我是执行人）或 "initiator"（我是发起人）
            status: 可选的状态过滤
            limit: 返回数量上限

        Returns:
            {"success": bool, "tasks": list[dict], "count": int, "reply": str}
        """
        try:
            if role == "executor":
                instances = await self._service.find_by_executor(user_id, status, limit)
                role_text = "待你执行"
            else:
                instances = await self._service.find_by_initiator(user_id, status, limit)
                role_text = "你发起的"

            tasks = []
            for inst in instances:
                tasks.append({
                    "instance_no": inst.instance_no,
                    "title": inst.title,
                    "status": inst.status,
                    "deadline_at": inst.deadline_at or "",
                    "project_id": inst.project_id or "",
                })

            status_text = ""
            if status:
                status_text = f"（状态：{status}）"

            return {
                "success": True,
                "tasks": tasks,
                "count": len(tasks),
                "reply": (
                    f"📋 {role_text}的任务{status_text}共 {len(tasks)} 项：\n"
                    + "\n".join(
                        f"  · [{t['status']}] {t['title']}（{t['instance_no']}）"
                        + (f" 截止 {t['deadline_at'][:10]}" if t['deadline_at'] else "")
                        for t in tasks[:10]
                    )
                    + ("\n  ..." if len(tasks) > 10 else "")
                ),
            }
        except Exception as e:
            logger.error("Failed to query tasks: %s", e)
            return {"success": False, "tasks": [], "count": 0, "reply": f"❌ 查询失败：{e}"}

    async def query_task_detail(self, instance_id: str) -> dict:
        """查询任务详情（含日志和成果）。"""
        try:
            instance = await self._service.get_by_id(instance_id)
            if instance is None:
                return {"success": False, "reply": "任务不存在"}

            logs = await self._service.get_instance_logs(instance_id)
            deliverables = await self._service.get_instance_deliverables(instance_id)

            log_list = [
                {
                    "from": log.from_status,
                    "to": log.to_status,
                    "reason": log.reason,
                    "time": log.created_at,
                }
                for log in logs
            ]

            d_list = [
                {
                    "type": d.type,
                    "content": d.content[:200] if d.content else "",
                    "submitted_by": d.submitted_by,
                    "submitted_at": d.submitted_at,
                }
                for d in deliverables
            ]

            return {
                "success": True,
                "instance": {
                    "instance_no": instance.instance_no,
                    "title": instance.title,
                    "description": instance.description,
                    "status": instance.status,
                    "deadline_at": instance.deadline_at or "",
                    "is_unscheduled": instance.is_unscheduled,
                    "anomaly_reason": instance.anomaly_reason,
                },
                "logs": log_list,
                "deliverables": d_list,
                "reply": (
                    f"📋 任务详情 [{instance.status}]\n"
                    f"编号：{instance.instance_no}\n"
                    f"标题：{instance.title}\n"
                    f"截止：{instance.deadline_at or '待定'}\n"
                    f"描述：{instance.description or '无'}\n"
                    + (f"⚠ 计划外事件：{instance.anomaly_reason}\n" if instance.is_unscheduled else "")
                    + f"\n变更记录（{len(log_list)} 条）：\n"
                    + "\n".join(f"  · {l['time'][:19]} {l['from'] or '创建'} → {l['to']}: {l['reason']}" for l in log_list[:10])
                ),
            }
        except Exception as e:
            logger.error("Failed to query task detail: %s", e)
            return {"success": False, "reply": f"❌ 查询失败：{e}"}

    # ── 统计查询 ──

    async def get_task_summary(self) -> dict:
        """各状态任务数 + 逾期数统计。"""
        try:
            counts = await self._service.count_by_status()
            return {
                "success": True,
                "summary": counts,
                "total": sum(counts.values()),
            }
        except Exception as e:
            return {"success": False, "summary": {}, "total": 0}

    async def get_today_due_tasks(self, limit: int = 50) -> dict:
        """今日待提交任务（按北京时间"今日"过滤，§5.5）。"""
        try:
            from datetime import datetime, timezone, timedelta
            from ..repositories.plan_task_repo import _parse_iso_utc

            BEIJING = timezone(timedelta(hours=8))
            now_local = datetime.now(BEIJING)
            today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1)

            instances = await self._service.find_by_status("WAITING", limit)
            today_tasks = []
            for inst in instances:
                dl = _parse_iso_utc(getattr(inst, "deadline_at", "") or "")
                if dl is None:
                    continue
                if today_start <= dl.astimezone(BEIJING) < today_end:
                    today_tasks.append({
                        "instance_no": inst.instance_no,
                        "title": inst.title,
                        "executor_id": inst.executor_id,
                        "deadline_at": inst.deadline_at,
                    })
            return {"success": True, "tasks": today_tasks, "count": len(today_tasks)}
        except Exception as e:
            logger.error("Failed to get today due tasks: %s", e)
            return {"success": False, "tasks": [], "count": 0}
