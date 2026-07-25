"""scriptmgr — ScriptManager CLI 薄壳。

用法：
    uv run python scripts/scriptmgr.py list
    uv run python scripts/scriptmgr.py describe <name>
    uv run python scripts/scriptmgr.py check [<name>]
    uv run python scripts/scriptmgr.py run <name> [--args "..."]
    uv run python scripts/scriptmgr.py test [<name>]
    uv run python scripts/scriptmgr.py export [--format markdown|json] [--out <path>]

退出码：0=成功，1=调用失败，2=脚本不存在
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# 零依赖加载 .env 文件
_ENV_FILE = _ROOT / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                _key = _key.strip()
                _val = _val.strip()
                if _key and _key not in os.environ:
                    os.environ[_key] = _val

_CORE_DIR = _ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("scriptmgr")


# ══════════════════════════════════════════════════════════════════════════════
# 初始化
# ══════════════════════════════════════════════════════════════════════════════

def _init_core():
    """完整初始化 EmilyCore（复用 bootstrap.init）。"""
    from emily_core.bootstrap import init
    core = init()
    core._ensure_initialized()
    return core


def _get_sm(core):
    """获取 ScriptManager 实例。"""
    sm = getattr(core, "_script_manager", None)
    if sm is None:
        print("ERROR: ScriptManager not initialized (core._script_manager is None)", file=sys.stderr)
        sys.exit(1)
    return sm


# ══════════════════════════════════════════════════════════════════════════════
# 输出格式化
# ══════════════════════════════════════════════════════════════════════════════

def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """纯文本对齐表格输出。"""
    if not rows:
        print("(empty)")
        return
    all_rows = [headers] + rows
    col_widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print("  ".join(str(row[i]).ljust(col_widths[i]) for i in range(len(headers))))


def _truncate(s: str, max_len: int = 60) -> str:
    """截断长字符串。"""
    s = str(s).replace("\n", " ")
    return s if len(s) <= max_len else s[:max_len - 3] + "..."


# ══════════════════════════════════════════════════════════════════════════════
# 子命令处理
# ══════════════════════════════════════════════════════════════════════════════

def cmd_list(sm, args) -> int:
    """列出所有脚本。"""
    scripts = sm.list()
    if args.json:
        print(json.dumps({"scripts": scripts, "count": len(scripts)}, ensure_ascii=False, indent=2))
        return 0

    # 按 category 分组
    categories: dict[str, list] = {}
    for s in scripts:
        cat = s.get("category", "unknown")
        categories.setdefault(cat, []).append(s)

    CAT_ORDER = ["evolution_pipeline", "cold_start", "cognition_cycle",
                 "node_management", "system_maintenance", "business_tool",
                 "file_api_manage", "data_collection", "one_shot", "aggregation_shell"]
    for cat in CAT_ORDER:
        cat_scripts = categories.get(cat, [])
        if not cat_scripts:
            continue
        print(f"\n[{cat}]")
        rows = [[s["name"], s["status"], "Y" if s["has_check"] else "N",
                 "Y" if s["writes_db"] else "N", _truncate(s["description"], 40)]
                for s in cat_scripts]
        _print_table(["NAME", "STATUS", "CHECK", "DB", "DESCRIPTION"], rows)

    print(f"\ntotal: {len(scripts)} scripts")
    return 0


def cmd_describe(sm, args) -> int:
    """显示脚本详情。"""
    if args.name:
        result = sm.describe(args.name)
        if "error" in result:
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"ERROR: {result['error']}")
            return result.get("code", 2)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        _print_single_script(result)
    else:
        result = sm.describe(None)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        # 表格输出全部
        scripts = result.get("scripts", [])
        print(f"\nScripts: {len(scripts)} total\n")
        rows = [[s["name"], s["category"], s["status"],
                 s.get("auto_run") or "-", s.get("check_arg") or "-"]
                for s in scripts]
        _print_table(["NAME", "CATEGORY", "STATUS", "AUTO_RUN", "CHECK_ARG"], rows)
    return 0


def _print_single_script(data: dict) -> None:
    """打印单个脚本详情。"""
    print(f"Name:           {data['name']}")
    print(f"Description:    {data.get('description', '-')}")
    print(f"Category:       {data.get('category', '-')}")
    print(f"Source:         {data.get('source_path', '-')}")
    print(f"Status:         {data.get('status', '-')}")
    print(f"Check Arg:      {data.get('check_arg') or '-'}")
    print(f"Writes DB:      {data.get('writes_db', False)}")
    print(f"Auto Run:       {data.get('auto_run') or '-'}")
    print(f"Auto Run Args:  {data.get('auto_run_args', [])}")
    print(f"Timeout:        {data.get('timeout_seconds', 60)}s")
    print(f"Invocation:     {data.get('invocation', '-')}")
    print(f"Agg Parent:     {data.get('aggregation_parent') or '-'}")
    if data.get('entrypoint'):
        print(f"Entrypoint:     {data['entrypoint']}")


def cmd_check(sm, args) -> int:
    """跑 check_arg 自检。"""
    result = sm.check(args.name if args.name else None)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if "error" in result:
        print(f"ERROR: {result['error']}")
        return result.get("code", 2)

    results = result.get("results", [])
    print(f"\nCheck: {len(results)} scripts\n")
    rows = [[r["name"], r["category"], str(r["ready"]),
             str(r.get("returncode") or "-"), _truncate(r.get("note", "-"), 50)]
            for r in results]
    _print_table(["NAME", "CATEGORY", "READY", "RC", "NOTE"], rows)

    n_a_count = sum(1 for r in results if r["ready"] == "n/a")
    ready_count = sum(1 for r in results if r["ready"] is True)
    not_ready = sum(1 for r in results if r["ready"] is False)
    print(f"\n{ready_count} ready, {not_ready} not ready, {n_a_count} n/a")
    return 0


def cmd_run(sm, args) -> int:
    """执行脚本。"""
    run_args = []
    if args.args:
        import shlex
        try:
            run_args = shlex.split(args.args)
        except ValueError:
            run_args = args.args.split()

    result = sm.run(args.name, args=run_args)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("code") == 2:
            print(f"ERROR: {result.get('error')}")
        elif result.get("success"):
            print(result.get("stdout", ""))
        else:
            print(f"ERROR: {result.get('error')}")
            if result.get("stderr"):
                print(f"STDERR: {result['stderr'][:500]}")

    return result.get("code", 0)


def cmd_test(sm, args) -> int:
    """跑 smoke 用例。"""
    result = sm.test(args.name if args.name else None)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        results = result.get("results", [])
        print(f"\nSmoke Test Results: {result.get('passed', 0)} passed, "
              f"{result.get('failed', 0)} failed, {len(results)} total\n")
        rows = [[r["name"], r["status"],
                 _truncate(r.get("error", r.get("stdout_truncated", "-")), 50)]
                for r in results]
        _print_table(["SCRIPT", "STATUS", "DETAIL"], rows)

    return 1 if result.get("failed", 0) > 0 else 0


def cmd_export(sm, args) -> int:
    """导出注册表信息。"""
    fmt = args.format or "markdown"
    if fmt == "json":
        data = sm.export(format="json")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    # markdown
    md = sm.export(format="markdown")
    if args.out:
        out_path = _ROOT / args.out
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Exported to {out_path}")
    else:
        print(md)
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="scriptmgr — ScriptManager CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  list                        列出所有脚本（按 category 分组）
  describe [<name>]           显示脚本详情（无 name 列出全部摘要）
  check [<name>]              跑 check_arg 自检
  run <name> [--args "..."]   执行脚本
  test [<name>]               跑预设 smoke 用例
  export [--format markdown|json] [--out <path>]  导出注册信息

退出码: 0=成功, 1=调用失败, 2=脚本不存在
        """,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # list
    sp_list = sub.add_parser("list", help="列出所有脚本")
    sp_list.add_argument("--json", action="store_true", help="JSON 格式输出")

    # describe
    sp_desc = sub.add_parser("describe", help="显示脚本详情")
    sp_desc.add_argument("name", nargs="?", default="", help="脚本名（默认全部）")
    sp_desc.add_argument("--json", action="store_true", help="JSON 格式输出")

    # check
    sp_check = sub.add_parser("check", help="跑 check_arg 自检")
    sp_check.add_argument("name", nargs="?", default="", help="脚本名（默认全部）")
    sp_check.add_argument("--json", action="store_true", help="JSON 格式输出")

    # run
    sp_run = sub.add_parser("run", help="执行脚本")
    sp_run.add_argument("name", help="脚本名")
    sp_run.add_argument("--args", default="", help="参数字符串")
    sp_run.add_argument("--json", action="store_true", help="JSON 格式输出")

    # test
    sp_test = sub.add_parser("test", help="跑预设 smoke 用例")
    sp_test.add_argument("name", nargs="?", default="", help="脚本名（默认全部）")
    sp_test.add_argument("--json", action="store_true", help="JSON 格式输出")

    # export
    sp_export = sub.add_parser("export", help="导出注册信息")
    sp_export.add_argument("--format", default="markdown", choices=["markdown", "json"],
                           help="输出格式 (default: markdown)")
    sp_export.add_argument("--out", default="", help="输出文件路径（相对仓库根）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    print("Initializing EmilyCore...", file=sys.stderr)
    core = _init_core()
    sm = _get_sm(core)
    print("Ready.", file=sys.stderr)

    handlers = {
        "list": cmd_list,
        "describe": cmd_describe,
        "check": cmd_check,
        "run": cmd_run,
        "test": cmd_test,
        "export": cmd_export,
    }

    handler = handlers.get(args.command)
    if handler:
        exit_code = handler(sm, args)
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
