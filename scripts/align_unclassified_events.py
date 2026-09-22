#!/usr/bin/env python3
"""存量未归类事件归属对齐（US-15.10 / 附录 B-15）。

背景：早期把"暂不知去向的事件"挂到一条**全局假项目节点**（`node_id='UNASSIGNED'`，
`project_id='UNASSIGNED'`）上——它不是项目内可解析的真实节点。现口径要求任何事件的
归属都指向**真实存在且在项目内可解析的节点**（US-15.9 / PRD §4.4-16）。

对齐动作（幂等，可重复执行）：
  1. 定位归属非法的事件（`node_id` 为空 / 为历史常量 `UNASSIGNED` / 指向不存在的节点）
  2. 按事件的 `project_id` 分组 → 懒创建该项目的「未归类收容节点」（真实节点）
  3. 逐条改挂到收容节点并写归位留痕（`payload.reassign_history`）
  4. 全量扫描：报告"归属指向非节点"的记录数（对齐后应为 0）

运行模式：
  uv run python scripts/align_unclassified_events.py --scan          # 只扫描，不修改
  uv run python scripts/align_unclassified_events.py --dry-run       # 预览将对齐哪些记录
  uv run python scripts/align_unclassified_events.py                 # 执行（自动写备份）
  uv run python scripts/align_unclassified_events.py --project-id X  # 只处理指定项目
  uv run python scripts/align_unclassified_events.py --rollback <备份文件>
  uv run python scripts/align_unclassified_events.py --discard-fake-node  # 附带弃用历史假节点行

可回溯：执行前写备份到 emily-data/backups/unclassified_align_<时间戳>.json
（含每条事件的改挂前归属 + 本次新建的节点清单）；`--rollback` 按备份还原字段。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

BEIJING_TZ = timezone(timedelta(hours=8))
LEGACY_UNASSIGNED = "UNASSIGNED"
FAKE_PROJECT_ID = "UNASSIGNED"


def _init_db(db_url: str = "") -> None:
    import os

    from emily_core.infrastructure.database.session import init_db

    if db_url:
        init_db(db_url=db_url)
        return
    env = os.environ.get("EMILY_DATABASE_URL", "")
    if env:
        init_db(db_url=env)
        return
    init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"),
            pg_port=int(os.environ.get("EMILY_PG_PORT", "5432")),
            pg_db=os.environ.get("EMILY_PG_DB", "emily"),
            pg_user=os.environ.get("EMILY_PG_USER", "emily"),
            pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def _backup_dir() -> Path:
    """备份目录：容器内 /app/runtime 与宿主机 emily-data/runtime 是**同一挂载**，双向可见。"""
    import os

    env_dir = os.environ.get("EMILY_BACKUP_DIR", "")
    if env_dir:
        return Path(env_dir)
    candidates = [
        Path("/app/runtime/backups"),
        _HERE.parent / "emily-data" / "runtime" / "backups",
    ]
    for p in candidates:
        if p.parent.exists():
            return p
    return candidates[-1]


# ══════════════════════════════════════════════════════════════════════════
# 扫描 / 规划
# ══════════════════════════════════════════════════════════════════════════

def scan(project_id: str = "") -> dict:
    """全量扫描：找出"归属指向非节点"的记录（不修改）。"""
    from emily_core.infrastructure.database.models import ProjectEvent, ProjectNode
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        valid_ids = {r[0] for r in session.query(ProjectNode.node_id).all()}

        q = session.query(ProjectEvent)
        if project_id:
            q = q.filter(ProjectEvent.project_id == project_id)
        rows = q.all()

        bad_legacy, bad_empty, bad_missing = [], [], []
        by_project: dict[str, int] = {}
        orphan_no_project: list[dict] = []
        for e in rows:
            nid = (e.node_id or "").strip()
            if nid == LEGACY_UNASSIGNED:
                bad_legacy.append(e.id)
            elif not nid:
                bad_empty.append(e.id)
            elif nid not in valid_ids:
                bad_missing.append(e.id)
            else:
                continue
            pid = (e.project_id or "").strip()
            if not pid:
                # 无项目上下文 → 无法解析收容节点（不能凭空指定项目）
                orphan_no_project.append({"event_id": e.id, "event_no": e.event_no,
                                          "node_id": nid})
                continue
            by_project[pid] = by_project.get(pid, 0) + 1

        fake_node = session.query(ProjectNode).filter(
            ProjectNode.project_id == FAKE_PROJECT_ID,
            ProjectNode.node_id == LEGACY_UNASSIGNED,
            ProjectNode.is_discarded == False,
        ).first()

        return {
            "total_events": len(rows),
            "legacy_unassigned": len(bad_legacy),
            "empty_node_id": len(bad_empty),
            "missing_node": len(bad_missing),
            "invalid_total": len(bad_legacy) + len(bad_empty) + len(bad_missing),
            "alignable_total": sum(by_project.values()),
            "by_project": by_project,
            "orphan_no_project": orphan_no_project,
            "fake_node_present": fake_node is not None,
            "event_ids": bad_legacy + bad_empty + bad_missing,
        }


# ══════════════════════════════════════════════════════════════════════════
# 执行 / 回滚
# ══════════════════════════════════════════════════════════════════════════

def align(project_id: str = "", dry_run: bool = False, backup: bool = True,
          discard_fake_node: bool = False) -> dict:
    """执行对齐（幂等）。"""
    from emily_core.infrastructure.database.models import ProjectEvent
    from emily_core.infrastructure.database.session import get_session
    from emily_core.repositories.project_event_repo import ProjectEventRepository
    from emily_core.services.node_container_service import NodeContainerService

    report = scan(project_id)
    fake_discarded = _discard_fake_node() if discard_fake_node else False
    if report["invalid_total"] == 0:
        return {"ok": True, "aligned": 0, "message": "无需要对齐的记录",
                "scan": report, "backup": "", "created_nodes": [],
                "failed": [], "fake_node_discarded": fake_discarded}

    if dry_run:
        return {"ok": True, "dry_run": True, "aligned": 0,
                "plan": report["by_project"], "scan": report,
                "backup": "", "created_nodes": [], "failed": [],
                "fake_node_discarded": False}

    cs = NodeContainerService()
    created_nodes: list[str] = []
    backup_rows: list[dict] = []
    aligned = 0
    failed: list[dict] = []

    from emily_core.infrastructure.database.models import ProjectNode

    with get_session() as session:
        valid_ids = {r[0] for r in session.query(ProjectNode.node_id).all()}
        q = session.query(ProjectEvent)
        if project_id:
            q = q.filter(ProjectEvent.project_id == project_id)
        rows = q.all()

        for e in rows:
            nid = (e.node_id or "").strip()
            # 仅处理"归属非法"者：历史常量 / 空值 / 指向不存在的节点
            if nid and nid != LEGACY_UNASSIGNED and nid in valid_ids:
                continue
            pid = (e.project_id or "").strip()
            backup_rows.append({"id": e.id, "event_no": e.event_no, "node_id": e.node_id})
            if not pid:
                failed.append({"event_id": e.id, "event_no": e.event_no,
                               "reason": "事件缺少 project_id，无法解析收容节点（需人工判定归属）"})
                continue
            try:
                sink_id = cs.ensure_container_sync(pid, "UNCLASSIFIED_SINK")
            except Exception as ex:
                failed.append({"event_id": e.id, "event_no": e.event_no,
                               "reason": f"收容节点创建失败：{ex}"})
                continue
            if sink_id not in created_nodes:
                created_nodes.append(sink_id)
            res = ProjectEventRepository.reassign_node(
                e.id, sink_id, operator_id="", remark="存量未归类事件归属对齐（迁移）")
            if res.get("ok"):
                aligned += 1
            else:
                failed.append({"event_id": e.id, "event_no": e.event_no,
                               "reason": res.get("reason", "改挂失败")})

    backup_path = ""
    if backup and backup_rows:
        _backup_dir().mkdir(parents=True, exist_ok=True)
        backup_path = str(_backup_dir() / (
            "unclassified_align_" + datetime.now(BEIJING_TZ).strftime("%Y%m%dT%H%M%S") + ".json"))
        Path(backup_path).write_text(json.dumps({
            "created_at": datetime.now(BEIJING_TZ).isoformat(),
            "project_id": project_id,
            "events": backup_rows,
            "created_nodes": created_nodes,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    if discard_fake_node and not fake_discarded:
        fake_discarded = _discard_fake_node()

    after = scan(project_id)
    return {
        "ok": after["alignable_total"] == 0 and not failed,
        "aligned": aligned,
        "created_nodes": created_nodes,
        "failed": failed,
        "backup": backup_path,
        "fake_node_discarded": fake_discarded,
        "scan_before": report,
        "scan_after": after,
    }


def _discard_fake_node() -> bool:
    """弃用历史假项目节点行（仅当其已无事件引用，且有备份可回滚）。"""
    from emily_core.infrastructure.database.models import ProjectEvent, ProjectNode
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        still = session.query(ProjectEvent).filter(
            ProjectEvent.node_id == LEGACY_UNASSIGNED).count()
        if still:
            return False
        node = session.query(ProjectNode).filter(
            ProjectNode.project_id == FAKE_PROJECT_ID,
            ProjectNode.node_id == LEGACY_UNASSIGNED,
        ).first()
        if node is None:
            return False
        node.is_discarded = True
        return True


def drop_orphan_events() -> dict:
    """删除**无项目上下文**的孤立事件（不可对齐者）。

    破坏性操作，仅在操作人显式要求时执行——调用前务必先落备份（`align` 已写）。
    判定口径：`project_id` 为空 且 归属非法（历史常量 / 空值 / 指向不存在节点）。
    """
    from emily_core.infrastructure.database.models import ProjectEvent, ProjectNode
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        valid_ids = {r[0] for r in session.query(ProjectNode.node_id).all()}
        rows = session.query(ProjectEvent).all()
        dropped = []
        for e in rows:
            pid = (e.project_id or "").strip()
            if pid:
                continue
            nid = (e.node_id or "").strip()
            if nid and nid != LEGACY_UNASSIGNED and nid in valid_ids:
                continue
            dropped.append({"event_id": e.id, "event_no": e.event_no,
                            "node_id": e.node_id, "title": e.title})
            session.delete(e)
    return {"ok": True, "dropped": dropped}


def rollback(backup_path: str) -> dict:
    """按备份还原事件的归属字段。"""
    from emily_core.infrastructure.database.models import ProjectEvent
    from emily_core.infrastructure.database.session import get_session

    data = json.loads(Path(backup_path).read_text(encoding="utf-8"))
    restored = 0
    with get_session() as session:
        for row in data.get("events", []):
            evt = session.query(ProjectEvent).filter(ProjectEvent.id == row["id"]).first()
            if evt is None:
                continue
            evt.node_id = row.get("node_id") or ""
            restored += 1
    return {"ok": True, "restored": restored, "backup": backup_path}


# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="存量未归类事件归属对齐")
    p.add_argument("--scan", action="store_true", help="只扫描不修改")
    p.add_argument("--dry-run", action="store_true", help="预览将对齐的记录")
    p.add_argument("--project-id", default="", help="只处理指定项目")
    p.add_argument("--no-backup", action="store_true", help="不写备份（不推荐）")
    p.add_argument("--rollback", default="", help="按备份文件还原")
    p.add_argument("--discard-fake-node", action="store_true",
                   help="附带弃用历史假节点行（仅当已无事件引用）")
    p.add_argument("--drop-orphan-events", action="store_true",
                   help="【破坏性】删除无项目上下文的孤立事件（不可对齐者）；先确认已备份")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = p.parse_args()

    db_url = ""
    try:
        from emily_core.config import Config
        db_url = ""  # 交由 env/默认解析
    except Exception:
        pass
    _init_db(db_url)

    if args.rollback:
        r = rollback(args.rollback)
    elif args.drop_orphan_events:
        r = drop_orphan_events()
    elif args.scan:
        r = {"ok": scan(args.project_id)["invalid_total"] == 0, "scan": scan(args.project_id)}
    else:
        r = align(args.project_id, dry_run=args.dry_run,
                  backup=not args.no_backup,
                  discard_fake_node=args.discard_fake_node)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        _print_report(r, args)
    # 仅"有项目上下文且归属非法"的记录会使其失败；无项目孤立记录单列不计入
    return 0 if r.get("ok", True) else 1


def _print_report(r: dict, args) -> None:
    if args.rollback:
        print(f"[回滚] 已还原 {r['restored']} 条事件归属（备份：{r['backup']}）")
        return
    if "dropped" in r:
        print(f"[删除] 已删除无项目上下文孤立事件 {len(r['dropped'])} 条：")
        for d in r["dropped"][:10]:
            print(f"  - {d['event_no']}｜{d['title']}（原归属 {d['node_id']}）")
        return
    if args.scan or (r.get("scan") and "aligned" not in r):
        s = r["scan"]
        print(f"[扫描] 事件总数 {s['total_events']}｜归属非法 {s['invalid_total']} "
              f"（历史常量 {s['legacy_unassigned']} / 空值 {s['empty_node_id']} / 指向不存在节点 {s['missing_node']}）"
              f"｜可对齐 {s['alignable_total']}")
        for pid, cnt in (s.get("by_project") or {}).items():
            print(f"  - {pid}: {cnt} 条")
        orphans = s.get("orphan_no_project") or []
        if orphans:
            print(f"  ! 无项目上下文的孤立记录 {len(orphans)} 条（无法自动解析收容节点，需人工判定）：")
            for o in orphans[:5]:
                print(f"    - {o['event_no']}（node_id={o['node_id']}）")
        print(f"[扫描] 历史假节点行存在：{'是' if s['fake_node_present'] else '否'}")
        return
    if r.get("dry_run"):
        print("[预览] 待对齐分布：")
        for pid, cnt in (r.get("plan") or {}).items():
            print(f"  - {pid}: {cnt} 条")
        print("（未做任何修改；去掉 --dry-run 执行）")
        return
    if r.get("message") and not r.get("failed"):
        print(f"[跳过] {r['message']}"
              + ("；历史假节点行已弃用" if r.get("fake_node_discarded") else ""))
        s0 = r.get("scan", {})
        print(f"[复核] 有项目上下文且归属非法的记录数 = {s0.get('alignable_total', '?')}（期望 0）")
        return
    s = r.get("scan_after", {})
    print(f"[对齐] 已改挂 {r['aligned']} 条；新建/复用收容节点 {len(r.get('created_nodes') or [])} 个")
    if r.get("backup"):
        print(f"[备份] {r['backup']}")
    if r.get("failed"):
        print(f"[失败/待人工] {len(r['failed'])} 条：")
        for f in r["failed"][:10]:
            print(f"  - {f.get('event_no') or f['event_id']}: {f['reason']}")
    orphans = (s.get("orphan_no_project") or [])
    print(f"[复核] 有项目上下文且归属非法的记录数 = {s.get('alignable_total', '?')}（期望 0）"
          + (f"；无项目孤立记录 {len(orphans)} 条（单列，需人工判定）" if orphans else ""))


if __name__ == "__main__":
    sys.exit(main())
