"""待解决问题清单工具 —— M8a。

为 MasterAgent 提供管理待解决问题清单的能力：
- 查看待处理/已处理列表
- 标记问题为已处理（需要管理员权限）
"""

import logging

from .definitions import ToolDefinition
from ..services.pending_issues import PendingIssuesService

logger = logging.getLogger("emily.tool.pending_issue")


def create_pending_issue_tool(
    pending_issues: PendingIssuesService,
    is_admin: bool = False,
) -> ToolDefinition:
    """创建待解决问题管理工具。

    Args:
        pending_issues: PendingIssuesService 实例
        is_admin: 当前用户是否为管理员（决定能否标记已处理）

    Returns:
        ToolDefinition: manage_pending_issues 工具
    """

    async def execute(args: dict) -> dict:
        action = args.get("action", "list_pending")

        if action == "list_pending":
            items = pending_issues.list_pending()
            if not items:
                return {"success": True, "reply": "当前没有待解决的问题。", "items": []}
            # 格式化输出
            lines = [f"共 {len(items)} 条待处理："]
            for item in items:
                lines.append(
                    f"  {item.get('id', '?')} · {item.get('提出人', '?')} · "
                    f"{item.get('问题描述', '?')[:50]}"
                )
            return {"success": True, "reply": "\n".join(lines), "items": items}

        elif action == "list_resolved":
            items = pending_issues.list_resolved()
            if not items:
                return {"success": True, "reply": "没有已处理的记录。", "items": []}
            lines = [f"最近已处理 {len(items)} 条："]
            for item in items[:10]:
                handler = item.get("处理人", "?")
                decision = item.get("决策", "?")[:50]
                lines.append(f"  {item.get('id', '?')} · 处理人：{handler} · {decision}")
            return {"success": True, "reply": "\n".join(lines), "items": items}

        elif action == "add":
            raised_by = args.get("raised_by", "用户")
            source = args.get("source", "")
            description = args.get("description", "")
            suggestion = args.get("suggestion", "")
            related_events = args.get("related_events", [])

            if not description:
                return {
                    "success": False,
                    "error_code": "missing_description",
                    "reply": "请描述需要记录的问题。",
                }

            # TC-N01: 运行时解析 raised_by 姓名（优先使用 _user_id 查 DB）
            if raised_by == "用户" or not raised_by:
                user_id = args.get("_user_id", "")
                if user_id:
                    try:
                        from ..repositories.user_repo import UserRepository
                        u = UserRepository.get(user_id)
                        if u:
                            raised_by = (
                                u.username or
                                "用户"
                            )
                    except Exception as e:
                        logger.warning("resolve raised_by from _user_id failed: %s", e, exc_info=True)

            issue_id = pending_issues.add(
                raised_by=raised_by,
                source=source or "用户通过对话添加",
                description=description,
                suggestion=suggestion,
                related_events=related_events if isinstance(related_events, list) else [],
            )
            return {
                "success": True,
                "reply": f"已记录待解决问题 {issue_id}",
                "issue_id": issue_id,
            }

        elif action == "resolve":
            if not is_admin:
                return {
                    "success": False,
                    "error_code": "permission_denied",
                    "reply": "只有项目总经理（管理员）可以处理待解决问题。",
                }

            issue_id = args.get("issue_id", "")
            if not issue_id:
                return {"success": False, "error_code": "missing_id", "reply": "请指定要处理的问题编号（如 PND-20260612-0001）。"}

            handler = args.get("handler", "管理员")
            decision = args.get("decision", "")
            decision_event_id = args.get("decision_event_id", "")

            if not decision:
                return {"success": False, "error_code": "missing_decision", "reply": "请说明处理决策。"}

            ok = pending_issues.resolve(
                issue_id=issue_id,
                handler=handler,
                decision=decision,
                decision_event_id=decision_event_id,
            )
            if ok:
                return {
                    "success": True,
                    "reply": f"已处理 {issue_id}：{decision}",
                }
            else:
                return {
                    "success": False,
                    "error_code": "not_found",
                    "reply": f"未找到待解决问题 {issue_id}。",
                }

        else:
            return {"success": False, "error_code": "unknown_action", "reply": f"不支持的操作: {action}"}

    return ToolDefinition(
        name="manage_pending_issues",
        description=(
            "管理待解决问题清单。\n"
            "操作类型：\n"
            "  list_pending — 列出所有待处理问题\n"
            "  list_resolved — 列出最近已处理的问题\n"
            "  add — 添加新的待解决问题，需 description；可选 raised_by/source/suggestion/related_events\n"
            "  resolve — 标记问题为已处理（需管理员权限），需 issue_id、handler、decision"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_pending", "list_resolved", "add", "resolve"],
                    "description": "操作类型",
                },
                "issue_id": {
                    "type": "string",
                    "description": "问题编号（resolve 时必填，如 PND-20260612-0001）",
                },
                "raised_by": {
                    "type": "string",
                    "description": "提出人姓名（add 时填写，如'彭工'、'守护Agent'）",
                },
                "source": {
                    "type": "string",
                    "description": "问题来源描述（add 时填写）",
                },
                "description": {
                    "type": "string",
                    "description": "问题详细描述（add 时必填）",
                },
                "suggestion": {
                    "type": "string",
                    "description": "建议处理方式（add 时可选）",
                },
                "related_events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "关联事件编号列表（add 时可选）",
                },
                "handler": {
                    "type": "string",
                    "description": "处理人姓名（resolve 时填写）",
                },
                "decision": {
                    "type": "string",
                    "description": "处理决策描述（resolve 时必填）",
                },
                "decision_event_id": {
                    "type": "string",
                    "description": "决策事件编号（可选，如 EVT-20260612-0015）",
                },
            },
            "required": ["action"],
        },
        execute=execute,
        require_admin=(not is_admin),  # resolve 需要 admin，但 list 不需要；简化处理：非 admin 时标记
    )
