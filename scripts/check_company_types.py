#!/usr/bin/env python3
"""企业类型 / 范围字段质量巡检（AC-US-07.5）。

只做「可巡检 + 告警」，**不修改任何数据**（企业台账数据治理由外部并行推动）：
  · 类型写法越出同义词表（`emily-data/config/company_type_synonyms.yaml`）→ 归一失败，装配时召回差
  · `scope`（承包范围）字段缺失 → 参与单位「范围关键词」匹配无法生效
  · 类型为空 / 未登记

用法：
    uv run python scripts/check_company_types.py
    uv run python scripts/check_company_types.py --json

退出码：0=无告警；1=存在告警（不阻断，供巡检引用）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


def _init_db() -> None:
    from emily_core.infrastructure.database.session import init_db

    env = os.environ.get("EMILY_DATABASE_URL", "")
    if env:
        init_db(db_url=env)
        return
    init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"),
            pg_port=int(os.environ.get("EMILY_PG_PORT", "5432")),
            pg_db=os.environ.get("EMILY_PG_DB", "emily"),
            pg_user=os.environ.get("EMILY_PG_USER", "emily"),
            pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def inspect() -> dict:
    """巡检企业类型 / 范围字段质量（只读）。"""
    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import CompanyInfo
    from emily_core.services.node_assembly_service import SynonymTable

    syn = SynonymTable()
    with get_session() as session:
        companies = session.query(CompanyInfo).filter(
            CompanyInfo.is_deleted.isnot(True)).all()

    rows = []
    for c in companies:
        raw_type = (c.type or "").strip()
        norm = syn.normalize(raw_type)
        rows.append({
            "company_id": c.id,
            "company_name": c.company_name or "",
            "type": raw_type,
            "type_norm": norm,
            "type_known": syn.is_known(raw_type),
            "scope": (c.scope or "").strip(),
        })

    unknown = [r for r in rows if not r["type_known"]]
    missing_scope = [r for r in rows if not r["scope"]]
    return {
        "synonym_table_loaded": syn.loaded,
        "synonym_table_error": syn.error,
        "standard_types": syn.standard_types,
        "total": len(rows),
        "unknown_type_count": len(unknown),
        "missing_scope_count": len(missing_scope),
        "type_distribution": dict(Counter(r["type_norm"] or "(空)" for r in rows)),
        "unknown_types": unknown,
        "missing_scope": missing_scope,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="企业类型 / 范围字段质量巡检（只读）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = p.parse_args()

    _init_db()
    r = inspect()

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(f"同义词表：{'已加载' if r['synonym_table_loaded'] else '未加载'}"
              f"（标准类型 {len(r['standard_types'])} 个）")
        if r["synonym_table_error"]:
            print(f"  ! {r['synonym_table_error']}")
        print(f"企业总数：{r['total']}｜类型未归一：{r['unknown_type_count']}"
              f"｜范围字段缺失：{r['missing_scope_count']}")
        print(f"类型分布：{r['type_distribution']}")
        if r["unknown_types"]:
            print("\n[告警] 以下企业类型写法未在同义词表中（装配时该企业不会被类型归一命中）：")
            for x in r["unknown_types"][:20]:
                print(f"  - {x['company_name']}：type=「{x['type'] or '(空)'}」")
            if len(r["unknown_types"]) > 20:
                print(f"  … 其余 {len(r['unknown_types']) - 20} 家略")
            print("  → 处置：在 emily-data/config/company_type_synonyms.yaml 增补写法，或修正企业台账")
        if r["missing_scope"]:
            print("\n[告警] 以下企业承包范围为空（范围关键词匹配无法生效）：")
            for x in r["missing_scope"][:10]:
                print(f"  - {x['company_name']}")
            if len(r["missing_scope"]) > 10:
                print(f"  … 其余 {len(r['missing_scope']) - 10} 家略")

    has_warning = bool(r["unknown_type_count"] or r["missing_scope_count"]
                       or not r["synonym_table_loaded"])
    if not args.json:
        print(f"\n[结论] {'存在告警（不阻断，数据治理并行推动）' if has_warning else '无告警'}")
    return 1 if has_warning else 0


if __name__ == "__main__":
    sys.exit(main())
