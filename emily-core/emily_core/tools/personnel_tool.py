"""人员管理业务工具 —— 等级调整 / 企业归属调整 / 企业增删。

薄壳：handler 仅解析参数并委托 `PersonnelService`（授权判定 + 校验 + 留痕
均已在 service 层完成）。IM 侧操作人由框架注入 `user_id`，不信任参数自述。
"""

from __future__ import annotations

import asyncio

from ..services.personnel_service import PersonnelService

_UPDATE_USER_LEVEL_SCHEMA = {
    "type": "object",
    "properties": {
        "target_user_id": {"type": "string", "description": "目标人员 ID（users.id）"},
        "new_level": {"type": "integer", "description": "目标等级（1-6）"},
        "reason": {"type": "string", "description": "调整理由（必填，留痕记录）"},
    },
    "required": ["target_user_id", "new_level", "reason"],
}

_UPDATE_USER_COMPANY_SCHEMA = {
    "type": "object",
    "properties": {
        "target_user_id": {"type": "string", "description": "目标人员 ID（users.id）"},
        "company_id": {"type": "string", "description": "目标企业 ID（空串表清空归属）"},
        "reason": {"type": "string", "description": "调整理由（必填，留痕记录）"},
    },
    "required": ["target_user_id", "company_id", "reason"],
}

_MANAGE_COMPANY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "delete"],
            "description": "动作：create 新增企业 / delete 删除企业",
        },
        "name": {"type": "string", "description": "企业名称（action=create 时必填）"},
        "company_id": {"type": "string", "description": "目标企业 ID（action=delete 时必填）"},
    },
    "required": ["action"],
}


async def handle_update_user_level(params: dict, user_id: str | None = None, **kw) -> dict:
    target_user_id = str(params.get("target_user_id") or "")
    reason = str(params.get("reason") or "")
    if not target_user_id:
        return {"success": False, "reason_code": "user_not_found", "message": "缺少 target_user_id"}
    try:
        new_level = int(params.get("new_level"))
    except (TypeError, ValueError):
        return {"success": False, "reason_code": "invalid_level", "message": "目标等级非法"}
    operator_id = user_id or str(params.get("operator_id") or "")
    return await asyncio.to_thread(
        PersonnelService.adjust_user_level, operator_id, target_user_id, new_level, reason)


async def handle_update_user_company(params: dict, user_id: str | None = None, **kw) -> dict:
    target_user_id = str(params.get("target_user_id") or "")
    company_id = str(params.get("company_id") or "")
    reason = str(params.get("reason") or "")
    if not target_user_id:
        return {"success": False, "reason_code": "user_not_found", "message": "缺少 target_user_id"}
    operator_id = user_id or str(params.get("operator_id") or "")
    return await asyncio.to_thread(
        PersonnelService.adjust_user_company, operator_id, target_user_id, company_id, reason)


async def handle_manage_company(params: dict, user_id: str | None = None, **kw) -> dict:
    action = str(params.get("action") or "")
    operator_id = user_id or str(params.get("operator_id") or "")
    if action == "create":
        return await asyncio.to_thread(
            PersonnelService.create_company, operator_id, str(params.get("name") or ""))
    if action == "delete":
        return await asyncio.to_thread(
            PersonnelService.delete_company, operator_id, str(params.get("company_id") or ""))
    return {"success": False, "reason_code": "invalid_action",
            "message": "action 须为 create 或 delete"}
