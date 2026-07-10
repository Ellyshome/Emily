"""evolution_apply.py — 补丁审批/应用/回滚脚本。

子命令：approve / reject / apply / rollback / list / status

用法：
    uv run python scripts/evolution_apply.py list --status DRAFT
    uv run python scripts/evolution_apply.py approve --patch-no EP-001
    uv run python scripts/evolution_apply.py apply --patch-no EP-001
    uv run python scripts/evolution_apply.py apply --patch-no EP-001 --dry-run
    uv run python scripts/evolution_apply.py rollback --patch-no EP-001
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


async def approve_patch(patch_no: str) -> dict:
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    return await applier.approve(patch_no)


async def apply_patch(patch_no: str, *, dry_run: bool = False) -> dict:
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    return await applier.apply(patch_no, dry_run=dry_run)


async def rollback_patch(patch_no: str, *, dry_run: bool = False) -> dict:
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    return await applier.rollback(patch_no, dry_run=dry_run)


async def reject_patch(patch_no: str, reason: str = "") -> dict:
    from emily_core.services.evolution.patch_applier import PatchApplier
    applier = PatchApplier()
    return await applier.reject(patch_no, reason)


async def list_patches(status: str = "DRAFT") -> list[dict]:
    from emily_core.repositories.evolution_repo import EvolutionRepo
    patches = EvolutionRepo.get_patches_by_status(status)
    return [
        {
            "patch_no": p.patch_no,
            "rule_no": p.rule_no,
            "target_path": p.target_path,
            "patch_type": p.patch_type,
            "risk_level": p.risk_level,
            "status": p.status,
            "created_at": p.created_at,
        }
        for p in patches
    ]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="补丁审批/应用/回滚脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="列出补丁")
    p.add_argument("--status", default="DRAFT", help="状态过滤")

    p = sub.add_parser("approve", help="审批补丁")
    p.add_argument("--patch-no", required=True)

    p = sub.add_parser("reject", help="拒绝补丁")
    p.add_argument("--patch-no", required=True)
    p.add_argument("--reason", default="")

    p = sub.add_parser("apply", help="应用补丁")
    p.add_argument("--patch-no", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("rollback", help="回滚补丁")
    p.add_argument("--patch-no", required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("status", help="查看补丁详情")
    p.add_argument("--patch-no", required=True)

    args = parser.parse_args()

    _init_db()

    if args.command == "list":
        patches = asyncio.run(list_patches(args.status))
        print(f"\n补丁列表 (status={args.status}): {len(patches)} 条")
        for p in patches:
            print(f"  {p['patch_no']}: {p['rule_no']} -> {p['target_path']} [{p.get('risk_level', '?')}]")

    elif args.command == "approve":
        result = asyncio.run(approve_patch(args.patch_no))
        print(f"\n审批结果: {result}")

    elif args.command == "reject":
        result = asyncio.run(reject_patch(args.patch_no, args.reason))
        print(f"\n拒绝结果: {result}")

    elif args.command == "apply":
        result = asyncio.run(apply_patch(args.patch_no, dry_run=args.dry_run))
        if args.dry_run:
            print(f"\n预览应用:\n{json.dumps(result, ensure_ascii=False, indent=2, default=str)}")
        else:
            print(f"\n应用结果: {result.get('status')}")

    elif args.command == "rollback":
        result = asyncio.run(rollback_patch(args.patch_no, dry_run=args.dry_run))
        print(f"\n回滚结果: {result.get('status')}")

    elif args.command == "status":
        from emily_core.repositories.evolution_repo import EvolutionRepo
        p = EvolutionRepo.get_patch_by_no(args.patch_no)
        if p:
            print(f"\n补丁 {p.patch_no}:")
            print(f"  规则: {p.rule_no}")
            print(f"  目标: {p.target_path}")
            print(f"  类型: {p.patch_type}")
            print(f"  风险: {p.risk_level}")
            print(f"  状态: {p.status}")
            print(f"  理由: {p.risk_reasoning}")
            print(f"  锚点: {p.search_anchor}")
            print(f"  内容: {p.patch_content[:200]}")
        else:
            print(f"补丁 {args.patch_no} 不存在")


if __name__ == "__main__":
    main()
