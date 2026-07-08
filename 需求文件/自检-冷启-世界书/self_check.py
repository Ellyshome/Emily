"""
self_check.py — Emily 系统自检数据采集脚本

═══════════════════════════════════════════════════════════════════════════════
定位：独立数据采集脚本。采集 Emily 系统的核心运行数据（用户、项目、业务量、
      RAG 知识库存量），输出结构化 JSON。供冷启动脚本或管理员调用。

数据源：
  - PostgreSQL（emily 库）：users / projects / events / tasks / meetings / files 表
  - SQLite data/knowledge_base/kb.db：knowledge_bases 表

设计原则：
  - 永不崩溃：每个查询独立 try/except，失败字段设 null + 追加 warnings
  - 如实反映：有空则空，有错才标记 _SENTINEL
  - 独立运行：自带最小 ORM 映射，不依赖 emily_core 包

参照源：scripts/collect_session_data.py / scripts/rescan_files.py
═══════════════════════════════════════════════════════════════════════════════

用法：
    # 预览模式（不写入数据库，仅输出报告）
    uv run python 需求文件/自检-冷启-世界书/self_check.py --dry-run

    # 正式执行（含 admin 账号保创建）
    uv run python 需求文件/自检-冷启-世界书/self_check.py

    # 指定数据库连接（默认 localhost:25432，对应 docker-compose 映射）
    uv run python 需求文件/自检-冷启-世界书/self_check.py --db-url "postgresql://emily:pass@localhost:25432/emily"

    # 仅输出 JSON
    uv run python 需求文件/自检-冷启-世界书/self_check.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional, Tuple

from sqlalchemy import (
    Boolean, Column, create_engine, ForeignKey, Integer,
    String, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, relationship

# ── 路径设置 ──
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
DEFAULT_KB_DB_PATH = _REPO_ROOT / "data" / "knowledge_base" / "kb.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("emily.self_check")

BEIJING_TZ = timezone(timedelta(hours=8))

# ══════════════════════════════════════════════════════════════════════════════
# 哨兵值 —— 仅在"获取过程出错"时使用
# ══════════════════════════════════════════════════════════════════════════════
_SENTINEL = "XXXXXXXXXX"

# ══════════════════════════════════════════════════════════════════════════════
# 数据库模型（最小子集，自包含，不依赖 emily_core 包）
# ══════════════════════════════════════════════════════════════════════════════


class Base(DeclarativeBase):
    pass


class User(Base):
    """users 表 ORM 映射 — 与实际数据库列对齐（非 models.py 完整定义）。

    注意：实际 DB 与 models.py 有差异：
      - DB 无 real_name（models.py 有但未迁移）
      - DB 无 project_id / long_term_memory / conversation_summary（models.py 有但未迁移）
    此映射以实际 DB 列为准，避免 UndefinedColumn 报错。
    """
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(100))
    phone = Column(String(50))
    email = Column(String(200))
    status = Column(String(50), default="active")
    is_admin = Column(Boolean, default=False)
    gender = Column(Integer, default=0)
    id_card = Column(String(50), default="")
    qq = Column(String(50), default="")
    wechat = Column(String(100), default="")
    remark = Column(String, default="")
    creator_id = Column(String, nullable=True)
    is_deleted = Column(Boolean, default=False)
    perm_list = Column(String, default="[]")
    org_category = Column(Integer, default=0)
    permission_level = Column(Integer, default=1)
    supervisor_id = Column(String, nullable=True)
    company = Column(String, nullable=True)
    position = Column(String, default="[]")
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat(), nullable=False)
    updated_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat(), nullable=False)


class UserImBinding(Base):
    __tablename__ = "user_im_bindings"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    im_platform = Column(String(50), nullable=False)
    im_user_id = Column(String(200), nullable=False)
    im_display_name = Column(String(200))
    status = Column(String(50), default="active")
    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    updated_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    __table_args__ = (UniqueConstraint("im_platform", "im_user_id", name="uq_im_platform_user"),)


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True)
    is_deleted = Column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(String, primary_key=True)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True)


class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(String, primary_key=True)
    is_deleted = Column(Boolean, default=False)


class File(Base):
    __tablename__ = "files"
    id = Column(String, primary_key=True)
    is_deleted = Column(Boolean, default=False)


# ══════════════════════════════════════════════════════════════════════════════
# 默认管理员账号配置
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_ADMIN = {
    "username": "admin",
    "qq": "927780870",
    "email": "927780870@qq.com",
    "permission_level": 6,       # 系统管理员
    "is_admin": True,
    "status": "active",
    "org_category": 4,           # 管理组
    "gender": 0,                 # 未知
    "remark": "系统默认管理员账号（自检脚本自动创建）",
}


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════


def _beijing_now_iso() -> str:
    """返回北京时间 ISO8601 字符串（带正确时区偏移 +08:00）。"""
    return datetime.now(BEIJING_TZ).isoformat()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_kb_db_path() -> Path:
    """解析 kb.db 路径：优先环境变量 → 仓库根目录 → cwd。"""
    env_path = os.environ.get("EMILY_KB_DB_PATH", "")
    if env_path:
        return Path(env_path)
    candidate = _REPO_ROOT / "data" / "knowledge_base" / "kb.db"
    if candidate.exists():
        return candidate
    cwd_candidate = Path.cwd() / "data" / "knowledge_base" / "kb.db"
    if cwd_candidate.exists():
        return cwd_candidate
    return candidate  # 返回预期路径（即使不存在，后续会标记 unavailable）


# ══════════════════════════════════════════════════════════════════════════════
# 子查询：每个函数返回 (value, error: str | None)
# ══════════════════════════════════════════════════════════════════════════════


def _sub_fetch_user_stats(session: Session) -> Tuple[dict, Optional[str]]:
    """从 DB users 表获取用户统计。

    Returns:
        ({"total": int, "admins": int, "admin_ensured": bool}, error_or_None)
    """
    try:
        total = session.query(User.id).filter(
            User.is_deleted == False,  # noqa: E712
        ).count()
        admins = session.query(User.id).filter(
            User.is_deleted == False,  # noqa: E712
            User.permission_level >= 5,
        ).count()
        return {"total": total, "admins": admins, "admin_ensured": False}, None
    except Exception as e:
        logger.error("_sub_fetch_user_stats DB error: %s", e)
        return {"total": _SENTINEL, "admins": _SENTINEL, "admin_ensured": False}, f"用户统计获取异常: {e}"


def _sub_ensure_admin(session: Session, dry_run: bool = False) -> Tuple[dict, Optional[str]]:
    """确保默认管理员账号 admin 存在。

    查找逻辑：
      1. 按 username='admin' 查找
      2. 按 qq='927780870' 查找（可能用户名被改了）
      3. 均不存在 → 创建

    创建时同时建立 napcat IM 绑定（QQ 号 → user_im_bindings）。

    Returns:
        ({"admin_ensured": bool, "admin_action": str}, error_or_None)
    """
    try:
        # 策略 1: 按 username 查找
        existing = session.query(User).filter(
            User.username == "admin",
            User.is_deleted == False,  # noqa: E712
        ).first()

        # 策略 2: 按 QQ 号查找
        if existing is None:
            binding = session.query(UserImBinding).filter(
                UserImBinding.im_platform == "napcat",
                UserImBinding.im_user_id == DEFAULT_ADMIN["qq"],
                UserImBinding.status == "active",
            ).first()
            if binding:
                existing = session.query(User).filter(
                    User.id == binding.user_id,
                    User.is_deleted == False,  # noqa: E712
                ).first()

        if existing is not None:
            # 已存在，检查是否需要升级权限
            needs_update = False
            if existing.permission_level < DEFAULT_ADMIN["permission_level"]:
                existing.permission_level = DEFAULT_ADMIN["permission_level"]
                existing.is_admin = True
                needs_update = True
            if existing.qq != DEFAULT_ADMIN["qq"]:
                existing.qq = DEFAULT_ADMIN["qq"]
                needs_update = True
            if existing.email != DEFAULT_ADMIN["email"]:
                existing.email = DEFAULT_ADMIN["email"]
                needs_update = True
            if needs_update and not dry_run:
                session.flush()
                logger.info("Admin account updated: username=%s, permission_level=%d",
                            existing.username, existing.permission_level)
            return {
                "admin_ensured": True,
                "admin_action": "updated" if needs_update else "already_exists",
            }, None

        # 不存在 → 创建
        if dry_run:
            logger.info("[DRY-RUN] Will create admin account: %s", DEFAULT_ADMIN["username"])
            return {
                "admin_ensured": False,
                "admin_action": "will_create",
            }, None

        now_iso = _utc_now_iso()
        new_id = str(uuid.uuid4())

        admin_user = User(
            id=new_id,
            username=DEFAULT_ADMIN["username"],
            qq=DEFAULT_ADMIN["qq"],
            email=DEFAULT_ADMIN["email"],
            permission_level=DEFAULT_ADMIN["permission_level"],
            is_admin=DEFAULT_ADMIN["is_admin"],
            status=DEFAULT_ADMIN["status"],
            org_category=DEFAULT_ADMIN["org_category"],
            gender=DEFAULT_ADMIN["gender"],
            remark=DEFAULT_ADMIN["remark"],
            creator_id=new_id,  # 自创建
            is_deleted=False,
        )
        session.add(admin_user)
        session.flush()

        # 创建 IM 绑定
        binding = UserImBinding(
            user_id=new_id,
            im_platform="napcat",
            im_user_id=DEFAULT_ADMIN["qq"],
            im_display_name="admin",
        )
        session.add(binding)
        session.flush()

        logger.info("Admin account created: id=%s, username=%s, qq=%s, permission_level=%d",
                     new_id, DEFAULT_ADMIN["username"], DEFAULT_ADMIN["qq"],
                     DEFAULT_ADMIN["permission_level"])
        return {
            "admin_ensured": True,
            "admin_action": "created",
        }, None

    except Exception as e:
        logger.error("_sub_ensure_admin error: %s", e)
        return {"admin_ensured": False, "admin_action": "error"}, f"管理员账号保创建异常: {e}"


def _sub_fetch_project_stats(session: Session) -> Tuple[dict, Optional[str]]:
    """从 DB projects 表获取项目统计。"""
    try:
        total = session.query(Project.id).filter(
            Project.is_deleted == False,  # noqa: E712
        ).count()
        return {"total": total}, None
    except Exception as e:
        logger.error("_sub_fetch_project_stats DB error: %s", e)
        return {"total": _SENTINEL}, f"项目统计获取异常: {e}"


def _sub_fetch_business_stats(session: Session) -> Tuple[dict, Optional[str]]:
    """从 DB events / tasks / meetings / files 表获取业务量统计。

    注意：events 和 tasks 表无 is_deleted 软删除字段，直接 COUNT 全表。
    meetings 和 files 使用 is_deleted == False。
    """
    try:
        events = session.query(Event.id).count()
        tasks = session.query(Task.id).count()
        meetings = session.query(Meeting.id).filter(
            Meeting.is_deleted == False,  # noqa: E712
        ).count()
        files = session.query(File.id).filter(
            File.is_deleted == False,  # noqa: E712
        ).count()

        return {
            "events": events,
            "tasks": tasks,
            "meetings": meetings,
            "files": files,
        }, None
    except Exception as e:
        logger.error("_sub_fetch_business_stats DB error: %s", e)
        return {
            "events": _SENTINEL,
            "tasks": _SENTINEL,
            "meetings": _SENTINEL,
            "files": _SENTINEL,
        }, f"业务量统计获取异常: {e}"


def _sub_fetch_kb_stats() -> Tuple[dict, Optional[str]]:
    """从 SQLite data/knowledge_base/kb.db 获取知识库存量统计。

    数据源：knowledge_bases 表的 doc_count 和 chunk_count 字段。
    只读模式打开，不存在则标记 unavailable。
    """
    kb_path = _resolve_kb_db_path()

    # 文件不存在 → unavailable
    if not kb_path.exists():
        logger.warning("KB db not found: %s", kb_path)
        return {
            "total_docs": 0,
            "total_chunks": 0,
            "index_status": "unavailable",
        }, None

    try:
        conn = sqlite3.connect(f"file:{kb_path}?mode=ro", uri=True)
        try:
            cursor = conn.execute(
                "SELECT COALESCE(SUM(doc_count), 0), COALESCE(SUM(chunk_count), 0) "
                "FROM knowledge_bases"
            )
            row = cursor.fetchone()
            total_docs = row[0] if row else 0
            total_chunks = row[1] if row else 0

            # 判定索引状态
            if total_docs == 0:
                index_status = "needs_rebuild"
            else:
                index_status = "normal"

            return {
                "total_docs": total_docs,
                "total_chunks": total_chunks,
                "index_status": index_status,
            }, None
        finally:
            conn.close()
    except Exception as e:
        logger.error("_sub_fetch_kb_stats SQLite error: %s", e)
        return {
            "total_docs": _SENTINEL,
            "total_chunks": _SENTINEL,
            "index_status": _SENTINEL,
        }, f"知识库统计获取异常: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# 主入口：SelfCheck.run()
# ══════════════════════════════════════════════════════════════════════════════


class SelfCheck:
    """自检数据采集器。

    静态方法 run() 执行全量数据采集 + admin 账号保创建，返回结构化 JSON 字典。
    遵循"永不崩溃"原则：每个子查询独立 try/except，
    失败字段 null + 追加 warnings，脚本级异常返回 status="error"。
    """

    @staticmethod
    def run(
        *,
        db_url: str = "postgresql://emily:emily_secret_2026@localhost:25432/emily",
        dry_run: bool = False,
    ) -> dict:
        """执行全量自检数据采集 + admin 账号保创建。

        Args:
            db_url: PostgreSQL 连接 URL。
            dry_run: 预览模式，不写入数据库。

        Returns:
            dict: {
                "check_time": str (ISO8601),
                "check_duration_ms": int,
                "status": "ok" | "warning" | "error",
                "users": {"total": int|null, "admins": int|null},
                "admin": {"admin_ensured": bool, "admin_action": str},
                "projects": {"total": int|null},
                "business": {"events": int|null, "tasks": int|null,
                             "meetings": int|null, "files": int|null},
                "knowledge_base": {"total_docs": int|null, "total_chunks": int|null,
                                   "index_status": str|null},
                "warnings": [str, ...],
                "error_message": str|null,
            }
        """
        t_start = time.monotonic()
        warnings: list[str] = []
        error_message: Optional[str] = None
        status = "ok"

        # ── 步骤 0: 初始化数据库连接 ──
        try:
            engine = create_engine(db_url, echo=False, pool_pre_ping=True)
            SessionLocal = sessionmaker(
                autocommit=False, autoflush=False, bind=engine, expire_on_commit=False,
            )
        except Exception as e:
            logger.error("DB engine creation failed: %s", e)
            return {
                "check_time": _beijing_now_iso(),
                "check_duration_ms": int((time.monotonic() - t_start) * 1000),
                "status": "error",
                "users": {"total": None, "admins": None},
                "admin": {"admin_ensured": False, "admin_action": "error"},
                "projects": {"total": None},
                "business": {"events": None, "tasks": None, "meetings": None, "files": None},
                "knowledge_base": {"total_docs": None, "total_chunks": None, "index_status": None},
                "warnings": [],
                "error_message": f"数据库连接失败: {e}",
            }

        # ── 辅助函数：记录子查询结果 ──
        def _record_item(err: Optional[str]) -> None:
            nonlocal status
            if err:
                warnings.append(err)
                if status == "ok":
                    status = "warning"

        # ── 步骤 1: 用户统计 ──
        session1 = SessionLocal()
        try:
            users, err = _sub_fetch_user_stats(session1)
            _record_item(err)
        finally:
            session1.close()

        # ── 步骤 2: admin 账号保创建 ──
        session2 = SessionLocal()
        try:
            admin, err = _sub_ensure_admin(session2, dry_run=dry_run)
            _record_item(err)
            if not dry_run:
                session2.commit()
        except Exception as e:
            session2.rollback()
            admin = {"admin_ensured": False, "admin_action": "error"}
            _record_item(f"管理员账号保创建异常: {e}")
        finally:
            session2.close()

        # admin 创建成功后，重新统计用户数（可能 +1）
        if admin.get("admin_action") == "created" and users.get("total") != _SENTINEL:
            session_recount = SessionLocal()
            try:
                users["total"] = session_recount.query(User.id).filter(
                    User.is_deleted == False,  # noqa: E712
                ).count()
                users["admins"] = session_recount.query(User.id).filter(
                    User.is_deleted == False,  # noqa: E712
                    User.permission_level >= 5,
                ).count()
                users["admin_ensured"] = admin.get("admin_ensured", False)
            finally:
                session_recount.close()

        # ── 步骤 3: 项目统计 ──
        session3 = SessionLocal()
        try:
            projects, err = _sub_fetch_project_stats(session3)
            _record_item(err)
        finally:
            session3.close()

        # ── 步骤 4: 业务量统计 ──
        session4 = SessionLocal()
        try:
            business, err = _sub_fetch_business_stats(session4)
            _record_item(err)
        finally:
            session4.close()

        # ── 步骤 5: 知识库存量统计 ──
        kb, err = _sub_fetch_kb_stats()
        _record_item(err)

        # ── 知识库索引状态影响 status ──
        kb_status = kb.get("index_status")
        if kb_status and kb_status not in ("normal", _SENTINEL):
            if status == "ok":
                status = "warning"

        # ── 组装最终输出 ──
        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        result: dict[str, Any] = {
            "check_time": _beijing_now_iso(),
            "check_duration_ms": elapsed_ms,
            "status": status,
            "users": {
                "total": users.get("total") if users.get("total") != _SENTINEL else None,
                "admins": users.get("admins") if users.get("admins") != _SENTINEL else None,
            },
            "admin": admin,
            "projects": {
                "total": projects.get("total") if projects.get("total") != _SENTINEL else None,
            },
            "business": {
                "events": business.get("events") if business.get("events") != _SENTINEL else None,
                "tasks": business.get("tasks") if business.get("tasks") != _SENTINEL else None,
                "meetings": business.get("meetings") if business.get("meetings") != _SENTINEL else None,
                "files": business.get("files") if business.get("files") != _SENTINEL else None,
            },
            "knowledge_base": {
                "total_docs": kb.get("total_docs") if kb.get("total_docs") != _SENTINEL else None,
                "total_chunks": kb.get("total_chunks") if kb.get("total_chunks") != _SENTINEL else None,
                "index_status": kb.get("index_status") if kb.get("index_status") != _SENTINEL else None,
            },
            "warnings": warnings,
            "error_message": error_message,
        }

        logger.info(
            "SelfCheck done: status=%s warnings=%d elapsed=%dms admin=%s",
            status, len(warnings), elapsed_ms, admin.get("admin_action", "n/a"),
        )

        engine.dispose()
        return result


# ══════════════════════════════════════════════════════════════════════════════
# CLI 调试入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Emily 系统自检数据采集")
    parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql://emily:emily_secret_2026@localhost:25432/emily",
        help="PostgreSQL 连接 URL (默认: localhost:25432)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：只输出报告，不写入数据库（不创建 admin 账号）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出完整 JSON",
    )
    args = parser.parse_args()

    print("=== Emily SelfCheck ===")
    data = SelfCheck.run(db_url=args.db_url, dry_run=args.dry_run)

    print(f"\nStatus: {data['status']}")
    print(f"Duration: {data['check_duration_ms']}ms")
    print(f"Check time: {data['check_time']}")

    print("\n--- Users ---")
    print(f"  Total: {data['users']['total']}, Admins (L5+): {data['users']['admins']}")

    print("\n--- Admin Account ---")
    action = data['admin']['admin_action']
    ensured = data['admin']['admin_ensured']
    action_labels = {
        "already_exists": "已存在，无需操作",
        "updated": "已存在，权限已补全",
        "created": "新创建",
        "will_create": "[DRY-RUN] 将创建",
        "error": "操作失败",
    }
    print(f"  Action: {action_labels.get(action, action)}, Ensured: {ensured}")

    print("\n--- Projects ---")
    print(f"  Total: {data['projects']['total']}")

    print("\n--- Business ---")
    print(f"  Events: {data['business']['events']}, Tasks: {data['business']['tasks']}")
    print(f"  Meetings: {data['business']['meetings']}, Files: {data['business']['files']}")

    print("\n--- Knowledge Base ---")
    print(f"  Docs: {data['knowledge_base']['total_docs']}, Chunks: {data['knowledge_base']['total_chunks']}")
    print(f"  Index: {data['knowledge_base']['index_status']}")

    print(f"\n--- Warnings ({len(data['warnings'])}) ---")
    if data["warnings"]:
        for w in data["warnings"]:
            print(f"  [!] {w}")
    else:
        print("  (none)")

    if data["error_message"]:
        print(f"\n[ERROR] {data['error_message']}")

    if args.json:
        print("\n--- Full JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))

    print("\nDone.")
