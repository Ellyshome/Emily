"""全景节点模板库业务工具 —— 注册到 BusinessFlowToolRegistry。

只读检索能力（**一个**，不拆成多个）：列模板清单 + 读指定模板的完整内容
（清单 / 成果 / 对象声明 / 附件清单）。经 SOP-011 声明接入，门槛 L4+。

设计约束：
  · 只读、幂等：调用前后相关表行数不变（不写库、不写盘）
  · 不生成任何编号：只转述模板侧已有字段
  · 模板库缺失 / 索引损坏 → 明确失败（success=false + 原因），不异常冒泡、不影响归档与建节点
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.tools.node_template_tool")


# ── JSON Schema ──

_READ_NODE_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "ref_id": {
            "type": "string",
            "description": "模板编号（如 REF-CONST-MP-001）。填写即读该模板的完整内容；留空则列出模板清单",
        },
        "node_type": {
            "type": "string",
            "enum": ["MILESTONE", "TASK"],
            "description": "按节点类型过滤清单（仅在列出模式生效）",
        },
        "keyword": {
            "type": "string",
            "description": "按关键词过滤清单（匹配模板编号/名称/摘要，仅在列出模式生效）",
        },
    },
    "required": [],
}

_READ_NODE_TEMPLATE_DESCRIPTION = (
    "读取全景节点参考模板库（只读）。\n"
    "用法：\n"
    "  · 列清单：不传 ref_id，可按 node_type / keyword 过滤 —— 用于回答「有哪些节点模板」\n"
    "  · 读详情：传 ref_id —— 返回该模板的成果清单、对象声明、前置条件与附件清单\n"
    "口径：模板是**判断依据 / 参考蓝图，不是硬规则**；模板库由人工维护，系统不自动制作模板。\n"
    "权限：L4+。"
)

_BUILD_NODE_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "ref_id": {
            "type": "string",
            "description": "模板编号（必填，如 REF-CONST-MP-001）",
        },
        "project_id": {
            "type": "string",
            "description": "目标项目 ID（必填；参与单位候选按项目内参建单位优先）",
        },
    },
    "required": ["ref_id", "project_id"],
}

_BUILD_NODE_DRAFT_DESCRIPTION = (
    "按模板装配「建节点草稿」（只读，不落库）：返回候选集合供人增删——\n"
    "  · 参与单位候选（项目内参建单位优先，未命中退全局企业）\n"
    "  · 成员候选（由已确定参与单位推出该单位在职人员，默认全员）\n"
    "  · 共享文件候选（限定在调用人可见文件集合内）\n"
    "  · 前置成果候选（只指向项目内已有成果）\n"
    "每条候选带依据（依据模板哪一条声明、匹配到什么、为什么命中）；\n"
    "声明了但零候选的条目在 unresolved 单列回显。\n"
    "**草稿未经确认不得落库**；确认后由 create_node 一次性落库六类对象。\n"
    "权限：L4+。"
)

_MIN_LEVEL = 4


def _operator_level(user_id: str) -> int:
    """取操作人等级；取不到一律 0（fail-closed）。"""
    if not user_id:
        return 0
    try:
        from ..repositories.permission_repo import PermissionRepository

        operator = PermissionRepository.get_user(user_id)
        return int(getattr(operator, "level", 0) or 0)
    except Exception:
        return 0


def _summary_payload(t) -> dict:
    return {
        "ref_id": t.ref_id,
        "node_name": t.node_name,
        "node_type": t.node_type,
        "stage_id": t.stage_id,
        "summary": t.summary,
        "attachment_count": t.attachment_count,
    }


def _detail_payload(d) -> dict:
    return {
        "ref_id": d.ref_id,
        "node_name": d.node_name,
        "node_type": d.node_type,
        "stage_id": d.stage_id,
        "summary": d.summary,
        "deliverables": [
            {
                "deliverable_name": x.name,
                "target_amount": x.target_amount,
                "unit": x.unit,
                "is_required": x.is_required,
                "typical_filenames": x.typical_filenames,
            }
            for x in d.deliverables
        ],
        "declarations": d.declarations_raw,
        "preconditions": d.preconditions,
        "attachments": [
            {"name": a.name, "size": a.size, "type": a.type} for a in d.attachments
        ],
        "warnings": d.warnings,
    }


async def handle_read_node_template(
    params: dict[str, Any],
    user_id: str = "",
    **kw,
) -> dict[str, Any]:
    """读取模板库（列清单 / 读详情）。只读、幂等、L4+。"""
    # 授权：L4+（与注册表 permission_flag=read_l4 双重把关，此处为服务层兜底）
    level = _operator_level(user_id)
    if level < _MIN_LEVEL:
        return {
            "success": False,
            "reply": "模板库读取需要 L4 及以上权限",
            "error_code": "permission_denied",
        }

    from ..services.node_template_loader import NodeTemplateLoader, TemplateUnavailable

    loader = NodeTemplateLoader()
    ref_id = (params.get("ref_id") or "").strip()
    node_type = (params.get("node_type") or "").strip()
    keyword = (params.get("keyword") or "").strip()

    try:
        if ref_id:
            detail = loader.get_template(ref_id)
            if detail is None:
                try:
                    available = [t.ref_id for t in loader.list_templates()]
                except TemplateUnavailable as e:
                    return {"success": False, "reply": f"模板库不可用：{e.reason}"}
                return {
                    "success": False,
                    "reply": f"模板库中没有编号为 {ref_id} 的模板（现有：{'、'.join(available)}）",
                    "error_code": "template_not_found",
                }
            payload = _detail_payload(detail)
            # M3 字段增强：对象声明解析为结构化 + 校验告警（不改 schema、不改签名）
            from ..services.node_assembly_service import NodeDeclarationParser

            parsed = NodeDeclarationParser().parse(detail.declarations_raw)
            payload["declarations_parsed"] = parsed.to_dict()
            payload["warnings"] = list(payload.get("warnings") or []) + list(parsed.warnings)
            req = [x["deliverable_name"] for x in payload["deliverables"] if x["is_required"]]
            reply = (
                f"模板 {detail.ref_id}「{detail.node_name}」"
                f"（{detail.node_type}，阶段 {detail.stage_id}）："
                f"成果 {len(payload['deliverables'])} 条（必需 {len(req)} 条）、"
                f"对象声明 {len(detail.declarations_raw)} 类、"
                f"附件 {len(payload['attachments'])} 个。"
            )
            if payload["warnings"]:
                reply += f" 注意：{len(payload['warnings'])} 条声明告警。"
            return {"success": True, "data": payload, "reply": reply}

        templates = loader.list_templates(node_type=node_type, keyword=keyword)
    except TemplateUnavailable as e:
        return {"success": False, "reply": f"模板库不可用：{e.reason}", "error_code": "template_unavailable"}

    if not templates:
        cond = "、".join(x for x in (node_type and f"类型={node_type}", keyword and f"关键词={keyword}") if x)
        return {
            "success": True,
            "data": {"templates": [], "total": 0},
            "reply": f"模板库中没有符合条件的模板（{cond or '无过滤条件'}）。",
        }

    payload = {"templates": [_summary_payload(t) for t in templates], "total": len(templates)}
    names = "；".join(f"{t.ref_id}「{t.node_name}」({t.node_type})" for t in templates)
    return {
        "success": True,
        "data": payload,
        "reply": f"模板库共 {len(templates)} 个模板：{names}。",
    }


async def handle_build_node_draft(
    params: dict[str, Any],
    user_id: str = "",
    **kw,
) -> dict[str, Any]:
    """按模板装配只读草稿（候选集合 + 依据 + 未解析项）。只读、幂等、L4+。"""
    level = _operator_level(user_id)
    if level < _MIN_LEVEL:
        return {
            "success": False,
            "reply": "按模板装配需要 L4 及以上权限",
            "error_code": "permission_denied",
        }

    ref_id = (params.get("ref_id") or "").strip()
    project_id = (params.get("project_id") or "").strip()
    if not ref_id or not project_id:
        return {"success": False, "reply": "缺少 ref_id 或 project_id"}

    from ..services.node_assembly_service import NodeAssemblyService
    from ..services.node_template_loader import TemplateUnavailable

    try:
        draft = NodeAssemblyService().build_draft(ref_id, project_id, user_id)
    except TemplateUnavailable as e:
        return {"success": False, "reply": f"模板库不可用：{e.reason}", "error_code": "template_unavailable"}
    except KeyError as e:
        return {"success": False, "reply": str(e), "error_code": "template_not_found"}

    data = draft.to_dict()
    cs = {k: v["total"] for k, v in data["candidates"].items()}
    reply = (
        f"已按模板「{draft.node_name}」装配草稿："
        f"参与单位候选 {cs.get('participant_companies', 0)} 家、"
        f"成员候选 {cs.get('participant_users', 0)} 人、"
        f"共享文件候选 {cs.get('shared_files', 0)} 份、"
        f"前置成果候选 {cs.get('pre_conditions', 0)} 项。"
        f"**草稿只读、尚未落库**——请确认（可增删候选）后再创建节点。"
    )
    if data["unresolved"]:
        reply += f" 有 {len(data['unresolved'])} 条声明未解析到候选，已在 unresolved 中列出。"
    if data["warnings"]:
        reply += f" 有 {len(data['warnings'])} 条声明告警。"
    return {"success": True, "data": data, "reply": reply}
