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
  - 独立运行：自带 sys.path 设置和 DB 初始化

参照源：scripts/collect_session_data.py
═══════════════════════════════════════════════════════════════════════════════

用法：
    >>> import sys
    >>> sys.path.insert(0, "需求文件/自检-冷启-世界书")
    >>> sys.path.insert(0, "emily-core")
    >>> from self_check import SelfCheck
    >>> result = SelfCheck.run()
    >>> print(result["status"])        # "ok" / "warning" / "error"
    >>> print(result["users"]["total"]) # 实际用户数 或 null
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional, Tuple

# ── 路径设置：脚本在 需求文件/自检-冷启-世界书/，需向上两级到仓库根目录 ──
_HERE = Path(__file__).resolve().parent          # 需求文件/自检-冷启-世界书/
_REPO_ROOT = _HERE.parent.parent                  # 仓库根目录
_CORE_DIR = _REPO_ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))                # 同目录脚本互相导入

logger = logging.getLogger("emily.self_check")

# ══════════════════════════════════════════════════════════════════════════════
# 哨兵值 —— 仅在"获取过程出错"时使用
# ══════════════════════════════════════════════════════════════════════════════
_SENTINEL = "XXXXXXXXXX"

# ══════════════════════════════════════════════════════════════════════════════
# 知识库 SQLite 路径
# ══════════════════════════════════════════════════════════════════════════════


def _resolve_kb_db_path() -> Path:
    """解析 kb.db 路径：仓库根目录 data/knowledge_base/kb.db → 绝对路径。"""
    candidate = _REPO_ROOT / "data" / "knowledge_base" / "kb.db"
    if candidate.exists():
        return candidate
    # fallback: 尝试从当前工作目录查找
    cwd_candidate = Path.cwd() / "data" / "knowledge_base" / "kb.db"
    if cwd_candidate.exists():
        return cwd_candidate
    return candidate  # 返回预期路径（即使不存在，后续会标记 unavailable）


# ══════════════════════════════════════════════════════════════════════════════
# 数据库连接
# ══════════════════════════════════════════════════════════════════════════════

def _init_db_if_needed() -> None:
    """按需初始化 PostgreSQL 数据库连接。"""
    from emily_core.infrastructure.database import init_db, get_db_path

    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if db_url:
        init_db(db_url)
    else:
        init_db()
    logger.debug("PG connected: %s", get_db_path())


# ══════════════════════════════════════════════════════════════════════════════
# 内部工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _beijing_now_iso() -> str:
    """返回北京时间 ISO8601 字符串。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# 子查询：每个函数返回 (value, error: str | None)
#   value: 实际值（空就是 0 / ""）
#   error: None = 成功，非 None = 获取过程中出错（value 可能为 _SENTINEL）
# ══════════════════════════════════════════════════════════════════════════════

def _sub_fetch_user_stats() -> Tuple[dict, Optional[str]]:
    """从 DB users 表获取用户统计。

    Returns:
        ({"total": int, "admins": int}, error_or_None)
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import User

        with get_session() as session:
            total = session.query(User.id).filter(
                User.is_deleted == False,
            ).count()
            admins = session.query(User.id).filter(
                User.is_deleted == False,
                User.is_admin == True,
            ).count()

        return {"total": total, "admins": admins}, None
    except Exception as e:
        logger.error("_sub_fetch_user_stats DB error: %s", e)
        return {"total": _SENTINEL, "admins": _SENTINEL}, f"用户统计获取异常: {e}"


def _sub_fetch_project_stats() -> Tuple[dict, Optional[str]]:
    """从 DB projects 表获取项目统计。

    Returns:
        ({"total": int}, error_or_None)
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import Project

        with get_session() as session:
            total = session.query(Project.id).filter(
                Project.is_deleted == False,
            ).count()

        return {"total": total}, None
    except Exception as e:
        logger.error("_sub_fetch_project_stats DB error: %s", e)
        return {"total": _SENTINEL}, f"项目统计获取异常: {e}"


def _sub_fetch_business_stats() -> Tuple[dict, Optional[str]]:
    """从 DB events / tasks / meetings / files 表获取业务量统计。

    注意：events 和 tasks 表无 is_deleted 软删除字段，
    直接 COUNT 全表。meetings 和 files 使用 is_deleted == False。
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import Event, Task, Meeting, File

        with get_session() as session:
            events = session.query(Event.id).count()
            tasks = session.query(Task.id).count()
            meetings = session.query(Meeting.id).filter(
                Meeting.is_deleted == False,
            ).count()
            files = session.query(File.id).filter(
                File.is_deleted == False,
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
                # 检查文件最后修改时间是否超过 7 天
                mtime = kb_path.stat().st_mtime
                age_days = (time.time() - mtime) / 86400
                if age_days > 7:
                    index_status = "needs_rebuild"
                else:
                    index_status = "normal"
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

    静态方法 run() 执行全量数据采集，返回结构化 JSON 字典。
    遵循"永不崩溃"原则：每个子查询独立 try/except，
    失败字段 null + 追加 warnings，脚本级异常返回 status="error"。
    """

    @staticmethod
    def run() -> dict:
        """执行全量自检数据采集。

        Returns:
            dict: {
                "check_time": str (ISO8601),
                "check_duration_ms": int,
                "status": "ok" | "warning" | "error",
                "users": {"total": int|null, "admins": int|null},
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
            _init_db_if_needed()
        except Exception as e:
            logger.error("DB init failed: %s", e)
            return {
                "check_time": _beijing_now_iso(),
                "check_duration_ms": int((time.monotonic() - t_start) * 1000),
                "status": "error",
                "users": {"total": None, "admins": None},
                "projects": {"total": None},
                "business": {"events": None, "tasks": None,
                             "meetings": None, "files": None},
                "knowledge_base": {"total_docs": None, "total_chunks": None,
                                   "index_status": None},
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
        users, err = _sub_fetch_user_stats()
        _record_item(err)

        # ── 步骤 2: 项目统计 ──
        projects, err = _sub_fetch_project_stats()
        _record_item(err)

        # ── 步骤 3: 业务量统计 ──
        business, err = _sub_fetch_business_stats()
        _record_item(err)

        # ── 步骤 4: 知识库存量统计 ──
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
            "SelfCheck done: status=%s warnings=%d elapsed=%dms",
            status, len(warnings), elapsed_ms,
        )

        return result


# ══════════════════════════════════════════════════════════════════════════════
# CLI 调试入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys as _sys

    print("=== Emily SelfCheck ===")
    data = SelfCheck.run()

    print(f"\nStatus: {data['status']}")
    print(f"Duration: {data['check_duration_ms']}ms")
    print(f"Check time: {data['check_time']}")

    print("\n--- Users ---")
    print(f"  Total: {data['users']['total']}, Admins: {data['users']['admins']}")

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

    # ── 输出完整 JSON ──
    if "--json" in _sys.argv:
        print("\n--- Full JSON ---")
        print(json.dumps(data, ensure_ascii=False, indent=2))

    print("\nDone.")
