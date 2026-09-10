"""backfill_node_company_and_participants —— 节点"单位标识"归一 + 参与单位回填。

背景（见 docs/Spec/项目归属与可见范围_Spec.md）：
  1) `project_nodes.related_company_id` 语义是 `company_info.id`，但存量存的是中文标签
     （"建设单位" / "总包" / "设计单位"…），属"名称当标识"的口径不一致；
  2) `node_participant_companies` 大量节点未登记 → 按归属口径，这些节点对**非管理单位
     用户不可见**（且不报错，静默失效）。

本脚本做两件事（幂等，可重复执行）：
  A. related_company_id：中文/名称 → company_info.id（CompanyResolver，唯一命中才改）
  B. 未登记参与单位的节点：用归一后的 related_company_id 登记；无则用责任人所属企业

用法：
    uv run python scripts/backfill_node_company_and_participants.py --dry-run
    uv run python scripts/backfill_node_company_and_participants.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if Path("/app/emily_core").exists() and "/app" not in sys.path:
    sys.path.insert(0, "/app")


def run(*, dry_run: bool = False, db_url: str = "") -> dict:
    """归一 related_company_id 并为未登记节点补参与单位。"""
    from emily_core.infrastructure.database import init_db
    init_db(db_url=db_url) if db_url else init_db()

    from emily_core.infrastructure.database.models import (
        NodeParticipantCompany, ProjectNode, User,
    )
    from emily_core.infrastructure.database.session import get_session
    from emily_core.repositories.node_repo import NodeParticipantCompanyRepo, ProjectNodeRepo
    from emily_core.services.company_resolver import CompanyResolver

    with get_session() as session:
        rows = [
            (n.node_id, n.project_id, n.related_company_id or "", n.responsible_user_id or "")
            for n in session.query(ProjectNode).filter(ProjectNode.is_discarded == False).all()
        ]
        uid_list = [r[3] for r in rows if r[3]]
        user_company = {
            u.id: (u.company or "")
            for u in session.query(User).filter(User.id.in_(uid_list)).all()
        } if uid_list else {}
        registered_ids = {r[0] for r in session.query(NodeParticipantCompany.node_id).distinct().all()}

    normalized: list[dict] = []   # A: 标签 → id
    unresolved: list[dict] = []   # A: 解析失败（保留原值，人工处理）
    registered: list[dict] = []   # B: 补登记参与单位
    no_company: list[str] = []    # B: 既无关联单位也无法定位责任人企业

    for node_id, project_id, related, resp_uid in rows:
        company_id = CompanyResolver.resolve(related, project_id) if related else None

        if company_id:
            if company_id != related:
                normalized.append({"node_id": node_id, "from": related, "to": company_id})
                if not dry_run:
                    ProjectNodeRepo.update_fields(node_id, related_company_id=company_id)
        elif related:
            # 存量解析不出来：不静默改写，登记待人工确认
            unresolved.append({"node_id": node_id, "related_company_id": related})
            company_id = None
        else:
            company_id = user_company.get(resp_uid) or None

        if node_id not in registered_ids:
            if company_id:
                registered.append({"node_id": node_id, "company_id": company_id})
                if not dry_run:
                    NodeParticipantCompanyRepo.replace_all(node_id, [company_id], "backfill")
            else:
                no_company.append(node_id)

    return {
        "dry_run": dry_run,
        "nodes_total": len(rows),
        "normalized": len(normalized), "normalized_samples": normalized[:5],
        "unresolved": len(unresolved), "unresolved_samples": unresolved[:5],
        "registered": len(registered), "registered_samples": registered[:5],
        "no_company": len(no_company), "no_company_samples": no_company[:5],
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="节点单位标识归一 + 参与单位回填")
    p.add_argument("--dry-run", action="store_true", help="仅预览，不写库")
    p.add_argument("--db-url", default="")
    args = p.parse_args()

    r = run(dry_run=args.dry_run, db_url=args.db_url)
    print(f"[dry_run={r['dry_run']}] 节点总数={r['nodes_total']}")
    print(f"  A. related_company_id 归一：{r['normalized']} 条")
    for s in r["normalized_samples"]:
        print(f"       · {s['node_id']}: {s['from']} → {s['to']}")
    print(f"  A. 解析失败（保留原值待人工）：{r['unresolved']} 条")
    for s in r["unresolved_samples"]:
        print(f"       · {s['node_id']}: {s['related_company_id']}")
    print(f"  B. 补登记参与单位：{r['registered']} 条")
    for s in r["registered_samples"]:
        print(f"       · {s['node_id']} → {s['company_id']}")
    print(f"  B. 无单位可登记（需人工）：{r['no_company']} 条 {r['no_company_samples']}")


if __name__ == "__main__":
    main()
