"""rescan_files.py — 扫描本地文件目录，对照 files 表补录缺失记录。

═══════════════════════════════════════════════════════════════════════════════
用途：
  扫描 emily-data/files/ 目录下的所有文件，对照 PostgreSQL files 表，
  数据库中不存在对应记录的文件将被补录。

匹配策略（按优先级）：
  1. storage_path 完全匹配
  2. filename + file_size 组合匹配
  3. 以上均不匹配 → 视为缺失，补录

补录规则：
  - 生成新的 file_no（FIL-YYYYMMDD-NNNN）
  - 计算 SHA256 文件指纹
  - 推断 file_ext、file_type
  - storage_path 记录相对于扫描根目录的相对路径
  - project_id / message_id / uploaded_by 等留空（无法自动推断）

用法：
    # 预览模式（不写入数据库，仅输出报告）
    uv run python scripts/rescan_files.py --dry-run

    # 实际补录
    uv run python scripts/rescan_files.py

    # 指定扫描目录（默认 emily-data/files）
    uv run python scripts/rescan_files.py --dir /path/to/files

    # 指定数据库连接（默认 localhost:25432，对应 docker-compose 映射）
    uv run python scripts/rescan_files.py --db-url "postgresql://emily:pass@localhost:25432/emily"

    # 同时扫描 attachments 目录
    uv run python scripts/rescan_files.py --include-attachments
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, Column, create_engine, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ── 项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCAN_DIR = PROJECT_ROOT / "emily-data" / "files"
DEFAULT_ATTACHMENTS_DIR = PROJECT_ROOT / "emily-data" / "attachments"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rescan_files")

# ══════════════════════════════════════════════════════════════════════════════
# 数据库模型（最小子集，仅 files 表）
# ══════════════════════════════════════════════════════════════════════════════

class Base(DeclarativeBase):
    pass


class File(Base):
    """files 表 ORM 映射（仅脚本所需字段）。"""
    __tablename__ = "files"

    id = Column(String, primary_key=True)
    file_no = Column(String(50), unique=True, nullable=False)
    project_id = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    filename = Column(String(500), nullable=False)
    file_type = Column(String(100))
    bucket = Column(String(100))
    object_key = Column(String)
    storage_path = Column(String)
    file_size = Column(Integer)
    uploaded_by = Column(String, nullable=True)
    parse_status = Column(String(50), default="pending")
    created_at = Column(String)
    file_ext = Column(String(50), default="")
    file_url = Column(String(1000), default="")
    file_hash = Column(String(256), default="")
    version = Column(String(50), default="V1.0")
    is_latest = Column(Boolean, default=True)
    parent_file_id = Column(String, nullable=True)
    confidentiality = Column(Integer, default=0)
    creator_id = Column(String, nullable=True)
    updated_at = Column(String)
    is_deleted = Column(Boolean, default=False)
    source_module_id = Column(String(100), default="")
    source_module_type = Column(String(50), default="")


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

# 扩展名 → file_type 映射
_EXT_TYPE_MAP: dict[str, str] = {
    ".pdf": "pdf",
    ".doc": "doc", ".docx": "doc",
    ".xls": "xls", ".xlsx": "xls",
    ".ppt": "ppt", ".pptx": "ppt",
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".gif": "image", ".bmp": "image", ".webp": "image",
    ".mp3": "audio", ".wav": "audio", ".amr": "audio",
    ".mp4": "video", ".avi": "video", ".mkv": "video",
    ".zip": "archive", ".rar": "archive", ".7z": "archive",
    ".txt": "text", ".md": "text", ".csv": "csv",
    ".json": "data", ".xml": "data",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(file_path: Path) -> str:
    """计算文件 SHA256 哈希。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def infer_file_type(ext: str) -> str:
    """根据扩展名推断 file_type。"""
    return _EXT_TYPE_MAP.get(ext.lower(), "file")


def generate_file_no(session: Session) -> str:
    """生成文件编号 FIL-YYYYMMDD-NNNN。"""
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"FIL-{today_str}-"
    last = (
        session.query(File)
        .filter(File.file_no.like(f"{prefix}%"))
        .order_by(File.file_no.desc())
        .first()
    )
    if last is None:
        return f"{prefix}0001"
    seq_str = last.file_no[len(prefix):]
    try:
        seq = int(seq_str) + 1
    except ValueError:
        seq = 1
    return f"{prefix}{seq:04d}"


def scan_directory(scan_dir: Path) -> list[Path]:
    """递归扫描目录，返回所有文件的路径列表。"""
    if not scan_dir.exists():
        logger.warning("扫描目录不存在: %s", scan_dir)
        return []
    files = []
    for root, _dirs, filenames in os.walk(scan_dir):
        for fn in filenames:
            fp = Path(root) / fn
            # 跳过隐藏文件和 gitkeep
            if fn.startswith(".") or fn == ".gitkeep":
                continue
            files.append(fp)
    return files


# ══════════════════════════════════════════════════════════════════════════════
# 核心逻辑
# ══════════════════════════════════════════════════════════════════════════════


def find_existing_record(session: Session, storage_path: str, filename: str, file_size: int):
    """在 files 表中查找已有记录。

    匹配策略：
      1. storage_path 完全匹配
      2. filename + file_size 组合匹配
    返回 File 对象或 None。
    """
    # 策略 1: storage_path 匹配
    if storage_path:
        record = session.query(File).filter(
            File.storage_path == storage_path,
            File.is_deleted == False,  # noqa: E712
        ).first()
        if record:
            return record

    # 策略 2: filename + file_size 匹配
    record = session.query(File).filter(
        File.filename == filename,
        File.file_size == file_size,
        File.is_deleted == False,  # noqa: E712
    ).first()
    if record:
        return record

    return None


def insert_file_record(session: Session, *, file_path: Path, scan_root: Path) -> str:
    """向 files 表插入一条文件记录，返回 file_no。"""
    file_no = generate_file_no(session)
    ext = file_path.suffix.lower()
    file_size = file_path.stat().st_size
    file_hash = compute_sha256(file_path)
    rel_path = str(file_path.relative_to(scan_root)).replace("\\", "/")
    file_type = infer_file_type(ext)
    now_iso = _utc_now()
    new_id = str(uuid.uuid4())

    record = File(
        id=new_id,
        file_no=file_no,
        filename=file_path.name,
        project_id=None,
        message_id=None,
        file_type=file_type,
        storage_path=rel_path,
        file_size=file_size,
        uploaded_by=None,
        parse_status="pending",
        created_at=now_iso,
        file_ext=ext.lstrip("."),
        file_url=rel_path,
        file_hash=file_hash,
        version="V1.0",
        is_latest=True,
        parent_file_id=None,
        confidentiality=0,
        creator_id=None,
        updated_at=now_iso,
        is_deleted=False,
        source_module_id="",
        source_module_type="",
    )
    session.add(record)
    session.flush()
    return file_no


def run_rescan(
    scan_dirs: list[Path],
    db_url: str,
    dry_run: bool = False,
) -> None:
    """执行扫描补录主流程。"""
    engine = create_engine(db_url, echo=False, pool_pre_ping=True)
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False,
    )

    total_scanned = 0
    total_existing = 0
    total_inserted = 0
    total_error = 0
    inserted_details: list[dict] = []
    existing_details: list[dict] = []
    error_details: list[str] = []

    for scan_dir in scan_dirs:
        logger.info("扫描目录: %s", scan_dir)
        file_paths = scan_directory(scan_dir)
        logger.info("发现 %d 个文件", len(file_paths))

        for fp in file_paths:
            total_scanned += 1
            rel_path = str(fp.relative_to(scan_dir)).replace("\\", "/")
            file_size = fp.stat().st_size

            try:
                session = SessionLocal()
                try:
                    existing = find_existing_record(session, rel_path, fp.name, file_size)

                    if existing:
                        total_existing += 1
                        existing_details.append({
                            "file": str(fp),
                            "file_no": existing.file_no,
                            "matched_by": "storage_path" if existing.storage_path == rel_path else "filename+size",
                        })
                        logger.debug("已存在: %s (file_no=%s)", fp.name, existing.file_no)
                    else:
                        if dry_run:
                            total_inserted += 1
                            inserted_details.append({
                                "file": str(fp),
                                "file_no": "[DRY-RUN]",
                                "rel_path": rel_path,
                                "size": file_size,
                            })
                            logger.info("[DRY-RUN] 将补录: %s (%d bytes)", fp.name, file_size)
                        else:
                            file_no = insert_file_record(
                                session,
                                file_path=fp,
                                scan_root=scan_dir,
                            )
                            session.commit()
                            total_inserted += 1
                            inserted_details.append({
                                "file": str(fp),
                                "file_no": file_no,
                                "rel_path": rel_path,
                                "size": file_size,
                            })
                            logger.info("已补录: %s → %s", fp.name, file_no)
                finally:
                    session.close()
            except Exception as e:
                total_error += 1
                error_details.append(f"{fp}: {e}")
                logger.error("处理失败: %s — %s", fp, e)

    # ── 输出报告 ──
    print("\n" + "=" * 60)
    print("扫描补录报告")
    print("=" * 60)
    print(f"扫描目录 : {', '.join(str(d) for d in scan_dirs)}")
    print(f"扫描文件 : {total_scanned}")
    print(f"已存在   : {total_existing}")
    print(f"新补录   : {total_inserted}" + (" (DRY-RUN)" if dry_run else ""))
    print(f"失败     : {total_error}")
    print("-" * 60)

    if existing_details:
        print("\n已存在文件（跳过）：")
        for item in existing_details:
            print(f"  {item['file']}  ->  {item['file_no']}  (匹配: {item['matched_by']})")

    if inserted_details:
        print("\n新补录文件：")
        for item in inserted_details:
            size_kb = item['size'] / 1024
            print(f"  {item['file']}  ->  {item['file_no']}  ({size_kb:.1f} KB)  path={item['rel_path']}")

    if error_details:
        print("\n失败文件：")
        for msg in error_details:
            print(f"  {msg}")

    print("=" * 60)

    engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="扫描本地文件目录，对照 files 表补录缺失记录",
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=str(DEFAULT_SCAN_DIR),
        help=f"扫描目录 (默认: {DEFAULT_SCAN_DIR})",
    )
    parser.add_argument(
        "--include-attachments",
        action="store_true",
        help="同时扫描 emily-data/attachments 目录",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql://emily:emily_secret_2026@localhost:25432/emily",
        help="PostgreSQL 连接 URL (默认: postgresql://emily:emily_secret_2026@localhost:25432/emily)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：只输出报告，不写入数据库",
    )
    args = parser.parse_args()

    scan_dirs = [Path(args.dir)]
    if args.include_attachments:
        scan_dirs.append(DEFAULT_ATTACHMENTS_DIR)

    logger.info("数据库: %s", args.db_url.replace("emily_secret_2026", "***"))

    run_rescan(scan_dirs, db_url=args.db_url, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
