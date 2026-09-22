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


def _check_template_library() -> dict:
    """模板库自检：索引与模板单元目录一致性（只读，不写盘）。

    索引写入只在宿主机执行（容器内模板目录 :ro 挂载），故容器侧以本项巡检
    替代已摘除的 bootstrap 自动刷新——索引是否新鲜可被观测。
    """
    try:
        from emily_core.services.node_template_loader import NodeTemplateLoader
        return NodeTemplateLoader().check_consistency()
    except Exception as e:
        return {"ok": False, "error": str(e), "template_count": 0}


def self_check(*, db_url: str = "", dry_run: bool = False,
               mode: str = "quick", check_tool_registry: bool = False,
               operator_id: str = "") -> dict:
    """系统级自检。

    mode: quick=快速一致性检查（check_quick），full=全量一致性检查（check_all）。
    check_tool_registry: 仅 full 模式生效，是否连库检查 tool_registry 表。
    operator_id: 后台触发者 UUID（console 传入）；传入即在本层（动作层）留痕，
                 入口层不再写留痕。CLI / 冷启动链不传，保持既有不留痕行为。
    """
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

        # 留痕治理：写入失败可观测（"未记"与"记失败"外部可区分）+ 流量性质分布
        try:
            from sqlalchemy import func as sa_func

            from emily_core.infrastructure.database.models import BusinessEventLog
            from emily_core.infrastructure.logging.audit import audit_write_stats

            stats = audit_write_stats()
            source_rows = session.query(
                BusinessEventLog.source, sa_func.count(BusinessEventLog.id),
            ).group_by(BusinessEventLog.source).all()
            by_source = {(row[0] or "(empty)"): int(row[1]) for row in source_rows}
            result["audit"] = {
                "write_failures": int(stats.get("failures", 0)),
                "last_error": str(stats.get("last_error", ""))[:200],
                "by_source": by_source,
            }
        except Exception as e:
            result["audit"] = {"error": str(e)}

    # 工具一致性检查（复用 self_check 启动链路；full 模式走 check_all）
    try:
        from emily_core.infrastructure.tools_consistency import check_quick, check_all
        if mode == "full":
            result["tools_consistency"] = check_all(check_tool_registry=check_tool_registry)
        else:
            result["tools_consistency"] = check_quick()
    except Exception as e:
        result["tools_consistency"] = {"ok": False, "error": str(e)}

    # 模板库一致性：索引与模板单元目录是否同步（只读巡检，不写盘）
    result["template_library"] = _check_template_library()

    # 留痕：自检动作由本层（动作层）记录，入口只传操作人（约束「挂载点唯一」）
    if operator_id:
        try:
            from emily_core.infrastructure.logging.audit import record_action

            record_action(
                category="system",
                action="self_checked",
                target_type="system",
                target_id="self_check",
                summary=f"系统自检（{mode}）",
                detail_json=json.dumps(
                    {"mode": mode, "check_tool_registry": check_tool_registry},
                    ensure_ascii=False),
                actor_id=operator_id,
            )
        except Exception as e:
            logger.debug("self_check audit record failed: %s", e)

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

    a = result.get("audit", {})
    if a.get("error"):
        lines.append(f"留痕治理：❌ 采集失败 {a.get('error')}")
    elif a:
        failures = a.get("write_failures", 0)
        status = "✅" if not failures else "❌"
        dist = a.get("by_source", {}) or {}
        dist_text = " ".join(f"{k}={v}" for k, v in sorted(dist.items()))
        lines.append(
            f"留痕治理：{status} 写入失败 {failures} 次"
            + (f"（最近：{a.get('last_error')}）" if failures and a.get("last_error") else "")
            + f"；存量按来源：{dist_text or '无'}"
        )

    tc = result.get("tools_consistency", {})
    if tc:
        status = "✅" if tc.get("ok") else "❌"
        lines.append(f"工具一致性：{status} Skill {tc.get('skills', 0)} 个，问题 {tc.get('issues', 0)} 处 (fatal {tc.get('fatal', 0)})")

    tl = result.get("template_library", {})
    if tl:
        status = "✅" if tl.get("ok") else "❌"
        detail = tl.get("error") or (
            f"新增 {tl.get('added', [])} / 删除 {tl.get('removed', [])} / 缺清单 {tl.get('missing_manifest', [])}"
            if not tl.get("ok") else "索引与模板单元同步"
        )
        lines.append(f"模板库：{status} {tl.get('template_count', 0)} 个模板 —— {detail}")

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
