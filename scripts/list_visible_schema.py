"""list_visible_schema.py — 列出指定用户可访问的数据库表。

用法：
  uv run python scripts/list_visible_schema.py <user_id> [--json]
"""

from __future__ import annotations

import argparse
import json
import io
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def _detect_docker_pg_port() -> int | None:
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().rsplit(":", 1)[-1])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _init_db():
    from emily_core.infrastructure.database import init_db

    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if db_url:
        init_db(db_url=db_url)
    else:
        pg_host = os.environ.get("EMILY_PG_HOST", os.environ.get("PG_HOST", "127.0.0.1"))
        pg_port_env = os.environ.get("EMILY_PG_PORT", os.environ.get("PG_PORT"))
        if pg_port_env:
            pg_port = int(pg_port_env)
        else:
            pg_port = _detect_docker_pg_port() or 5432
        pg_db = os.environ.get("EMILY_PG_DB", "emily")
        pg_user = os.environ.get("EMILY_PG_USER", "emily")
        pg_password = os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026")
        init_db(pg_host=pg_host, pg_port=pg_port, pg_db=pg_db, pg_user=pg_user, pg_password=pg_password)


TABLE_LABELS = {
    "project": "项目表",
    "event": "事件表",
    "task": "任务表",
    "meeting": "会议表",
    "file": "文件表",
    "message": "消息表",
    "user": "用户表",
    "financial": "财务表",
    "company_info": "企业信息表",
    "project_nodes": "节点表",
    "node_deliverables": "节点成果表",
    "node_accessible_files": "节点文件授权表",
    "plan_task_instances": "计划任务实例表",
    "plan_task_templates": "计划任务模板表",
}


def main():
    parser = argparse.ArgumentParser(description="列出指定用户可访问的数据库表")
    parser.add_argument("user_id", help="用户 ID")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    _init_db()

    from emily_core.repositories.user_repo import UserRepository
    from emily_core.services.permission_service import PermissionService

    user = UserRepository.get_by_id(args.user_id)
    if user is None:
        print(f"[ERROR] 用户不存在: {args.user_id}")
        sys.exit(1)

    perm_service = PermissionService()
    perms = perm_service.build_permission_dict(args.user_id)

    db_perms = perms.get("db_perms", {})

    if args.json:
        print(json.dumps(db_perms, ensure_ascii=False, indent=2))
    else:
        for tbl, perm in sorted(db_perms.items()):
            print(f"{tbl}={perm}")


if __name__ == "__main__":
    main()
