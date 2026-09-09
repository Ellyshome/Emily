"""three_books_dump.py — 三书（世界书 / 规则书 / 认知书）产出观察工具。

目标：把系统真实维护的「三书」内容生成/导出为本地 md 文件 + manifest，
方便人工观察、对比 LLM/DB 驱动的产出是否符合预期。

- 世界书（项目态势书）：按项目，读 DB 现存产物或 --rebuild 用生产 builder 重建
- 认知书（系统描述）：读 DB 现存产物或 --rebuild 用生产 builder 重建
- 规则书（组织规则书）：人工维护 md 文件，仅装载展示

用法：
    # 只读导出当前产物（不写 DB）
    uv run python scripts/three_books_dump.py --out-dir temporary/three_books

    # 强制重建并落 DB 后再导出（世界书/认知书）
    uv run python scripts/three_books_dump.py --rebuild --project-id <UUID>

    # 容器内运行（真实环境）
    python /tmp/three_books_dump.py --out-dir /app/out_three_books
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
if Path("/app/emily_core").exists() and "/app" not in sys.path:
    sys.path.insert(0, "/app")  # 容器内运行时

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("three_books_dump")

_RULE_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "emily-data" / "rules" / "规则书.md",
    Path("/app/rules/规则书.md"),
]


def _detect_docker_pg_port() -> int | None:
    try:
        r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().rsplit(":", 1)[-1])
    except Exception:
        pass
    return None


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
        return
    env_url = os.environ.get("EMILY_DATABASE_URL", "")
    if env_url:
        init_db(db_url=env_url)
        return
    pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
    pg_port_env = os.environ.get("EMILY_PG_PORT")
    pg_port = int(pg_port_env) if pg_port_env else (_detect_docker_pg_port() or 5432)
    init_db(pg_host=pg_host, pg_port=pg_port,
            pg_db=os.environ.get("EMILY_PG_DB", "emily"),
            pg_user=os.environ.get("EMILY_PG_USER", "emily"),
            pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


# ── 各书读取 / 重建 ───────────────────────────────────────────────

def _list_projects():
    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import Project
    with get_session() as s:
        rows = s.query(Project).filter(Project.is_deleted == False).order_by(Project.name).all()
    return [{"id": p.id, "name": p.name or "?", "status": p.status or "?"} for p in rows]


def _world_read(project_id: str) -> dict:
    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo
    wb = ProjectWorldBookRepo.get_by_project(project_id)
    if wb is None:
        return {"found": False, "text": "", "tokens": 0}
    return {"found": True, "text": wb.content_text or "", "tokens": wb.token_count or 0}


def _world_build(project_id: str) -> dict:
    from emily_core.services.world_book_builder import ProjectWorldBookBuilder
    res = ProjectWorldBookBuilder().build(project_id, generated_by="tool_observe", dry_run=False)
    return {"found": True, "text": res.get("content_text", "") or "",
            "tokens": int(res.get("token_count") or 0), "status": res.get("status", "built")}


def _system_read() -> dict:
    from emily_core.repositories.system_description_repo import SystemDescriptionRepo
    desc = SystemDescriptionRepo.get_latest()
    if desc is None:
        return {"found": False, "text": ""}
    return {"found": True, "text": desc.content_text or ""}


def _system_build() -> dict:
    from emily_core.services.system_description_builder import SystemDescriptionBuilder
    res = SystemDescriptionBuilder().build(generated_by="tool_observe", dry_run=False)
    return {"found": True, "text": res.get("content_text", "") or "", "status": res.get("status", "built")}


def _rule_read() -> dict:
    for p in _RULE_CANDIDATES:
        if p.exists():
            text = p.read_text(encoding="utf-8")
            return {"found": True, "text": text, "source": str(p)}
    return {"found": False, "text": "", "source": ""}


# ── 落盘 ─────────────────────────────────────────────────────────

def _write(out_dir: Path, rel: str, text: str) -> None:
    target = out_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="三书产出观察工具")
    parser.add_argument("--target", default="all", choices=["all", "world", "system", "rule"])
    parser.add_argument("--project-id", default="", help="世界书项目 UUID；留空则导出全部 active 项目")
    parser.add_argument("--rebuild", action="store_true", help="用生产 builder 重建并落 DB（默认只读现存产物）")
    parser.add_argument("--out-dir", default="", help="输出目录（默认：容器 /app/out_three_books；开发临时/three_books）")
    args = parser.parse_args()

    _init_db()

    # 输出目录
    if args.out_dir:
        out = Path(args.out_dir)
    elif Path("/app").exists():
        out = Path("/app/out_three_books")
    else:
        out = Path(__file__).resolve().parent.parent / "temporary" / "three_books"
    out.mkdir(parents=True, exist_ok=True)

    manifest = {
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rebuild": args.rebuild,
        "items": {},
    }

    # 规则书（不重建，仅装载）
    if args.target in ("all", "rule"):
        rule = _rule_read()
        rel = "rule_book.md"
        _write(out, rel, rule["text"])
        manifest["items"]["rule"] = {"found": rule["found"], "chars": len(rule["text"]),
                                     "source": rule["source"]}
        print(f"[rule] found={rule['found']} chars={len(rule['text'])} -> {out / rel}")

    # 认知书（系统描述）
    if args.target in ("all", "system"):
        sys_res = _system_build() if args.rebuild else _system_read()
        rel = "system_description.md"
        _write(out, rel, sys_res["text"])
        manifest["items"]["system"] = {"found": sys_res["found"], "chars": len(sys_res["text"]),
                                       "status": sys_res.get("status", "")}
        print(f"[system] found={sys_res['found']} chars={len(sys_res['text'])} -> {out / rel}")

    # 世界书（按项目）
    if args.target in ("all", "world"):
        if args.project_id:
            projects = [{"id": args.project_id, "name": "custom", "status": ""}]
        else:
            projects = [p for p in _list_projects() if p["status"] in ("active", "")]
            if not projects:
                projects = _list_projects()
        world_items = []
        for p in projects:
            res = _world_build(p["id"]) if args.rebuild else _world_read(p["id"])
            safe_name = "".join(c for c in p["name"] if c not in '\\/:*?"<>|').strip() or p["id"][:8]
            rel = f"world/{safe_name}.md"
            if res["found"]:
                _write(out, rel, res["text"])
            world_items.append({
                "project_id": p["id"], "project_name": p["name"],
                "found": res["found"], "chars": len(res["text"]),
                "tokens": res.get("tokens", 0),
                "file": rel if res["found"] else None,
            })
            print(f"[world] {p['name']} found={res['found']} chars={len(res['text'])} "
                  f"tokens={res.get('tokens', 0)}")
        manifest["items"]["world"] = world_items

    _write(out, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"导出完成: {out}")


if __name__ == "__main__":
    main()
