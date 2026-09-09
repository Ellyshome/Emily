"""dump_session_prompt_live.py — 用「真实 core」渲染 Session prompt 并落盘观察。

本脚本在 emily-core 容器内以生产 bootstrap.init() 构建真实 EmilyCore（真实 RAG /
SkillRegistry / 权限服务），走与生产 Session 创建一致的路径渲染 session.md，供观察
真实环境下用户实际看到的 prompt。需提供 USERS 表中真实存在的用户 UUID。

用法（推荐在容器内运行）：
    docker cp scripts/dump_session_prompt_live.py emily-core:/tmp/dump_session_prompt_live.py
    docker exec -w /app emily-core python /tmp/dump_session_prompt_live.py \
        --user-id <UUID> --out-dir /app/out_session_prompt

宿主开发环境仅当已配置 EMILY_DATABASE_URL / EMILY_LLM_* / EMILY_TEI_URL 等时可用：
    uv run python scripts/dump_session_prompt_live.py --user-id <UUID>
    uv run ./scripts/three_books_dump.py --out-dir ./temporary/sanshu/
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if Path("/app/emily_core").exists() and "/app" not in sys.path:
    sys.path.insert(0, "/app")  # 容器内运行时

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dump_session_prompt_live")


def render_session_prompt(user_id: str) -> dict:
    """用真实 core 渲染指定用户的 Session prompt（生产路径）。

    Returns:
        {
          "user_id", "user_name", "level_label", "project_name",
          "rag": {...}, "available_skills": [...],
          "template_chars", "rendered_chars", "system_prompt",
          "variables": {var: (value前200字, 字数)}, "errors": [...]
        }
    """
    from emily_core import bootstrap
    from emily_core.infrastructure.llm.prompt_loader import load_prompt
    from emily_core.session.session_context import SessionContext

    # 与生产 api/server.py 一致的启动路径
    core = bootstrap.init()
    # 生产在首条消息时由 _ensure_initialized 加载 skill/permission 子系统；
    # 此处按生产次序补齐，使渲染等价于真实 Session 创建。
    core._init_permission_module()
    core._init_skill_module()

    rag = {}
    if core._rag_provider is not None:
        from emily_core.session.fetchers.fetch_rag_info import fetch as fetch_rag_info
        rag = fetch_rag_info(core)
    rag["provider"] = type(core._rag_provider).__name__ if core._rag_provider else None

    ctx = SessionContext.create(user_id=user_id, conversation_id="", sender_name="", core=core)

    # 渲染 system prompt（复刻 SessionAgent._build_session_prompt_base + 补权限变量）
    template = load_prompt("session")
    try:
        sop_catalog = core._skill_registry.dump_as_text() if core._skill_registry else ""
    except Exception:
        sop_catalog = ""
    prompt = template.replace("{sop_catalog}", sop_catalog)
    prompt_vars = ctx.get_prompt_variables()
    for key, value in prompt_vars.items():
        prompt = prompt.replace(key, str(value) if value else "（无）")
    # 每轮识别前会清空续接占位符
    prompt = prompt.replace("{paused_context}", "").replace("{paused_sop_id}", "")

    variables = {
        k: (str(v)[:200], len(str(v)))
        for k, v in prompt_vars.items()
    }

    from emily_core.permission.level import level_label
    return {
        "user_id": user_id,
        "user_name": ctx.user_name or "（未知）",
        "user_position": ctx.user_position or "",
        "level_label": level_label(ctx.level),
        "is_management_unit": ctx.is_management_unit,
        "project_name": ctx.project_name or "",
        "rag": rag,
        "available_skills": list(ctx.available_skills),
        "template_chars": len(template),
        "rendered_chars": len(prompt),
        "estimated_tokens": round(len(prompt) / 1.5),
        "system_prompt": prompt,
        "variables": variables,
    }


def _render_markdown(result: dict) -> str:
    lines = ["# Session Prompt（真实 core 渲染）", ""]
    position = result["user_position"] or "（无职位）"
    lines.append(f"- 用户: {result['user_name']}（{position}）· {result['level_label']}")
    lines.append(f"- 管理单位: {result['is_management_unit']} · 项目: {result['project_name'] or '（无）'}")
    rag = result["rag"]
    lines.append(f"- RAG: provider={rag.get('provider')} available={rag.get('available', False)} "
                 f"collections={rag.get('collections', [])}")
    lines.append(f"- 可用技能({len(result['available_skills'])}): {', '.join(result['available_skills'])}")
    lines.append(f"- 模板 {result['template_chars']} 字 → 渲染后 {result['rendered_chars']} 字 "
                 f"（估 {result['estimated_tokens']} tokens）")
    lines += ["", "=" * 70, "  渲染后的完整 System Prompt", "=" * 70, "", result["system_prompt"], ""]
    lines += ["", "=" * 70, "  变量对照表", "=" * 70, ""]
    for k, (val, chars) in result["variables"].items():
        display = val[:120] + "..." if len(val) > 120 else (val or "（空）")
        lines.append(f"  {k:<32} → {display}  ({chars} 字)")
    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="真实 core 渲染 Session prompt")
    parser.add_argument("--user-id", required=True, help="用户 UUID（USERS 表）")
    parser.add_argument("--out-dir", default="", help="输出目录（默认：容器 /app/out_session_prompt；开发 temporary/session_prompt_live）")
    parser.add_argument("--prompt-only", action="store_true", help="仅打印渲染后 prompt（供管道/重定向）")
    args = parser.parse_args()

    result = render_session_prompt(args.user_id)

    if args.prompt_only:
        print(result["system_prompt"])
        return

    if args.out_dir:
        out = Path(args.out_dir)
    elif Path("/app").exists():
        out = Path("/app/out_session_prompt")
    else:
        out = Path(__file__).resolve().parent.parent / "temporary" / "session_prompt_live"
    out.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c for c in result["user_name"] if c not in '\\/:*?"<>|').strip() or "user"
    md_path = out / f"{safe_name}_session_prompt_live.md"
    md_path.write_text(_render_markdown(result), encoding="utf-8")

    manifest = {k: v for k, v in result.items() if k not in ("system_prompt", "variables")}
    manifest["variables_count"] = len(result["variables"])
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    print(f"\n已保存: {md_path}")


if __name__ == "__main__":
    main()
