"""self_check.py — 系统级自检（复用 V1）。

输出：用户/项目/业务量/知识库统计。

用法：
    uv run python scripts/self_check.py
    uv run python scripts/self_check.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("self_check")

BEIJING_TZ = timezone(timedelta(hours=8))


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def self_check(*, db_url: str = "", dry_run: bool = False) -> dict:
    """系统级自检。"""
    _init_db(db_url)

    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import User, Project, Event, Task, ProjectNode, ProjectWorldBook

    result = {
        "checked_at": datetime.now(BEIJING_TZ).isoformat(),
        "dry_run": dry_run,
    }

    with get_session() as session:
        # 用户统计
        total_users = session.query(User).filter(User.is_deleted == False).count()
        active_users = session.query(User).filter(User.is_deleted == False, User.status == "active").count()
        admin_users = session.query(User).filter(User.is_deleted == False, User.is_admin == True).count()
        result["users"] = {"total": total_users, "active": active_users, "admins": admin_users}

        # 项目统计
        total_projects = session.query(Project).filter(Project.is_deleted == False).count()
        active_projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").count()
        result["projects"] = {"total": total_projects, "active": active_projects}

        # 业务量
        event_count = session.query(Event).count()
        task_count = session.query(Task).count()
        node_count = session.query(ProjectNode).filter(ProjectNode.is_discarded == False).count()
        result["business"] = {"events": event_count, "tasks": task_count, "nodes": node_count}

        # 世界书
        wb_count = session.query(ProjectWorldBook).count()
        wb_activated = session.query(ProjectWorldBook).filter(ProjectWorldBook.is_activated == True).count()
        result["world_books"] = {"total": wb_count, "activated": wb_activated}

        # 知识库
        sop_count = 0
        try:
            from emily_core.skill.registry import SkillRegistry
            skill_dir = "/app/skills"
            if not Path(skill_dir).exists():
                dev_dir = str(Path(__file__).resolve().parent.parent / "emily-data" / "skills")
                if Path(dev_dir).exists():
                    skill_dir = dev_dir
            if skill_dir and Path(skill_dir).exists():
                reg = SkillRegistry(skill_directory=skill_dir)
                reg.load()
                sop_count = len(reg.list_sop_ids())
        except Exception:
            pass
        result["knowledge"] = {"sop_count": sop_count}

    # 工具一致性快速检查（方案 B：复用 self_check 启动链路）
    try:
        from emily_core.infrastructure.tools_consistency import check_quick
        skill_dir = "/app/skills"
        if not Path(skill_dir).exists():
            dev_dir = str(Path(__file__).resolve().parent.parent / "emily-data" / "skills")
            if Path(dev_dir).exists():
                skill_dir = dev_dir
        result["tools_consistency"] = check_quick(skill_dir)
    except Exception as e:
        result["tools_consistency"] = {"ok": False, "error": str(e)}

    return result


def _format_self_check(result: dict) -> str:
    """格式化自检报告。"""
    lines = []
    lines.append("Emily 系统自检报告")
    lines.append("=" * 40)
    lines.append(f"检查时间：{result['checked_at']}")
    lines.append("=" * 40)

    u = result.get("users", {})
    lines.append(f"\n用户：{u.get('active', 0)} 活跃 / {u.get('total', 0)} 总计 / {u.get('admins', 0)} 管理员")

    p = result.get("projects", {})
    lines.append(f"项目：{p.get('active', 0)} 活跃 / {p.get('total', 0)} 总计")

    b = result.get("business", {})
    lines.append(f"业务：{b.get('events', 0)} 事件 / {b.get('tasks', 0)} 任务 / {b.get('nodes', 0)} 节点")

    wb = result.get("world_books", {})
    lines.append(f"世界书：{wb.get('total', 0)} 份 / {wb.get('activated', 0)} 已激活")

    k = result.get("knowledge", {})
    lines.append(f"知识库：{k.get('sop_count', 0)} 个 SOP")

    tc = result.get("tools_consistency", {})
    if tc:
        status = "✅" if tc.get("ok") else "❌"
        lines.append(f"工具一致性：{status} Skill {tc.get('skills', 0)} 个，问题 {tc.get('issues', 0)} 处 (fatal {tc.get('fatal', 0)})")

    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Emily 系统自检")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = self_check(db_url=args.db_url, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_self_check(result))


if __name__ == "__main__":
    main()
