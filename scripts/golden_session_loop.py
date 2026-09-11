"""golden_session_loop.py — 会话主循环 golden 语料回放与度量（计划 M9 / US-10）。

用途：
  · 以 golden 语料回放新旧两条会话路径，产出**四类断言**结果与 token/延迟度量，
    供 req-verify 消费（AC-US-10.1 / AC-US-10.2）。

四类断言（分组）：
  capability_hit        能力命中正确性 —— 期望能力名出现在该轮能力调用清单中
  permission_block      权限拦截 —— 越权诉求被拒绝且无对应写操作
  suspend_resume        挂起续接 —— 缺参提问 → 用户回答 → 续接同一能力
  archive_completeness  归档完整性 —— 轮次归档段含五要素（能力/参数/成果/触发者/成败）

用法：
    # 预览（不发请求）
    uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --dry-run
    # 回放 + 报告
    uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --report emily-data/golden/report_new.json

双通道：CLI + `run()` / `load_corpus()` / `assert_case()` 可 import。

⚠ 语料中的 `sender_id` 必须是 users 表**真实 UUID**（宪法 Q3）：伪造 ID 会新建用户并降级到
访客路径，测试结果不可信。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scripts.golden_session_loop")

ASSERTION_GROUPS = ("capability_hit", "permission_block", "suspend_resume", "archive_completeness")
DEFAULT_ENDPOINT = "http://127.0.0.1:18080/api/v1/message/send"


def _default_archive_dir() -> str:
    dev = _HERE.parent / "emily-data" / "session_archives"
    return str(dev if dev.exists() else Path("/app/session_archives"))


def _default_trace_path() -> str:
    dev = _HERE.parent / "emily-data" / "logs" / "llm_trace.jsonl"
    return str(dev if dev.exists() else Path("/app/logs/llm_trace.jsonl"))


@dataclass
class GoldenCase:
    """单条 golden 语料。"""

    case_id: str
    message: str
    sender_id: str = ""
    sender_name: str = ""
    conversation_id: str = ""
    group: str = "business"
    expect: dict = field(default_factory=dict)
    resume_message: str = ""


def load_corpus(corpus_path: str) -> list:
    """载入 golden 语料（YAML 或 JSON）。"""
    p = Path(corpus_path)
    if not p.exists():
        raise FileNotFoundError(f"corpus not found: {corpus_path}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml
        raw = yaml.safe_load(text) or {}
    else:
        raw = json.loads(text)
    items = raw.get("cases") if isinstance(raw, dict) else raw
    cases = []
    for i, item in enumerate(items or [], 1):
        if not isinstance(item, dict):
            continue
        cases.append(GoldenCase(
            case_id=str(item.get("id") or f"case-{i}"),
            message=str(item.get("message") or ""),
            sender_id=str(item.get("sender_id") or ""),
            sender_name=str(item.get("sender_name") or ""),
            conversation_id=str(item.get("conversation_id") or f"golden-{item.get('id') or i}"),
            group=str(item.get("group") or "business"),
            expect=dict(item.get("expect") or {}),
            resume_message=str(item.get("resume_message") or ""),
        ))
    return cases


def assert_case(case: GoldenCase, observed: dict) -> list:
    """对单条语料做断言，返回失败描述列表（空 = 全过）。"""
    failures: list = []
    exp = case.expect or {}
    reply = observed.get("reply") or ""
    capabilities = observed.get("capabilities") or []

    for want in (exp.get("capability_hit") or []):
        if want not in capabilities:
            failures.append(f"capability_hit: 期望 {want}，实际 {capabilities}")

    if exp.get("permission_block"):
        blocked = any(k in reply for k in ("无法", "不可用", "没有相应权限", "请联系管理员", "不可"))
        if not blocked:
            failures.append(f"permission_block: 未出现拒绝说明 —— {reply[:80]}")
        if any(c in capabilities for c in (exp.get("must_not_call") or [])):
            failures.append(f"permission_block: 越权能力被调用 —— {capabilities}")

    sr = exp.get("suspend_resume")
    if sr:
        needle = sr.get("question_contains") or ""
        if needle and needle not in reply:
            failures.append(f"suspend_resume: 提问未包含 {needle!r} —— {reply[:80]}")
        resumed = observed.get("resumed_capabilities") or []
        want_cap = sr.get("capability")
        if want_cap and want_cap not in resumed:
            failures.append(f"suspend_resume: 续接能力 {want_cap} 未命中 —— {resumed}")

    if exp.get("archive_completeness"):
        section = observed.get("archive_section") or ""
        missing = [k for k in ("能力", "参数", "成果", "触发者") if k not in section]
        if missing:
            failures.append(f"archive_completeness: 归档段缺要素 {missing}")

    for must in (exp.get("reply_contains") or []):
        if must not in reply:
            failures.append(f"reply_contains: 缺少 {must!r} —— {reply[:80]}")

    return failures


# ══════════════════════════════════════════════════════════════════════════════
# 回放与证据采集
# ══════════════════════════════════════════════════════════════════════════════

def _post_message(endpoint: str, case: GoldenCase, content: str) -> dict:
    payload = {
        "message_id": f"golden-{case.case_id}-{int(time.time() * 1000)}",
        "platform": "golden",
        "conversation_type": "private",
        "conversation_id": case.conversation_id,
        "sender_id": case.sender_id,
        "sender_name": case.sender_name,
        "content": content,
        "is_at_bot": True,
        "event_id": "",
    }
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body.strip() else {}


def _trace_metrics(trace_path: str, since_offset: int) -> tuple:
    """统计 trace 文件自 offset 起的新增行的 token 用量。"""
    p = Path(trace_path)
    if not p.exists():
        return 0, 0
    tokens, count = 0, 0
    try:
        with p.open("r", encoding="utf-8") as f:
            f.seek(since_offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                count += 1
                try:
                    rec = json.loads(line)
                    usage = (rec.get("response") or {}).get("usage") or rec.get("usage") or {}
                    tokens += int(usage.get("total_tokens") or 0)
                except Exception:  # noqa: BLE001
                    continue
    except Exception as e:  # noqa: BLE001
        logger.warning("trace metrics failed: %s", e)
    return tokens, count


def _read_archive_section(archive_dir: str, conversation_id: str) -> str:
    """读取该会话归档文件中最后一次「能力调用」段。"""
    d = Path(archive_dir)
    if not d.exists():
        return ""
    short = conversation_id[:8]
    candidates = sorted(d.glob(f"*{short}*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        idx = text.rfind("### 🔧 能力调用")
        if idx >= 0:
            return text[idx:idx + 2000]
    return ""


def run(corpus_path: str, path: str = "new", *, report_path: str = "",
        dry_run: bool = False, endpoint: str = "", archive_dir: str = "",
        trace_path: str = "") -> dict:
    """回放 golden 语料并产出报告。

    Args:
        corpus_path: 语料文件路径。
        path: "new"（会话主循环）或 "old"（旧派发链路，仅用于基线度量）。
        report_path: 报告输出路径（空则不落盘）。
        dry_run: True 时仅打印计划，不发请求。
    """
    cases = load_corpus(corpus_path)
    endpoint = endpoint or DEFAULT_ENDPOINT
    archive_dir = archive_dir or _default_archive_dir()
    trace_path = trace_path or _default_trace_path()

    groups_present = {g: 0 for g in ASSERTION_GROUPS}
    for c in cases:
        exp = c.expect or {}
        if exp.get("capability_hit"):
            groups_present["capability_hit"] += 1
        if exp.get("permission_block"):
            groups_present["permission_block"] += 1
        if exp.get("suspend_resume"):
            groups_present["suspend_resume"] += 1
        if exp.get("archive_completeness"):
            groups_present["archive_completeness"] += 1

    if dry_run:
        print(f"[dry-run] path={path} cases={len(cases)} endpoint={endpoint}")
        for c in cases:
            print(f"  - {c.case_id} [{c.group}] {c.message[:60]}")
        print(f"[dry-run] assertion groups: {groups_present}")
        return {
            "path": path, "dry_run": True, "cases": len(cases),
            "groups": groups_present, "results": [],
        }

    results = []
    for c in cases:
        offset = Path(trace_path).stat().st_size if Path(trace_path).exists() else 0
        t0 = time.monotonic()
        try:
            resp = _post_message(endpoint, c, c.message)
            reply = resp.get("content", "") if isinstance(resp, dict) else ""
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            reply = ""
            logger.error("case %s request failed: %s", c.case_id, e)
        latency_ms = int((time.monotonic() - t0) * 1000)
        tokens, calls = _trace_metrics(trace_path, offset)

        resumed_capabilities = []
        if c.resume_message:
            try:
                resp2 = _post_message(endpoint, c, c.resume_message)
                reply2 = resp2.get("content", "") if isinstance(resp2, dict) else ""
                if reply2:
                    reply = f"{reply}\n---\n{reply2}"
            except Exception as e:  # noqa: BLE001
                logger.error("case %s resume request failed: %s", c.case_id, e)

        section = _read_archive_section(archive_dir, c.conversation_id)
        for cap in (c.expect.get("capability_hit") or []):
            if cap in section:
                resumed_capabilities.append(cap)

        observed = {
            "reply": reply, "capabilities": resumed_capabilities,
            "resumed_capabilities": resumed_capabilities,
            "archive_section": section, "tokens": tokens, "llm_calls": calls,
            "latency_ms": latency_ms,
        }
        failures = assert_case(c, observed)
        results.append({
            "id": c.case_id, "group": c.group, "path": path,
            "passed": not failures, "failures": failures,
            "tokens": tokens, "llm_calls": calls, "latency_ms": latency_ms,
            "reply_digest": (reply or "")[:200],
        })
        logger.info("case %s: %s (tokens=%d, latency=%dms)",
                    c.case_id, "PASS" if not failures else f"FAIL {failures}", tokens, latency_ms)

    summary = {
        "path": path,
        "cases": len(cases),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "groups": groups_present,
        "chitchat_avg_tokens": _avg([r["tokens"] for r in results
                                     if _group_of(cases, r["id"]) == "chitchat"]),
        "chitchat_avg_latency_ms": _avg([r["latency_ms"] for r in results
                                         if _group_of(cases, r["id"]) == "chitchat"]),
        "business_avg_tokens": _avg([r["tokens"] for r in results
                                     if _group_of(cases, r["id"]) != "chitchat"]),
        "results": results,
    }
    if report_path:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("report written: %s", out)
    return summary


def _group_of(cases: list, case_id: str) -> str:
    for c in cases:
        if c.case_id == case_id:
            return c.group
    return "business"


def _avg(values: list) -> float:
    vals = [v for v in values if v]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="会话主循环 golden 语料回放与度量")
    parser.add_argument("--corpus", required=True, help="语料文件路径（yaml/json）")
    parser.add_argument("--path", default="new", choices=["new", "old"], help="被测路径")
    parser.add_argument("--report", default="", help="报告输出路径")
    parser.add_argument("--endpoint", default="", help="message/send 端点")
    parser.add_argument("--archive-dir", default="", help="会话归档目录")
    parser.add_argument("--trace", default="", help="llm_trace.jsonl 路径")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = parser.parse_args(argv)

    summary = run(
        args.corpus, args.path, report_path=args.report, dry_run=args.dry_run,
        endpoint=args.endpoint, archive_dir=args.archive_dir, trace_path=args.trace,
    )
    if not args.dry_run:
        print(f"path={summary['path']} passed={summary['passed']}/{summary['cases']} "
              f"groups={summary['groups']}")
        print(f"chitchat avg: tokens={summary['chitchat_avg_tokens']} "
              f"latency={summary['chitchat_avg_latency_ms']}ms")
    return 0 if summary.get("dry_run") or summary.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
