"""migrate_node_type_two_layers.py — 全景节点两层制 · 存量类型迁移。

═══════════════════════════════════════════════════════════════════════════════
用途：
  将全景节点类型从三层（MILESTONE / WORK_PACKAGE / TASK）收敛为两层
  （MILESTONE / TASK），使存量数据与「类型单向派生」规则一致。

迁移动作（全部为 UPDATE，幂等）：
  1. 有子节点但不是里程碑的节点  → MILESTONE（结构提升）
  2. 无子节点且类型为 WORK_PACKAGE → TASK（已退场类型收敛）

不迁移的项（合法保留）：
  - 无子节点的 MILESTONE：登记后尚未分解任务的里程碑，其状态即为「未启动」
  - MILESTONE / TASK 且与结构一致者：不动

用法：
    uv run python scripts/migrate_node_type_two_layers.py --dry-run    # 预览
    uv run python scripts/migrate_node_type_two_layers.py              # 执行
    uv run python scripts/migrate_node_type_two_layers.py --project-id EMR   # 限定项目
    uv run python scripts/migrate_node_type_two_layers.py --recalc-status    # 迁移后重算状态

`--recalc-status`（建议一并执行）：
  类型收敛伴随状态语义变更（任务完成量为 0 → 未启动、里程碑由子节点聚合），
  旧语义下的存量状态需要按新判据自底向上重算一次，否则库内状态与新规则不一致。

回滚：
    迁移前备份：docker exec emily-postgres pg_dump -U emily -d emily -t project_nodes \
      > backup_project_nodes_before_two_layers.sql

系统调用通道：
    from scripts.migrate_node_type_two_layers import run
    result = run(dry_run=True)   # → dict
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import logging
import os
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
logger = logging.getLogger("migrate_node_type_two_layers")

# 容器内 localhost:25432 不可达（那是宿主机映射端口），优先用注入的库地址
DB_URL_DEFAULT = os.environ.get(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily",
)


def _init_db(db_url: str) -> None:
    """初始化数据库连接（复用 emily_core 的 session 模块）。"""
    from emily_core.infrastructure.database.session import init_db
    init_db(db_url)


_DB_READY = False


def _ensure_db(db_url: str) -> None:
    """首次调用时初始化 DB 连接（幂等）。"""
    global _DB_READY
    if not _DB_READY:
        _init_db(db_url)
        _DB_READY = True

_HAS_CHILDREN = (
    "EXISTS (SELECT 1 FROM project_nodes c "
    "WHERE c.parent_node_id = p.node_id AND c.is_discarded = false)"
)


def _project_filter(project_id: str) -> str:
    return f" AND p.project_id = '{project_id}'" if project_id else ""


def _migrations(project_id: str) -> list[dict]:
    """迁移动作清单（label / count_sql / set_sql）。"""
    pf = _project_filter(project_id)
    return [
        {
            "label": "有子节点但非里程碑 → MILESTONE（结构提升）",
            "count_sql": (
                f"SELECT count(*) FROM project_nodes p "
                f"WHERE p.is_discarded = false AND p.node_type <> 'MILESTONE' "
                f"AND {_HAS_CHILDREN}{pf}"
            ),
            "set_sql": (
                f"UPDATE project_nodes p SET node_type = 'MILESTONE' "
                f"WHERE p.is_discarded = false AND p.node_type <> 'MILESTONE' "
                f"AND {_HAS_CHILDREN}{pf}"
            ),
        },
        {
            "label": "无子节点的工作包 → TASK（退场类型收敛）",
            "count_sql": (
                f"SELECT count(*) FROM project_nodes p "
                f"WHERE p.is_discarded = false AND p.node_type = 'WORK_PACKAGE' "
                f"AND NOT {_HAS_CHILDREN}{pf}"
            ),
            "set_sql": (
                f"UPDATE project_nodes p SET node_type = 'TASK' "
                f"WHERE p.is_discarded = false AND p.node_type = 'WORK_PACKAGE' "
                f"AND NOT {_HAS_CHILDREN}{pf}"
            ),
        },
    ]


def preview_nodes(project_id: str = "", db_url: str = DB_URL_DEFAULT) -> list[dict]:
    """列出待迁移节点的逐条映射（供 --dry-run 人工核对）。"""
    from sqlalchemy import text

    from emily_core.infrastructure.database.session import get_session

    _ensure_db(db_url)
    pf = _project_filter(project_id)
    rows: list[dict] = []
    with get_session() as session:
        result = session.execute(text(
            "SELECT p.node_id, p.node_name, p.node_type, "
            "  (SELECT count(*) FROM project_nodes c "
            "   WHERE c.parent_node_id = p.node_id AND c.is_discarded = false) AS kids "
            "FROM project_nodes p "
            "WHERE p.is_discarded = false AND p.node_type = 'WORK_PACKAGE'"
            + pf +
            " ORDER BY p.node_id"
        ))
        for r in result:
            rows.append({
                "node_id": r[0],
                "node_name": r[1],
                "from": r[2],
                "to": "MILESTONE" if r[3] else "TASK",
                "children": r[3],
            })
    return rows


def run(dry_run: bool = False, project_id: str = "", db_url: str = DB_URL_DEFAULT,
        recalc_status: bool = False) -> dict:
    """执行/预览存量类型迁移。

    Args:
        dry_run: True 只统计将影响的行数，不写库
        project_id: 可选，限定单个项目
        db_url: PostgreSQL 连接 URL
        recalc_status: 迁移后自底向上重算全部节点状态（状态语义变更后的存量对齐）

    Returns:
        {"dry_run": bool, "project_id": str,
         "items": [{"label","affected","action"}...],
         "total_affected": int, "mapping": [...], "recalc": {...}}
    """
    from sqlalchemy import text

    from emily_core.infrastructure.database.session import get_session

    _ensure_db(db_url)
    items: list[dict] = []
    total = 0

    with get_session() as session:
        for spec in _migrations(project_id):
            count = session.execute(text(spec["count_sql"])).scalar() or 0
            affected = 0
            if count and not dry_run:
                result = session.execute(text(spec["set_sql"]))
                session.commit()
                affected = result.rowcount or 0
            elif dry_run:
                affected = count

            items.append({
                "label": spec["label"],
                "affected": affected,
                "action": "preview" if dry_run else "applied",
            })
            if affected:
                logger.info("[%s] %s：%d 行",
                            "DRY-RUN" if dry_run else "APPLIED", spec["label"], affected)
            total += affected

        if dry_run:
            session.rollback()

    mapping = preview_nodes(project_id, db_url) if dry_run else []

    # ── 状态存量对齐：类型收敛后，旧语义下的状态需按新判据重算 ──
    recalc: dict = {}
    if recalc_status and not dry_run:
        import asyncio as _asyncio

        from emily_core.services.node_service import NodeService

        recalc = _asyncio.run(NodeService().recalc_all_statuses(project_id))
        logger.info("状态重算：total=%s changed=%s", recalc.get("total"), recalc.get("changed"))

    logger.info("两层制类型迁移%s完成：合计影响 %d 行",
                "预览" if dry_run else "", total)
    return {
        "dry_run": dry_run,
        "project_id": project_id,
        "items": items,
        "total_affected": total,
        "mapping": mapping,
        "recalc": recalc,
    }


def type_distribution(project_id: str = "", db_url: str = DB_URL_DEFAULT) -> dict:
    """当前类型分布（用于迁移后核验）。"""
    from sqlalchemy import text

    from emily_core.infrastructure.database.session import get_session

    _ensure_db(db_url)
    pf = _project_filter(project_id)
    with get_session() as session:
        result = session.execute(text(
            "SELECT p.node_type, count(*) FROM project_nodes p "
            "WHERE p.is_discarded = false" + pf + " GROUP BY 1 ORDER BY 1"
        ))
        return {r[0]: r[1] for r in result}


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _print_report(result: dict, dist_before: dict, dist_after: dict) -> None:
    print("\n" + "=" * 78)
    print(f"全景节点两层制 · 存量类型迁移{'（DRY-RUN 预览）' if result['dry_run'] else ''}")
    if result["project_id"]:
        print(f"限定项目：{result['project_id']}")
    print("=" * 78)
    print(f"{'迁移项':<48} {'影响行数':>10}")
    print("-" * 78)
    for it in result["items"]:
        print(f"{it['label']:<48} {it['affected']:>10}")
    print("-" * 78)
    print(f"{'合计':<48} {result['total_affected']:>10}")

    if result["mapping"]:
        print("-" * 78)
        print(f"{'节点编号':<18} {'名称':<20} {'子节点':>6}  迁移")
        print("-" * 78)
        for m in result["mapping"]:
            print(f"{m['node_id']:<18} {m['node_name'][:18]:<20} {m['children']:>6}  "
                  f"{m['from']} → {m['to']}")

    print("-" * 78)
    print(f"类型分布  迁移前：{dist_before}")
    if not result["dry_run"]:
        print(f"          迁移后：{dist_after}")
    if result.get("recalc"):
        print(f"状态重算  总节点 {result['recalc'].get('total')}，"
              f"状态变化 {result['recalc'].get('changed')}")
    print("=" * 78 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="全景节点两层制 · 存量类型迁移")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计将影响的行数并列出逐条映射，不写库")
    parser.add_argument("--project-id", default="", help="限定项目ID（可选）")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    parser.add_argument("--recalc-status", action="store_true",
                        help="迁移后自底向上重算全部节点状态（旧状态语义 → 新判据的存量对齐）")
    args = parser.parse_args()

    dist_before = type_distribution(args.project_id, args.db_url)
    result = run(dry_run=args.dry_run, project_id=args.project_id, db_url=args.db_url,
                 recalc_status=args.recalc_status)
    dist_after = type_distribution(args.project_id, args.db_url)
    _print_report(result, dist_before, dist_after)

    if not args.dry_run and "WORK_PACKAGE" in dist_after:
        print(f"⚠ 仍有 WORK_PACKAGE 残留：{dist_after}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
