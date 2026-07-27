"""fetch_system_description —— 获取认知书文本（按用户权限裁剪）。

被 SessionDataFetcher._sub_fetch_system_description() 调用。
也可独立运行：python -m emily_core.session.fetchers.fetch_system_description --user-id <UUID>

Strategy B：DB 存全量 content_json，fetcher 按 db_perms 过滤 D1 后格式化返回。

参照模式：emily_core/session/fetchers/fetch_visible_schema.py
"""

from __future__ import annotations

import json
import logging
import argparse

logger = logging.getLogger("emily.session.fetchers.fetch_system_description")

import os
DB_URL_DEFAULT = os.getenv(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily"
)

# ── 表名中文映射（与 builder 同步）──
_TABLE_DISPLAY_NAMES = {
    "project": "项目表",
    "events": "事件表",
    "tasks": "任务表",
    "meetings": "会议表",
    "files": "文件表",
    "financial": "财务表",
}


def fetch(perms: dict) -> str:
    """获取认知书纯文本摘要（按用户权限裁剪）。

    Args:
        perms: 权限字典，含 db_perms 等

    Returns:
        裁剪后的系统描述文本
    """
    try:
        from ...repositories.system_description_repo import SystemDescriptionRepo
        desc = SystemDescriptionRepo.get_latest()
        if desc is None:
            return ""

        if not desc.content_json:
            return desc.content_text or ""

        content = json.loads(desc.content_json)
        return _format_filtered_text(content, perms)
    except Exception as e:
        logger.error("fetch_system_description failed: %s", e)
        return ""


def _format_filtered_text(content: dict, perms: dict) -> str:
    """按用户 db_perms 过滤 D1 数据库部分，格式化为文本。

    全量描述存于 DB（content_json），此处按用户权限动态裁剪：
    - D1 数据库：只显示 db_perms 中有权限的表
    - D2 文件：全部显示（文件访问由 confidentiality 运行时控制）
    - D3 权限：全部显示（权限体系是公共知识）
    """
    db_perms = perms.get("db_perms", {})
    lines = []

    # D1: 数据库认知（按 db_perms 过滤）
    db = content.get("database", {})
    visible_tables = db.get("visible_tables", [])
    if visible_tables and db_perms:
        lines.append("📊 数据库资源（你有权访问的表）")
        for tbl in visible_tables:
            tbl_name = tbl.get("table_name", "")
            perm = db_perms.get(tbl_name, "")
            if not perm:
                # 此表用户无权限，跳过
                continue
            perm_cn = "读写" if perm == "read_write" else "只读"
            display_name = tbl.get("display_name", _TABLE_DISPLAY_NAMES.get(tbl_name, tbl_name))
            desc = tbl.get("description", "")
            lines.append(f"· {display_name}: {perm_cn} — {desc}")
            # 核心字段（精简格式，一行）
            fields = tbl.get("key_fields", [])
            if fields:
                field_strs = []
                for f in fields[:5]:
                    req_mark = ",必填" if f.get("required") else ""
                    ftype = f.get("type", "")
                    fdesc = f.get("description", "")
                    if fdesc:
                        field_strs.append(f"{f['name']}({fdesc}{req_mark})")
                    else:
                        field_strs.append(f"{f['name']}({ftype}{req_mark})")
                lines.append(f"  核心字段：{' / '.join(field_strs)}")

        # 统计不可见表数量
        hidden_count = db.get("hidden_table_count", 0)
        # 还需加上可见表中用户无权限的表
        no_perm_count = sum(1 for tbl in visible_tables if not db_perms.get(tbl.get("table_name", ""), ""))
        total_hidden = hidden_count + no_perm_count
        total = db.get("total_tables", 0)
        lines.append(f"（另有 {total_hidden} 张表按权限不可直接查询，共 {total} 张）")

        # 表间关系（仅显示与用户可见表相关的）
        relations = db.get("key_relations", [])
        if relations:
            user_tables = set(db_perms.keys())
            rel_strs = []
            for r in relations:
                from_tbl = r.get("from", "").split(".")[0]
                if from_tbl in user_tables:
                    rel_strs.append(f"{r['from']} → {r['to']}")
            if rel_strs:
                lines.append("🔗 表间关系")
                lines.append(" / ".join(rel_strs[:5]))

    elif not db_perms:
        lines.append("📊 数据库资源（无数据库访问权限）")

    # D2: 文件认知（不过滤，全部显示）
    fc = content.get("file", {})
    categories = fc.get("categories", [])
    if categories:
        lines.append("")
        lines.append("📁 文件分类体系")
        cat_strs = [c["name"] for c in categories]
        lines.append(" / ".join(cat_strs))

    access_rules = fc.get("access_rules", [])
    if access_rules:
        lines.append("文件访问规则：" + "；".join(access_rules[:3]))

    # D3: 权限认知（不过滤，全部显示）
    perm = content.get("permission", {})
    levels = perm.get("levels", [])
    if levels:
        lines.append("")
        lines.append("🔐 权限分级体系")
        participant_levels = [l for l in levels if l.get("line") == "参建线"]
        if participant_levels:
            pl_str = " → ".join(f"L{l['level']} {l['name']}" for l in participant_levels)
            lines.append(f"参建线: L1 访客 → {pl_str}")
        construction_levels = [l for l in levels if l.get("line") == "建设线"]
        if construction_levels:
            cl_str = " → ".join(f"L{l['level']} {l['name']}" for l in construction_levels)
            lines.append(f"建设线: L1 访客 → {cl_str}")

        inheritance = perm.get("inheritance", {})
        note = inheritance.get("note", "") if isinstance(inheritance, dict) else ""
        if note:
            lines.append(f"⚠️ {note}；跨线访问需临时/永久授权")

        # 当前用户位置（动态注入）
        user_level = perms.get("level", 1)
        from ...permission.level import LEVEL_NAME, effective_levels
        level_name = LEVEL_NAME.get(user_level, "未知")
        eff = effective_levels(user_level)
        chain_str = "→".join(f"L{l}" for l in sorted(eff, reverse=True))
        line_map = {1: "公共", 2: "参建线", 3: "参建线", 4: "建设线", 5: "建设线", 6: "建设线"}
        user_line = line_map.get(user_level, "")
        lines.append(f"你的位置：{level_name}(L{user_level}) {user_line}，继承链 {chain_str}")

    return "\n".join(lines)


def main():
    """独立运行入口。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="获取认知书文本")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    from ...infrastructure.database import init_db
    init_db(db_url=args.db_url)

    from ...services.permission_service import PermissionService
    perms = PermissionService().build_permission_dict(args.user_id)

    result = fetch(perms)
    print(result)


if __name__ == "__main__":
    main()
