"""
cold_start.py — Emily 冷启动编排脚本

═══════════════════════════════════════════════════════════════════════════════
定位：冷启动流程编排。串联自检 → 邮件通知 → 世界书 Prompt 生成。
     作为独立脚本运行，也可被外部调用。

工作流程：
  1. 调用 self_check.SelfCheck.run() 获取系统自检数据
  2. 构造 EmailCredentials → 调用 EmailService.send() 发送自检报告给管理员
  3. 读取 world_book.md 模板 → 注入自检数据 → 返回完整 Prompt 上下文文本

Fail-Open 策略：
  - 自检脚本异常 → 使用空数据占位，继续生成 prompt
  - 邮件凭证缺失 → 跳过邮件步骤，记录日志 warning
  - 邮件发送失败 → 记录日志 warning，继续后续步骤
  - 世界书模板缺失 → 使用内置默认 prompt

参照源：
  - scripts/collect_session_data.py（路径设置模式）
  - emily_core/tools/email_tool.py（邮件凭证构造模式）
═══════════════════════════════════════════════════════════════════════════════

用法：
    >>> import sys
    >>> sys.path.insert(0, "需求文件/自检-冷启-世界书")
    >>> sys.path.insert(0, "emily-core")
    >>> from cold_start import ColdStart
    >>> prompt_text = await ColdStart.run()
    >>> print(prompt_text[:200])
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

# ── 路径设置：脚本在 需求文件/自检-冷启-世界书/，需向上两级到仓库根目录 ──
_HERE = Path(__file__).resolve().parent          # 需求文件/自检-冷启-世界书/
_REPO_ROOT = _HERE.parent.parent                  # 仓库根目录
_CORE_DIR = _REPO_ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))                # 同目录脚本互相导入

logger = logging.getLogger("emily.cold_start")

# ══════════════════════════════════════════════════════════════════════════════
# 默认 Prompt 模板（world_book.md 缺失时的降级方案）
# ══════════════════════════════════════════════════════════════════════════════

_FALLBACK_PROMPT = """[Emily 系统认知]
你是 Emily，一个团队协作公共 AI 大脑，专为地产开发类工程协作搭建。
当前运行状态：服务 {users.total} 用户、管理 {projects.total} 个项目。
请基于实际能力回复用户问题，不得虚构或夸大系统能力范围。
"""

# ══════════════════════════════════════════════════════════════════════════════
# 内置默认管理员邮箱列表
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_ADMIN_EMAILS = [
    "admin@example.com",
]


# ══════════════════════════════════════════════════════════════════════════════
# 邮件凭证构造（参照 emily_core/tools/email_tool.py _get_credentials）
# ══════════════════════════════════════════════════════════════════════════════

def _build_email_credentials() -> Optional[Any]:
    """从环境变量构造 EmailCredentials。

    优先级：环境变量 EMILY_EMAIL_* → None（跳过邮件）

    需要的环境变量：
      EMILY_EMAIL_IDKEY     — SMTP/IMAP 用户名（邮箱地址）
      EMILY_EMAIL_PASSWORD  — SMTP/IMAP 授权码
      EMILY_EMAIL_SMTP_HOST — SMTP 服务器（默认 smtp.qq.com）
      EMILY_EMAIL_SMTP_PORT — SMTP 端口（默认 465）
      EMILY_EMAIL_IMAP_HOST — IMAP 服务器（默认 imap.qq.com）
      EMILY_EMAIL_IMAP_PORT — IMAP 端口（默认 993）

    Returns:
        EmailCredentials | None
    """
    username = os.getenv("EMILY_EMAIL_IDKEY", "")
    password = os.getenv("EMILY_EMAIL_PASSWORD", "")

    if not username or not password:
        logger.warning(
            "Email credentials not configured (EMILY_EMAIL_IDKEY/EMILY_EMAIL_PASSWORD), "
            "skipping email notification"
        )
        return None

    from emily_core.providers.email.base import EmailCredentials

    return EmailCredentials(
        smtp_host=os.getenv("EMILY_EMAIL_SMTP_HOST", "smtp.qq.com"),
        smtp_port=int(os.getenv("EMILY_EMAIL_SMTP_PORT", "465")),
        imap_host=os.getenv("EMILY_EMAIL_IMAP_HOST", "imap.qq.com"),
        imap_port=int(os.getenv("EMILY_EMAIL_IMAP_PORT", "993")),
        username=username,
        password=password,
        use_ssl=True,
    )


def _get_admin_emails() -> list[str]:
    """获取管理员邮箱列表。

    优先级：环境变量 EMILY_ADMIN_EMAILS（逗号分隔）→ 默认值
    """
    env_val = os.getenv("EMILY_ADMIN_EMAILS", "")
    if env_val:
        return [e.strip() for e in env_val.split(",") if e.strip()]
    return list(_DEFAULT_ADMIN_EMAILS)


# ══════════════════════════════════════════════════════════════════════════════
# 世界书模板加载
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_world_book_path() -> Path:
    """解析 world_book.md 路径（三个文件集中存放，同目录查找）。"""
    env_dir = os.getenv("EMILY_PROMPTS_DIR", "")
    if env_dir:
        candidate = Path(env_dir) / "world_book.md"
        if candidate.exists():
            return candidate
    # 同目录（三个文件集中存放）
    return _HERE / "world_book.md"


def _load_world_book() -> str:
    """加载世界书模板文件。

    Returns:
        str: 模板内容（含 {变量} 占位符）

    降级：文件不存在时返回内置默认模板。
    """
    path = _resolve_world_book_path()
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            logger.info("World book loaded: %s (%d chars)", path, len(content))
            return content
        except Exception as e:
            logger.warning("World book read error (%s): %s, using fallback", path, e)
    else:
        logger.warning("World book not found at %s, using fallback", path)
    return _FALLBACK_PROMPT


# ══════════════════════════════════════════════════════════════════════════════
# 变量注入 + 邮件正文生成
# ══════════════════════════════════════════════════════════════════════════════

def _inject_self_check_data(template: str, data: dict) -> str:
    """将自检数据注入模板，替换 {变量名} 占位符。

    支持的占位符格式：
      - 简单路径：{status} → data["status"]
      - 嵌套路径：{users.total} → data["users"]["total"]
      - 嵌套路径：{knowledge_base.total_docs} → data["knowledge_base"]["total_docs"]

    未匹配的占位符保留原样（不报错）。
    Null 值显示为 "N/A"。
    """
    def _resolve(key_path: str) -> str:
        """解析点分隔的 JSON 路径，返回字符串值。"""
        parts = key_path.split(".")
        current: Any = data
        try:
            for part in parts:
                current = current[part]
            if current is None:
                return "N/A"
            return str(current)
        except (KeyError, TypeError):
            return f"{{{key_path}}}"  # 保留未匹配的占位符

    # 匹配 {xxx.yyy.zzz} 或 {xxx.yyy} 或 {xxx} 格式的占位符
    _PLACEHOLDER_RE = re.compile(r"\{([a-z_]+\.[a-z_.]+|[a-z_]+)\}")

    def _replace(match: re.Match) -> str:
        key_path = match.group(1)
        return _resolve(key_path)

    return _PLACEHOLDER_RE.sub(_replace, template)


def _build_email_body(data: dict) -> str:
    """根据自检数据生成邮件正文（纯文本）。"""
    lines = [
        "Emily 系统冷启动自检报告",
        "==============================",
        f"检查时间：{data.get('check_time', 'N/A')}",
        f"总耗时：{data.get('check_duration_ms', 'N/A')}ms",
        f"整体状态：{data.get('status', 'N/A')}",
        "",
        "--- 用户 ---",
        f"总用户：{_safe_get(data, 'users', 'total')}",
        f"管理员：{_safe_get(data, 'users', 'admins')}",
        "",
        "--- 项目 ---",
        f"项目总数：{_safe_get(data, 'projects', 'total')}",
        "",
        "--- 业务数据 ---",
        f"事件：{_safe_get(data, 'business', 'events')}",
        f"任务：{_safe_get(data, 'business', 'tasks')}",
        f"会议：{_safe_get(data, 'business', 'meetings')}",
        f"文件：{_safe_get(data, 'business', 'files')}",
        "",
        "--- 知识库 ---",
        f"文档：{_safe_get(data, 'knowledge_base', 'total_docs')}",
        f"分块：{_safe_get(data, 'knowledge_base', 'total_chunks')}",
        f"索引：{_safe_get(data, 'knowledge_base', 'index_status')}",
        "",
    ]
    if data.get("warnings"):
        lines.append("--- 警告 ---")
        for w in data["warnings"]:
            lines.append(f"  [!] {w}")
    if data.get("error_message"):
        lines.append("--- 错误 ---")
        lines.append(f"  [ERROR] {data['error_message']}")
    lines.append("==============================")
    return "\n".join(lines)


def _safe_get(data: dict, *keys: str) -> str:
    """安全获取嵌套 dict 值，None → "N/A"。"""
    current: Any = data
    try:
        for k in keys:
            current = current[k]
        if current is None:
            return "N/A"
        return str(current)
    except (KeyError, TypeError):
        return "N/A"


# ══════════════════════════════════════════════════════════════════════════════
# 主入口：ColdStart.run()
# ══════════════════════════════════════════════════════════════════════════════

class ColdStart:
    """冷启动流程编排器。

    静态方法 run() 执行完整冷启动流程：
      1. 自检数据采集
      2. 邮件通知管理员
      3. 世界书 Prompt 生成

    Fail-Open：邮件失败不阻塞，模板缺失用降级方案。
    """

    @staticmethod
    async def run() -> str:
        """执行完整冷启动流程。

        Returns:
            str: 完整的系统 Prompt 上下文文本（世界书 + 注入的自检数据）
        """
        import time as _time
        from self_check import SelfCheck

        t_start = _time.monotonic()
        logger.info("ColdStart: begin")

        # ── 步骤 1: 自检数据采集 ──
        try:
            check_data = SelfCheck.run()
            logger.info(
                "ColdStart: self_check done — status=%s duration=%dms",
                check_data.get("status"), check_data.get("check_duration_ms", 0),
            )
        except Exception as e:
            logger.error("ColdStart: self_check failed: %s", e)
            # 使用空数据占位
            check_data = {
                "check_time": "N/A",
                "check_duration_ms": 0,
                "status": "error",
                "users": {"total": "N/A", "admins": "N/A"},
                "projects": {"total": "N/A"},
                "business": {"events": "N/A", "tasks": "N/A",
                             "meetings": "N/A", "files": "N/A"},
                "knowledge_base": {"total_docs": "N/A", "total_chunks": "N/A",
                                   "index_status": "N/A"},
                "warnings": [],
                "error_message": str(e),
            }

        # ── 步骤 2: 邮件通知 ──
        creds = _build_email_credentials()
        if creds is not None:
            admins = _get_admin_emails()
            email_body = _build_email_body(check_data)
            subject = f"Emily 冷启动自检报告 — {str(check_data.get('status', 'unknown')).upper()}"

            try:
                from emily_core.providers.email.smtp_provider import SMTPEmailProvider
                from emily_core.services.email_service import EmailService
                from emily_core.providers.email.imap_provider import IMAPEmailProvider

                email_service = EmailService(
                    smtp=SMTPEmailProvider(),
                    imap=IMAPEmailProvider(),
                )

                for admin_email in admins:
                    try:
                        result = await email_service.send(
                            creds=creds,
                            to=admin_email,
                            subject=subject,
                            body=email_body,
                            html=False,
                        )
                        if result.success:
                            logger.info(
                                "ColdStart: email sent to %s (message_id=%s)",
                                admin_email, result.message_id,
                            )
                        else:
                            logger.warning(
                                "ColdStart: email failed to %s: %s",
                                admin_email, result.error,
                            )
                    except Exception as e:
                        logger.warning(
                            "ColdStart: email error to %s: %s", admin_email, e,
                        )
            except Exception as e:
                logger.warning("ColdStart: email service init failed: %s", e)
        else:
            logger.info("ColdStart: email skipped (no credentials)")

        # ── 步骤 3: 世界书 Prompt 生成 ──
        try:
            template = _load_world_book()
            prompt = _inject_self_check_data(template, check_data)
            logger.info("ColdStart: world book injected (%d chars)", len(prompt))
        except Exception as e:
            logger.error("ColdStart: world book generation failed: %s", e)
            prompt = _FALLBACK_PROMPT

        elapsed = int((_time.monotonic() - t_start) * 1000)
        logger.info("ColdStart: done — total=%dms", elapsed)

        return prompt


# ══════════════════════════════════════════════════════════════════════════════
# CLI 调试入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys as _sys

    async def _main():
        print("=== Emily ColdStart ===")
        prompt = await ColdStart.run()

        if "--prompt-only" in _sys.argv:
            print(prompt)
        else:
            print(f"\nPrompt generated: {len(prompt)} chars")
            print(f"\n--- Prompt Preview (first 500 chars) ---")
            print(prompt[:500])
            if len(prompt) > 500:
                print(f"... (truncated, total {len(prompt)} chars)")

        print("\nDone.")

    asyncio.run(_main())
