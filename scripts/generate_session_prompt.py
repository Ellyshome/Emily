"""generate_session_prompt.py — 为指定用户生成 Session prompt（世界书+规则书）。

用法：
    uv run python scripts/generate_session_prompt.py --user-id <UUID> --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("generate_session_prompt")


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


def generate_session_prompt(user_id: str, *, db_url: str = "", dry_run: bool = False) -> dict:
    """生成指定用户的 Session prompt 段（世界书+规则书）。"""
    _init_db(db_url)

    from emily_core.repositories.user_repo import UserRepository
    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo

    # 查用户关联项目
    user = UserRepository.get_by_id(user_id)
    if user is None:
        return {"error": "用户不存在", "user_id": user_id}

    project_id = getattr(user, "project_id", None)

    # 世界书
    world_book_text = ""
    world_book_tokens = 0
    if project_id:
        wb = ProjectWorldBookRepo.get_by_project(project_id)
        if wb:
            world_book_text = wb.content_text or ""
            world_book_tokens = wb.token_count or 0

    # 规则书
    rule_book_text = ""
    rule_book_path = Path(_CORE_DIR) / ".." / "emily-data" / "rules" / "规则书.md"
    if not rule_book_path.exists():
        # 尝试容器内路径
        rule_book_path = Path("/app/rules/规则书.md")
    if rule_book_path.exists():
        try:
            rule_book_text = rule_book_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read rule book: %s", e)
            rule_book_text = ""

    rule_book_tokens = int(len(rule_book_text) / 1.5) if rule_book_text else 0

    return {
        "user_id": user_id,
        "user_name": user.username,
        "project_id": project_id or "",
        "world_book_text": world_book_text,
        "world_book_tokens": world_book_tokens,
        "rule_book_text_length": len(rule_book_text),
        "rule_book_tokens": rule_book_tokens,
        "total_tokens": world_book_tokens + rule_book_tokens,
        "dry_run": dry_run,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="生成 Session prompt 段")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-text", action="store_true", help="显示完整文本（默认只显示统计）")
    args = parser.parse_args()

    result = generate_session_prompt(args.user_id, db_url=args.db_url, dry_run=args.dry_run)

    if args.show_text:
        print("=== 世界书 ===")
        print(result.get("world_book_text", "（无）"))
        print(f"\n=== 规则书（{result['rule_book_tokens']} tokens）===")
        print(result.get("rule_book_text_length", 0) > 0 and "（已加载）" or "（未找到）")
    else:
        # 只显示统计
        output = {k: v for k, v in result.items() if k not in ("world_book_text",)}
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
