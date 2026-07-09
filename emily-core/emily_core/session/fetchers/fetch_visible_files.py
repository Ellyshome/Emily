"""fetch_visible_files —— 获取用户可见文件摘要。

被 SessionDataFetcher._sub_fetch_visible_files() 调用。
也可独立运行：python -m emily_core.session.fetchers.fetch_visible_files --user-id <UUID>

从 session_accessible_files 表获取用户可见文件的统计摘要。
"""

from __future__ import annotations

import json
import logging
import argparse

logger = logging.getLogger("emily.session.fetchers.fetch_visible_files")

import os
DB_URL_DEFAULT = os.getenv(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily"
)


def fetch(user_id: str) -> dict:
    """获取用户可见文件摘要。

    Args:
        user_id: 用户 UUID

    Returns:
        {"count": 23, "by_type": {"施工图": 8, "报告": 5, "规范": 3, "其他": 7}}
    """
    try:
        from ...repositories.session_accessible_file_repo import SessionAccessibleFileRepo
        return SessionAccessibleFileRepo.get_file_summary(user_id)
    except Exception as e:
        logger.error("fetch_visible_files failed user=%s: %s", user_id, e)
        return {"count": 0, "by_type": {}}


def format_summary(visible_files: dict) -> str:
    """格式化可见文件摘要为文本（供 SessionContext 使用）。"""
    count = visible_files.get("count", 0)
    if count == 0:
        return "无可见文件"

    by_type = visible_files.get("by_type", {})
    type_parts = []
    for ft, cnt in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        type_parts.append(f"{ft}({cnt}个)")

    by_category = visible_files.get("by_category", {})
    cat_parts = []
    from ...infrastructure.database.models import FileCategory
    for cat, cnt in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
        cat_parts.append(f"{FileCategory.display(cat)}({cnt}个)")

    cat_str = f"，分类：{', '.join(cat_parts)}" if cat_parts else ""
    return f"共 {count} 个文件（{', '.join(type_parts)}）{cat_str}" if type_parts else f"共 {count} 个文件{cat_str}"


def main():
    """独立运行入口。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="获取用户可见文件摘要")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    parser.add_argument("--text", action="store_true", help="输出文本摘要而非 JSON")
    args = parser.parse_args()

    from ...infrastructure.database import init_db
    init_db(db_url=args.db_url)

    result = fetch(args.user_id)

    if args.text:
        print(format_summary(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
