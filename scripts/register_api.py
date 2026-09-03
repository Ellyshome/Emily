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
# 每个条目: (api_id, module_path, schema_var_or_None, display_name, category, permission_flag)
# schema_var_or_None: 该工具的 schema 常量名（如 _EVENT_TOOL_SCHEMA）；无 schema 常量的工具填 None
# category/permission_flag 与 register_all（tools/registry.py）保持一致
_TOOL_SCRIPTS = [
    # base
    ("query_data", "emily_core.tools.query_tool", "_QUERY_TOOL_SCHEMA", "查询项目数据", "base", "all"),
    ("knowledge_search", "emily_core.tools.knowledge_search_tool", "_KNOWLEDGE_SEARCH_SCHEMA", "搜索知识库获取领域知识", "base", "all"),
    # business — 4 个核心 CRUD
    ("record_event", "emily_core.tools.event_tool", "_EVENT_TOOL_SCHEMA", "记录项目事件", "business", "write"),
    ("record_task", "emily_core.tools.task_tool", "_TASK_TOOL_SCHEMA", "创建任务", "business", "write"),
    ("record_meeting", "emily_core.tools.meeting_tool", "_MEETING_TOOL_SCHEMA", "归档会议纪要", "business", "write"),
    ("record_file", "emily_core.tools.file_tool", "_FILE_TOOL_SCHEMA", "记录文件元数据", "business", "write"),
    # business — 文件查询 + 分类
    ("query_files", "emily_core.tools.file_tool", "_QUERY_FILES_SCHEMA", "按分类或关键词查询项目文件", "business", "all"),
    ("update_file_category", "emily_core.tools.file_tool", "_UPDATE_CATEGORY_SCHEMA", "修改文件分类归属", "business", "write"),
    # business — 用户记忆
    ("write_user_memory", "emily_core.tools.memory_tool", None, "写入用户长期记忆", "business", "all"),
    # business — 节点任务 5 个
    ("create_task_node", "emily_core.tools.node_task_tool", None, "创建TASK类型叶子节点", "business", "write"),
    ("submit_node_deliverable", "emily_core.tools.node_task_tool", None, "提交节点成果", "business", "write"),
    ("confirm_node_deliverable", "emily_core.tools.node_task_tool", None, "确认节点成果", "business", "write"),
    ("return_node_deliverable", "emily_core.tools.node_task_tool", None, "退回节点成果", "business", "write"),
    ("query_my_nodes", "emily_core.tools.node_task_tool", None, "查询我负责的节点", "business", "write"),
    # project — 全景节点 8 个
    ("create_node", "emily_core.tools.node_tool", "_CREATE_NODE_SCHEMA", "创建全景节点", "project", "admin"),
    ("query_node", "emily_core.tools.node_tool", "_QUERY_NODE_SCHEMA", "查询全景节点", "project", "admin"),
    ("update_node_progress", "emily_core.tools.node_tool", "_UPDATE_PROGRESS_SCHEMA", "更新节点进度", "project", "admin"),
    ("add_node_dependency", "emily_core.tools.node_tool", "_ADD_DEPENDENCY_SCHEMA", "添加节点依赖", "project", "admin"),
    ("mount_child_node", "emily_core.tools.node_tool", "_MOUNT_CHILD_SCHEMA", "挂载子节点", "project", "admin"),
    ("update_nodes", "emily_core.tools.node_tool", "_UPDATE_NODES_SCHEMA", "批量更新节点", "project", "admin"),
    ("activate_nodes", "emily_core.tools.node_tool", "_ACTIVATE_NODES_SCHEMA", "批量激活节点", "project", "admin"),
    ("discard_nodes", "emily_core.tools.node_tool", "_DISCARD_NODES_SCHEMA", "批量废弃节点", "project", "admin"),
    # project — 邮箱 2 个
    ("send_email", "emily_core.tools.project", "_SEND_EMAIL_SCHEMA", "发送邮件", "base", "all"),
    ("fetch_inbox", "emily_core.tools.project", "_FETCH_INBOX_SCHEMA", "获取收件箱", "base", "all"),
    # project — 其他
    ("chat_archive", "emily_core.tools.project", "_CHAT_ARCHIVE_SCHEMA", "聊天归档查询", "base", "all"),
    ("manage_pending_issues", "emily_core.tools.project", "_PENDING_ISSUE_SCHEMA", "管理待解决问题", "base", "all"),
    # emily_core/scripts 下的工具
    ("search_files", "emily_core.scripts.search_files", "SEARCH_FILES_SCHEMA", "搜索可见文件", "base", "all"),
]


def do_register(api_id: str) -> bool:
    """注册单个 API：从模块取 schema 常量构建 signature，写入 tool_registry 表。

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

    api_id, module_path, schema_var, display_name, category, perm_flag = entry

    # 从模块 import schema 常量（构建 signature）
    parameters = {"type": "object", "properties": {}}
    handler_module = module_path
    try:
        m = importlib.import_module(module_path)
        if schema_var:
            schema = getattr(m, schema_var, None)
            if isinstance(schema, dict):
                parameters = schema
        # handler_module 取模块里 handle_xxx 函数的 __module__（若存在）
        for attr_name in dir(m):
            if attr_name.startswith("handle_") or attr_name == "register":
                attr = getattr(m, attr_name)
                if hasattr(attr, "__module__"):
                    handler_module = attr.__module__
                    break
    except Exception as e:
        print(f"[WARN] import {module_path} failed: {e}（仍写入 DB，parameters 用空 schema）")

    # DB 录入
    from emily_core.repositories.tool_registry_repo import ToolRegistryRepo

    signature = json.dumps(
        {"params": parameters, "returns": "dict"},
        ensure_ascii=False,
    )

    ok = ToolRegistryRepo.upsert(
        api_id=api_id,
        signature=signature,
        display_name=display_name,
        category=category,
        permission_flag=perm_flag,
        handler_module=handler_module,
    )

    if ok:
        print(f"  [DB]   Tool '{api_id}' registered ({category}/{perm_flag})")
    else:
        print(f"  [DB]   Tool '{api_id}' DB upsert FAILED")
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
