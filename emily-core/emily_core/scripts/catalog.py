"""catalog — doc-as-code 生成器，从 ScriptRegistry 生成 docs/脚本工具目录.md。"""

from __future__ import annotations

import io
from .registry import ScriptRegistry


def generate_markdown(registry: ScriptRegistry) -> str:
    """从注册表生成 docs/脚本工具目录.md 内容。

    Args:
        registry: ScriptRegistry 实例。

    Returns:
        完整的 Markdown 字符串。
    """
    buf = io.StringIO()
    w = buf.write

    # 文件头
    w("<!-- AUTO-GENERATED from emily-data/config/scripts_registry.yaml by scriptmgr export. Do not edit by hand. -->\n\n")
    w(f"# 脚本工具目录\n\n")
    if registry.prologue:
        w(registry.prologue.strip() + "\n\n")
    else:
        w(f"> 本文档收录 `scripts/` 目录下全部 {registry.count} 个可执行脚本，按功能域分组，说明职责、聚合归属、典型用法、调度归属。\n")
        w(">\n")
        w("> **设计约定**：每个功能以独立可执行脚本交付，聚合薄壳仅串联入口不含业务逻辑。独立脚本提供双通道：① CLI 可独立执行 + `--dry-run` 预览；② 核心函数可被系统 import 调用返回 dict。\n")
        w(">\n")
        w("> **由 `scriptmgr export` 自动生成，请勿手动编辑。**\n\n")

    # 目录
    w("---\n\n")
    w("## 目录\n\n")

    # 按 category 分组
    groups = _group_by_category(registry)
    for idx, (cat_key, cat_label, entries) in enumerate(groups, 1):
        w(f"- [{idx}. {cat_label}](#{_anchor(cat_label)})\n")

    w("\n---\n\n")

    # 各分组内容
    for idx, (cat_key, cat_label, entries) in enumerate(groups, 1):
        w(f"## {idx}. {cat_label}\n\n")
        _write_category_section(w, cat_key, cat_label, entries, registry)

    # 附录 A：速查表
    w("---\n\n")
    w("## 附录 A：速查表\n\n")
    w("| # | 脚本 | 功能域 | 聚合壳 | 调度归属 | `--dry-run` | 写 DB |\n")
    w("|---|------|--------|--------|----------|-------------|-------|\n")
    idx = 0
    for cat_key, cat_label, entries in groups:
        for e in entries:
            idx += 1
            check = f"`{e.check_arg}`" if e.check_arg else "—"
            parent = e.aggregation_parent or "—（独立）"
            scheduling = e.scheduling_note or "纯手动"
            writes = "✅" if e.writes_db else "否"
            w(f"| {idx} | `{e.name}.py` | {cat_label} | {parent} | {scheduling} | {check} | {writes} |\n")

    # 附录 B：聚合关系图
    w("\n---\n\n")
    w("## 附录 B：聚合关系图\n\n")
    aggregations = registry.aggregations
    if aggregations:
        for shell_name, agg_data in aggregations.items():
            if not isinstance(agg_data, dict):
                continue
            children = agg_data.get("children", [])
            if children:
                w(f"```\n{shell_name}  ─── {agg_data.get('description', '')}\n")
                for i, child in enumerate(children):
                    prefix = "  └──" if i == len(children) - 1 else "  ├──"
                    child_entry = registry.get(child)
                    child_desc = f" ← {child_entry.description}" if child_entry else ""
                    w(f"{prefix} {child}.py{child_desc}\n")
                w("```\n\n")

    if registry.epilogue:
        w(registry.epilogue.strip() + "\n")

    return buf.getvalue()


def _group_by_category(registry: ScriptRegistry) -> list[tuple[str, str, list]]:
    """按 category 分组，自定义排序。

    Returns:
        [(category_key, category_label, [ScriptEntry, ...]), ...]
    """
    CATEGORY_ORDER = {
        "evolution_pipeline": "进化闭环（聚合壳：evolution.py）",
        "cold_start": "冷启动（聚合壳：cold_start.py）",
        "cognition_cycle": "认知进化周期（聚合壳：cognition_cycle.py）",
        "node_management": "全景节点管理",
        "system_maintenance": "系统维护",
        "business_tool": "系统自描述与会话 Prompt",
        "file_api_manage": "文件与 API 管理",
        "data_collection": "数据收集",
        "one_shot": "一次性工具",
        "aggregation_shell": "聚合壳",
    }

    grouped: dict[str, list] = {}
    for e in registry.entries:
        cat = e.category
        grouped.setdefault(cat, []).append(e)

    result = []
    for cat_key, cat_label in CATEGORY_ORDER.items():
        if cat_key in grouped:
            entries = sorted(grouped[cat_key], key=lambda x: x.name)
            result.append((cat_key, cat_label, entries))
            del grouped[cat_key]

    # 其余未分类
    for cat_key, entries in sorted(grouped.items()):
        result.append((cat_key, cat_key, sorted(entries, key=lambda x: x.name)))

    return result


def _write_category_section(w, cat_key: str, cat_label: str, entries: list, registry: ScriptRegistry) -> None:
    """写单个分类章节。"""
    for e in entries:
        w(f"### {e.name}.py — {e.description}\n\n")
        w(f"**职责**：{e.description}\n\n")
        if e.source_path:
            w(f"**路径**：[{e.source_path}](../{e.source_path})\n\n")
        if e.aggregation_parent:
            w(f"**聚合归属**：`{e.aggregation_parent}`\n\n")
        if e.flow_note:
            w(f"> {e.flow_note}\n\n")
        if e.check_arg:
            w(f"**自检**：`{e.check_arg}`\n\n")
        w(f"**调用**：\n```bash\n{e.invocation}\n```\n\n")
        if e.writes_db:
            w("**写数据库**：是\n\n")
        w("---\n\n")


def _anchor(label: str) -> str:
    """生成 Markdown 锚点。"""
    return label.replace(" ", "-").replace("（", "").replace("）", "").replace("：", "").replace(".", "").lower()
