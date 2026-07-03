"""计划任务 Tool Handler —— 注册到 BusinessFlowToolRegistry。

四个工具：
  - record_plan_task：创建计划任务（一次性或循环模板）
  - submit_plan_task：提交计划任务成果
  - review_plan_task：审核计划任务成果（确认或退回）
  - query_plan_tasks：查询计划任务列表
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("emily.plan_task_tool")


# ══════════════════════════════════════════════════════════════════════════════
# JSON Schema 定义
# ══════════════════════════════════════════════════════════════════════════════

_RECORD_PLAN_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "任务标题，简洁描述任务内容",
        },
        "description": {
            "type": "string",
            "description": "任务详细描述，包括具体要求",
        },
        "executor_name": {
            "type": "string",
            "description": "执行人姓名或 IM 昵称",
        },
        "deadline_at": {
            "type": "string",
            "description": "截止时间，ISO8601 格式，如 2026-07-15T17:00:00",
        },
        "is_recurring": {
            "type": "boolean",
            "description": "是否为循环任务",
            "default": False,
        },
        "deadline_rule": {
            "type": "string",
            "description": "循环规则描述，如'每周五17:00'、'每月20日'",
        },
        "verification_standard": {
            "type": "string",
            "description": "成果核验标准，JSON 格式字符串",
        },
        "project_name": {
            "type": "string",
            "description": "关联项目名称",
        },
        "phase_code": {
            "type": "string",
            "description": "关联项目阶段编码",
        },
        "node_id": {
            "type": "string",
            "description": "关联全景节点编号（必填，成果→任务→节点合规链要求）",
        },
        "force": {
            "type": "boolean",
            "description": "强制执行，跳过守护核验",
            "default": False,
        },
        "guardian_notes": {
            "type": "string",
            "description": "守护核验备注",
        },
    },
    "required": ["title"],
}

_SUBMIT_PLAN_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "instance_no": {
            "type": "string",
            "description": "要提交成果的任务实例编号（PTI-...）",
        },
        "content": {
            "type": "string",
            "description": "成果描述或正文内容",
        },
        "file_url": {
            "type": "string",
            "description": "附件文件 URL（如有）",
        },
        "type": {
            "type": "string",
            "description": "成果类型：TEXT / FILE / JSON",
            "default": "TEXT",
        },
        "is_acceptance_check": {
            "type": "boolean",
            "description": "是否为完工确认报告",
            "default": False,
        },
    },
    "required": ["instance_no", "content"],
}

_REVIEW_PLAN_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "instance_no": {
            "type": "string",
            "description": "要审核的任务实例编号（PTI-...）",
        },
        "action": {
            "type": "string",
            "description": "审核动作：confirm（确认）或 return（退回修改）",
            "enum": ["confirm", "return"],
        },
        "reason": {
            "type": "string",
            "description": "审核意见/原因",
        },
    },
    "required": ["instance_no", "action"],
}

_QUERY_PLAN_TASKS_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "description": "按状态过滤：WAITING / SUBMITTED / RETURNED / CONFIRMED / ARCHIVED",
        },
        "role": {
            "type": "string",
            "description": "查询身份：executor（我是执行人）或 initiator（我是发起人）",
            "enum": ["executor", "initiator"],
            "default": "executor",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量上限",
            "default": 20,
        },
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# Tool Handler 函数
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# 名称 → ID 解析辅助（best-effort，未匹配返回空串）
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_executor_id(executor_name: str) -> str:
    """执行人姓名 → user_id（未匹配返回空串，任务将标记为待指派）。"""
    if not executor_name:
        return ""
    try:
        from ..repositories.user_repo import UserRepository
        user = UserRepository.find_by_name(executor_name)
        if user:
            return user.id
        logger.warning("executor_name '%s' 未匹配到用户，任务标记为待指派", executor_name)
        return ""
    except Exception as e:
        logger.warning("resolve executor_name '%s' failed: %s", executor_name, e)
        return ""


def _resolve_project_id(project_name: str, project_id: str = "") -> str:
    """项目名称/编码 → project_id（优先用已提供的 project_id，未匹配返回空串）。"""
    if project_id:
        return project_id
    if not project_name:
        return ""
    try:
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import Project
        with get_session() as session:
            proj = (
                session.query(Project)
                .filter(Project.name == project_name, Project.is_deleted == False)
                .first()
            )
            if not proj:
                proj = (
                    session.query(Project)
                    .filter(Project.code == project_name, Project.is_deleted == False)
                    .first()
                )
            if proj:
                return proj.id
            logger.warning("project_name '%s' 未匹配到项目", project_name)
            return ""
    except Exception as e:
        logger.warning("resolve project_name '%s' failed: %s", project_name, e)
        return ""


def _resolve_instance_id(instance_no: str) -> str:
    """实例编号 PTI-... → instance_id（数据库主键，未找到返回空串）。"""
    if not instance_no:
        return ""
    try:
        from ..repositories.plan_task_repo import PlanTaskInstanceRepo
        inst = PlanTaskInstanceRepo.get_by_instance_no(instance_no)
        return inst.id if inst else ""
    except Exception as e:
        logger.warning("resolve instance_no '%s' failed: %s", instance_no, e)
        return ""


def _infer_task_type(deadline_rule: str) -> str:
    """从 deadline_rule 推断循环类型：含'月'→MONTHLY，否则默认 WEEKLY。"""
    rule = deadline_rule or ""
    if "月" in rule or "month" in rule.lower():
        return "MONTHLY"
    return "WEEKLY"


# ══════════════════════════════════════════════════════════════════════════════
# Tool Handler 函数
# ══════════════════════════════════════════════════════════════════════════════


async def handle_record_plan_task(
    params: dict[str, Any],
    plan_task_app=None,
    user_id: str = "",
    message_id: str = "",
    pending_issues: Any = None,
    config: Any = None,
) -> dict:
    """创建计划任务（一次性或循环）。

    Args:
        params: 工具参数，对齐 _RECORD_PLAN_TASK_SCHEMA
        plan_task_app: PlanTaskApplication 实例
        user_id: 当前用户 ID
        message_id: 触发消息 ID
        pending_issues: PendingIssues 实例（可选）
        config: Config 实例（可选）

    Returns:
        {"success": bool, "object_type": str, "object_id": str, "reply": str}
    """
    if plan_task_app is None:
        return {"success": False, "object_type": "plan_task", "reply": "计划任务模块未初始化"}

    from ..services.plan_task_commands import CreateInstanceCommand, CreateTemplateCommand

    title = params.get("title", "")
    description = params.get("description", "")
    deadline_at = params.get("deadline_at", "")
    is_recurring = params.get("is_recurring", False)
    deadline_rule = params.get("deadline_rule", "")
    verification_standard = params.get("verification_standard", "{}")
    project_name = params.get("project_name", "")
    project_id_param = params.get("project_id", "")
    phase_code = params.get("phase_code", "")
    node_id = params.get("node_id", "")
    executor_name = params.get("executor_name", "")

    # 解析执行人姓名 → user_id、项目名称 → project_id（best-effort，在线程池执行避免阻塞事件循环）
    executor_id = await asyncio.to_thread(_resolve_executor_id, executor_name)
    project_id = await asyncio.to_thread(_resolve_project_id, project_name, project_id_param)

    # BUG-007 修复：executor_id 缺失时降级为发起人自己（待分配），而非直接失败
    if not executor_id:
        executor_id = user_id
        logger.info(
            "record_plan_task: no executor resolved (executor_name=%r), "
            "falling back to initiator_id=%s",
            executor_name, user_id,
        )

    # ── 循环任务：创建模板 + 激活，调度机自动生成每期实例 ──
    if is_recurring and deadline_rule:
        task_type = _infer_task_type(deadline_rule)
        tpl_cmd = CreateTemplateCommand(
            name=title,
            description=description,
            initiator_id=user_id,
            executor_id=executor_id,
            project_id=project_id,
            task_type=task_type,
            deadline_rule=deadline_rule,
            verification_standard=verification_standard,
            creator_id=user_id,
        )
        tpl_result = await plan_task_app.create_template_from_command(tpl_cmd)
        if not tpl_result["success"]:
            return {
                "success": False,
                "object_type": "plan_task_template",
                "reply": tpl_result["reply"],
            }
        # 激活模板，调度机下一 tick 即开始生成当前周期实例
        await plan_task_app.activate_template(tpl_result["object_id"])
        return {
            "success": True,
            "object_type": "plan_task_template",
            "object_id": tpl_result["template_no"],
            "template_no": tpl_result["template_no"],
            "reply": (
                f"✅ 循环任务模板「{title}」已创建并激活（编号：{tpl_result['template_no']}）。\n"
                f"调度规则：{deadline_rule}（{task_type}），调度机将自动生成每期任务实例。"
            ),
        }

    # ── 一次性任务：创建实例 ──
    cmd = CreateInstanceCommand(
        title=title,
        description=description,
        initiator_id=user_id,
        executor_id=executor_id,
        project_id=project_id,
        phase_code=phase_code,
        node_id=node_id,
        deadline_at=deadline_at,
        verification_standard=verification_standard,
    )

    result = await plan_task_app.create_task_from_command(cmd)

    return {
        "success": result["success"],
        "object_type": "plan_task",
        "object_id": result.get("instance_no", ""),
        "instance_no": result.get("instance_no", ""),
        "status": result.get("status", ""),
        "anomaly": result.get("anomaly", False),
        "reply": result["reply"],
    }


async def handle_submit_plan_task(
    params: dict[str, Any],
    plan_task_app=None,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """提交计划任务成果。"""
    if plan_task_app is None:
        return {"success": False, "object_type": "plan_task", "reply": "计划任务模块未初始化"}

    from ..services.plan_task_commands import SubmitDeliverableCommand

    instance_no = params.get("instance_no", "")

    # 通过 instance_no 解析 instance_id（数据库主键）
    instance_id = await asyncio.to_thread(_resolve_instance_id, instance_no)
    if not instance_id:
        return {
            "success": False,
            "object_type": "plan_task_deliverable",
            "reply": f"❌ 找不到任务实例：{instance_no or '（未提供编号）'}",
        }

    cmd = SubmitDeliverableCommand(
        instance_id=instance_id,
        type=params.get("type", "TEXT"),
        content=params.get("content", ""),
        file_url=params.get("file_url", ""),
        file_name=params.get("file_name", ""),
        submitted_by=user_id,
        is_acceptance_check=params.get("is_acceptance_check", False),
    )

    result = await plan_task_app.submit_task(cmd)

    return {
        "success": result["success"],
        "object_type": "plan_task_deliverable",
        "object_id": result.get("instance_no", ""),
        "status": result.get("status", ""),
        "reply": result["reply"],
    }



async def handle_review_plan_task(
    params: dict[str, Any],
    plan_task_app=None,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """审核计划任务成果（确认或退回）。"""
    if plan_task_app is None:
        return {"success": False, "object_type": "plan_task", "reply": "计划任务模块未初始化"}

    from ..services.plan_task_commands import ReviewTaskCommand

    instance_no = params.get("instance_no", "")
    action = params.get("action", "confirm")

    # 通过 instance_no 解析 instance_id（数据库主键）
    instance_id = await asyncio.to_thread(_resolve_instance_id, instance_no)
    if not instance_id:
        return {
            "success": False,
            "object_type": "plan_task",
            "reply": f"❌ 找不到任务实例：{instance_no or '（未提供编号）'}",
        }

    cmd = ReviewTaskCommand(
        instance_id=instance_id,
        operator_id=user_id,
        action=action,
        reason=params.get("reason", ""),
    )

    result = await plan_task_app.review_task(cmd)

    return {
        "success": result["success"],
        "object_type": "plan_task",
        "object_id": result.get("instance_no", ""),
        "status": result.get("status", ""),
        "reply": result["reply"],
    }


async def handle_query_plan_tasks(
    params: dict[str, Any],
    plan_task_app=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """查询计划任务列表。"""
    if plan_task_app is None:
        return {"success": False, "object_type": "plan_task", "reply": "计划任务模块未初始化"}

    status = params.get("status") or None
    role = params.get("role", "executor")
    limit = params.get("limit", 20)

    result = await plan_task_app.query_my_tasks(user_id, role=role, status=status, limit=limit)

    return {
        "success": result["success"],
        "object_type": "plan_task_query",
        "tasks": result.get("tasks", []),
        "count": result.get("count", 0),
        "reply": result["reply"],
    }
