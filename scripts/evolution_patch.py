"""evolution_patch.py — 补丁生成脚本。

从 CONFIRMED 规则生成配置文件变更补丁。
可独立运行，也可 import generate_patches()。

用法：
    uv run python scripts/evolution_patch.py --all
    uv run python scripts/evolution_patch.py --rule-no R-003
    uv run python scripts/evolution_patch.py --rule-no R-003 --preview
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
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evolution_patch")


async def generate_patches(rule_nos: list[str] | None = None, *, db_url: str = "", dry_run: bool = False) -> list[dict]:
    """补丁生成（脚本入口）。

    Args:
        rule_nos: 指定规则编号列表，为 None 则为所有 CONFIRMED 规则生成
        db_url: PostgreSQL 连接 URL
        dry_run: 预览模式

    Returns:
        生成的补丁列表
    """
    from emily_core.services.evolution.patch_generator import PatchGenerator
    from evolution_metrics import _init_db

    _init_db(db_url)

    llm_client = None
    if not dry_run:
        try:
            from emily_core.infrastructure.llm.client import LLMClient
            api_key = os.environ.get("EMILY_LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
            base_url = os.environ.get("EMILY_LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
            model = os.environ.get("EMILY_LLM_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o"))
            if api_key and base_url:
                llm_client = LLMClient(api_key=api_key, base_url=base_url, model=model)
            else:
                logger.warning("LLM not configured, running in preview mode")
                dry_run = True
        except Exception as e:
            logger.warning("Failed to init LLM: %s", e)
            dry_run = True

    generator = PatchGenerator(llm_client=llm_client)
    patches = await generator.generate(rule_nos, dry_run=dry_run)
    return patches


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="进化补丁生成脚本")
    parser.add_argument("--all", action="store_true", help="为所有 CONFIRMED 规则生成补丁")
    parser.add_argument("--rule-no", default="", help="为指定规则生成补丁")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--preview", action="store_true", help="预览模式（不调 LLM）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")

    args = parser.parse_args()

    rule_nos = None
    if args.rule_no:
        rule_nos = [args.rule_no]
    elif not args.all:
        print("请指定 --all 或 --rule-no R-XXX")
        sys.exit(1)

    patches = asyncio.run(generate_patches(
        rule_nos,
        db_url=args.db_url,
        dry_run=args.preview,
    ))

    if args.json:
        print(json.dumps(patches, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n补丁生成完成: {len(patches)} 个补丁")
        for p in patches:
            status = p.get("status", "")
            patch_no = p.get("patch_no", "?")
            rule_no = p.get("rule_no", "?")
            risk = p.get("risk_level", "?")
            if status == "preview":
                print(f"  [PREVIEW] {rule_no} -> {p.get('target_path', '?')}")
            elif status == "generated":
                print(f"  {patch_no}: {rule_no} -> {p.get('target_path', '?')} [{risk}]")
                print(f"    锚点: {p.get('search_anchor', 'N/A')[:50]}")
            else:
                print(f"  [ERROR] {rule_no}: {p.get('error', '?')}")


if __name__ == "__main__":
    main()
