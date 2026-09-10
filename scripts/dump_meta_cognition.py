"""dump_meta_cognition —— 按 book(+query) 打印三书裁剪后全文。

用于验证 meta_cognition_read 工具的裁剪口径与输出规模。

用法：
    uv run python scripts/dump_meta_cognition.py --user-id <UUID> --book world
    uv run python scripts/dump_meta_cognition.py --user-id <UUID> --book rule --query 删除
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if Path("/app/emily_core").exists() and "/app" not in sys.path:
    sys.path.insert(0, "/app")


async def run(*, user_id: str, book: str, query: str = "", db_url: str = "") -> dict:
    """核心通道：返回 handler 原始结果 dict（供系统/其他脚本 import）。"""
    from emily_core.infrastructure.database import init_db
    init_db(db_url=db_url) if db_url else init_db()
    from emily_core.tools.meta_cognition_tool import handle_meta_cognition_read
    return await handle_meta_cognition_read({"book": book, "query": query}, user_id=user_id)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="三书裁剪后全文预览")
    p.add_argument("--user-id", required=True, help="真实用户 UUID")
    p.add_argument("--book", required=True, choices=["world", "rule", "system"])
    p.add_argument("--query", default="")
    p.add_argument("--db-url", default="")
    args = p.parse_args()

    result = asyncio.run(run(user_id=args.user_id, book=args.book,
                             query=args.query, db_url=args.db_url))
    print(f"[success={result.get('success')}] [matched_lines={result.get('matched_lines')}]")
    print(result.get("reply", ""))


if __name__ == "__main__":
    main()
