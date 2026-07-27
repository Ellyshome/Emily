"""一次性迁移：按 file_category 推断 purpose，标 purpose_confirmed。

用法：
  uv run python scripts/migrate_file_purpose.py --dry-run   # 预览
  uv run python scripts/migrate_file_purpose.py --apply       # 实际写入
"""

import argparse
import logging
import sys
from pathlib import Path

# 确保 emily_core 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "emily-core"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("migrate_file_purpose")

# 映射表：file_category → purpose（设计文档 §十三 事项 1）
CATEGORY_TO_PURPOSE = {
    "PROJECT_LICENSE": "EVIDENCE",
    "CONTRACT": "EVIDENCE",
    "WORK_RECORD": "RECORD",
    "PHASE_DELIVERABLE": "RECORD",
    "PROCESS_DOC": "DESIGN",
    "MANAGEMENT_SPEC": "REFERENCE",
    "OTHER": "RECORD",  # 兜底，标 purpose_confirmed=False
}


def migrate(dry_run: bool = True) -> None:
    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import File

    with get_session() as session:
        files = session.query(File).filter(File.is_deleted == False).all()

        if not files:
            logger.info("没有找到非删除文件")
            return

        stats: dict[str, int] = {}
        updates = 0
        for f in files:
            old_category = (f.file_category or "OTHER").strip()
            new_purpose = CATEGORY_TO_PURPOSE.get(old_category, "RECORD")
            confirmed = (old_category != "OTHER")

            f.purpose = new_purpose
            f.purpose_confirmed = confirmed

            stats[new_purpose] = stats.get(new_purpose, 0) + 1
            updates += 1

        if dry_run:
            logger.info("=== DRY RUN ===")
            logger.info("共 %d 条记录将更新：", updates)
            for p, c in sorted(stats.items()):
                logger.info("  %s: %d", p, c)
        else:
            session.commit()
            logger.info("=== 已写入 ===")
            logger.info("共 %d 条记录已更新：", updates)
            for p, c in sorted(stats.items()):
                logger.info("  %s: %d", p, c)


def main():
    parser = argparse.ArgumentParser(description="按 file_category 推断 purpose 迁移")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="预览不写入")
    group.add_argument("--apply", action="store_true", help="实际写入")
    args = parser.parse_args()

    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
