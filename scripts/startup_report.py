"""startup_report.py — 冷启动邮件报告预览/发送脚本。

从 bootstrap.py 抽取的 build_startup_email_body() 纯函数，支持独立执行，
供开发者在本地预览冷启动邮件内容，或手动发送启动通知。

用法：
    uv run python scripts/startup_report.py --dry-run     # 仅输出邮件正文到 stdout，不发送
    uv run python scripts/startup_report.py               # 采集数据 + 输出正文 + 发送邮件
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

# 加载 .env 文件（位于项目根目录）
_ENV_FILE = _HERE.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip().strip("'").strip('"')
                if _key and _key not in os.environ:
                    os.environ[_key] = _val

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("startup_report")

BEIJING_TZ = timezone(timedelta(hours=8))

# ── 抑制 emily 模块的详细日志，只保留 WARNING 级别 ──
for _mod in ["emily", "emily.bootstrap", "emily.node_repo"]:
    logging.getLogger(_mod).setLevel(logging.WARNING)


def _init_db(db_url: str = "") -> None:
    """初始化数据库连接（复用 cold_start.py 的模式）。"""
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def _get_email_credentials():
    """从环境变量获取邮箱凭证，失败返回 None（fail-open）。"""
    from emily_core.providers.email.base import EmailCredentials

    username = os.environ.get("EMILY_EMAIL_IDKEY", "")
    password = os.environ.get("EMILY_EMAIL_PASSWORD", "")
    if not username or not password:
        return None

    return EmailCredentials(
        smtp_host=os.environ.get("EMILY_EMAIL_SMTP_HOST", "smtp.qq.com"),
        smtp_port=int(os.environ.get("EMILY_EMAIL_SMTP_PORT", "465")),
        imap_host=os.environ.get("EMILY_EMAIL_IMAP_HOST", "imap.qq.com"),
        imap_port=int(os.environ.get("EMILY_EMAIL_IMAP_PORT", "993")),
        username=username,
        password=password,
        use_ssl=True,
    )


def _check_base_tools() -> list[dict]:
    """基座工具就绪检查（复刻 bootstrap.py 逻辑）。"""
    import urllib.request
    checks = []

    # 1. 数据查询 — 依赖数据库连接（查询事件/任务/项目/消息等业务数据）
    try:
        from emily_core.infrastructure.database.session import get_session
        from emily_core.infrastructure.database.models import Project
        with get_session() as session:
            count = session.query(Project).count()
        checks.append({"name": "数据查询", "status": "ok", "detail": f"{count} 个项目"})
    except Exception as e:
        checks.append({"name": "数据查询", "status": "degraded", "detail": str(e)})

    # 2. 知识库检索 — 检查 EMILY_KB_ENABLED + TEI 是否可达
    kb_enabled = os.environ.get("EMILY_KB_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
    tei_url = os.environ.get("EMILY_TEI_URL", "").strip()
    if not kb_enabled or not tei_url:
        checks.append({"name": "知识库检索", "status": "degraded", "detail": "未启用（EMILY_KB_ENABLED 或 EMILY_TEI_URL 未设置）"})
    else:
        # 尝试连接 TEI 做健康检查
        try:
            req = urllib.request.Request(f"{tei_url}/health", method="GET")
            urllib.request.urlopen(req, timeout=5)
            checks.append({"name": "知识库检索", "status": "ok", "detail": f"TEI 已连接 ({tei_url})"})
        except Exception as e:
            checks.append({"name": "知识库检索", "status": "degraded", "detail": f"TEI 不可达 ({tei_url}): {e}"})

    return checks


def _collect_active_projects() -> list[dict]:
    """采集活跃项目信息：名称 / 项目负责人 / 超级管理员。"""
    try:
        from emily_core.infrastructure.database.session import get_session
        from emily_core.infrastructure.database.models import Project, User, CompanyInfo

        with get_session() as session:
            projects = session.query(Project).filter(
                Project.is_deleted == False,
                Project.status == "active",
            ).all()

            result: list[dict] = []
            for p in projects:
                # 项目负责人：通过该项目管理单位（CompanyInfo.is_admin=True）的 project_leader_id 查找
                leader_name = ""
                try:
                    # 找到该项目的管理单位
                    mgmt_company = (
                        session.query(CompanyInfo)
                        .join(User, User.company == CompanyInfo.id)
                        .filter(User.project_id == p.id, CompanyInfo.is_admin == True)
                        .first()
                    )
                    if mgmt_company and mgmt_company.project_leader_id:
                        leader = session.query(User).filter(
                            User.id == mgmt_company.project_leader_id,
                            User.is_deleted == False,
                        ).first()
                        if leader:
                            leader_name = leader.username
                except Exception:
                    pass

                # 超级管理员：项目中 level >= 5 的用户
                admins = (
                    session.query(User)
                    .filter(
                        User.project_id == p.id,
                        User.level >= 5,
                        User.is_deleted == False,
                    )
                    .all()
                )
                admin_names = [u.username for u in admins]

                result.append({
                    "project_name": p.name,
                    "project_leader": leader_name or "(未设置)",
                    "super_admins": admin_names if admin_names else ["(无)"],
                })

            return result
    except Exception as e:
        logger.warning("Active projects collection failed: %s", e)
        return []


async def run_startup_report(*, db_url: str = "", dry_run: bool = False) -> str:
    """采集启动报告数据，构建邮件正文，可选发送。

    Returns:
        邮件正文字符串。
    """
    _init_db(db_url)

    started_at = datetime.now(BEIJING_TZ)
    started_monotonic = time.monotonic()

    # ── 采集启动报告数据（复刻 bootstrap.py init() 中的 startup_report 组装）──
    from emily_core.infrastructure.database.models import Base

    db_ready = True  # _init_db 成功则 DB 就绪
    llm_configured = bool(os.environ.get("EMILY_LLM_API_KEY"))
    smtp_configured = bool(os.environ.get("EMILY_EMAIL_IDKEY"))
    kb_enabled = os.environ.get("EMILY_KB_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")

    degradations: list[str] = []
    if not llm_configured:
        degradations.append("LLM 未配置 — 语义理解/自主规划不可用")
    if not smtp_configured:
        degradations.append("SMTP 未配置 — 邮件通知不可用")

    startup_report = {
        "started_at": started_at,
        "duration_s": time.monotonic() - started_monotonic,
        "hostname": socket.gethostname(),
        "env": os.environ.get("EMILY_ENV", "unspecified"),
        "db_ready": db_ready,
        "db_tables": len(Base.metadata.tables),
        "migrations": [],
        "llm_configured": llm_configured,
        "llm_model": os.environ.get("EMILY_LLM_MODEL", "unspecified"),
        "rag_enabled": kb_enabled,
        "smtp_configured": smtp_configured,
        "base_tools": _check_base_tools(),
        "degradations": degradations,
        "active_projects": _collect_active_projects(),
        "config_summary": {
            "takeover_mode": os.environ.get("EMILY_TAKEOVER_MODE", "unknown"),
            "bot_name": os.environ.get("EMILY_BOT_NAME", "unknown"),
            "kb_enabled": os.environ.get("EMILY_KB_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"),
        },
    }

    # ── 构建邮件正文 ──
    from emily_core.bootstrap import build_startup_email_body
    body = build_startup_email_body(startup_report)

    # ── 输出 ──
    print("=" * 60)
    print(body)
    print("=" * 60)

    # ── 发送邮件（非 dry-run）──
    if not dry_run:
        creds = _get_email_credentials()
        if creds is None:
            logger.warning("邮件发送跳过：未配置 EMILY_EMAIL_IDKEY / EMILY_EMAIL_PASSWORD")
        else:
            try:
                from emily_core.providers.email.smtp_provider import SMTPEmailProvider
                from emily_core.services.email_service import EmailService

                smtp = SMTPEmailProvider()
                email_service = EmailService(smtp=smtp, imap=None)
                subject = "[Emily] 系统启动完成"

                result = await email_service.send(
                    creds=creds,
                    to=creds.username,
                    subject=subject,
                    body=body,
                )
                if result.success:
                    logger.info("Startup report email sent to %s", creds.username)
                else:
                    logger.warning("Startup report email failed: %s", result.error)
            except Exception as e:
                logger.warning("Startup report email send failed: %s", e)

    return body


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Emily 冷启动邮件报告预览/发送")
    parser.add_argument("--db-url", default="", help="数据库连接串（可选，优先级高于 .env）")
    parser.add_argument("--dry-run", action="store_true", help="仅输出邮件正文到 stdout，不发送")
    args = parser.parse_args()

    body = asyncio.run(run_startup_report(db_url=args.db_url, dry_run=args.dry_run))

    if args.dry_run:
        print("\n[dry-run] 邮件未实际发送。去掉 --dry-run 以发送邮件。")


if __name__ == "__main__":
    main()
