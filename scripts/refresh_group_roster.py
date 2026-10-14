"""refresh_group_roster.py —— 群成员名册刷新（人在场路径的入口之一）。

名册保鲜采用"惰性刷新 + 新鲜度门槛"：不设调度，只在被用到时才检查并刷新。
本脚本是其中一个触发点（另两个是控制台打开群追踪、查看某群名册）；
机器人检索路径**不触发**任何名册写入。

**通道说明**：成员清单须在内核 HTTP 进程内经薄插件拉取（SSE 通道绑定该进程），
故本脚本是"薄 CLI → 调内核端点 `POST /api/v1/groups/roster-refresh`"。

用法：
    uv run python scripts/refresh_group_roster.py --group 群号
    uv run python scripts/refresh_group_roster.py --limit 20        # 刷新前 20 个过期群
    uv run python scripts/refresh_group_roster.py --group 群号 --dry-run

失败时如实回报且**不写**名册同步时间（不把失败当新鲜）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = PROJECT_ROOT / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("refresh_group_roster")

DEFAULT_BASE_URL = os.environ.get("EMILY_CORE_BASE_URL", "http://127.0.0.1:18080")


def _declare_source() -> None:
    try:
        from emily_core.infrastructure.logging.audit import declare_script_source

        declare_script_source("refresh_group_roster")
    except Exception as ex:  # noqa: BLE001 — 非阻断
        logger.debug("declare_script_source failed: %s", ex)


def _post(path: str, payload: dict, base_url: str, timeout: float) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("EMILY_API_TOKEN", "")
    if token:
        headers["X-Emily-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "error": f"内核返回 HTTP {e.code}：{detail}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"无法连接内核 {url}：{e}"}


def run(*, group: str = "", limit: int = 0, operator_id: str = "",
        dry_run: bool = False, timeout: float = 8.0, base_url: str = "") -> dict:
    """核心函数（可 import）：刷新指定群（留空 = 前 N 个过期群）的成员名册。

    Returns:
        {"ok", "dry_run", "results": [...], "ok_count", "fail_count", "error"}
    """
    url = base_url or DEFAULT_BASE_URL
    out = _post("/api/v1/groups/roster-refresh",
                {"group": group, "operator_id": operator_id, "timeout": timeout,
                 "dry_run": bool(dry_run), "limit": int(limit)},
                url, timeout)
    return {
        "ok": bool(out.get("ok")),
        "dry_run": bool(out.get("dry_run", dry_run)),
        "results": out.get("results") or [],
        "ok_count": int(out.get("ok_count") or 0),
        "fail_count": int(out.get("fail_count") or 0),
        "error": out.get("error") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="群成员名册刷新（人在场路径）")
    parser.add_argument("--group", default="", help="群：备注名 / 群名 / 群号；留空 = 前 N 个过期群")
    parser.add_argument("--limit", type=int, default=0, help="未指定群时的处理上限（默认 50）")
    parser.add_argument("--operator-id", default="", help="操作人 UUID（留痕用）")
    parser.add_argument("--timeout", type=float, default=8.0, help="单群平台动作等待秒数")
    parser.add_argument("--dry-run", action="store_true", help="只拉取预览，不写库")
    parser.add_argument("--base-url", default="", help=f"内核地址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（供机器读）")
    args = parser.parse_args()

    _declare_source()
    result = run(group=args.group, limit=args.limit, operator_id=args.operator_id,
                 dry_run=args.dry_run, timeout=args.timeout, base_url=args.base_url)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    print("\n" + "=" * 60)
    print(f"名册刷新报告{' (DRY-RUN 预览)' if args.dry_run else ''}")
    print("=" * 60)
    for r in result.get("results") or []:
        g = r.get("group") or {}
        name = g.get("display_name") or g.get("group_id") or "-"
        if r.get("ok"):
            c = r.get("counts") or {}
            print(f"  ✓ [{g.get('group_id') or '-'}] {name}"
                  f" 在册 {c.get('active_total', 0)} 人"
                  f"（新增 {c.get('inserted', 0)} / 更新 {c.get('updated', 0)}"
                  f" / 恢复 {c.get('reactivated', 0)} / 退群 {c.get('deactivated', 0)}）")
        else:
            print(f"  ✗ [{g.get('group_id') or '-'}] {name}  {r.get('error') or '刷新失败'}")
    print(f"  成功 {result['ok_count']} · 失败 {result['fail_count']}")
    if result.get("error"):
        print(f"  失败原因: {result['error']}")
    print("=" * 60)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
