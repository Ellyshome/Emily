"""evolution_validate.py — 补丁效果验证脚本。

验证已应用 >= 7 天的补丁效果。

用法：
    uv run python scripts/evolution_validate.py --all
    uv run python scripts/evolution_validate.py --patch-no EP-001
    uv run python scripts/evolution_validate.py --all --preview
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from evolution_metrics import _init_db


async def validate_patches(patch_nos: list[str] | None = None, *, db_url: str = "", dry_run: bool = False) -> list[dict]:
    """补丁验证（脚本入口）。"""
    _init_db(db_url)

    from emily_core.services.evolution.patch_validator import PatchValidator
    validator = PatchValidator()
    return await validator.validate(patch_nos, dry_run=dry_run)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="补丁效果验证脚本")
    parser.add_argument("--all", action="store_true", help="验证所有已应用 >= 7 天的补丁")
    parser.add_argument("--patch-no", default="", help="验证指定补丁")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--preview", action="store_true", help="预览模式（只看指标对比）")

    args = parser.parse_args()

    patch_nos = None
    if args.patch_no:
        patch_nos = [args.patch_no]
    elif not args.all:
        print("请指定 --all 或 --patch-no EP-XXX")
        sys.exit(1)

    results = asyncio.run(validate_patches(
        patch_nos,
        db_url=args.db_url,
        dry_run=args.preview,
    ))

    print(f"\n补丁验证结果: {len(results)} 条")
    for r in results:
        print(f"  {r.get('patch_no', '?')}: {r.get('decision', r.get('status', '?'))} "
              f"(before={r.get('avg_health_before', '?')}, after={r.get('avg_health_after', '?')})")


if __name__ == "__main__":
    main()
