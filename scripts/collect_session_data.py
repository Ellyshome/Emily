"""
collect_session_data.py — Session 聚合根数据收集脚本（薄壳）

本脚本已简化为 SessionDataFetcher.fetch() 的薄包装。
数据采集逻辑已迁移到 emily_core.session.session_data_fetcher.SessionDataFetcher。

用法：
    >>> from scripts.collect_session_data import collect_session_data
    >>> data = collect_session_data(user_id="80137af6-78e0-...")
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logger = logging.getLogger("emily.collect_session_data")

_SENTINEL = "XXXXXXXXXX"


def _detect_docker_pg_port() -> int | None:
    """尝试从 Docker 自动检测 emily-postgres 的宿主机映射端口。

    通过 ``docker port emily-postgres 5432/tcp`` 查询，输出形如
    ``127.0.0.1:25432``，提取冒号后数字即为宿主机端口。
    Docker 未运行或容器不存在时返回 None。
    """
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = result.stdout.strip().rsplit(":", 1)[-1]
            return int(port_str)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _init_db_if_needed() -> None:
    """按需初始化数据库连接。"""
    from emily_core.infrastructure.database import init_db, get_db_path

    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if db_url:
        init_db(db_url=db_url)
    else:
        # 宿主机默认 127.0.0.1（本脚本始终在宿主机运行，不进入 Docker 网络）
        pg_host = os.environ.get("EMILY_PG_HOST", os.environ.get("PG_HOST", "127.0.0.1"))

        # 端口优先级：环境变量 > Docker 自动检测 > 默认 5432
        pg_port_env = os.environ.get("EMILY_PG_PORT", os.environ.get("PG_PORT"))
        if pg_port_env:
            pg_port = int(pg_port_env)
        else:
            pg_port = _detect_docker_pg_port() or 5432

        pg_db = os.environ.get("EMILY_PG_DB", "emily")
        pg_user = os.environ.get("EMILY_PG_USER", "emily")
        pg_password = os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026")
        init_db(pg_host=pg_host, pg_port=pg_port, pg_db=pg_db, pg_user=pg_user, pg_password=pg_password)
    logger.debug("DB connected: %s", get_db_path())


def collect_session_data(
    user_id: str,
    conversation_id: str = "",
) -> dict:
    """收集 Session 聚合根所需的全部数据（薄壳）。

    委托 SessionDataFetcher.fetch() 完成实际采集。
    """
    if not user_id:
        raise ValueError("user_id 不能为空")

    _init_db_if_needed()

    from emily_core.session.session_data_fetcher import SessionDataFetcher
    return SessionDataFetcher.fetch(user_id, conversation_id, core=None)


# ══════════════════════════════════════════════════════════════════════════════
# CLI 调试入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import io

    # Fix 4: Windows GBK 编码自处理（CLI 入口级别，不影响生产路径）
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    hot_update = "--hot-update" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--hot-update"]

    test_user = args[0] if args else "80137af6-78e0-41fd-9795-435e0b9eaeab"
    test_conv = args[1] if len(args) > 1 else ""  # Fix 5: 默认空串

    print(f"=== collect_session_data({test_user!r}) ===")
    data = collect_session_data(user_id=test_user, conversation_id=test_conv)

    # ── 错误汇总 ──
    print("\n--- ERRORS (获取过程中异常) ---")
    if data["errors"]:
        for err in data["errors"]:
            print(f"  ✗ {err}")
    else:
        print("  (无异常)")

    # ── 热更新分类（D11）──
    # 🔒 冻结 / 🔥 可热更新 / 🔄 可热更新(谨慎) / 📝 运行时自维护
    # 扁平化结构：所有字段均为 snapshot 顶层 key
    _HOT_CLASS = {
        # 🔒 冻结
        "conversation_id": "🔒", "user_id": "🔒", "user_name": "🔒",
        "user_position": "🔒", "created_at": "🔒",
        # 🔥 可热更新
        "permission_level": "🔥", "company_id": "🔥", "company_type": "🔥",
        "company_name": "🔥", "department": "🔥", "project_ids": "🔥",
        "partner_ids": "🔥", "scopes": "🔥", "sop_allow": "🔥",
        "db_perms": "🔥", "info_level": "🔥", "supervisor_id": "🔥",
        "granted_codes": "🔥", "denied_codes": "🔥",
        "authorized_node_ids": "🔥", "permission_version": "🔥",
        "permissions_loaded_at": "🔥",
        # 🔄 可热更新(谨慎)
        "project_name": "🔄", "project_type": "🔄", "project_status": "🔄",
        # 📝 运行时自维护
        "long_term_memory": "📝", "conversation_summary": "📝",
    }

    # 分组展示顺序
    _SNAPSHOT_GROUPS = [
        ("🔒 标识字段", ["conversation_id", "user_id", "user_name", "user_position", "created_at"]),
        ("🔥 权限字段", [
            "permission_level", "company_id", "company_type", "company_name",
            "department", "project_ids", "partner_ids", "scopes",
            "sop_allow", "db_perms", "info_level", "supervisor_id",
            "granted_codes", "denied_codes", "authorized_node_ids",
            "permission_version", "permissions_loaded_at",
        ]),
        ("🔄 项目字段", ["project_name", "project_type", "project_status"]),
        ("📝 记忆字段", ["long_term_memory", "conversation_summary"]),
    ]

    # ── Snapshot（分组展示，扁平数据）──
    snapshot = data["session_snapshot"]
    print("\n--- SessionSnapshot (扁平化) ---")
    for group_label, group_keys in _SNAPSHOT_GROUPS:
        tag = group_label[:1]  # emoji from label
        if hot_update:
            print(f"\n  {group_label}")
        for k in group_keys:
            v = snapshot.get(k, "(缺失)")
            flag = " ＜= ERROR" if (isinstance(v, str) and v == _SENTINEL) else ""
            hot_tag = f" {tag}" if hot_update else ""
            val_str = str(v)[:150] + "..." if len(str(v)) > 150 else str(v)
            # Fix 5: conversation_id 空串时显示占位说明
            if k == "conversation_id" and v == "":
                val_str = "(未指定，recent_turns 为跨会话全局查询)"
            print(f"  {k:<25s} = {val_str}{flag}{hot_tag}")

    # ── Runtime（DB 采集，只含 recent_turns）──
    print("\n--- SessionRuntime (DB采集) ---")
    for k, v in data["session_runtime"].items():
        val_str = str(v)[:120] + "..." if len(str(v)) > 120 else str(v)
        print(f"  {k:<25s} = {val_str}")

    # ── 汇总 ──
    sentinel_count = sum(
        1 for v in snapshot.values()
        if (isinstance(v, str) and v == _SENTINEL)
    )

    if data["errors"]:
        print(f"\n共 {len(data['errors'])} 个异常（含 {sentinel_count} 个哨兵值），需排查。")
    else:
        print(f"\n全部获取成功（哨兵值: {sentinel_count}）。")

    print("Done.")
