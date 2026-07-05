"""list_available_tools.py — 列出指定用户可用的 API 工具 + RAG 状态。

用法：
  uv run python scripts/list_available_tools.py <user_id> [--json] [--help-tool <tool_name>]
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


CATEGORY_LABELS = {
    "base": "基座能力（全员可用）",
    "business": "业务工具（权限过滤）",
    "project": "项目工具（仅管理员）",
}


def main():
    parser = argparse.ArgumentParser(description="列出指定用户可用的 API 工具 + RAG 状态")
    parser.add_argument("user_id", help="用户 ID")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--help-tool", help="查看某个工具的详细信息")
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

    # ── API 工具 ──
    from emily_core.session.session_data_fetcher import _sub_fetch_available_tools
    tools = _sub_fetch_available_tools(perms)

    if args.help_tool:
        target = None
        for t in tools:
            if t["api_id"] == args.help_tool:
                target = t
                break
        if target:
            if args.json:
                print(json.dumps(target, ensure_ascii=False, indent=2))
            else:
                print(f"\n工具: {target['api_id']}")
                print(f"  描述: {target['display_name']}")
                print(f"  分类: {target['category']} ({CATEGORY_LABELS.get(target['category'], '')})")
                print(f"  权限: {target['permission_flag']}")
                print(f"  模块: {target.get('handler_module', '')}")
                try:
                    sig = json.loads(target.get("signature", "{}"))
                    if "params" in sig and sig["params"]:
                        print(f"  参数:")
                        if isinstance(sig["params"], dict):
                            props = sig["params"].get("properties", {})
                            required = sig["params"].get("required", [])
                            for pname, pinfo in props.items():
                                req = " [必填]" if pname in required else ""
                                print(f"    - {pname}: {pinfo.get('description', pinfo.get('type', ''))}{req}")
                except json.JSONDecodeError:
                    pass
        else:
            print(f"[ERROR] 工具 '{args.help_tool}' 不可用或不存在")
        return

    if args.json:
        print(json.dumps(tools, ensure_ascii=False, indent=2))
    else:
        if not tools:
            print("(无可用工具)")
        else:
            for t in tools:
                print(t["api_id"])


if __name__ == "__main__":
    main()
