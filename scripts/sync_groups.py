"""sync_groups.py —— 群清单同步：拉取 bot 所在全部群并写入群台账（幂等）。

用途：机器人被拉进新群后（或需要立即刷新群清单时）手动/外部调度触发一次同步。
系统内不存在周期性触发机制——需要周期执行时由宿主机调度调用本脚本或聚合薄壳
`group_info_maintenance.py`。

**通道说明**：平台动作（OneBot）必须在内核 HTTP 进程内发起——薄插件经 SSE 长连接
订阅的是那个进程，脚本子进程里没有订阅者。故本脚本是"薄 CLI →调内核端点"，
实际拉取由 `POST /api/v1/groups/pull` 在进程内完成。控制台脚本面板与本脚本同源同入口。

用法：
    uv run python scripts/sync_groups.py              # 实际同步
    uv run python scripts/sync_groups.py --dry-run    # 只拉取并预览，不写库

写入边界：只写 conversations 的 title / member_count；**人工备注（note）不参与写入**。
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
logger = logging.getLogger("sync_groups")

DEFAULT_BASE_URL = os.environ.get("EMILY_CORE_BASE_URL", "http://127.0.0.1:18080")


def _declare_source() -> None:
    try:
        from emily_core.infrastructure.logging.audit import declare_script_source

        declare_script_source("sync_groups")
    except Exception as ex:  # noqa: BLE001 — 非阻断
        logger.debug("declare_script_source failed: %s", ex)


def _post(path: str, payload: dict, base_url: str, timeout: float) -> dict:
    """POST 到内核端点，返回 JSON（异常转成结构化失败）。"""
    url = f"{base_url.rstrip('/')}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("EMILY_API_TOKEN", "")
    if token:
        headers["X-Emily-Token"] = token
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "error": f"内核返回 HTTP {e.code}：{detail}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"无法连接内核 {url}：{e}"}


def run(*, dry_run: bool = False, platform: str = "napcat",
        timeout: float = 8.0, base_url: str = "") -> dict:
    """核心函数（可 import）：拉取群清单并（可选）写入台账。

    Returns:
        {"ok", "dry_run", "pulled", "upserted", "groups", "error"}
    """
    url = base_url or DEFAULT_BASE_URL
    out = _post("/api/v1/groups/pull",
                {"platform": platform, "timeout": timeout, "dry_run": bool(dry_run)},
                url, timeout)
    if "code" in out and "ok" not in out:
        # 控制台端点风格 {code, message, data} 不会出现在本端点；防御性兜底
        return {"ok": False, "dry_run": dry_run, "pulled": 0, "upserted": 0,
                "groups": [], "error": out.get("message") or "未知响应"}
    return {
        "ok": bool(out.get("ok")),
        "dry_run": bool(out.get("dry_run", dry_run)),
        "pulled": int(out.get("pulled") or 0),
        "upserted": int(out.get("upserted") or 0),
        "groups": out.get("groups") or [],
        "error": out.get("error") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="群清单同步：拉取 bot 所在全部群并写入群台账（幂等）",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只拉取并预览将写入的群清单，不写库")
    parser.add_argument("--platform", default="napcat", help="平台标识（默认 napcat）")
    parser.add_argument("--timeout", type=float, default=8.0, help="平台动作等待秒数")
    parser.add_argument("--base-url", default="",
                        help=f"内核地址（默认 {DEFAULT_BASE_URL}）")
    args = parser.parse_args()

    _declare_source()
    result = run(dry_run=args.dry_run, platform=args.platform,
                 timeout=args.timeout, base_url=args.base_url)

    print("\n" + "=" * 60)
    print(f"群清单同步报告{' (DRY-RUN 预览)' if args.dry_run else ''}")
    print("=" * 60)
    print(f"  拉取群数: {result['pulled']}")
    print(f"  写入群数: {result['upserted']}")
    for g in result.get("groups") or []:
        print(f"    · [{g.get('group_id')}] {g.get('group_name') or '(未命名)'}"
              f" 成员 {g.get('member_count', 0)}")
    if result.get("error"):
        print(f"  失败原因: {result['error']}")
    print("=" * 60)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
