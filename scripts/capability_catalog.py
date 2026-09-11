"""capability_catalog.py — 能力目录枚举与准入一致性校验 CLI（计划 M2）。

用途：
  · --check   准入一致性闸门（**不依赖 DB**，可在 CI 跑）：
                sops/*.md 总数 == 准入能力数 + 排除集命中数；无孤儿；短形编号不重名；
                排除集对应的 .md 文件仍存在（旧路径依赖，不得误删）。
  · --list    列出当前操作者可见的能力目录（需要 DB：--actor-id 指定真实用户 UUID）。
  · --json    以 JSON 输出（供下游断言/脚本消费）。

用法：
    # 准入一致性闸门（CI / 验收）
    uv run python scripts/capability_catalog.py --check

    # 列出某用户可见能力（真实 UUID，禁止伪造）
    docker exec emily-postgres psql -U emily -d emily \\
        -c "SELECT id, username, permission_level FROM users WHERE status='active' LIMIT 5;"
    uv run python scripts/capability_catalog.py --list --actor-id <UUID>
    uv run python scripts/capability_catalog.py --list --actor-id <UUID> --json

双通道：CLI + `run_catalog()` / `run_check_admission()` 可 import。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scripts.capability_catalog")


# ══════════════════════════════════════════════════════════════════════════════
# 准入一致性闸门（无 DB）
# ══════════════════════════════════════════════════════════════════════════════

def _load_skill_registry():
    from emily_core.infrastructure.paths import resolve_data_path
    from emily_core.skill.registry import SkillRegistry

    # 优先仓库内 emily-data/sops（脚本可能在宿主机运行：容器 /app/sops 不存在）
    repo_sops = _HERE.parent / "emily-data" / "sops"
    if repo_sops.exists():
        skill_dir = repo_sops.parent / "skills"
    else:
        skill_dir = resolve_data_path("", "/app/skills", "emily-data/skills")
    reg = SkillRegistry(skill_directory=str(skill_dir))
    reg.load()
    return reg


def run_check_admission() -> dict:
    """准入一致性校验（纯文件，无 DB）。

    Returns:
        dict: {ok, sops, capabilities, excluded, orphans, duplicates, missing_files, threshold}
    """
    from emily_core.session.capability_runner import (
        SYSTEM_INTERNAL_SOPS, is_capability_sop, short_sop_id,
    )
    from emily_core.session.capability_catalog import CAPABILITY_TWO_STAGE_THRESHOLD

    reg = _load_skill_registry()
    docs = reg.list_skills()
    stems = [getattr(d, "sop_id", "") for d in docs]

    admitted = [s for s in stems if is_capability_sop(s)]
    excluded = [s for s in stems if short_sop_id(s) in SYSTEM_INTERNAL_SOPS]
    orphans = [
        s for s in stems
        if not is_capability_sop(s) and short_sop_id(s) not in SYSTEM_INTERNAL_SOPS
    ]

    counts = Counter(short_sop_id(s) for s in admitted)
    duplicates = [k for k, v in counts.items() if v > 1]

    missing_files = []
    for d in docs:
        stem = getattr(d, "sop_id", "")
        if short_sop_id(stem) in SYSTEM_INTERNAL_SOPS:
            fp = getattr(d, "file_path", "")
            if fp and not Path(fp).exists():
                missing_files.append(stem)

    ok = (
        len(stems) > 0          # 一个 SOP 都没扫到 = 环境/路径错误，不得报 OK
        and not orphans
        and not duplicates
        and not missing_files
        and len(stems) == len(admitted) + len(excluded)
    )
    return {
        "ok": ok,
        "sops": len(stems),
        "capabilities": len(admitted),
        "excluded": len(excluded),
        "excluded_ids": sorted(SYSTEM_INTERNAL_SOPS),
        "orphans": orphans,
        "duplicates": duplicates,
        "missing_files": missing_files,
        "threshold": CAPABILITY_TWO_STAGE_THRESHOLD,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 能力目录枚举（需要 DB / Core）
# ══════════════════════════════════════════════════════════════════════════════

def run_catalog(*, actor_id: str = "", core=None) -> dict:
    """列出能力目录。

    Args:
        actor_id: 真实用户 UUID；为空则用匿名 L1 快照（大多数业务工具会被 fail-closed 过滤）。
        core: 可注入已初始化的 EmilyCore（不注入则自行 bootstrap）。

    Returns:
        dict: {"capabilities": [{name, kind, sop_id, display_name, write_mode}]}
    """
    from emily_core.session.capability_catalog import CapabilityCatalog

    if core is None:
        from emily_core.bootstrap import init
        core = init({})
    ensure = getattr(core, "_ensure_initialized", None)
    if callable(ensure):
        ensure()

    if actor_id:
        from emily_core.session.session_context import SessionContext
        from emily_core.session.session_data_fetcher import SessionDataFetcher
        ctx = SessionContext.create(
            user_id=actor_id, conversation_id="cli-catalog",
            sender_name="cli", core=core,
        )
        actor = SessionDataFetcher.fetch_actor_snapshot(actor_id, core) or {}
        actor.setdefault("user_id", actor_id)
    else:
        logger.warning("未提供 --actor-id：使用匿名 L1 快照，业务工具将因 fail-closed 被过滤")
        ctx = None
        actor = {"user_id": "", "level": 1, "is_management_unit": False}

    catalog = CapabilityCatalog(core=core)
    entries = catalog.list_capabilities(actor, ctx)
    return {
        "capabilities": [
            {
                "name": e.name, "kind": e.kind, "sop_id": e.sop_id,
                "display_name": e.display_name, "write_mode": e.write_mode,
            }
            for e in entries
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def run(*, check: bool = False, actor_id: str = "", as_json: bool = False) -> int:
    """统一入口：返回退出码。"""
    if check:
        report = run_check_admission()
        if as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"sops={report['sops']}, capabilities={report['capabilities']}, "
                  f"excluded={report['excluded']} (threshold={report['threshold']})")
            if report["orphans"]:
                print(f"  orphans: {report['orphans']}")
            if report["duplicates"]:
                print(f"  duplicates: {report['duplicates']}")
            if report["missing_files"]:
                print(f"  missing_files: {report['missing_files']}")
            print("  OK" if report["ok"] else "  FAIL")
        return 0 if report["ok"] else 1

    data = run_catalog(actor_id=actor_id)
    caps = data["capabilities"]
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"能力目录（{len(caps)} 项）：")
        for c in caps:
            extra = f" sop_id={c['sop_id']}" if c["sop_id"] else ""
            print(f"  - [{c['kind']}] {c['name']} ({c['write_mode']}){extra}")
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="能力目录枚举与准入一致性校验")
    parser.add_argument("--check", action="store_true", help="准入一致性闸门（不依赖 DB）")
    parser.add_argument("--list", action="store_true", help="列出能力目录（需要 DB）")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 输出")
    parser.add_argument("--actor-id", default="", help="真实用户 UUID（users 表，禁止伪造）")
    args = parser.parse_args(argv)

    if not args.check and not args.list:
        parser.print_help()
        return 1
    return run(check=args.check, actor_id=args.actor_id, as_json=args.as_json)


if __name__ == "__main__":
    sys.exit(main())
