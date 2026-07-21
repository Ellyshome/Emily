"""cold_start.py — 冷启动流程薄聚合脚本。

串联：self_check → check_initialization → build_world_book → 邮件通知
不含业务逻辑，仅做编排。

用法：
    uv run python scripts/cold_start.py
    uv run python scripts/cold_start.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cold_start")

BEIJING_TZ = timezone(timedelta(hours=8))


def _init_db(db_url: str = "") -> None:
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


async def run_cold_start(*, db_url: str = "", dry_run: bool = False) -> dict:
    """冷启动流程：self_check → check_initialization → build_world_book → 邮件通知。"""
    _init_db(db_url)

    # Step 1: 系统自检
    from self_check import self_check
    check_result = self_check(db_url=db_url, dry_run=dry_run)
    print(f"[1/4] 系统自检完成: {check_result.get('projects', {}).get('active', 0)} 个活跃项目")

    # Step 2: 遍历所有 active 项目，检查初始化
    from emily_core.infrastructure.database.models import Project
    from emily_core.infrastructure.database.session import get_session
    from emily_core.services.initialization_checker import InitializationChecker

    checker = InitializationChecker()
    init_results = []

    with get_session() as session:
        projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()

    for p in projects:
        init_result = checker.check(p.id)
        init_results.append({
            "project_id": p.id,
            "project_name": p.name,
            **init_result,
        })
    print(f"[2/4] 初始化检查完成: {len(init_results)} 个项目")

    # Step 3: 构建世界书（对未构建或需要重建的项目）
    from emily_core.services.world_book_builder import ProjectWorldBookBuilder
    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo

    builder = ProjectWorldBookBuilder()
    build_results = []

    for p in projects:
        existing = ProjectWorldBookRepo.get_by_project(p.id)
        if existing is None:
            # 首次构建
            build_result = builder.build(p.id, generated_by="startup", dry_run=dry_run)
            build_results.append({
                "project_id": p.id,
                "project_name": p.name,
                "action": "created",
                **build_result,
            })
            print(f"  世界书构建: {p.name} -> tier=T{build_result.get('initialization_tier', 0)}")
    print(f"[3/4] 世界书构建完成: {len(build_results)} 个新建")

    # Step 4: 邮件通知（fail-open）
    email_sent = 0
    email_failed = 0
    if not dry_run:
        creds = _get_email_credentials()
        if creds is None:
            logger.warning("邮件通知跳过：未配置 EMILY_EMAIL_IDKEY / EMILY_EMAIL_PASSWORD，请检查 .env")
        else:
            from emily_core.infrastructure.database.models import User
            from emily_core.providers.email.smtp_provider import SMTPEmailProvider
            from emily_core.services.email_service import EmailService

            smtp = SMTPEmailProvider()
            email_service = EmailService(smtp=smtp, imap=None)

            for init_r in init_results:
                try:
                    with get_session() as session:
                        admins = session.query(User).filter(
                            User.project_id == init_r["project_id"],
                            User.is_deleted == False,
                            User.is_admin == True,
                        ).all()

                    for admin in admins:
                        if admin.email:
                            tier = init_r.get("tier", "?")
                            subject = f"[Emily] 冷启动完成 - {init_r['project_name']} (T{tier})"
                            body = (
                                f"项目「{init_r['project_name']}」冷启动初始化已完成。\n"
                                f"初始化等级：T{tier}\n"
                                f"时间：{datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
                            )
                            result = await email_service.send(
                                creds=creds,
                                to=admin.email,
                                subject=subject,
                                body=body,
                            )
                            if result.success:
                                logger.info("Cold start email sent: %s -> %s", init_r["project_name"], admin.email)
                                email_sent += 1
                            else:
                                logger.warning("Cold start email failed: %s -> %s: %s",
                                               init_r["project_name"], admin.email, result.error)
                                email_failed += 1
                except Exception as e:
                    logger.warning("Email notification failed for project %s: %s", init_r["project_id"], e)
                    email_failed += 1
    print(f"[4/4] 邮件通知: {email_sent} 已发送, {email_failed} 失败")

    return {
        "self_check": check_result,
        "initialization": init_results,
        "world_books_built": len(build_results),
        "email_sent": email_sent,
        "email_failed": email_failed,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Emily 冷启动流程")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(run_cold_start(db_url=args.db_url, dry_run=args.dry_run))
    print(json.dumps(
        {k: v for k, v in result.items() if k != "self_check"},
        ensure_ascii=False, indent=2, default=str,
    ))


if __name__ == "__main__":
    main()
