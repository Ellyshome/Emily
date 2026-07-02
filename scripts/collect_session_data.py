"""
collect_session_data.py — Session 聚合根数据收集脚本

═══════════════════════════════════════════════════════════════════════════════
定位：拼组脚本（SessionFactory）调用的数据收集层。
     以 user_id 为输入，以结构化 Session 聚合根参数为输出。
     本脚本只做数据收集，不改变任何状态，不产生副作用。

数据源策略：
  只从真实环境获取（DB / Service / 文件系统）。
  如实反映：有就是有、空就是空、出错才标记 'XXXXXXXXXX' 哨兵。
  ⚠ 区分"没有值"（正常空）和"获取失败"（需要排查）。

对应需求文档：需求文件/Session聚合根数据获取清单.md
              需求文件/Session数据获取可实施性分表.md
═══════════════════════════════════════════════════════════════════════════════

用法：
    >>> from scripts.collect_session_data import collect_session_data
    >>> data = collect_session_data(user_id="80137af6-78e0-...")
    >>> print(data["session_snapshot"]["user_name"])    # 实际值 或 "" 或 "XXXXXXXXXX"
    >>> print(data["errors"])                            # ["条目A获取失败", ...]
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional, Tuple

# ── 允许从仓库根目录或 emily-core/ 目录运行 ──
_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logger = logging.getLogger("emily.collect_session_data")

# ══════════════════════════════════════════════════════════════════════════════
# 哨兵值 —— 仅在"获取过程出错"时使用，不作为空值默认值
# ══════════════════════════════════════════════════════════════════════════════
_SENTINEL = "XXXXXXXXXX"


# ══════════════════════════════════════════════════════════════════════════════
# 数据库连接
# ══════════════════════════════════════════════════════════════════════════════

def _init_db_if_needed() -> None:
    """按需初始化数据库连接。"""
    from emily_core.infrastructure.database import init_db, get_db_path

    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if db_url:
        init_db(db_url)
    else:
        init_db()
    logger.debug("DB connected: %s", get_db_path())


# ══════════════════════════════════════════════════════════════════════════════
# 内部工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _beijing_now_str() -> str:
    """返回北京时间字符串。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_position_json(position_raw: str) -> Tuple[str, Optional[str]]:
    """解析 users.position JSON 数组字段，取首个岗位名。

    Returns:
        (value, error): error 为 None 表示解析成功（可能为空字符串），非 None 表示解析异常。
    """
    if not position_raw or position_raw == "[]":
        return "", None
    try:
        positions = json.loads(position_raw)
        if isinstance(positions, list) and positions:
            return str(positions[0]), None
    except (json.JSONDecodeError, IndexError, TypeError) as e:
        return _SENTINEL, f"position JSON 解析失败: {e}"
    return "", None


def _format_recent_turns(recent_turns: list[dict]) -> str:
    """将最近对话列表格式化为 prompt 可用的纯文本。"""
    if not recent_turns:
        return ""
    lines = []
    for turn in recent_turns:
        role_label = "用户" if turn.get("role") == "user" else "Emy"
        time_str = turn.get("time", "")[:16]
        content = turn.get("content", "")
        lines.append(f"[{time_str}] {role_label}: {content}")
    return "\n".join(lines)


def _format_node_ids(node_ids: list[str]) -> str:
    """将节点 ID 列表格式化为中文顿号分隔字符串。"""
    if not node_ids:
        return ""
    return "、".join(node_ids)


def _format_permission_level(level: int) -> str:
    """将权限层级数字映射为可读标签。"""
    labels = {
        1: "访客",
        2: "参建执行",
        3: "参建管理",
        4: "建设主管",
        5: "管理员",
        6: "系统管理员",
    }
    label = labels.get(level, f"L{level}")
    return f"{label}(L{level})"


def _translate_project_status(status: str) -> str:
    """将 projects.status 英文值翻译为中文。"""
    status_map = {
        "active": "进行中",
        "planning": "规划中",
        "paused": "已暂停",
        "completed": "已竣工",
        "cancelled": "已取消",
        "draft": "草稿",
    }
    return status_map.get(status, status)


# ══════════════════════════════════════════════════════════════════════════════
# 子脚本
# ══════════════════════════════════════════════════════════════════════════════
#
# 每个函数返回 (value, error: str | None)
#   value: 实际值（空就是 "" / {} / []）
#   error: None = 成功，非 None = 获取过程中出错（value 为 _SENTINEL）


def _sub_fetch_user_name(user_id: str) -> Tuple[str, Optional[str]]:
    """从 DB users 表获取用户姓名。

    数据源: DB users.username [models.py:66]
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import User

        with get_session() as session:
            row = session.query(User.username).filter(
                User.id == user_id,
                User.is_deleted == False,
            ).first()

            if row is not None:
                return (row.username or ""), None

        logger.warning("_sub_fetch_user_name: user not found, user_id=%s", user_id)
        return "", None
    except Exception as e:
        logger.error("_sub_fetch_user_name DB error (user_id=%s): %s", user_id, e)
        return _SENTINEL, f"user_name 获取异常: {e}"


def _sub_fetch_user_position(user_id: str) -> Tuple[str, Optional[str]]:
    """从 DB users 表获取用户职务。

    数据源: DB users.position [models.py:86]，JSON 数组取首个
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import User

        with get_session() as session:
            row = session.query(User.position).filter(
                User.id == user_id,
                User.is_deleted == False,
            ).first()

            if row is not None:
                return _parse_position_json(row.position)

        logger.warning("_sub_fetch_user_position: user not found, user_id=%s", user_id)
        return "", None
    except Exception as e:
        logger.error("_sub_fetch_user_position DB error (user_id=%s): %s", user_id, e)
        return _SENTINEL, f"user_position 获取异常: {e}"


def _sub_fetch_permissions(user_id: str) -> Tuple[dict, Optional[str]]:
    """从 PermissionService 获取权限快照。

    数据源: PermissionService.build_permission_snapshot(user_id)
      涉及 DB 表: users, company_info, permission_grants, sop_business_flows

    返回 dict 中每个字段如实的值，获取异常时才填哨兵。
    """
    try:
        from emily_core.services.permission_service import PermissionService

        service = PermissionService()
        snapshot = service.build_permission_snapshot(user_id)

        def _safe(fn, is_scalar=True):
            try:
                return fn()
            except Exception:
                return _SENTINEL if is_scalar else ([] if is_scalar is False else {})

        result = {
            "permission_level":     _safe(lambda: snapshot.permission_level),
            "company_id":           _safe(lambda: snapshot.company_id),
            "company_type":         _safe(lambda: snapshot.company_type),
            "company_name":         _safe(lambda: snapshot.company_name),
            "department":           _safe(lambda: snapshot.department),
            "project_ids":          _safe(lambda: list(snapshot.project_ids), False),
            "partner_ids":          _safe(lambda: list(snapshot.partner_ids), False),
            "scopes":               _safe(lambda: list(snapshot.scopes), False),
            "sop_allow":            _safe(lambda: list(snapshot.sop_allow), False),
            "db_perms":             _safe(lambda: dict(snapshot.db_perms), {}),
            "info_level":           _safe(lambda: snapshot.info_level),
            "supervisor_id":        _safe(lambda: snapshot.supervisor_id),
            "org_group":            _safe(lambda: snapshot.org_group),
            "granted_codes":        _safe(lambda: list(snapshot.granted_codes), False),
            "denied_codes":         _safe(lambda: list(snapshot.denied_codes), False),
            "authorized_node_ids":  _safe(lambda: list(snapshot.authorized_node_ids), False),
            "permissions_loaded_at": _safe(lambda: snapshot.permissions_loaded_at or _beijing_now_str()),
            "permission_version":   _safe(lambda: snapshot.permission_version),
            "extra_perms":          _safe(lambda: dict(snapshot.extra_perms), {}),
        }
        return result, None

    except Exception as e:
        logger.error("_sub_fetch_permissions error (user_id=%s): %s", user_id, e)
        sentinel_dict = {
            "permission_level": _SENTINEL,
            "company_id": _SENTINEL, "company_type": _SENTINEL, "company_name": _SENTINEL,
            "department": _SENTINEL,
            "project_ids": [], "partner_ids": [], "scopes": [],
            "sop_allow": [], "db_perms": {}, "info_level": _SENTINEL,
            "supervisor_id": _SENTINEL, "org_group": _SENTINEL,
            "granted_codes": [], "denied_codes": [],
            "authorized_node_ids": [],
            "permissions_loaded_at": _SENTINEL, "permission_version": _SENTINEL,
            "extra_perms": {},
        }
        return sentinel_dict, f"permissions 获取异常: {e}"


def _sub_fetch_project(user_id: str) -> Tuple[dict, Optional[str]]:
    """从 DB 获取用户关联的项目上下文。

    数据源: users.project_id FK → projects 表
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import User, Project

        with get_session() as session:
            row = session.query(User.project_id).filter(
                User.id == user_id,
                User.is_deleted == False,
            ).first()

            if row is None:
                return {"project_name": "", "project_type": "", "project_status": ""}, None

            if not row.project_id:
                logger.debug("_sub_fetch_project: user %s has no project_id", user_id)
                return {"project_name": "", "project_type": "", "project_status": ""}, None

            project = session.query(Project).filter(
                Project.id == row.project_id,
                Project.is_deleted == False,
            ).first()

            if project is None:
                logger.warning("_sub_fetch_project: project %s not found", row.project_id)
                return {"project_name": "", "project_type": "", "project_status": ""}, None

            stage = getattr(project, "lifecycle_stage", 0) or 0
            type_map = {0: "工程项目", 1: "工程项目", 2: "房屋建筑", 3: "工程项目"}
            return {
                "project_name": project.name or "",
                "project_type": type_map.get(stage, ""),
                "project_status": _translate_project_status(project.status or ""),
            }, None

    except Exception as e:
        logger.error("_sub_fetch_project DB error (user_id=%s): %s", user_id, e)
        return (
            {"project_name": _SENTINEL, "project_type": _SENTINEL, "project_status": _SENTINEL},
            f"project 获取异常: {e}",
        )


def _sub_fetch_user_memory_and_summary(user_id: str) -> Tuple[dict, Optional[str]]:
    """从 DB users 表获取长期记忆和对话摘要。

    数据源: DB users 表的 long_term_memory 和 conversation_summary 字段。

    Returns:
        ({"user_memory": str, "conversation_summary": str}, error_or_None)
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import User

        with get_session() as session:
            row = session.query(
                User.long_term_memory,
                User.conversation_summary,
            ).filter(
                User.id == user_id,
                User.is_deleted == False,
            ).first()

            if row is not None:
                return {
                    "user_memory": row.long_term_memory or "",
                    "conversation_summary": row.conversation_summary or "",
                }, None

        logger.warning("_sub_fetch_user_memory_and_summary: user not found, user_id=%s", user_id)
        return {"user_memory": "", "conversation_summary": ""}, None
    except Exception as e:
        logger.error("_sub_fetch_user_memory_and_summary DB error (user_id=%s): %s", user_id, e)
        return (
            {"user_memory": _SENTINEL, "conversation_summary": _SENTINEL},
            f"user_memory/conversation_summary 获取异常: {e}",
        )


def _sub_fetch_recent_turns(user_id: str) -> Tuple[list[dict], Optional[str]]:
    """从 DB messages 表获取用户最近 20 条入站消息。

    数据源: DB messages 表 [models.py:125-160]
      按 sender_user_id 匹配，按 created_at 倒序，取最近 20 条。
    """
    try:
        from emily_core.infrastructure.database import get_session
        from emily_core.infrastructure.database.models import Message

        with get_session() as session:
            rows = session.query(
                Message.content,
                Message.created_at,
                Message.direction,
                Message.sender_name,
            ).filter(
                Message.sender_user_id == user_id,
            ).order_by(
                Message.created_at.desc(),
            ).limit(20).all()

            if rows:
                turns = []
                for row in reversed(rows):  # 正序排列
                    role = "user" if row.direction == "user_to_agent" else "agent"
                    turns.append({
                        "role": role,
                        "time": row.created_at or "",
                        "content": row.content or "",
                        "sender_name": row.sender_name or "",
                    })
                return turns, None

        logger.debug("_sub_fetch_recent_turns: no messages found for user_id=%s", user_id)
        return [], None
    except Exception as e:
        logger.error("_sub_fetch_recent_turns DB error (user_id=%s): %s", user_id, e)
        return [], f"recent_turns 获取异常: {e}"


def _sub_fetch_doc_visible_set(permissions: dict) -> Tuple[set[str], Optional[str]]:
    """获取用户可见的文档 ID 集合。

    ❌ DocVisibilityResolver 类尚不存在。
    """
    logger.warning("_sub_fetch_doc_visible_set: method not implemented")
    return set(), "doc_visible_set — 方法待实现"


# ══════════════════════════════════════════════════════════════════════════════
# 主入口：collect_session_data()
# ══════════════════════════════════════════════════════════════════════════════

def collect_session_data(
    user_id: str,
    conversation_id: str = "",
) -> dict:
    """收集 Session 聚合根所需的全部数据。

    只从真实环境获取。
    如实反映：有值则如实返回、值为空则返回空、获取过程出错才返回 'XXXXXXXXXX'。
    同时返回 errors 列表标明哪些条目获取异常。

    Returns:
        {
            "session_snapshot": { ... },
            "session_runtime":  { ... },
            "prompt_variables": { ... },
            "errors": ["条目A获取异常: xxx", "条目B: 方法待实现", ...],
        }
    """
    if not user_id:
        raise ValueError("user_id 不能为空")

    errors: list[str] = []

    def _record(value, label: str, err: Optional[str]) -> None:
        """如 err 非空则记录；如 value 中含哨兵也记录。"""
        if err:
            errors.append(f"{label} — {err}")
            return
        if isinstance(value, str) and value == _SENTINEL:
            errors.append(label)
        elif isinstance(value, dict):
            for k, v in value.items():
                if v == _SENTINEL:
                    errors.append(f"{label}.{k}")

    # ── 步骤 0: 初始化数据库连接 ──
    try:
        _init_db_if_needed()
    except Exception as e:
        logger.error("DB init failed: %s", e)
        errors.append(f"数据库连接失败: {e}")

    # ── 步骤 1: 用户姓名 ────────────────────────────────────────
    user_name, err = _sub_fetch_user_name(user_id)
    _record(user_name, "user_name", err)

    # ── 步骤 2: 用户职务 ────────────────────────────────────────
    user_position, err = _sub_fetch_user_position(user_id)
    _record(user_position, "user_position", err)

    # ── 步骤 3: 权限快照 ────────────────────────────────────────
    permissions, err = _sub_fetch_permissions(user_id)
    _record(permissions, "permissions", err)

    # ── 步骤 4: 项目上下文 ──────────────────────────────────────
    project, err = _sub_fetch_project(user_id)
    _record(project, "project", err)

    # ── 步骤 5: 长期记忆 + 对话摘要 ──────────────────── (DB: users 表) ──
    memory_data, err = _sub_fetch_user_memory_and_summary(user_id)
    user_memory = memory_data["user_memory"]
    conversation_summary = memory_data["conversation_summary"]
    _record(memory_data, "user_memory/conversation_summary", err)

    # ── 步骤 6: 最近对话 ────────────────────────────── (DB: messages 表) ──
    recent_turns_raw, err = _sub_fetch_recent_turns(user_id)
    _record(recent_turns_raw, "recent_turns", err)

    # ═══════════════════════════════════════════════════════════════════
    # 组装输出
    # ═══════════════════════════════════════════════════════════════════

    created_at = datetime.now(timezone.utc).isoformat()

    session_snapshot = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "user_name": user_name,
        "user_position": user_position,
        "created_at": created_at,
        "project_name": project["project_name"],
        "project_type": project["project_type"],
        "project_status": project["project_status"],
        "permissions": permissions,
        "user_memory": user_memory,
        "conversation_summary": conversation_summary,
    }

    session_runtime = {
        "recent_turns": recent_turns_raw,
        "cached_lookups": {},
        "active_focus": None,
        "pending_confirms": [],
        "baggage": {},
    }

    # ── Prompt 变量映射 ──
    perm_level = permissions.get("permission_level", 1)
    if isinstance(perm_level, int) and perm_level >= 1:
        perm_level_str = _format_permission_level(perm_level)
    else:
        perm_level_str = _SENTINEL

    authorized_nodes = permissions.get("authorized_node_ids", [])
    if isinstance(authorized_nodes, list) and authorized_nodes:
        current_node_ids_fmt = _format_node_ids(authorized_nodes)
    elif isinstance(authorized_nodes, str) and authorized_nodes != _SENTINEL:
        current_node_ids_fmt = authorized_nodes
    else:
        current_node_ids_fmt = ""

    prompt_variables = {
        "{project_name}":          session_snapshot["project_name"],
        "{project_type}":          session_snapshot["project_type"],
        "{project_status}":        session_snapshot["project_status"],
        "{user_name}":             session_snapshot["user_name"],
        "{user_company}":          permissions.get("company_name", ""),
        "{user_company_type}":     permissions.get("company_type", ""),
        "{user_department}":       permissions.get("department", ""),
        "{user_position}":         session_snapshot["user_position"],
        "{user_permission_level}": perm_level_str,
        "{current_node_ids}":      current_node_ids_fmt,
        "{recent_turns}":          _format_recent_turns(recent_turns_raw),
        "{user_longterm_memory}":  session_snapshot["user_memory"],
        "{conversation_summary}":  session_snapshot["conversation_summary"],
    }

    logger.info(
        "collect_session_data done: user=%s, errors=%d",
        user_id, len(errors),
    )
    if errors:
        logger.warning("collect_session_data errors: %s", " | ".join(errors))

    return {
        "session_snapshot": session_snapshot,
        "session_runtime": session_runtime,
        "prompt_variables": prompt_variables,
        "errors": errors,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 快捷函数
# ══════════════════════════════════════════════════════════════════════════════

def collect_prompt_variables(user_id: str, conversation_id: str = "") -> dict:
    """快捷函数：只返回 prompt 模板变量。"""
    return collect_session_data(user_id, conversation_id)["prompt_variables"]


# ══════════════════════════════════════════════════════════════════════════════
# CLI 调试入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    test_user = sys.argv[1] if len(sys.argv) > 1 else "80137af6-78e0-41fd-9795-435e0b9eaeab"
    test_conv = sys.argv[2] if len(sys.argv) > 2 else "debug-conv-001"

    print(f"=== collect_session_data({test_user!r}) ===")
    data = collect_session_data(user_id=test_user, conversation_id=test_conv)

    # ── 错误汇总 ──
    print("\n--- ERRORS (获取过程中异常) ---")
    if data["errors"]:
        for err in data["errors"]:
            print(f"  ✗ {err}")
    else:
        print("  (无异常)")

    # ── Snapshot ──
    print("\n--- SessionSnapshot ---")
    for k, v in data["session_snapshot"].items():
        flag = ""
        if isinstance(v, str) and v == _SENTINEL:
            flag = " ＜= ERROR"
        elif isinstance(v, dict):
            sentinel_count = sum(1 for sv in v.values() if sv == _SENTINEL)
            if sentinel_count > 0:
                flag = f" (含 {sentinel_count} 个哨兵字段)"
        val_str = str(v)[:150] + "..." if len(str(v)) > 150 else str(v)
        print(f"  {k:<25s} = {val_str}{flag}")

    # ── Runtime ──
    print("\n--- SessionRuntime (initial) ---")
    for k, v in data["session_runtime"].items():
        val_str = str(v)[:120] + "..." if len(str(v)) > 120 else str(v)
        print(f"  {k:<25s} = {val_str}")

    # ── Prompt 变量 ──
    print("\n--- Prompt Variables ---")
    for k, v in data["prompt_variables"].items():
        flag = " ＜= ERROR" if (isinstance(v, str) and v == _SENTINEL) else ""
        val_str = str(v)[:120] + "..." if len(str(v)) > 120 else str(v)
        print(f"  {k:<35s} = {val_str}{flag}")

    # ── 汇总 ──
    sentinel_count = 0
    for d in [data["session_snapshot"], data["prompt_variables"]]:
        for v in d.values():
            if isinstance(v, str) and v == _SENTINEL:
                sentinel_count += 1
            elif isinstance(v, dict):
                for sv in v.values():
                    if sv == _SENTINEL:
                        sentinel_count += 1

    if data["errors"]:
        print(f"\n共 {len(data['errors'])} 个异常（含 {sentinel_count} 个哨兵值），需排查。")
    else:
        print(f"\n全部获取成功（哨兵值: {sentinel_count}）。")

    print("Done.")
