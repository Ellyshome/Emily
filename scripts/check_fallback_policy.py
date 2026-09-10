"""check_fallback_policy.py — 分级兜底策略一致性检查 CLI。

校验 FallbackPolicy 内部白名单自洽，以及与 Config 默认值 / 权限等级的对应关系。
方案 B：独立审核脚本，供开发者改完分级兜底后验证 + 回归保障。

用法：
    uv run python scripts/check_fallback_policy.py
    uv run python scripts/check_fallback_policy.py --json

退出码：0=通过；1=存在致命不一致（便于 CI / 脚本集成）。
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


def _split(s: str) -> set[str]:
    return {x.strip() for x in (s or "").split(",") if x.strip()}


def _collect() -> dict:
    from emily_core.config import Config
    from emily_core.permission.level import PermissionLevel
    from emily_core.workitem.langgraph_engine.agent.fallback_policy import FallbackPolicy

    cfg = Config()
    basic = FallbackPolicy.basic_tools()
    read = FallbackPolicy.advanced_read_tools()
    append = FallbackPolicy.advanced_append_tools()

    issues: list[dict] = []

    def add(check: str, detail: str, fatal: bool = True):
        issues.append({"check": check, "detail": detail, "severity": "fatal" if fatal else "warning"})

    # 1. 基础兜底必须非空且零写
    if not basic:
        add("basic_nonempty", "基础兜底白名单为空，fail-open 风险", fatal=True)
    if basic & append:
        add("basic_zero_write", f"基础兜底不应包含写工具：{sorted(basic & append)}", fatal=True)

    # 2. 高级只读集必须覆盖基础集
    if not basic <= read:
        add("read_covers_basic", f"高级只读集未覆盖基础集：{sorted(basic - read)}", fatal=True)

    # 3. 追加写白名单不得与只读集重叠
    if append & read:
        add("append_read_disjoint", f"追加写与只读集重叠：{sorted(append & read)}", fatal=False)

    # 4. 高级档最低等级 = PermissionLevel.OWNER_SUPERVISOR
    if cfg.fallback_admin_min_level != PermissionLevel.OWNER_SUPERVISOR.value:
        add("min_level", f"fallback_admin_min_level={cfg.fallback_admin_min_level} "
                         f"应等于 OWNER_SUPERVISOR={PermissionLevel.OWNER_SUPERVISOR.value}", fatal=False)

    # 5. Config 默认值与策略单一事实源对齐
    if _split(cfg.fallback_basic_tools) != basic:
        add("config_basic", f"fallback_basic_tools={sorted(_split(cfg.fallback_basic_tools))} "
                            f"≠ 策略 basic_tools={sorted(basic)}", fatal=True)
    if _split(cfg.fallback_advanced_write_tools) != append:
        add("config_append", f"fallback_advanced_write_tools={sorted(_split(cfg.fallback_advanced_write_tools))} "
                             f"≠ 策略 advanced_append_tools={sorted(append)}", fatal=True)

    return {
        "summary": {
            "basic_count": len(basic),
            "read_count": len(read),
            "append_count": len(append),
            "total_issues": len(issues),
            "fatal_issues": sum(1 for i in issues if i["severity"] == "fatal"),
        },
        "basic_tools": sorted(basic),
        "advanced_read_tools": sorted(read),
        "advanced_append_tools": sorted(append),
        "issues": issues,
    }


def _format(r: dict) -> str:
    lines = ["=" * 70, "分级兜底策略一致性检查报告", "=" * 70]
    s = r["summary"]
    lines.append(f"\n[摘要] basic={s['basic_count']} | advanced_read={s['read_count']} | "
                 f"advanced_append={s['append_count']} | 问题 {s['total_issues']} "
                 f"(fatal {s['fatal_issues']})")
    for i in r["issues"]:
        mark = "❌" if i["severity"] == "fatal" else "⚠️"
        lines.append(f"  {mark} [{i['check']}] {i['detail']}")
    if not r["issues"]:
        lines.append("\n✅ 分级兜底策略自洽")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="分级兜底策略一致性检查")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    result = _collect()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format(result))

    sys.exit(1 if result["summary"]["fatal_issues"] > 0 else 0)


if __name__ == "__main__":
    main()
