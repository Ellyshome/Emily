"""search_files.py — 文件搜索工具。

双用途：
  1. 作为 API 供系统调用（register() 注册到 BusinessFlowToolRegistry）
  2. 作为独立脚本运行：uv run python -m emily_core.scripts.search_files "精装施工图"

API 签名：
  params:
    query: str        # 自然语言描述或关键词
    top_k: int = 5    # 返回结果数上限
    user_id: str      # 内部注入，不暴露给调用者
  returns:
    dict: {success, total, files: [{file_id, filename, file_type, description, score}], reply}
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ── API 签名 ──
SEARCH_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "自然语言描述或关键词，用于匹配文件名或文件一句话描述",
        },
        "top_k": {
            "type": "integer",
            "description": "返回结果数上限，默认 5",
        },
    },
    "required": ["query"],
}

SEARCH_FILES_DISPLAY_NAME = "根据自然语言描述搜索可见文件"


# ── Handler（系统调用）──

async def handle_search_files(params: dict, **kwargs) -> dict:
    """搜索可见文件 handler。

    通过关键词匹配 session_accessible_files 中用户可见文件的
    文件名（filename）和一句话描述字段。
    """
    query = params.get("query", "").strip()
    top_k = min(int(params.get("top_k", 5)), 20)
    user_id = params.get("_user_id", "") or kwargs.get("user_id", "")

    if not query:
        return {"success": False, "reply": "请提供搜索关键词"}
    if not user_id:
        return {"success": False, "reply": "用户身份未识别"}

    from ..repositories.session_accessible_file_repo import SessionAccessibleFileRepo
    results = SessionAccessibleFileRepo.search(user_id, query, top_k=top_k)

    if not results:
        return {
            "success": True,
            "total": 0,
            "files": [],
            "reply": f"未找到与「{query}」相关的文件",
        }

    reply_parts = [f"找到 {len(results)} 个相关文件："]
    for f in results:
        desc = f.get("description", f.get("file_type", ""))
        reply_parts.append(f"  · {f['filename']}（{desc}）")

    return {
        "success": True,
        "total": len(results),
        "files": results,
        "reply": "\n".join(reply_parts),
    }


# ── 注册函数（供 register_api.py 调用）──

def register(core=None):
    """返回 BusinessFlowTool 实例，供注册器使用。"""
    from ..tools.business_flow_tools import BusinessFlowTool
    return BusinessFlowTool(
        name="search_files",
        description=SEARCH_FILES_DISPLAY_NAME,
        parameters=SEARCH_FILES_SCHEMA,
        handler=handle_search_files,
    )


# ── 独立脚本入口（运维/测试）──

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

    _HERE = Path(__file__).resolve().parent.parent.parent.parent  # project root
    _CORE_DIR = _HERE / "emily-core"
    if str(_CORE_DIR) not in sys.path:
        sys.path.insert(0, str(_CORE_DIR))

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


def main():
    parser = argparse.ArgumentParser(description="搜索可见文件（独立脚本模式）")
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--top-k", type=int, default=5, help="返回结果数上限")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--sync", action="store_true", help="先同步可见文件再搜索")
    args = parser.parse_args()

    _init_db()

    from emily_core.repositories.session_accessible_file_repo import SessionAccessibleFileRepo

    if args.sync:
        # 基本同步：如果有 project_ids 则同步，否则仅清理
        n = SessionAccessibleFileRepo.sync_for_user(
            user_id=args.user_id,
            project_ids=[],
            info_level="internal",
        )
        print(f"[sync] {n} files synced for user {args.user_id}")

    results = SessionAccessibleFileRepo.search(args.user_id, args.query, top_k=args.top_k)

    if args.json:
        print(json.dumps({"total": len(results), "files": results}, ensure_ascii=False, indent=2))
    else:
        if not results:
            print(f"未找到与「{args.query}」相关的文件")
        else:
            print(f"找到 {len(results)} 个相关文件：")
            for f in results:
                desc = f.get("description", f.get("file_type", ""))
                print(f"  · {f['filename']}（{desc}）")


if __name__ == "__main__":
    main()
