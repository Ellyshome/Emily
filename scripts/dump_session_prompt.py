"""dump_session_prompt.py — 为指定用户完整渲染 Session 拉起时的全量注入 prompt。

从 USERS 表按 UUID 取用户，模拟 SessionContext.create() → build_llm_messages() 过程，
输出渲染后的完整 system prompt + 消息历史 + 变量映射表，供开发人员校对 prompt 模板。

用法：
    uv run python scripts/dump_session_prompt.py --user-id <UUID>
    uv run python scripts/dump_session_prompt.py              （交互式选择用户）

输出结构：
    [1] 用户与上下文摘要
    [2] 渲染后的完整 system prompt
    [3] 消息历史（若有）
    [4] 变量值对照表（摘要）
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dump_session_prompt")


# ══════════════════════════════════════════════════════════════════════════════
# 数据库初始化（复用 collect_session_data 模式）
# ══════════════════════════════════════════════════════════════════════════════

def _detect_docker_pg_port() -> int | None:
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().rsplit(":", 1)[-1])
    except Exception:
        pass
    return None


def _init_db() -> None:
    from emily_core.infrastructure.database.session import init_db

    db_url = os.environ.get("EMILY_DATABASE_URL", "")
    if db_url:
        init_db(db_url=db_url)
    else:
        pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
        pg_port_env = os.environ.get("EMILY_PG_PORT")
        pg_port = int(pg_port_env) if pg_port_env else (_detect_docker_pg_port() or 5432)
        init_db(
            pg_host=pg_host, pg_port=pg_port,
            pg_db=os.environ.get("EMILY_PG_DB", "emily"),
            pg_user=os.environ.get("EMILY_PG_USER", "emily"),
            pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"),
        )


# ══════════════════════════════════════════════════════════════════════════════
# 核心逻辑
# ══════════════════════════════════════════════════════════════════════════════

def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _level_label(level: int) -> str:
    from emily_core.permission.level import LEVEL_NAME
    return f"{LEVEL_NAME.get(level, '未知')}(L{level})"


def _list_users() -> list[tuple[str, str, str, str]]:
    """查询 USERS 表，返回 [(user_id, username, position, level), ...]。

    需要先调用 _init_db()。
    """
    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import User
    import json

    with get_session() as s:
        users = s.query(User).filter(User.is_deleted == False).order_by(User.username).all()
        result: list[tuple[str, str, str, str]] = []
        for u in users:
            # 解析职位（取 JSON 数组第一个）
            pos = ""
            try:
                pos_list = json.loads(u.position or "[]")
                if pos_list:
                    pos = str(pos_list[0])
            except (json.JSONDecodeError, IndexError):
                pass
            level_str = _level_label(u.level or 1)
            result.append((u.id, u.username or "", pos, level_str))
        return result


def _select_user() -> str:
    """交互式选择用户，返回 user_id。"""
    _init_db()
    users = _list_users()

    if not users:
        print("USERS 表中没有用户记录。")
        sys.exit(1)

    print(f"\nUSERS 表中共 {len(users)} 个用户：\n")
    print(f"  {'序号':<5} {'姓名':<10} {'职位':<20} {'权限'}")
    print(f"  {'-'*5} {'-'*10} {'-'*20} {'-'*10}")
    for i, (uid, name, pos, level) in enumerate(users, 1):
        print(f"  [{i}]    {name:<10} {pos:<20} {level}")

    while True:
        try:
            choice = input(f"\n请选择用户序号 (1-{len(users)}): ").strip()
            idx = int(choice)
            if 1 <= idx <= len(users):
                selected = users[idx - 1]
                print(f"\n已选择: {selected[1]} ({selected[2]})")
                return selected[0]
            print(f"无效序号，请输入 1-{len(users)}")
        except (ValueError, EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(0)


def dump_session_prompt(user_id: str) -> dict:
    """为指定用户渲染 Session 拉起时的全量注入 prompt。

    Returns:
        {
            "user_id": str,
            "user_name": str,
            "conversation_id": str,
            "system_prompt": str,        # 渲染后的完整 system prompt
            "template_name": str,        # 模板文件名
            "template_chars": int,       # 模板原文字数
            "rendered_chars": int,       # 渲染后字数
            "message_history": list,     # 最近消息历史
            "variables": dict,           # {变量名: (值, 字数)}
            "errors": list,              # 采集错误
        }
    """
    _init_db()

    from emily_core.session.session_data_fetcher import SessionDataFetcher
    from emily_core.infrastructure.llm.prompt_loader import load_prompt

    # 1. 采集数据
    data = SessionDataFetcher.fetch(user_id, "", core=None)
    snapshot = data.get("session_snapshot", {})
    runtime = data.get("session_runtime", {})
    errors = data.get("errors", [])

    user_name = snapshot.get("user_name", "（未知）")
    if not user_name or user_name == "XXXXXXXXXX":
        user_name = "（未知）"

    # 2. 加载 session.md 模板
    template = load_prompt("session")
    template_chars = len(template)

    # 3. 构建变量映射（模拟 SessionContext.get_prompt_variables()）
    from emily_core.session.session_context import SessionContext
    ctx = SessionContext()
    ctx.user_name = snapshot.get("user_name", "")
    ctx.user_position = snapshot.get("user_position", "")
    ctx.project_name = snapshot.get("project_name", "")
    ctx.project_type = snapshot.get("project_type", "")
    ctx.project_status = snapshot.get("project_status", "")
    ctx.long_term_memory = snapshot.get("long_term_memory", "")
    ctx.conversation_summary = snapshot.get("conversation_summary", "")
    ctx.level = snapshot.get("level", 1)
    ctx.company_id = snapshot.get("company_id", "")
    ctx.company_type = snapshot.get("company_type", "")
    ctx.company_name = snapshot.get("company_name", "")
    ctx.department = list(snapshot.get("department", []))
    ctx.authorized_node_ids = list(snapshot.get("authorized_node_ids", []))
    ctx.sop_allow = list(snapshot.get("sop_allow", []))
    ctx.available_skills = list(ctx.sop_allow)
    ctx.available_tools = list(snapshot.get("available_tools", []))
    ctx.visible_schema_summary = snapshot.get("visible_schema_summary", "")
    ctx.visible_files_summary = snapshot.get("visible_files_summary", "")
    ctx.rag_available = snapshot.get("rag_available", False)
    ctx.rag_collections = list(snapshot.get("rag_collections", []))
    ctx.project_world_book = snapshot.get("project_world_book", "")
    ctx.rule_book = snapshot.get("rule_book", "")
    ctx.system_description = snapshot.get("system_description", "")
    ctx.current_datetime = _beijing_now_str()

    # SOP 目录摘要（模拟 SkillRegistry，离线回退为 sop_allow 列表）
    if ctx.sop_allow:
        ctx.sop_catalog_summary = f"可用业务流程 ({len(ctx.sop_allow)}): {', '.join(ctx.sop_allow)}"
    else:
        ctx.sop_catalog_summary = "（无可用的业务流程）"

    # 4. 获取完整变量映射
    prompt_vars = ctx.get_prompt_variables()

    # 5. 两阶段渲染（模拟 SessionAgent._recognize_intent）
    system_prompt = template
    # 阶段1：sop_catalog + current_datetime
    system_prompt = system_prompt.replace("{sop_catalog}", ctx.sop_catalog_summary)
    system_prompt = system_prompt.replace("{current_datetime}", ctx.current_datetime)
    # 阶段2：Session 级变量（始终替换，空值→"（无）"，与生产逻辑一致）
    for key, value in prompt_vars.items():
        replacement = str(value) if value else "（无）"
        system_prompt = system_prompt.replace(key, replacement)

    # 6. 收集消息历史
    message_history: list[dict] = []
    recent_turns = runtime.get("recent_turns", [])
    for turn in recent_turns:
        role = turn.get("role", "user")
        name = turn.get("sender_name", "") if role == "user" else None
        message_history.append({
            "role": role if role in ("user", "assistant") else "user",
            "content": turn.get("content", "") or "",
            "name": name if name else None,
        })

    # 7. 构建变量值摘要
    variables_summary: dict[str, tuple[str, int]] = {}
    for key, value in prompt_vars.items():
        val_str = str(value)
        variables_summary[key] = (val_str[:200], len(val_str))

    return {
        "user_id": user_id,
        "user_name": user_name,
        "system_prompt": system_prompt,
        "template_name": "session.md",
        "template_chars": template_chars,
        "rendered_chars": len(system_prompt),
        "message_history": message_history,
        "variables": variables_summary,
        "errors": errors,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 输出渲染
# ══════════════════════════════════════════════════════════════════════════════

def _print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _print_minor(title: str) -> None:
    print(f"\n── {title}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="渲染 Session 拉起时的全量注入 prompt")
    parser.add_argument("--user-id", default="", help="用户 UUID（来自 USERS 表）。不提供则交互式选择。")
    parser.add_argument("--prompt-only", action="store_true", help="仅输出渲染后 prompt 文本")
    parser.add_argument("--vars-only", action="store_true", help="仅输出变量映射表")
    args = parser.parse_args()

    user_id = args.user_id or _select_user()
    result = dump_session_prompt(user_id)

    if args.prompt_only:
        print(result["system_prompt"])
        return

    if args.vars_only:
        print(f"# 模板: {result['template_name']} ({result['template_chars']} 字) → 渲染后 {result['rendered_chars']} 字")
        print()
        for var, (val, chars) in result["variables"].items():
            display_val = val[:100] + "..." if len(val) > 100 else val
            print(f"  {var:<35s} = {display_val}  ({chars} 字)")
        return

    # ── 完整输出 ──

    # [1] 摘要
    _print_section("用户与上下文")
    print(f"  用户ID:  {result['user_id']}")
    print(f"  姓名:    {result['user_name']}")
    print(f"  模板:    {result['template_name']} ({result['template_chars']} 字原文)")
    print(f"  渲染后:  {result['rendered_chars']} 字")
    if result["errors"]:
        print(f"  错误:    {len(result['errors'])} 个")
        for e in result["errors"]:
            print(f"    - {e}")

    # [2] 渲染后的 system prompt
    _print_section("渲染后的完整 System Prompt")
    print(result["system_prompt"])

    # [3] 消息历史
    if result["message_history"]:
        _print_section("消息历史 (message_history)")
        for i, msg in enumerate(result["message_history"]):
            role = msg.get("role", "?")
            name = msg.get("name", "")
            content = (msg.get("content", "") or "")[:120]
            label = "用户" if role == "user" else "Emy"
            name_suffix = f" ({name})" if name and role == "user" else ""
            print(f"  [{i}] {label}{name_suffix}: {content}")
    else:
        _print_section("消息历史")
        print("  （无历史消息）")

    # [4] 变量值对照表
    _print_section("变量值对照表")
    for var, (val, chars) in result["variables"].items():
        # 大文本仅显示摘要
        if chars > 200:
            display_val = f"（大文本，{chars} 字）"
        else:
            display_val = val if val else "（空）"
        print(f"  {var:<35s} → {display_val}")


if __name__ == "__main__":
    main()
