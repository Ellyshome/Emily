"""set_group_note.py —— 设置某群的人工功能备注（写库 + 留痕）。

群台账的人工标注是与平台群名**分离留存**的：平台群名（title）随同步被覆盖，
本脚本写入的 note 只由人工维护，任何同步链路都不写它。

用法：
    uv run python scripts/set_group_note.py --group 群号 --note "景观施工工程群" \
        --operator-id <用户UUID>
    uv run python scripts/set_group_note.py --group ... --note ... --operator-id ... --dry-run

控制台侧经既有脚本通道调用本脚本：未二次确认时 ScriptManager 会按 `--dry-run`
降级为预览（不写库、不留痕）。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
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
logger = logging.getLogger("set_group_note")

DEFAULT_DB_URL = os.environ.get(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily",
)


def _init_db(db_url: str) -> None:
    from emily_core.infrastructure.database.session import init_db

    init_db(db_url)


def _declare_source() -> None:
    try:
        from emily_core.infrastructure.logging.audit import declare_script_source

        declare_script_source("set_group_note")
    except Exception as ex:  # noqa: BLE001 — 非阻断
        logger.debug("declare_script_source failed: %s", ex)


def run(*, group: str, note: str, operator_id: str = "",
        dry_run: bool = False, db_url: str = "") -> dict:
    """核心函数（可 import）：预览或写入群备注。

    Returns:
        {"ok", "dry_run", "group", "old_note", "new_note", "error"}
    """
    _init_db(db_url or DEFAULT_DB_URL)

    from emily_core.services.group_sync_service import GroupSyncService

    svc = GroupSyncService(platform_action=None, registry=None)
    if dry_run:
        out = svc.preview_note(group_ref=group, note=note)
        return {"ok": bool(out.get("ok")), "dry_run": True,
                "group": out.get("group") or {}, "old_note": out.get("old_note", ""),
                "new_note": out.get("new_note", note), "error": out.get("error") or ""}

    out = svc.set_note(group_ref=group, note=note, operator_id=operator_id)
    return {"ok": bool(out.get("ok")), "dry_run": False,
            "group": out.get("group") or {}, "old_note": out.get("old_note", ""),
            "new_note": out.get("new_note", note), "error": out.get("error") or ""}


def main() -> int:
    parser = argparse.ArgumentParser(description="设置某群的人工功能备注（写库 + 留痕）")
    parser.add_argument("--group", required=True, help="群：备注名 / 平台群名 / 群号任一表达")
    parser.add_argument("--note", required=True, help="人工功能备注（会以它作为群的主标识）")
    parser.add_argument("--operator-id", default="", help="操作人 UUID（写库留痕用）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写库、不留痕")
    parser.add_argument("--db-url", default="", help="库地址（默认取 EMILY_DATABASE_URL）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（供机器读）")
    args = parser.parse_args()

    _declare_source()
    result = run(group=args.group, note=args.note, operator_id=args.operator_id,
                 dry_run=args.dry_run, db_url=args.db_url)

    if args.json:
        import json

        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    print("\n" + "=" * 60)
    print(f"群备注维护{'（预览）' if args.dry_run else ''}")
    print("=" * 60)
    g = result.get("group") or {}
    print(f"  群      : {g.get('display_name') or args.group}（{g.get('group_id') or '-'}）")
    print(f"  旧备注  : {result.get('old_note') or '(空)'}")
    print(f"  新备注  : {result.get('new_note') or '(空)'}")
    if args.dry_run:
        print("  --dry-run：未写库、未留痕")
    if result.get("error"):
        print(f"  失败原因: {result['error']}")
    print("=" * 60)

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
