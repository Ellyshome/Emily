"""send_email / fetch_inbox — Agent 邮件收发工具。

由 tools/__init__.py 的 create_all_tools() 注册到 ToolRegistry（LLM 可见）。
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Config
    from ..services.email_service import EmailService

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Tool Schemas
# ══════════════════════════════════════════════════════════════════════════════

_SEND_EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {
            "type": "string",
            "description": "收件人邮箱地址",
        },
        "subject": {
            "type": "string",
            "description": "邮件主题",
        },
        "body": {
            "type": "string",
            "description": "邮件正文（支持 Markdown）",
        },
        "credential_source": {
            "type": "string",
            "enum": ["user_memory", "env"],
            "description": "user_memory=从用户长期记忆中获取凭证；env=使用环境变量中的默认邮箱凭证。默认 env。",
        },
    },
    "required": ["to", "subject", "body"],
}

_FETCH_INBOX_SCHEMA = {
    "type": "object",
    "properties": {
        "credential_source": {
            "type": "string",
            "enum": ["user_memory", "env"],
            "description": "凭证来源。默认 env。",
        },
        "limit": {
            "type": "integer",
            "description": "最多返回邮件数，默认 10。",
        },
    },
    "required": [],
}


# ══════════════════════════════════════════════════════════════════════════════
# Tool 工厂函数
# ══════════════════════════════════════════════════════════════════════════════


def create_send_email_tool(email_service: "EmailService", config: "Config" = None):
    """创建 send_email 工具。

    Args:
        email_service: EmailService 实例
        config: 全局配置（用于读取 email_smtp_host 等默认值）

    Returns:
        ToolDefinition
    """
    from emily_core.tools.definitions import ToolDefinition

    async def execute(args: dict) -> dict:
        to = (args.get("to", "") or "").strip()
        subject = (args.get("subject", "") or "").strip()
        body = (args.get("body", "") or "").strip()
        credential_source = args.get("credential_source", "env") or "env"

        if not to or "@" not in to:
            return {"success": False, "error": "收件人邮箱地址无效"}
        if not subject:
            return {"success": False, "error": "邮件主题不能为空"}
        if not body:
            return {"success": False, "error": "邮件正文不能为空"}

        # 获取凭证
        creds = _get_credentials(credential_source, config)
        if creds is None:
            return {"success": False, "error": "无法获取邮箱凭证。请设置 EMILY_EMAIL_IDKEY 和 EMILY_EMAIL_PASSWORD 环境变量。"}

        try:
            result = await email_service.send(
                creds=creds,
                to=to,
                subject=subject,
                body=body,
                html=False,
            )
            if result.success:
                return {
                    "success": True,
                    "message": f"邮件已发送至 {to}",
                    "message_id": result.message_id or "",
                }
            else:
                return {"success": False, "error": result.error or "发送失败"}

        except Exception as e:
            logger.warning("send_email tool failed: %s", e)
            return {"success": False, "error": f"邮件发送异常：{e}"}

    return ToolDefinition(
        name="send_email",
        description=(
            "发送邮件。需要调用者提供邮箱凭证（默认从环境变量获取）。"
            "当用户要求发送邮件、发通知、发报告时使用此工具。"
        ),
        parameters=_SEND_EMAIL_SCHEMA,
        execute=execute,
        require_admin=False,
    )


def create_fetch_inbox_tool(email_service: "EmailService", config: "Config" = None):
    """创建 fetch_inbox 工具。

    Args:
        email_service: EmailService 实例
        config: 全局配置

    Returns:
        ToolDefinition
    """
    from emily_core.tools.definitions import ToolDefinition

    async def execute(args: dict) -> dict:
        credential_source = args.get("credential_source", "env") or "env"
        limit = args.get("limit", 10) or 10

        # 获取凭证
        creds = _get_credentials(credential_source, config)
        if creds is None:
            return {"success": False, "error": "无法获取邮箱凭证。请设置 EMILY_EMAIL_IDKEY 和 EMILY_EMAIL_PASSWORD 环境变量。"}

        try:
            envelopes = await email_service.fetch_inbox(
                creds=creds,
                unread_only=True,
                limit=min(limit, 50),
            )

            if not envelopes:
                return {"success": True, "message": "收件箱中没有未读邮件。", "count": 0}

            # 格式化为自然语言
            lines = [f"收件箱中共 {len(envelopes)} 封未读邮件："]
            for i, env in enumerate(envelopes, 1):
                sender = env.sender or "(未知)"
                date_str = env.date.strftime("%m-%d %H:%M") if env.date else "(无日期)"
                subject = env.subject or "(无主题)"
                preview = env.body_plain[:100] + "..." if len(env.body_plain) > 100 else env.body_plain
                lines.append(f"\n{i}. **{subject}**")
                lines.append(f"   发件人: {sender} | {date_str}")
                lines.append(f"   预览: {preview}")

            return {"success": True, "message": "\n".join(lines), "count": len(envelopes)}

        except Exception as e:
            logger.warning("fetch_inbox tool failed: %s", e)
            return {"success": False, "error": f"邮件查询异常：{e}"}

    return ToolDefinition(
        name="fetch_inbox",
        description=(
            "检查邮箱收件箱，获取未读邮件列表。"
            "当用户要求查邮件、查收件箱、看看有没有新邮件时使用此工具。"
        ),
        parameters=_FETCH_INBOX_SCHEMA,
        execute=execute,
        require_admin=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 凭证获取
# ══════════════════════════════════════════════════════════════════════════════


def _get_credentials(source: str, config: "Config" = None):
    """从指定来源获取邮箱凭证。"""
    from ..providers.email.base import EmailCredentials

    if source == "user_memory":
        # TODO: 从用户长期记忆中获取凭证（Phase 2）
        # 暂 fallback 到 env
        logger.debug("credential_source=user_memory 暂不支持，fallback 到 env")

    # 从环境变量获取
    username = os.getenv("EMILY_EMAIL_IDKEY", "")
    password = os.getenv("EMILY_EMAIL_PASSWORD", "")

    if not username or not password:
        return None

    smtp_host = getattr(config, "email_smtp_host", "smtp.qq.com") if config else "smtp.qq.com"
    smtp_port = getattr(config, "email_smtp_port", 465) if config else 465
    imap_host = getattr(config, "email_imap_host", "imap.qq.com") if config else "imap.qq.com"
    imap_port = getattr(config, "email_imap_port", 993) if config else 993

    return EmailCredentials(
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        imap_host=imap_host,
        imap_port=imap_port,
        username=username,
        password=password,
        use_ssl=True,
    )
