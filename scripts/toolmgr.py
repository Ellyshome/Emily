"""toolmgr — ToolManager CLI 薄壳。

用法：
    uv run python scripts/toolmgr.py list
    uv run python scripts/toolmgr.py show query_data
    uv run python scripts/toolmgr.py call query_data --params '{"query_type":"task"}'
    uv run python scripts/toolmgr.py call query_data -f params.json
    uv run python scripts/toolmgr.py test
    uv run python scripts/toolmgr.py export --json
    uv run python scripts/toolmgr.py selfcheck

退出码：0=成功，1=调用失败，2=工具不存在
"""

from __future__ import annotations

import argparse
import asyncio
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
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("toolmgr")


# ══════════════════════════════════════════════════════════════════════════════
# 初始化
# ══════════════════════════════════════════════════════════════════════════════

def _init_core():
    """完整初始化 EmilyCore（复用 bootstrap.init）。"""
    from emily_core.bootstrap import init
    core = init()
    core._ensure_initialized()
    return core


def _get_tm(core):
    """获取 ToolManager 实例。"""
    tm = getattr(core, "_tool_manager", None)
    if tm is None:
        print("ERROR: ToolManager not initialized (core._tool_manager is None)", file=sys.stderr)
        sys.exit(1)
    return tm


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
    # 表头
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

def cmd_list(tm, args) -> int:
    """列出所有工具。"""
    tools = tm.list()
    if args.json:
        print(json.dumps({"tools": tools, "count": len(tools)}, ensure_ascii=False, indent=2))
        return 0

    # 按 category 分组
    categories: dict[str, list] = {}
    for t in tools:
        cat = t.get("category", "unknown")
        categories.setdefault(cat, []).append(t)

    for cat in ["base", "business", "project"]:
        cat_tools = categories.get(cat, [])
        if not cat_tools:
            continue
        print(f"\n[{cat}]")
        rows = [[t["name"], t["permission"], _truncate(t["description"], 50)] for t in cat_tools]
        _print_table(["NAME", "PERM", "DESCRIPTION"], rows)

    # 其他分类
    for cat, cat_tools in sorted(categories.items()):
        if cat in ("base", "business", "project"):
            continue
        print(f"\n[{cat}]")
        rows = [[t["name"], t["permission"], _truncate(t["description"], 50)] for t in cat_tools]
        _print_table(["NAME", "PERM", "DESCRIPTION"], rows)

    print(f"\ntotal: {len(tools)} tools")
    return 0


def cmd_show(tm, args) -> int:
    """显示工具详情 + schema。"""
    tool = tm.describe(args.name)
    if "error" in tool:
        if args.json:
            print(json.dumps(tool, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {tool['error']}")
        return tool.get("code", 2)

    if args.json:
        print(json.dumps(tool, ensure_ascii=False, indent=2))
        return 0

    print(f"Name:        {tool['name']}")
    print(f"Category:    {tool.get('category', '-')}")
    print(f"Permission:  {tool.get('permission', '-')}")
    print(f"Description: {tool.get('description', '-')}")
    print()

    params = tool.get("parameters", {})
    if params.get("properties"):
        print("Parameters (schema):")
        props = params["properties"]
        required = params.get("required", [])
        for pname, pinfo in sorted(props.items()):
            marker = " *REQUIRED*" if pname in required else ""
            ptype = pinfo.get("type", "string")
            pdesc = pinfo.get("description", "")
            print(f"  {pname} ({ptype}){marker}")
            if pdesc:
                print(f"    {pdesc}")
    else:
        print("Parameters: (no schema — takes raw dict)")
    return 0


def cmd_call(tm, args) -> int:
    """调用工具。"""
    # 读取参数
    if args.params_file:
        try:
            with open(args.params_file, "r", encoding="utf-8") as f:
                params = json.load(f)
        except Exception as e:
            err = {"success": False, "error": f"Failed to read params file: {e}", "code": 1}
            if args.json:
                print(json.dumps(err, ensure_ascii=False, indent=2))
            else:
                print(f"ERROR: Failed to read params file: {e}")
            return 1
    elif args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            err = {"success": False, "error": f"Invalid JSON params: {e}", "code": 1}
            if args.json:
                print(json.dumps(err, ensure_ascii=False, indent=2))
            else:
                print(f"ERROR: Invalid JSON params: {e}")
            return 1
    else:
        params = {}

    # 调用
    result = asyncio.run(tm.call(args.name, params))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("code") == 2:
            print(f"ERROR: {result.get('error')}")
        elif result.get("success"):
            print(json.dumps(result["result"], ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {result.get('error')}")
            print(f"  tool: {result.get('tool')}")
            print(f"  params: {json.dumps(params, ensure_ascii=False)}")
            print(f"  hint: run 'toolmgr show {args.name}' to see schema")

    return result.get("code", 0)


def cmd_test(tm, args) -> int:
    """跑预设 smoke 用例。"""
    # 尝试加载用例文件
    cases_path = _HERE.parent / "emily-core" / "tests" / "toolmgr_cases.yaml"
    cases: dict = {}
    if cases_path.exists():
        try:
            import yaml
            with open(cases_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if isinstance(raw, dict):
                cases = raw
        except Exception as e:
            if not args.json:
                print(f"WARN: Failed to load test cases: {e}")

    if not cases:
        msg = "No test cases found. Create emily-core/tests/toolmgr_cases.yaml to add smoke tests."
        if args.json:
            print(json.dumps({"error": msg, "results": [], "count": 0}, ensure_ascii=False, indent=2))
        else:
            print(msg)
        return 0

    # 过滤要测的工具
    target_names = [args.name] if args.name else list(cases.keys())
    results = []
    passed = 0
    failed = 0

    for name in target_names:
        if name not in cases:
            results.append({"name": name, "status": "skipped", "error": "no test case defined"})
            continue
        case = cases[name]
        params = case.get("params", {}) if isinstance(case, dict) else {}
        result = asyncio.run(tm.call(name, params))
        status = "passed" if result.get("success") else "failed"
        if status == "passed":
            passed += 1
        else:
            failed += 1
        results.append({"name": name, "status": status, "result": result})

    if args.json:
        print(json.dumps({"results": results, "passed": passed, "failed": failed, "count": len(results)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\nSmoke Test Results: {passed} passed, {failed} failed, {len(results)} total\n")
        rows = [[r["name"], r["status"],
                 _truncate(r.get("result", {}).get("error", "-"), 50)]
                for r in results]
        _print_table(["TOOL", "STATUS", "DETAIL"], rows)

    return 1 if failed > 0 else 0


def cmd_export(tm, args) -> int:
    """导出全部工具 schema。"""
    data = tm.export()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_selfcheck(tm, args) -> int:
    """依赖就绪检查。"""
    result = tm.selfcheck()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    tools = result.get("tools", [])
    print(f"\nSelf-check: {result.get('count', 0)} tools\n")
    rows = [[t["name"], t["category"],
             "READY" if t["ready"] else "NOT READY",
             t.get("note", "-")]
            for t in tools]
    _print_table(["NAME", "CATEGORY", "STATUS", "NOTE"], rows)

    not_ready = [t for t in tools if not t["ready"]]
    if not_ready:
        print(f"\n{len(not_ready)} tool(s) not ready:")
        for t in not_ready:
            print(f"  - {t['name']}: {t.get('note', '-')}")
    else:
        print("\nAll tools ready.")
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="toolmgr — ToolManager CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  list                  列出所有工具（按 category 分组）
  show <name>           显示工具详情 + schema
  call <name>           调用工具（--params 或 -f 入参）
  test [<name>]         跑预设 smoke 用例
  export                导出全部 schema (JSON)
  selfcheck             依赖就绪检查

退出码: 0=成功, 1=调用失败, 2=工具不存在
        """,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # list
    sp_list = sub.add_parser("list", help="列出所有工具")
    sp_list.add_argument("--json", action="store_true", help="JSON 格式输出")

    # show
    sp_show = sub.add_parser("show", help="显示工具详情")
    sp_show.add_argument("name", help="工具名")
    sp_show.add_argument("--json", action="store_true", help="JSON 格式输出")

    # call
    sp_call = sub.add_parser("call", help="调用工具")
    sp_call.add_argument("name", help="工具名")
    sp_call.add_argument("--params", default="", help="JSON 参数字符串")
    sp_call.add_argument("-f", "--params-file", dest="params_file", default="",
                         help="从 JSON 文件读取参数")
    sp_call.add_argument("--json", action="store_true", help="JSON 格式输出")

    # test
    sp_test = sub.add_parser("test", help="跑预设 smoke 用例")
    sp_test.add_argument("name", nargs="?", default="", help="指定工具名（默认全部）")
    sp_test.add_argument("--json", action="store_true", help="JSON 格式输出")

    # export
    sp_export = sub.add_parser("export", help="导出全部 schema")
    sp_export.add_argument("--json", action="store_true", help="JSON 格式输出")

    # selfcheck
    sp_sc = sub.add_parser("selfcheck", help="依赖就绪检查")
    sp_sc.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    print("Initializing EmilyCore...", file=sys.stderr)
    core = _init_core()
    tm = _get_tm(core)
    print("Ready.", file=sys.stderr)

    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "call": cmd_call,
        "test": cmd_test,
        "export": cmd_export,
        "selfcheck": cmd_selfcheck,
    }

    handler = handlers.get(args.command)
    if handler:
        exit_code = handler(tm, args)
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
