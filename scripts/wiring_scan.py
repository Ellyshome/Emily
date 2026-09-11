"""wiring_scan.py —— 接线可达性静态扫描（宪法 §3 Q6 的核验工具）。

扫描"定义 vs 使用"，找出**半截交付 / 断线**：
  1. Config 字段（**默认，高精度**）：仅定义无读取（死开关）、仅登记进 env_map 无读取
  2. DB 列（`--with-models`，**低置信度**）：仅读无写、仅写无读、读写皆无
     —— 按属性名匹配，会与其他模型的同名字段混淆，且 ORM 常以 **kwargs/dict 写入，
     结果仅作线索，必须人工复核（见 scan_models 文档字符串）

判据来自项目宪法 §3 Q6：任何产出物必须有消费者，任何开关必须有读取方。
用静态证据（tokenize 后的 NAME/STRING 计数）而非运行时行为——运行时"看起来正常"
不能证明已接线（默认值巧合正确尤其危险）。

"写而不读"这类**跨模型的接缝**（如"记忆写入无人读取"）静态难以可靠判定，
由 req-verify 的强制"端到端闭环用例"覆盖，本脚本不冒充。

用法：
    python scripts/wiring_scan.py                     # Config 字段（高精度，默认）
    python scripts/wiring_scan.py --with-models       # 追加 DB 列（低置信度，需复核）
    python scripts/wiring_scan.py --markdown          # Markdown 表格（供测试报告粘贴）
    python scripts/wiring_scan.py --json              # JSON
    python scripts/wiring_scan.py --only expert_review_enabled,long_term_memory

退出码：0 = 无断线；1 = 发现断线；2 = 运行错误。

双通道：CLI（本文件） + 可 import（run() 返回 dict），符合项目"独立脚本"约定。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tokenize
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "emily-core" / "emily_core"
CONFIG_PY = CORE / "config.py"
BOOTSTRAP_PY = CORE / "bootstrap.py"
MODELS_PY = CORE / "infrastructure" / "database" / "models.py"

# 扫描"消费者"范围 = 全仓 Python；排除依赖/数据/缓存目录
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
              "site-packages", "postgres_data", "dist-info"}

_TOKEN_CACHE: dict[str, tuple[int, list]] = {}


def _iter_py_files(root: Path):
    """遍历 root 下所有 .py 文件（跳过依赖/数据/缓存目录）。"""
    for p in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def _tokens(path: Path):
    """返回文件中"有意义的 token"列表：[(type_str, string), ...]（带缓存）。

    自动剔除 COMMENT / NEWLINE / INDENT / DEDENT / NL / ENCODING / ENDMARKER，
    因此注释与文档字符串不会污染计数（这是本脚本比 grep 更准的关键）。
    """
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    hit = _TOKEN_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "rb") as f:
            raw = list(tokenize.tokenize(f.readline))
    except (SyntaxError, tokenize.TokenError, UnicodeDecodeError, OSError):
        _TOKEN_CACHE[key] = (mtime, [])
        return []
    skip = {"COMMENT", "NEWLINE", "NL", "INDENT", "DEDENT", "ENCODING", "ENDMARKER"}
    out = []
    for tok in raw:
        name = tokenize.tok_name.get(tok.type, "")
        if name in skip:
            continue
        out.append((name, tok.string))
    _TOKEN_CACHE[key] = (mtime, out)
    return out


def _count_refs(path: Path, target: str) -> tuple[int, int, int]:
    """统计 target 的引用次数，返回 (reads, writes, string_mentions)。

    - 属性/标识符出现：NAME token（`cfg.target`、局部变量）
    - 属性赋值 `x.target = ...`、关键字实参 `target=` 计为 write
    - 字符串字面量 `"target"` 计为 string_mention
      （本项目大量使用 `getattr(cfg, "target", default)`，只数 NAME 会漏报）
    """
    toks = _tokens(path)
    reads = writes = strings = 0
    for i, (tname, tstr) in enumerate(toks):
        if tname == "STRING":
            if tstr.strip("\"'") == target:
                strings += 1
            continue
        if tname != "NAME" or tstr != target:
            continue
        prev = toks[i - 1][1] if i > 0 else ""
        nxt = toks[i + 1][1] if i + 1 < len(toks) else ""
        is_write = nxt == "=" and (prev == "." or prev in ("(", ",", ""))
        if is_write:
            writes += 1
        else:
            reads += 1
    return reads, writes, strings


def _load_env_map_values() -> set[str]:
    """解析 bootstrap.py 的 env_map，返回其映射到的 Config 字段名集合。"""
    import re

    if not BOOTSTRAP_PY.exists():
        return set()
    text = BOOTSTRAP_PY.read_text(encoding="utf-8")
    m = re.search(r"env_map\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        return set()
    return set(re.findall(r':\s*"([^"]+)"', m.group(1)))



def _load_config_fields() -> list[str]:
    """解析 config.py 中 `class Config` 的字段名。"""
    import re

    if not CONFIG_PY.exists():
        return []
    text = CONFIG_PY.read_text(encoding="utf-8")
    lines = text.splitlines()
    # 定位 class Config
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^class Config\b", ln):
            start = i
            break
    if start is None:
        return []
    # 字段形式：4 空格缩进 + 名称 + `: 类型` + `=`（或行尾）
    field_re = re.compile(
        r"^    ([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z_][A-Za-z0-9_\[\], .]*\s*(?:=|$)"
    )
    fields: list[str] = []
    for ln in lines[start + 1:]:
        if ln and not ln.startswith((" ", "\t", "#")):
            break  # 回到顶层（class/def/空行后的顶层语句）
        m = field_re.match(ln)
        if m:
            fields.append(m.group(1))
    return fields


def _load_model_columns() -> list[tuple[str, str]]:
    """解析 models.py 中的 Column 定义，返回 [(类名, 列名), ...]。"""
    import re

    if not MODELS_PY.exists():
        return []
    lines = MODELS_PY.read_text(encoding="utf-8").splitlines()
    col_re = re.compile(r"^    ([a-z_][a-z0-9_]*)\s*=\s*Column\(")
    cls_re = re.compile(r"^class (\w+)\b")
    current = ""
    out: list[tuple[str, str]] = []
    for ln in lines:
        mc = cls_re.match(ln)
        if mc:
            current = mc.group(1)
            continue
        m = col_re.match(ln)
        if m:
            out.append((current, m.group(1)))
    return out


def scan_config(only: set[str] | None) -> list[dict[str, Any]]:
    """扫描 Config 字段的接线情况。"""
    fields = _load_config_fields()
    consumers = [p for p in _iter_py_files(ROOT) if p != CONFIG_PY]
    env_map_values = _load_env_map_values()
    findings: list[dict[str, Any]] = []
    total = {f: {"reads": 0, "writes": 0, "strings": 0} for f in fields}
    for f in fields:
        for p in consumers:
            r, w, s = _count_refs(p, f)
            total[f]["reads"] += r
            total[f]["writes"] += w
            # bootstrap 中作为 env_map 映射值的那次字符串不算"消费"
            if p == BOOTSTRAP_PY and f in env_map_values and s > 0:
                s -= 1
            total[f]["strings"] += s
    for f in fields:
        if only and f not in only:
            continue
        reads = total[f]["reads"]
        writes = total[f]["writes"]
        strings = total[f]["strings"]
        if reads + writes + strings > 0:
            continue
        if f in env_map_values:
            findings.append({
                "kind": "config", "name": f,
                "severity": "WARN", "type": "MAPPED_ONLY",
                "detail": "已登记进 bootstrap env_map，但全仓无读取方（读入配置后没人消费）",
                "reads": 0, "writes": 0,
            })
        else:
            findings.append({
                "kind": "config", "name": f,
                "severity": "FAIL", "type": "DEAD_SWITCH",
                "detail": "仅定义无使用（死开关）：全仓除 config.py 定义处外零引用",
                "reads": 0, "writes": 0,
            })
    return findings


def scan_models(only: set[str] | None) -> list[dict[str, Any]]:
    """扫描 DB 列的读写接线情况。

    注意：ORM 动态写入（**kwargs / bulk_update / 原生 SQL）无法被静态识别，
    故本项结果一律标 WARN，需人工复核，不作为 FAIL 依据。
    """
    columns = _load_model_columns()
    consumers = [p for p in _iter_py_files(ROOT) if p != MODELS_PY]
    findings: list[dict[str, Any]] = []
    agg: dict[str, dict[str, int]] = {}
    for _, col in columns:
        agg.setdefault(col, {"reads": 0, "writes": 0})
    for col in agg:
        for p in consumers:
            r, w, s = _count_refs(p, col)
            agg[col]["reads"] += r + s  # 字符串引用（getattr/dict）视为读
            agg[col]["writes"] += w
    seen: set[str] = set()
    for cls, col in columns:
        if col in seen:
            continue
        seen.add(col)
        if only and col not in only:
            continue
        reads, writes = agg[col]["reads"], agg[col]["writes"]
        ftype = None
        if reads > 0 and writes == 0:
            ftype = "READ_NO_WRITE"
            detail = "有读取方但全仓无写入方（读有写无）—— 读取结果恒为默认值/空"
        elif writes > 0 and reads == 0:
            ftype = "WRITE_NO_READ"
            detail = "有写入方但全仓无读取方（写而不读）—— 写入后无人消费"
        elif reads == 0 and writes == 0:
            ftype = "UNUSED_COLUMN"
            detail = "全仓无任何读写（孤儿列）"
        if ftype:
            findings.append({
                "kind": "model", "name": col, "owner": cls,
                "severity": "WARN", "type": ftype, "detail": detail,
                "reads": reads, "writes": writes,
            })
    return findings


def run(only: list[str] | None = None, with_models: bool = False) -> dict[str, Any]:
    """执行扫描，返回结构化结果（可 import 调用）。

    Args:
        only: 只扫描指定名称（None = 全部）
        with_models: 是否附加 DB 列扫描（低置信度，默认 False）
    """
    only_set = set(only) if only else None
    config_findings = scan_config(only_set)
    model_findings = scan_models(only_set) if with_models else []
    findings = config_findings + model_findings
    return {
        "scope": str(ROOT),
        "config_fields_scanned": len(_load_config_fields()),
        "model_columns_scanned": len(_load_model_columns()) if with_models else 0,
        "findings": findings,
        "summary": {
            "total": len(findings),
            "fail": sum(1 for f in findings if f["severity"] == "FAIL"),
            "warn": sum(1 for f in findings if f["severity"] == "WARN"),
        },
    }


def _render_markdown(result: dict[str, Any]) -> str:
    lines = ["### 接线可达性扫描结果（scripts/wiring_scan.py）", ""]
    lines.append(f"扫描范围：`{result['scope']}` "
                 f"｜ Config 字段 {result['config_fields_scanned']} 个"
                 f"｜ DB 列 {result['model_columns_scanned']} 个")
    if result["model_columns_scanned"]:
        lines.append("")
        lines.append("> ⚠️ DB 列项为低置信度（按属性名匹配，可能误判同名字段），需人工复核。")
    lines.append("")
    if not result["findings"]:
        lines.append("**扫描结论：无断线。**")
        return "\n".join(lines)
    lines.append("| 对象 | 类型 | 定义/归属 | 读 | 写 | 判定 | 说明 |")
    lines.append("|------|------|----------|----|----|------|------|")
    for f in result["findings"]:
        owner = f.get("owner", "Config")
        lines.append(
            f"| `{f['name']}` | {f['kind']} | {owner} | "
            f"{f['reads']} | {f['writes']} | "
            f"{'❌' if f['severity'] == 'FAIL' else '⚠️'} {f['type']} | {f['detail']} |"
        )
    s = result["summary"]
    lines.append("")
    lines.append(f"**扫描结论：发现 {s['total']} 处（FAIL {s['fail']} / WARN {s['warn']}）。**")
    return "\n".join(lines)


def _render_text(result: dict[str, Any]) -> str:
    lines = [
        f"接线可达性扫描 — 范围 {result['scope']}",
        f"  Config 字段 {result['config_fields_scanned']} 个 | "
        f"DB 列 {result['model_columns_scanned']} 个",
        "",
    ]
    if result["model_columns_scanned"]:
        lines.append("提示：DB 列项为低置信度（按属性名匹配），需人工复核。")
        lines.append("")
    if not result["findings"]:
        lines.append("无断线。")
        return "\n".join(lines)
    for f in result["findings"]:
        owner = f.get("owner", "Config")
        mark = "FAIL" if f["severity"] == "FAIL" else "WARN"
        lines.append(
            f"[{mark}] {f['kind']}:{f['name']} ({owner}) "
            f"读={f['reads']} 写={f['writes']} [{f['type']}]"
        )
        lines.append(f"       {f['detail']}")
    s = result["summary"]
    lines.append("")
    lines.append(f"合计 {s['total']} 处（FAIL {s['fail']} / WARN {s['warn']}）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="接线可达性静态扫描（宪法 Q6）")
    parser.add_argument("--markdown", action="store_true", help="输出 Markdown 表格")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--only", default="", help="只扫描指定名称（逗号分隔）")
    parser.add_argument("--with-models", action="store_true",
                        help="附加 DB 列扫描（低置信度，结果需人工复核）")
    args = parser.parse_args(argv)

    try:
        result = run(
            only=[x.strip() for x in args.only.split(",") if x.strip()] or None,
            with_models=args.with_models,
        )
    except Exception as e:  # 运行错误 → 退出码 2
        print(f"wiring_scan 执行失败：{e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(_render_markdown(result))
    else:
        print(_render_text(result))

    return 1 if result["findings"] else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文
    except (AttributeError, io.UnsupportedOperation):
        pass
    sys.exit(main())
