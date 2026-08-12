"""专家库管理工具 — 4 个 BusinessFlowTool handler。

create_expert / approve_expert / toggle_expert / query_experts
权限校验在 handler 内完成（fail-closed）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("emily.tool.expert_manage")

# ══════════════════════════════════════════════════════════════════════════════
# Schema 常量
# ══════════════════════════════════════════════════════════════════════════════

_EXPERT_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "专家名称",
        },
        "function_desc": {
            "type": "string",
            "description": "一句话职能描述",
        },
        "manual_path": {
            "type": "string",
            "description": "职能手册文件名（相对 Expert Work Manual/ 目录）",
        },
        "task_manual_path": {
            "type": "string",
            "description": "任务手册文件名",
        },
        "review_schema": {
            "type": "object",
            "description": "评审成果 JSON schema，注入 prompt 约束输出格式",
        },
        "sop_id": {
            "type": "string",
            "description": "绑定的 SOP ID，可选",
        },
    },
    "required": ["name", "function_desc", "manual_path", "task_manual_path"],
}

_EXPERT_APPROVE_SCHEMA = {
    "type": "object",
    "properties": {
        "expert_no": {
            "type": "string",
            "description": "专家编号，如 EXP-001",
        },
        "action": {
            "type": "string",
            "enum": ["APPROVE", "REJECT"],
            "description": "APPROVE=通过，REJECT=驳回",
        },
        "reason": {
            "type": "string",
            "description": "审批意见",
        },
    },
    "required": ["expert_no", "action"],
}

_EXPERT_TOGGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "expert_no": {
            "type": "string",
            "description": "专家编号，如 EXP-001",
        },
        "action": {
            "type": "string",
            "enum": ["ENABLE", "DISABLE"],
            "description": "ENABLE=启用，DISABLE=停用",
        },
        "reason": {
            "type": "string",
            "description": "启停原因",
        },
    },
    "required": ["expert_no", "action"],
}

_EXPERT_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["PENDING", "ACTIVE", "DISABLED", "REJECTED"],
            "description": "按状态过滤，不传返回全部 ACTIVE",
        },
        "sop_id": {
            "type": "string",
            "description": "按 SOP ID 过滤",
        },
    },
    "required": [],
}

# ══════════════════════════════════════════════════════════════════════════════
# Helper
# ══════════════════════════════════════════════════════════════════════════════

def _check_admin(user_id: str, perm_dict: dict | None) -> bool:
    """检查用户是否为 L5+ 管理员。"""
    if not user_id:
        return False
    # 从 perm_dict 取 level
    if perm_dict:
        level = perm_dict.get("level", 0)
        if level >= 5:
            return True
    # 兜底：查 DB
    try:
        from emily_core.repositories.user_repo import UserRepository
        user = UserRepository.get_by_id(user_id)
        if user and getattr(user, "level", 0) >= 5:
            return True
    except Exception:
        pass
    return False


def _check_management_unit(user_id: str, perm_dict: dict | None) -> bool:
    """检查用户是否为管理单位员工。"""
    if not user_id:
        return False
    if perm_dict and perm_dict.get("is_management_unit"):
        return True
    # 从 perm_dict 取 level → can_access(level, 4)
    if perm_dict:
        try:
            from emily_core.permission.level import can_access
            level = perm_dict.get("level", 0)
            if level > 0 and can_access(level, 4):
                return True
        except Exception:
            pass
    # 兜底：查 DB
    try:
        from emily_core.repositories.user_repo import UserRepository
        user = UserRepository.get_by_id(user_id)
        if user:
            level = getattr(user, "level", 0)
            if level > 0:
                from emily_core.permission.level import can_access
                if can_access(level, 4):
                    return True
    except Exception:
        pass
    return False


async def _get_user_id_from_tool_context() -> str:
    """从 tool_node 的 BusContext 获取当前 user_id。"""
    try:
        from emily_core.workitem.langgraph_engine.state import get_bus_context
        ctx = get_bus_context()
        wi = getattr(ctx, "work_item", None)
        if wi:
            return getattr(wi, "user_id", "")
        session_ctx = getattr(ctx, "session_ctx", None)
        if session_ctx:
            return getattr(session_ctx, "user_id", "")
    except RuntimeError:
        pass
    return ""


async def _get_perm_dict() -> dict | None:
    """从 BusContext 获取权限快照。"""
    try:
        from emily_core.workitem.langgraph_engine.state import get_bus_context
        ctx = get_bus_context()
        session_ctx = getattr(ctx, "session_ctx", None)
        if session_ctx:
            return getattr(session_ctx, "perm_dict", None)
    except RuntimeError:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Handler 函数
# ══════════════════════════════════════════════════════════════════════════════

async def handle_create_expert(params: dict, user_id: str = "") -> dict:
    """创建新专家（PENDING 状态，需管理员审批）。"""
    if not user_id:
        user_id = await _get_user_id_from_tool_context()
    perm_dict = await _get_perm_dict()

    # 权限校验：管理单位 或 L4+
    if not _check_management_unit(user_id, perm_dict):
        return {"success": False, "reply": "权限不足：仅管理单位员工可创建专家。"}

    name = (params.get("name") or "").strip()
    function_desc = (params.get("function_desc") or "").strip()
    manual_path = (params.get("manual_path") or "").strip()
    task_manual_path = (params.get("task_manual_path") or "").strip()
    review_schema = params.get("review_schema") or {}
    sop_id = (params.get("sop_id") or "").strip()

    if not all([name, function_desc, manual_path, task_manual_path]):
        return {"success": False, "reply": "参数不完整：name/function_desc/manual_path/task_manual_path 为必填。"}

    try:
        from emily_core.repositories.expert_repo import ExpertRepository
        expert_no = ExpertRepository.generate_expert_no()
        expert = ExpertRepository.create(
            expert_no=expert_no,
            name=name,
            function_desc=function_desc,
            manual_path=manual_path,
            task_manual_path=task_manual_path,
            review_schema=review_schema,
            sop_id=sop_id,
            creator_id=user_id,
        )
        return {
            "success": True,
            "reply": f"专家 '{name}' 已创建（编号 {expert_no}），状态 PENDING，等待管理员审批。",
            "expert_no": expert_no,
        }
    except Exception as e:
        logger.error("create_expert failed: %s", e, exc_info=True)
        return {"success": False, "reply": f"创建专家失败: {e}"}


async def handle_approve_expert(params: dict, user_id: str = "") -> dict:
    """审批专家（L5+）。"""
    if not user_id:
        user_id = await _get_user_id_from_tool_context()
    perm_dict = await _get_perm_dict()

    if not _check_admin(user_id, perm_dict):
        return {"success": False, "reply": "权限不足：仅 L5+ 管理员可审批专家。"}

    expert_no = (params.get("expert_no") or "").strip()
    action = (params.get("action") or "").strip().upper()
    reason = (params.get("reason") or "").strip()

    if not expert_no or action not in ("APPROVE", "REJECT"):
        return {"success": False, "reply": "参数错误：expert_no 和 action(APPROVE/REJECT) 为必填。"}

    try:
        from emily_core.repositories.expert_repo import ExpertRepository, ExpertApprovalRepository

        expert = ExpertRepository.get_by_expert_no(expert_no)
        if not expert:
            return {"success": False, "reply": f"专家 {expert_no} 不存在。"}

        # 状态机校验
        if expert.status != "PENDING":
            return {"success": False, "reply": f"专家 {expert_no} 当前状态为 {expert.status}，不支持审批操作。"}

        new_status = "ACTIVE" if action == "APPROVE" else "REJECTED"
        ExpertRepository.update_status(expert.id, new_status, approver_id=user_id)
        ExpertApprovalRepository.create(
            expert_id=expert.id, action=action,
            operator_id=user_id, reason=reason,
        )

        action_cn = "已通过" if action == "APPROVE" else "已驳回"
        return {"success": True, "reply": f"专家 {expert_no}({expert.name}) {action_cn}。",
                "expert_no": expert_no, "new_status": new_status}
    except Exception as e:
        logger.error("approve_expert failed: %s", e, exc_info=True)
        return {"success": False, "reply": f"审批操作失败: {e}"}


async def handle_toggle_expert(params: dict, user_id: str = "") -> dict:
    """启停专家（L5+）。"""
    if not user_id:
        user_id = await _get_user_id_from_tool_context()
    perm_dict = await _get_perm_dict()

    if not _check_admin(user_id, perm_dict):
        return {"success": False, "reply": "权限不足：仅 L5+ 管理员可启停专家。"}

    expert_no = (params.get("expert_no") or "").strip()
    action = (params.get("action") or "").strip().upper()
    reason = (params.get("reason") or "").strip()

    if not expert_no or action not in ("ENABLE", "DISABLE"):
        return {"success": False, "reply": "参数错误：expert_no 和 action(ENABLE/DISABLE) 为必填。"}

    try:
        from emily_core.repositories.expert_repo import ExpertRepository, ExpertApprovalRepository

        expert = ExpertRepository.get_by_expert_no(expert_no)
        if not expert:
            return {"success": False, "reply": f"专家 {expert_no} 不存在。"}

        # 状态机校验
        if action == "DISABLE" and expert.status != "ACTIVE":
            return {"success": False, "reply": f"专家 {expert_no} 当前状态为 {expert.status}，不支持停用（需为 ACTIVE）。"}
        if action == "ENABLE" and expert.status != "DISABLED":
            return {"success": False, "reply": f"专家 {expert_no} 当前状态为 {expert.status}，不支持启用（需为 DISABLED）。"}

        new_status = "DISABLED" if action == "DISABLE" else "ACTIVE"
        ExpertRepository.update_status(expert.id, new_status)
        ExpertApprovalRepository.create(
            expert_id=expert.id, action=action,
            operator_id=user_id, reason=reason,
        )

        action_cn = "已停用" if action == "DISABLE" else "已启用"
        return {"success": True, "reply": f"专家 {expert_no}({expert.name}) {action_cn}。",
                "expert_no": expert_no, "new_status": new_status}
    except Exception as e:
        logger.error("toggle_expert failed: %s", e, exc_info=True)
        return {"success": False, "reply": f"启停操作失败: {e}"}


async def handle_query_experts(params: dict, user_id: str = "") -> dict:
    """查询专家列表。"""
    status = (params.get("status") or "").strip().upper()
    sop_id = (params.get("sop_id") or "").strip()

    try:
        from emily_core.repositories.expert_repo import ExpertRepository

        if status and status in ("PENDING", "ACTIVE", "DISABLED", "REJECTED"):
            experts = ExpertRepository.list_by_status(status)
        elif sop_id:
            expert = ExpertRepository.get_by_sop_id(sop_id)
            experts = [expert] if expert else []
        else:
            experts = ExpertRepository.list_active()

        if not experts:
            return {"success": True, "reply": "暂无匹配的专家记录。"}

        lines = []
        for e in experts:
            lines.append(
                f"- {e.expert_no} | {e.name} | {e.function_desc} | {e.status}"
                f"{' | SOP: ' + e.sop_id if e.sop_id else ''}"
            )
        return {"success": True, "reply": "\n".join(lines),
                "count": len(experts)}
    except Exception as e:
        logger.error("query_experts failed: %s", e, exc_info=True)
        return {"success": False, "reply": f"查询专家失败: {e}"}
