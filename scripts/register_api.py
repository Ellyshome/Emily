#!/usr/bin/env python3
"""register_api.py — API 注册器。

将工具脚本注册为系统 API，同时完成：
  1. 代码注册到 BusinessFlowToolRegistry
  2. 元数据写入 tool_registry 表

用法：
  # 注册单个 API
  uv run python scripts/register_api.py --api search_files

  # 注册全部 API
  uv run python scripts/register_api.py --all

  # 查看已注册 API
  uv run python scripts/register_api.py --list

  # 查看某个 API 帮助
  uv run python scripts/register_api.py --help-api search_files
"""

from __future__ import annotations

import argparse
import importlib
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

logger = logging.getLogger("emily.register_api")

# Sentinel for mock detection
_SENTINEL = "XXXXXXXXXX"


def _detect_docker_pg_port() -> int | None:
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = result.stdout.strip().rsplit(":", 1)[-1]
            return int(port_str)
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


# ── 工具脚本索引 ──
# 每个条目: (api_id, module_path, category, permission_flag)
_TOOL_SCRIPTS = [
    ("search_files", "emily_core.tools.scripts.search_files", "base", "all"),
]


def do_register(api_id: str) -> bool:
    """注册单个 API：代码注册 + DB 录入。

    Returns:
        True 注册成功，False 注册失败
    """
    _init_db()

    # 查找条目
    entry = None
    for e in _TOOL_SCRIPTS:
        if e[0] == api_id:
            entry = e
            break

    if entry is None:
        print(f"[ERROR] Unknown API: {api_id}")
        return False

    api_id, module_path, category, perm_flag = entry

    # 1. 从工具脚本目录导入 register()
    try:
        module = importlib.import_module(module_path)
        register_fn = getattr(module, "register", None)
        if register_fn is None:
            print(f"[ERROR] {module_path} has no register() function")
            return False
    except Exception as e:
        print(f"[ERROR] Import {module_path} failed: {e}")
        return False

    # 2. 调用 register() 获得 BusinessFlowTool
    try:
        bft = register_fn(core=None)
    except Exception as e:
        print(f"[ERROR] register() failed: {e}")
        return False

    # 3. 代码注册（如果 EmilyCore 已启动则写入，否则跳过）
    # 独立脚本模式只做 DB 录入
    print(f"  [code] Tool '{bft.name}' loaded from {module_path}")

    # 4. DB 录入
    from emily_core.repositories.tool_registry_repo import ToolRegistryRepo

    signature = json.dumps(
        {"params": bft.parameters, "returns": "dict"},
        ensure_ascii=False,
    )

    ok = ToolRegistryRepo.upsert(
        api_id=bft.name,
        signature=signature,
        display_name=(
            bft.description.split("。")[0][:80]
            if bft.description
            else f"Tool: {bft.name}"
        ),
        category=category,
        permission_flag=perm_flag,
        handler_module=getattr(bft.handler, "__module__", module_path),
    )

    if ok:
        print(f"  [DB]   Tool '{bft.name}' registered in tool_registry table")
    else:
        print(f"  [DB]   Tool '{bft.name}' DB upsert FAILED")
        return False

    return True


def cmd_list():
    """列出所有已注册 API。"""
    _init_db()
    from emily_core.repositories.tool_registry_repo import ToolRegistryRepo

    rows = ToolRegistryRepo.get_all()
    if not rows:
        print("(no registered APIs)")
        return

    print(f"\n{'API ID':<24s} {'Category':<10s} {'Active':<6s} Description")
    print("-" * 80)
    for r in rows:
        active = "YES" if r.get("is_active") else "NO"
        print(
            f"  {r['api_id']:<24s} {r.get('category', ''):<10s} "
            f"{active:<6s} {r.get('display_name', '')}"
        )


def cmd_help_api(api_id: str):
    """查看某个 API 帮助。"""
    # 先查 DB
    _init_db()
    from emily_core.repositories.tool_registry_repo import ToolRegistryRepo

    rows = ToolRegistryRepo.get_all()
    found = None
    for r in rows:
        if r["api_id"] == api_id:
            found = r
            break

    if found:
        print(f"\nAPI: {found['api_id']}")
        print(f"  描述: {found.get('display_name', '')}")
        print(f"  类别: {found.get('category', '')}")
        print(f"  权限: {found.get('permission_flag', '')}")
        print(f"  模块: {found.get('handler_module', '')}")
        try:
            sig = json.loads(found.get("signature", "{}"))
            print(f"  签名: {json.dumps(sig, ensure_ascii=False, indent=2)}")
        except json.JSONDecodeError:
            print(f"  签名: {found.get('signature', '')}")
    else:
        print(f"[ERROR] API '{api_id}' not found in tool_registry")


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description="API 注册器")
    parser.add_argument("--api", help="注册单个 API")
    parser.add_argument("--all", action="store_true", help="注册全部 API")
    parser.add_argument("--list", action="store_true", help="查看已注册 API")
    parser.add_argument("--help-api", help="查看某个 API 帮助")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.help_api:
        cmd_help_api(args.help_api)
    elif args.all:
        success = 0
        for e in _TOOL_SCRIPTS:
            api_id = e[0]
            print(f"\n=== Registering {api_id} ===")
            if do_register(api_id):
                success += 1
        print(f"\nDone: {success}/{len(_TOOL_SCRIPTS)} APIs registered")
    elif args.api:
        do_register(args.api)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
