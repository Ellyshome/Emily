"""check_fallback_tools.py — 分级兜底白名单 ↔ 工具写语义一致性检查 CLI。

交叉校验 FallbackPolicy 白名单与静态 write_mode 元数据（TOOL_WRITE_MODE_MAP）：
  - 追加写白名单中的工具必须为 append/transition 语义；
  - 只读白名单中的工具不得为写语义；
  - 覆盖/删除语义工具不得出现在任何兜底白名单。

用法：
    uv run python scripts/check_fallback_tools.py
    uv run python scripts/check_fallback_tools.py --json

退出码：0=通过；1=存在致命不一致。
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

_WRITE_MODES = {"append", "transition", "overwrite", "delete"}


def _collect() -> dict:
    from emily_core.infrastructure.tools_consistency import TOOL_WRITE_MODE_MAP
    from emily_core.workitem.langgraph_engine.agent.fallback_policy import FallbackPolicy

    basic = FallbackPolicy.basic_tools()
    read = FallbackPolicy.advanced_read_tools()
    append = FallbackPolicy.advanced_append_tools()

    issues: list[dict] = []

    def add(check: str, detail: str, fatal: bool = True):
        issues.append({"check": check, "detail": detail, "severity": "fatal" if fatal else "warning"})

    def mode_of(name: str) -> str:
        return TOOL_WRITE_MODE_MAP.get(name, "read")

    # 1. 追加写白名单工具必须为 append/transition
    for t in sorted(append):
        m = mode_of(t)
        if m not in ("append", "transition"):
            add("append_mode", f"追加写工具 '{t}' 的 write_mode={m}，应为 append/transition", fatal=True)

    # 2. 只读白名单工具不得为写语义
    for t in sorted(read):
        m = mode_of(t)
        if m in _WRITE_MODES:
            add("read_mode", f"只读白名单工具 '{t}' 的 write_mode={m}，不应为写语义", fatal=True)

    # 3. 覆盖/删除语义工具不得出现在任何兜底白名单
    for t in sorted(read | append | basic):
        m = mode_of(t)
        if m in ("overwrite", "delete"):
            add("no_overwrite_delete", f"工具 '{t}' 为 {m} 语义，不得进入兜底白名单", fatal=True)

    # 4. write_mode 元数据中的覆盖/删除工具不得进入白名单（双向）
    for t, m in sorted(TOOL_WRITE_MODE_MAP.items()):
        if m in ("overwrite", "delete") and t in (read | append | basic):
            add("meta_in_whitelist", f"覆盖/删除工具 '{t}' 误入兜底白名单", fatal=True)

    return {
        "summary": {
            "basic_count": len(basic),
            "read_count": len(read),
            "append_count": len(append),
            "total_issues": len(issues),
            "fatal_issues": sum(1 for i in issues if i["severity"] == "fatal"),
        },
        "issues": issues,
    }


def _format(r: dict) -> str:
    lines = ["=" * 70, "分级兜底白名单 ↔ 写语义一致性检查报告", "=" * 70]
    s = r["summary"]
    lines.append(f"\n[摘要] basic={s['basic_count']} | advanced_read={s['read_count']} | "
                 f"advanced_append={s['append_count']} | 问题 {s['total_issues']} "
                 f"(fatal {s['fatal_issues']})")
    for i in r["issues"]:
        mark = "❌" if i["severity"] == "fatal" else "⚠️"
        lines.append(f"  {mark} [{i['check']}] {i['detail']}")
    if not r["issues"]:
        lines.append("\n✅ 分级兜底白名单与写语义一致")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="分级兜底白名单 ↔ 写语义一致性检查")
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
