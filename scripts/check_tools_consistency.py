"""check_tools_consistency.py — BusinessFlowToolRegistry 一致性检查 CLI。

薄壳脚本，核心逻辑在 emily_core.infrastructure.tools_consistency。
方案 B：独立审核脚本，供开发者改完 Skill YAML / 工具后验证 + 回归保障。

用法：
    uv run python scripts/check_tools_consistency.py
    uv run python scripts/check_tools_consistency.py --skill-dir emily-data/skills
    uv run python scripts/check_tools_consistency.py --json
    uv run python scripts/check_tools_consistency.py --no-tool-registry

退出码：0=无 fatal 问题；1=有 fatal 问题（便于 CI / 脚本集成）。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


def _find_skill_dir(explicit: str = "") -> str:
    """多级回退查找 skills 目录：--skill-dir 参数 → 容器 /app/skills → 开发 emily-data/skills。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
        print(f"[WARN] --skill-dir 指定路径不存在: {explicit}", file=sys.stderr)
    candidates = [
        Path("/app/skills"),
        _HERE.parent / "emily-data" / "skills",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(_HERE.parent / "emily-data" / "skills")  # 默认值（可能不存在，check_all 会返回空 skills）


def _format_report(r: dict) -> str:
    """格式化报告为终端可读文本。"""
    lines = []
    lines.append("=" * 70)
    lines.append("BusinessFlowToolRegistry 一致性检查报告")
    lines.append("=" * 70)

    s = r.get("summary", {})
    lines.append(f"\n[摘要] 注册工具 {s.get('registered', 0)} | 有 schema {s.get('with_schema', 0)} | "
                 f"Skill 文件 {s.get('skills', 0)} | 问题 {s.get('total_issues', 0)} (fatal {s.get('fatal_issues', 0)})")

    # V5: 空 schema
    empty = r.get("empty_schema_tools", [])
    if empty:
        lines.append(f"\n[V5] 空 schema 工具 ({len(empty)}):")
        for t in empty:
            lines.append(f"  ⚠️  {t}")

    # V13: tool_registry 表
    tr = r.get("tool_registry")
    if tr:
        if "error" in tr:
            lines.append(f"\n[V13] tool_registry 表检查失败: {tr['error']}")
        else:
            lines.append(f"\n[V13] tool_registry 表: DB {tr['db_count']} 条 | "
                         f"内存缺 DB {len(tr['missing_in_db'])} | DB 缺内存 {len(tr['extra_in_db'])}")
            for t in tr["missing_in_db"]:
                lines.append(f"  ⚠️  内存有 DB 无: {t}")
            for t in tr["extra_in_db"]:
                lines.append(f"  ⚠️  DB 有内存无: {t}")

    # 问题清单
    issues = r.get("issues", [])
    fatal = [i for i in issues if i["severity"] == "fatal"]
    warning = [i for i in issues if i["severity"] == "warning"]
    if fatal:
        lines.append(f"\n[fatal] {len(fatal)} 处致命问题:")
        for i in fatal:
            loc = i.get("skill", i.get("tool", ""))
            step = i.get("step", "")
            loc_str = f"{loc}/{step}" if step else loc
            lines.append(f"  ❌ [{i['check']}] {loc_str}: {i['detail']}")
    if warning:
        lines.append(f"\n[warning] {len(warning)} 处警告:")
        for i in warning:
            loc = i.get("skill", i.get("tool", ""))
            lines.append(f"  ⚠️  [{i['check']}] {loc}: {i['detail']}")

    if not issues:
        lines.append("\n✅ 所有一致性检查通过")

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="BusinessFlowToolRegistry 一致性检查（方案 B：独立审核脚本）",
    )
    parser.add_argument("--skill-dir", default="", help="Skill YAML 目录（默认多级回退）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--no-tool-registry", action="store_true",
                        help="跳过 tool_registry 表检查（不连 DB）")
    args = parser.parse_args()

    skill_dir = _find_skill_dir(args.skill_dir)

    from emily_core.infrastructure.tools_consistency import check_all

    result = check_all(skill_dir, check_tool_registry=not args.no_tool_registry)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_report(result))

    # 退出码：有 fatal 则 1
    fatal = result.get("summary", {}).get("fatal_issues", 0)
    sys.exit(1 if fatal > 0 else 0)


if __name__ == "__main__":
    main()
