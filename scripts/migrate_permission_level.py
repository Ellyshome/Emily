#!/usr/bin/env python
"""权限系统 v2.0 数据迁移脚本 —— grouping→org_category + permission_level 1-6。

按需求-完整版 §2 和实施计划 §3.1.1 映射表迁移：
  旧 grouping 0(临时组) → permission_level 1(L1 访客)
  旧 grouping 1(访客组) → permission_level 1(L1 访客)
  旧 grouping 2(工程组) → permission_level 2(L2 参建执行)
  旧 grouping 3(供货商) → permission_level 2(L2 参建执行)
  旧 grouping 4(管理组) → permission_level 5(L5 管理员) ⚠ 需人工确认是否升级 L6
  is_admin=True → permission_level 6(L6 系统管理员)

同时：
  - users.grouping 改名 org_category（保留旧值作组织标签，不参与鉴权）
  - users.permission_level default 改 1
  - users.company 清理空 JSON "[]" → NULL（语义改 FK→company_info.id）
  - permission_groups.min_grouping_level → min_permission_level
  - sop_business_flows.min_grouping → min_permission_level + 新增 security_level/required_node_ids
  - company_info 新增 type/status/scope/partners/parent_id/department/function_scope
  - 8 张新表由 ORM create_all 创建
  - permission_audit_log 不可篡改触发器（禁止 UPDATE/DELETE）

用法：
  uv run python scripts/migrate_permission_level.py --dry-run   # 仅打印 SQL 不执行
  uv run python scripts/migrate_permission_level.py             # 执行迁移
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from sqlalchemy import create_engine, inspect, text

# 确保容器内 /app 或宿主机 emily-core 在 path（用于步骤7 create_all 导入 emily_core.models）
from pathlib import Path as _Path
for _candidate in [_Path("/app"), _Path(__file__).resolve().parents[1] / "emily-core"]:
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_permission")

# grouping → permission_level 映射（实施计划 §3.1.1）
# is_admin 优先于 grouping（管理员直接 L6）
GROUPING_TO_LEVEL_SQL = """
    CASE
        WHEN is_admin = TRUE THEN 6
        WHEN grouping = 0 THEN 1
        WHEN grouping = 1 THEN 1
        WHEN grouping = 2 THEN 2
        WHEN grouping = 3 THEN 2
        WHEN grouping = 4 THEN 5
        ELSE 1
    END
"""


def get_engine():
    """从环境变量或默认值构建 SQLAlchemy engine。"""
    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if not db_url:
        # 默认连接宿主机映射的 emily-postgres
        db_url = "postgresql://emily:emily@localhost:5432/emily"
    logger.info("数据库连接: %s", db_url.replace("emily:emily", "***"))
    return create_engine(db_url)


def column_exists(inspector, table: str, column: str) -> bool:
    try:
        cols = [c["name"] for c in inspector.get_columns(table)]
    except Exception:
        return False
    return column in cols


def table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def run(conn, sql: str, dry_run: bool, label: str):
    """执行一条 SQL（dry_run 时仅打印）。"""
    logger.info("[%s] %s", label, " ".join(sql.split()))
    if not dry_run:
        conn.execute(text(sql))


def migrate(engine, dry_run: bool = False):
    inspector = inspect(engine)

    with engine.begin() as conn:
        # ── 1. users.grouping → org_category ──
        if column_exists(inspector, "users", "grouping") and not column_exists(inspector, "users", "org_category"):
            run(conn, "ALTER TABLE users RENAME COLUMN grouping TO org_category", dry_run, "1")
        else:
            logger.info("[1] skip: users.grouping 已改名或 org_category 已存在")

        # 刷新 inspector（列改名后，用同一连接避免死锁）
        inspector = inspect(conn)

        # ── 2. users.permission_level 值映射 + default ──
        if column_exists(inspector, "users", "permission_level") and column_exists(inspector, "users", "org_category"):
            # 旧 grouping 值已保留在 org_category，据此映射 permission_level
            mapping_sql = f"""
                UPDATE users SET permission_level = CASE
                    WHEN is_admin = TRUE THEN 6
                    WHEN org_category = 0 THEN 1
                    WHEN org_category = 1 THEN 1
                    WHEN org_category = 2 THEN 2
                    WHEN org_category = 3 THEN 2
                    WHEN org_category = 4 THEN 5
                    ELSE 1
                END
            """
            run(conn, mapping_sql, dry_run, "2-mapping")
            run(conn, "ALTER TABLE users ALTER COLUMN permission_level SET DEFAULT 1", dry_run, "2-default")
        else:
            logger.info("[2] skip: users.permission_level 或 org_category 列不存在")

        # ── 3. users.company 清理空 JSON（语义改 FK）──
        if column_exists(inspector, "users", "company"):
            run(conn, "UPDATE users SET company = NULL WHERE company IN ('[]', '')", dry_run, "3")

        # ── 4. permission_groups.min_grouping_level → min_permission_level ──
        if column_exists(inspector, "permission_groups", "min_grouping_level") and not column_exists(inspector, "permission_groups", "min_permission_level"):
            run(conn, "ALTER TABLE permission_groups RENAME COLUMN min_grouping_level TO min_permission_level", dry_run, "4")

        # ── 5. sop_business_flows.min_grouping → min_permission_level + 新增列 ──
        if column_exists(inspector, "sop_business_flows", "min_grouping") and not column_exists(inspector, "sop_business_flows", "min_permission_level"):
            run(conn, "ALTER TABLE sop_business_flows RENAME COLUMN min_grouping TO min_permission_level", dry_run, "5-rename")
        inspector = inspect(conn)
        if not column_exists(inspector, "sop_business_flows", "security_level"):
            run(conn, "ALTER TABLE sop_business_flows ADD COLUMN security_level VARCHAR(20) DEFAULT 'PUBLIC'", dry_run, "5-security")
        if not column_exists(inspector, "sop_business_flows", "required_node_ids"):
            run(conn, "ALTER TABLE sop_business_flows ADD COLUMN required_node_ids VARCHAR DEFAULT '[]'", dry_run, "5-nodes")

        # ── 6. company_info 新增列 ──
        new_cols = [
            ("type", "VARCHAR(50) DEFAULT ''"),
            ("status", "VARCHAR(50) DEFAULT 'active'"),
            ("scope", "VARCHAR DEFAULT '[]'"),
            ("partners", "VARCHAR DEFAULT '[]'"),
            ("department", "VARCHAR DEFAULT '[]'"),
            ("function_scope", "TEXT DEFAULT '{}'"),
        ]
        for col, typedef in new_cols:
            if not column_exists(inspector, "company_info", col):
                run(conn, f"ALTER TABLE company_info ADD COLUMN {col} {typedef}", dry_run, f"6-{col}")
        if not column_exists(inspector, "company_info", "parent_id"):
            run(conn, "ALTER TABLE company_info ADD COLUMN parent_id VARCHAR REFERENCES company_info(id)", dry_run, "6-parent_id")

    # ── 7. 新增 8 张表由 ORM create_all 创建 ──
    logger.info("[7] create_all 新表（permission_def/grants/requests/audit_log/...）")
    if not dry_run:
        try:
            from emily_core.infrastructure.database.models import Base
            Base.metadata.create_all(engine, checkfirst=True)
            logger.info("[7] 新表创建完成（checkfirst 幂等）")
        except Exception as e:
            logger.error("[7] create_all 失败: %s", e)
            raise

    # ── 8. permission_audit_log 不可篡改触发器（需求 §8.2）──
    inspector = inspect(engine)
    if table_exists(inspector, "permission_audit_log"):
        trigger_sql = """
        CREATE OR REPLACE FUNCTION prevent_permission_audit_log_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'permission_audit_log is append-only (INSERT only)';
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS trg_pal_no_update ON permission_audit_log;
        CREATE TRIGGER trg_pal_no_update BEFORE UPDATE ON permission_audit_log
            FOR EACH ROW EXECUTE FUNCTION prevent_permission_audit_log_modify();

        DROP TRIGGER IF EXISTS trg_pal_no_delete ON permission_audit_log;
        CREATE TRIGGER trg_pal_no_delete BEFORE DELETE ON permission_audit_log
            FOR EACH ROW EXECUTE FUNCTION prevent_permission_audit_log_modify();
        """
        logger.info("[8] 创建 permission_audit_log 不可篡改触发器（禁止 UPDATE/DELETE）")
        if not dry_run:
            with engine.begin() as conn:
                conn.execute(text(trigger_sql))

    logger.info("=" * 60)
    logger.info("迁移完成 ✅" if not dry_run else "[dry-run] 未执行变更（加 --dry-run 已移除即真实执行）")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="权限系统 v2.0 数据迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅打印 SQL 不执行")
    args = parser.parse_args()

    try:
        engine = get_engine()
        migrate(engine, dry_run=args.dry_run)
    except Exception as e:
        logger.error("迁移失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
