"""migrate_remove_department.py — 部门维度移除 · 存量迁移（PRD US-07）。

═══════════════════════════════════════════════════════════════════════════════
用途：
  一次性清理存量"部门标识"与"审批阻断状态"，使系统与"权限只由单位性质+等级决定、
  信息先记录后认可"的规格一致。

迁移动作（全部为 UPDATE，幂等）：
  1. company_info.department                            → '[]'
  2. permission_groups.department                       → ''
  3. sop_business_flows.require_department_match        → FALSE
  4. sop_business_flows.allowed_departments             → '[]'
  5. project_nodes.owner_dept_id                        → ''
  6. project_nodes.status='NOT_ACTIVATED'               → 'CONDITIONS_NOT_MET'

约束（PRD §4.4 约束 7/8）：
  - 幂等：重复执行第二次影响行数为 0
  - 可预览：--dry-run 只统计不写库
  - 不改可见范围与内容：只动上述列

用法：
    uv run python scripts/migrate_remove_department.py --dry-run   # 预览
    uv run python scripts/migrate_remove_department.py             # 执行

系统调用通道：
    from scripts.migrate_remove_department import run
    result = run(dry_run=True)   # → dict
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# ── 项目根目录（与 manage_nodes.py 同模式，保证可独立运行）──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = PROJECT_ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate_remove_department")

DB_URL_DEFAULT = "postgresql://emily:emily_secret_2026@localhost:25432/emily"


# ══════════════════════════════════════════════════════════════════════════════
# 迁移动作清单
# ══════════════════════════════════════════════════════════════════════════════

_MIGRATIONS: list[dict] = [
    {
        "label": "company_info.department → '[]'",
        "table": "company_info",
        "where": "department IS NOT NULL AND department <> '[]'",
        "set": "department = '[]'",
    },
    {
        "label": "permission_groups.department → ''",
        "table": "permission_groups",
        "where": "department IS NOT NULL AND department <> ''",
        "set": "department = ''",
    },
    {
        "label": "sop_business_flows.require_department_match → FALSE",
        "table": "sop_business_flows",
        "where": "require_department_match IS TRUE",
        "set": "require_department_match = FALSE",
    },
    {
        "label": "sop_business_flows.allowed_departments → '[]'",
        "table": "sop_business_flows",
        "where": "allowed_departments IS NOT NULL AND allowed_departments <> '[]'",
        "set": "allowed_departments = '[]'",
    },
    {
        "label": "project_nodes.owner_dept_id → ''",
        "table": "project_nodes",
        "where": "owner_dept_id IS NOT NULL AND owner_dept_id <> ''",
        "set": "owner_dept_id = ''",
    },
    {
        "label": "project_nodes.status NOT_ACTIVATED → CONDITIONS_NOT_MET",
        "table": "project_nodes",
        "where": "status = 'NOT_ACTIVATED'",
        "set": "status = 'CONDITIONS_NOT_MET'",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# 核心迁移逻辑
# ══════════════════════════════════════════════════════════════════════════════

def run(dry_run: bool = False) -> dict:
    """执行/预览存量迁移。

    Args:
        dry_run: True 只统计将影响的行数，不写库

    Returns:
        {
          "dry_run": bool,
          "items": [{"label","table","affected","action"}...],
          "total_affected": int,
        }
    """
    from sqlalchemy import text

    from emily_core.infrastructure.database.session import get_session

    items: list[dict] = []
    total = 0

    with get_session() as session:
        for spec in _MIGRATIONS:
            table = spec["table"]
            where = spec["where"]

            count = session.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}")
            ).scalar() or 0

            affected = 0
            if count and not dry_run:
                result = session.execute(
                    text(f"UPDATE {table} SET {spec['set']} WHERE {where}")
                )
                session.commit()
                affected = result.rowcount or 0
            elif dry_run:
                affected = count

            item = {
                "label": spec["label"],
                "table": table,
                "affected": affected,
                "action": "preview" if dry_run else "applied",
            }
            items.append(item)

            if affected:
                logger.info("[%s] %s：%d 行",
                            "DRY-RUN" if dry_run else "APPLIED",
                            spec["label"], affected)

            total += affected

        if dry_run:
            # 预览模式不提交（get_session 上下文结束时不落库；显式 rollback 更稳）
            session.rollback()

    logger.info("迁移%s完成：合计影响 %d 行",
                "预览" if dry_run else "", total)
    return {"dry_run": dry_run, "items": items, "total_affected": total}


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _print_report(result: dict) -> None:
    print("\n" + "=" * 72)
    print(f"部门维度移除 · 存量迁移{'（DRY-RUN 预览）' if result['dry_run'] else ''}")
    print("=" * 72)
    print(f"{'迁移项':<52} {'影响行数':>10}")
    print("-" * 72)
    for it in result["items"]:
        print(f"{it['label']:<52} {it['affected']:>10}")
    print("-" * 72)
    print(f"{'合计':<52} {result['total_affected']:>10}")
    print("=" * 72)
    if result["dry_run"]:
        print("（预览模式：未写入数据库；去掉 --dry-run 执行）")


def main() -> None:
    parser = argparse.ArgumentParser(description="部门维度移除 · 存量迁移（幂等）")
    parser.add_argument("--dry-run", "--check", action="store_true",
                        help="仅预览将影响的行数，不写库")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    from emily_core.infrastructure.database.session import init_db
    init_db(args.db_url)

    try:
        result = run(dry_run=args.dry_run)
    except Exception as e:
        logger.exception("迁移失败：%s", e)
        sys.exit(1)

    _print_report(result)


if __name__ == "__main__":
    main()
