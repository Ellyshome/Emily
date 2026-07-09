"""文件分类迁移脚本 — 加字段 + LLM 批量回填。

用法：
  uv run python scripts/migrate_file_category.py --dry-run      # 预览模式
  uv run python scripts/migrate_file_category.py                # 实际写入
  uv run python scripts/migrate_file_category.py --ddl-only     # 仅执行 DDL
"""

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

DB_URL = os.getenv(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@emily-postgres:5432/emily",
)


def run_ddl(conn):
    """执行 DDL：加字段 + 加索引。"""
    cur = conn.cursor()

    # 检查字段是否已存在
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'files' AND column_name = 'file_category'
    """)
    if cur.fetchone():
        print("  file_category 列已存在，跳过 DDL")
        return

    cur.execute("ALTER TABLE files ADD COLUMN file_category VARCHAR(50) DEFAULT 'OTHER'")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_files_category ON files(file_category)")
    conn.commit()
    print("  DDL 完成: file_category 列 + 索引已创建")


def run_backfill(conn, llm_client=None, dry_run=False):
    """LLM 批量回填已有文件的分类。"""
    import psycopg2.extras

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, file_no, filename, file_type, file_category
        FROM files
        WHERE file_category = 'OTHER' AND is_deleted = FALSE
        ORDER BY created_at DESC
        LIMIT 500
    """)
    rows = cur.fetchall()
    print(f"  需回填文件数: {len(rows)}")

    if not rows:
        print("  无需回填")
        return

    CATEGORY_PROMPT = """请根据以下文件信息判断其业务分类。

文件名: {filename}
文件类型: {file_type}

分类选项（只返回枚举值，不要解释）:
- PROJECT_LICENSE: 项目证照（政府许可、法律效力证书）
- CONTRACT: 承包合同（参建单位合同、界面划分、预算清单）
- WORK_RECORD: 工作记录（完工确认、进场记录、巡检报告、验收单）
- PHASE_DELIVERABLE: 阶段成果（设计方案、施工图、专项方案）
- PROCESS_DOC: 过程文件（政府整改/通知、飞检评分、年终总结）
- MANAGEMENT_SPEC: 管理规程（操作手册、工艺规程、标准规范）
- OTHER: 其他文件

只返回枚举值（如 CONTRACT），不要返回其他内容。"""

    updated = 0
    for row in rows:
        filename = row["filename"] or ""
        file_type = row["file_type"] or ""

        if llm_client:
            try:
                prompt = CATEGORY_PROMPT.format(filename=filename, file_type=file_type)
                result = llm_client.chat(prompt)
                category = result.strip().split("\n")[0].strip()
                # 校验
                from emily_core.infrastructure.database.models import FileCategory
                category = FileCategory.validate(category)
            except Exception as e:
                print(f"    LLM 回填失败 {row['file_no']}: {e}, 使用 OTHER")
                category = "OTHER"
        else:
            category = "OTHER"

        if not dry_run and category != "OTHER":
            cur.execute(
                "UPDATE files SET file_category = %s WHERE id = %s",
                (category, row["id"]),
            )
            updated += 1

        print(f"    {row['file_no']}: {filename[:30]:30} → {category}")

    if not dry_run:
        conn.commit()

    print(f"  回填完成: 更新 {updated} 条 (dry_run={dry_run})")


def main():
    parser = argparse.ArgumentParser(description="文件分类迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览模式")
    parser.add_argument("--ddl-only", action="store_true", help="仅执行 DDL")
    parser.add_argument("--db-url", default=DB_URL, help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    print("=== 文件分类迁移 ===")

    try:
        import psycopg2
        # 本地开发用 localhost 端口
        local_url = args.db_url.replace("emily-postgres", "localhost").replace(":5432", ":25432")
        conn = psycopg2.connect(local_url)
    except Exception as e:
        print(f"数据库连接失败: {e}")
        print("提示: 确保 PostgreSQL 可达，或通过 --db-url 指定连接串")
        return

    print("Step 1: DDL")
    run_ddl(conn)

    if not args.ddl_only:
        print("Step 2: 回填")
        run_backfill(conn, dry_run=args.dry_run)

    conn.close()
    print("Done!")


if __name__ == "__main__":
    main()
