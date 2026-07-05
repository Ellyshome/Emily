"""fetch_visible_schema —— 获取用户可见的数据库 Schema 摘要。

被 SessionDataFetcher._sub_fetch_visible_schema() 调用。
也可独立运行：python -m emily_core.session.fetchers.fetch_visible_schema --user-id <UUID>

从 db_perms 获取可访问表列表，格式化为精简文本供 LLM 消费。
"""

from __future__ import annotations

import json
import logging
import argparse

logger = logging.getLogger("emily.session.fetchers.fetch_visible_schema")

DB_URL_DEFAULT = "postgresql://emily:emily_secret_2026@localhost:25432/emily"


def fetch(perms: dict) -> str:
    """获取用户可见的数据库 Schema 摘要。

    Args:
        perms: 权限字典，含 db_perms 等

    Returns:
        精简文本："· 事件表: 读写\n· 任务表: 只读"
    """
    try:
        db_perms = perms.get("db_perms", {})
        if not db_perms:
            return "（无数据库访问权限）"

        # 表名 → 中文标签映射
        table_names = {
            "project": "项目表",
            "events": "事件表",
            "tasks": "任务表",
            "meetings": "会议表",
            "files": "文件表",
            "messages": "消息表",
            "users": "用户表",
            "financial": "财务表",
            "project_nodes": "节点表",
            "node_deliverables": "节点成果表",
        }

        lines = []
        for tbl, perm in sorted(db_perms.items()):
            label = table_names.get(tbl, tbl)
            perm_cn = "读写" if perm == "read_write" else "只读"
            lines.append(f"  · {label}: {perm_cn}")
        return "\n".join(lines) if lines else "（无数据库访问权限）"
    except Exception as e:
        logger.error("fetch_visible_schema failed: %s", e)
        return ""


def main():
    """独立运行入口。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="获取用户可见的数据库 Schema 摘要")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    from ...infrastructure.database import init_db
    init_db(db_url=args.db_url)

    from ...services.permission_service import PermissionService
    perms = PermissionService().build_permission_dict(args.user_id)

    result = fetch(perms)
    print(result)


if __name__ == "__main__":
    main()
