#!/usr/bin/env python3
"""全景节点计划解析器：Excel → Markdown 节点树 → PostgreSQL。

用法:
    # 预览模式：解析 Excel 输出为 Markdown 文档（不写 DB）
    uv run python scripts/parse_nodes.py --file "H:\工作经验\全生命周期计划\生态城26#地.xlsx" --dry-run

    # 实际写入数据库
    uv run python scripts/parse_nodes.py --file "H:\工作经验\全生命周期计划\生态城26#地.xlsx" --write

    # 清空重建
    uv run python scripts/parse_nodes.py --file "H:\工作经验\全生命周期计划\生态城26#地.xlsx" --write --reset

    # 输出到指定目录
    uv run python scripts/parse_nodes.py --file "xxx.xlsx" --dry-run --output "需求文件/全景节点图-解析结果/"

工作原理:
    1. 读取 Excel，自动识别 sheet 结构（签确版 + 全生命周期节点计划）
    2. 从签确版提取阶段分组（前期/设计/成本/工程/营销/交付）
    3. 从节点计划 sheet 提取节点树（WBS 层级：一级→八级）
    4. 自动生成节点编号（阶段码-序号，如 SJ-01、SG-JG-01）
    5. 自动推断依赖关系（阶段内顺序依赖 + 阶段间依赖）
    6. --dry-run 模式输出 Markdown 文档供人工审视
    7. --write 模式写入 sm_nodes + sm_node_dependencies 表
"""

import re
import sys
import os
import argparse
from datetime import datetime
from collections import OrderedDict

import pandas as pd

# Ensure emily-core is on path
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, "..", "emily-core"))
sys.path.insert(0, os.path.join(_script_dir, ".."))
sys.path.insert(0, "/app")


# ══════════════════════════════════════════════════════════════════════════════
# Stage mapping: Excel 条线名 → 阶段码
# ══════════════════════════════════════════════════════════════════════════════

STAGE_CODE_MAP = {
    "前期": "QQ",
    "设计": "SJ",
    "成本": "CB",
    "工程": "SG",
    "营销": "YX",
    "市场": "YX",
    "交付一期": "JF",
    "交付二期": "JF2",
    "交付": "JF",
}


def map_stage(stage_name: str) -> tuple[str, int]:
    """Map a Chinese stage name to (stage_code, stage_sort_order)."""
    for key, code in STAGE_CODE_MAP.items():
        if key in stage_name:
            idx = list(STAGE_CODE_MAP.keys()).index(key)
            return code, idx + 1
    return "XX", 99


# ══════════════════════════════════════════════════════════════════════════════
# Column detectors — Excel files vary, so we detect structure heuristically
# ══════════════════════════════════════════════════════════════════════════════

def _find_header_row(df, col_indices: list[int], keywords: list[str]) -> int:
    """Find the header row by scanning for keyword matches in given columns."""
    for i in range(min(5, len(df))):
        for col in col_indices:
            val = str(df.iloc[i, col]) if pd.notna(df.iloc[i, col]) else ""
            if all(kw in val for kw in keywords):
                return i
    return 0


def detect_sheets(dfs: dict) -> dict:
    """Auto-detect which sheet is what. Returns {'signoff': name, 'detail': name}."""
    result = {"signoff": None, "detail": None}
    for name, df in dfs.items():
        # Flatten first 3 rows to a string for detection
        header_smell = ""
        for i in range(min(3, len(df))):
            for c in range(min(8, df.shape[1])):
                val = str(df.iloc[i, c]) if pd.notna(df.iloc[i, c]) else ""
                if val and len(val) < 20:
                    header_smell += val + " "

        # 签确版: has 条线 (stage line), 序号, 签确
        if "签确" in header_smell or "条线" in header_smell:
            result["signoff"] = name
        # Detail: has 阶段, 级别, or 计划节点名称
        elif "节点名称" in header_smell or "节点计划" in header_smell:
            result["detail"] = name

    # Fallback: use sheet indices
    if result["detail"] is None and len(dfs) >= 1:
        result["detail"] = list(dfs.keys())[-1]  # last sheet
    if result["signoff"] is None:
        # Find any sheet with stage line headers
        for name, df in dfs.items():
            for i in range(min(5, len(df))):
                v0 = str(df.iloc[i, 0]) if pd.notna(df.iloc[i, 0]) else ""
                if v0 in ["前期", "设计", "成本", "工程"]:
                    result["signoff"] = name
                    break

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Parsers
# ══════════════════════════════════════════════════════════════════════════════

def parse_signoff_sheet(df) -> list[dict]:
    """Parse the 一级计划签确版 sheet → extract stages and key milestones."""
    stages = OrderedDict()
    milestones = []
    current_stage = None

    for i in range(len(df)):
        v0 = str(df.iloc[i, 0]) if pd.notna(df.iloc[i, 0]) else ""
        v1 = str(df.iloc[i, 1]) if pd.notna(df.iloc[i, 1]) else ""
        v2 = str(df.iloc[i, 2]) if pd.notna(df.iloc[i, 2]) else ""
        v3 = str(df.iloc[i, 3]) if pd.notna(df.iloc[i, 3]) else ""
        v4 = str(df.iloc[i, 4]) if pd.notna(df.iloc[i, 4]) else ""
        v5 = str(df.iloc[i, 5]) if pd.notna(df.iloc[i, 5]) else ""
        v6 = str(df.iloc[i, 6]) if pd.notna(df.iloc[i, 6]) else ""

        # Detect stage line header (e.g., "前期", "设计", "成本")
        # Skip rows that are signatories (contain "签" or "总" or "经理")
        is_signature = any(kw in v0 for kw in ["签", "经理", "总经", "董事", "审批"])
        if v0 and not is_signature and v0 not in ["条线", "全生命周期节点计划", "审批", "签确"] and len(v0) < 10:
            # Check if this looks like a stage name
            if any(kw in v0 for kw in STAGE_CODE_MAP) or any(c in v0 for c in ["前期", "设计", "成本", "工程", "营销", "交付"]):
                current_stage = v0
                code, order = map_stage(v0)
                if current_stage not in stages:
                    stages[current_stage] = {"code": code, "order": order, "milestones": []}

        # Detect milestone row: col 1 is a sequential number
        # Signoff columns: col0=stage, col1=seq, col2=name, col3=owner, col4=criteria, col5=start, col6=end
        if current_stage and v1 and v1.isdigit():
            node_name = v2.strip() if v2 and v2.strip() else f"{current_stage}里程碑{len(stages.get(current_stage, {}).get('milestones', [])) + 1}"
            milestones.append({
                "stage_name": current_stage,
                "stage_code": stages.get(current_stage, {}).get("code", "XX"),
                "seq": int(v1),
                "node_name": node_name,
                "owner": v3.strip() if v3 else "",
                "criteria": v4.strip() if v4 else "",
                "planned_start": v5 if v5 and "-" in str(v5) else "",
                "planned_end": v6 if v6 and "-" in str(v6) else "",
                "is_milestone": True,
                "plan_level": "一级",
            })
            if current_stage in stages:
                stages[current_stage]["milestones"].append(v2)

    return milestones, stages


def parse_detail_sheet(df, stages: OrderedDict = None) -> list[dict]:
    """Parse the 全生命周期节点计划 sheet → extract all nodes with WBS hierarchy.

    Column mapping (auto-detected from header):
        col1 = 级别 (WBS nesting: 一级/二级/.../八级)
        col2 = 计划节点名称
        col3 = 完成标准
        col4 = 主办部门
        col5 = 双向考核部门 (dropped)
        col6 = 开始时间
        col7 = 工期
        col8 = 结束时间

    Args:
        df: pandas DataFrame of the detail sheet
        stages: OrderedDict from signoff sheet, used to map 一级 nodes to correct stage codes
    """
    # Detect header row
    header_row = 0
    for i in range(min(5, len(df))):
        v1 = str(df.iloc[i, 1]) if pd.notna(df.iloc[i, 1]) else ""
        v2 = str(df.iloc[i, 2]) if pd.notna(df.iloc[i, 2]) else ""
        if "级别" in v1 and ("节点名称" in v2 or "计划" in v2):
            header_row = i
            break
        # Also detect: col1="级别" col2="计划节点名称"
        if "级别" in v1 and "节点" in v2:
            header_row = i
            break

    # If no explicit header found, try to find the first data row
    if header_row == 0:
        for i in range(min(5, len(df))):
            v1 = str(df.iloc[i, 1]) if pd.notna(df.iloc[i, 1]) else ""
            if v1 in ["一级", "二级", "三级"]:
                header_row = i - 1 if i > 0 else 0
                break

    # Stack to track WBS parents at each level
    wbs_stack = [None] * 10  # max depth 10
    wbs_counter = [0] * 10   # counter per level for node ID generation
    stage_code = None
    current_stage_order = 0
    # Build stage list from signoff sheet for mapping
    stage_name_list = []
    if stages:
        stage_name_list = list(stages.keys())
    else:
        stage_name_list = ["前期", "设计", "成本", "工程", "营销", "交付"]
    stage_seq = 0  # how many 一级 nodes we've seen (maps to stage_name_list)

    nodes = []

    for i in range(header_row + 1, len(df)):
        v1 = str(df.iloc[i, 1]) if pd.notna(df.iloc[i, 1]) else ""
        v2 = str(df.iloc[i, 2]) if pd.notna(df.iloc[i, 2]) else ""
        v3 = str(df.iloc[i, 3]) if pd.notna(df.iloc[i, 3]) else ""
        v4 = str(df.iloc[i, 4]) if pd.notna(df.iloc[i, 4]) else ""
        v6 = df.iloc[i, 6] if pd.notna(df.iloc[i, 6]) else None  # start date
        v8 = df.iloc[i, 8] if pd.notna(df.iloc[i, 8]) else None  # end date

        # Skip empty rows and header-like rows
        if not v2:
            continue
        if v2 in ["计划节点名称", "节点名称"]:
            continue

        # Determine WBS level
        level_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
        wbs_level = 2  # default: 二级
        if v1:
            for cn, lv in level_map.items():
                if f"{cn}级" in v1:
                    wbs_level = lv
                    break

        # Clean node name: remove parenthetical stage annotations
        node_name = re.sub(r"（[^）]*）\s*$", "", v2).strip()

        # Determine if 关键节点 (milestone): check if original name contains milestone markers,
        # or if the completion criteria mentions approvals
        is_milestone = False
        if wbs_level <= 3:  # 一级~三级 are typically milestones/key nodes
            is_milestone = True

        # Adjust WBS stack
        wbs_stack[wbs_level] = node_name
        wbs_stack[wbs_level + 1:] = [None] * (9 - wbs_level)
        wbs_counter[wbs_level] += 1
        for lv in range(wbs_level + 1, 10):
            wbs_counter[lv] = 0

        # Build parent reference
        parent_name = None
        for lv in range(wbs_level - 1, 0, -1):
            if wbs_stack[lv] is not None:
                parent_name = wbs_stack[lv]
                break

        # Detect stage transition: 一级 nodes change → new stage
        if wbs_level == 1:
            stage_code, current_stage_order = map_stage(node_name)
            if stage_code == "XX":
                # Infer stage from signoff sheet ordering
                if stage_seq < len(stage_name_list):
                    stage_name = stage_name_list[stage_seq]
                    stage_code, current_stage_order = map_stage(stage_name)
                else:
                    current_stage_order = stage_seq + 1
                    stage_code = f"ST-{current_stage_order:02d}"
            stage_seq += 1

        # Parse dates
        start_date = ""
        end_date = ""
        try:
            if v6 is not None:
                if hasattr(v6, "strftime"):
                    start_date = v6.strftime("%Y-%m-%d")
                else:
                    # Could be an Excel serial number
                    from datetime import datetime as dt, timedelta
                    try:
                        n = float(str(v6))
                        epoch = dt(1899, 12, 30)
                        start_date = (epoch + timedelta(days=n)).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        start_date = str(v6)[:10]
        except Exception:
            start_date = ""

        try:
            if v8 is not None:
                if hasattr(v8, "strftime"):
                    end_date = v8.strftime("%Y-%m-%d")
                else:
                    from datetime import datetime as dt, timedelta
                    try:
                        n = float(str(v8))
                        epoch = dt(1899, 12, 30)
                        end_date = (epoch + timedelta(days=n)).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        end_date = str(v8)[:10]
        except Exception:
            end_date = ""

        plan_level = "一级" if wbs_level == 1 else "二级"

        nodes.append({
            "wbs_level": wbs_level,
            "node_name": node_name,
            "criteria": v3.strip(),
            "owner": v4.strip(),
            "parent_name": parent_name,
            "stage_code": stage_code or "XX",
            "stage_order": current_stage_order,
            "planned_start": start_date,
            "planned_end": end_date,
            "is_milestone": is_milestone,
            "plan_level": plan_level,
            "row": i,
        })

    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# Node ID generator
# ══════════════════════════════════════════════════════════════════════════════

def generate_node_ids(nodes: list[dict]) -> list[dict]:
    """Generate node_id for each node based on stage code + WBS counter."""
    # First pass: assign sequential IDs per stage
    stage_counters = {}
    for node in nodes:
        sc = node["stage_code"]
        if sc not in stage_counters:
            stage_counters[sc] = 0
        stage_counters[sc] += 1
        node["node_id"] = f"{sc}-{stage_counters[sc]:03d}"

    return nodes


# ══════════════════════════════════════════════════════════════════════════════
# Dependency inferrer
# ══════════════════════════════════════════════════════════════════════════════

def infer_dependencies(nodes: list[dict]) -> list[dict]:
    """Infer node dependencies from WBS hierarchy and stage ordering."""
    dependencies = []

    # Build parent → children index
    node_by_name = {}
    for n in nodes:
        node_by_name[n["node_name"]] = n["node_id"]

    # Rule 1: Sequential dependencies within same parent
    current_parent = None
    prev_sibling = None
    for n in nodes:
        parent = n.get("parent_name")
        if parent != current_parent:
            current_parent = parent
            prev_sibling = None

        if prev_sibling and prev_sibling.get("parent_name") == parent:
            dependencies.append({
                "from_node_id": n["node_id"],
                "to_node_id": prev_sibling["node_id"],
                "weight": 0.5,
                "required": False,  # weak dependency within same parent
                "reason": f"阶段内顺序: {prev_sibling['node_name'][:20]} → {n['node_name'][:20]}",
            })

        prev_sibling = n

    # Rule 2: Stage-to-stage dependency (each stage's first 一级 node depends on previous stage's last)
    stages_in_order = []
    seen_stages = set()
    for n in nodes:
        if n["wbs_level"] == 1 and n["stage_code"] not in seen_stages:
            stages_in_order.append(n)
            seen_stages.add(n["stage_code"])

    for i in range(1, len(stages_in_order)):
        prev_stage = stages_in_order[i - 1]
        curr_stage = stages_in_order[i]
        dependencies.append({
            "from_node_id": curr_stage["node_id"],
            "to_node_id": prev_stage["node_id"],
            "weight": 1.0,
            "required": True,
            "reason": f"阶段间: {prev_stage['node_name'][:20]} → {curr_stage['node_name'][:20]}",
        })

    return dependencies


# ══════════════════════════════════════════════════════════════════════════════
# Markdown generator
# ══════════════════════════════════════════════════════════════════════════════

def generate_markdown(
    nodes: list[dict],
    dependencies: list[dict],
    stages: OrderedDict,
    milestones: list[dict],
    source_file: str,
    sheet_stats: dict,
) -> str:
    """Generate a Markdown node tree from parsed data."""

    lines = []
    lines.append(f"# 全景节点树 — 解析结果")
    lines.append(f"")
    lines.append(f"> **源文件**：{source_file}")
    lines.append(f"> **解析时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> **节点总数**：{len(nodes)}（一级：{len([n for n in nodes if n['wbs_level']==1])}，二级：{len([n for n in nodes if n['wbs_level']>=2])}）")
    lines.append(f"> **关键节点**：{len([n for n in nodes if n['is_milestone']])}")
    lines.append(f"> **推断依赖**：{len(dependencies)} 条")
    stage_names = [s for s in stages.keys()] if stages else list(set(n["stage_code"] for n in nodes))
    lines.append(f"> **识别阶段**：{' / '.join(stage_names)}")
    lines.append(f"> **Sheet 统计**：{sheet_stats}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # Stage summary
    lines.append(f"## 阶段概览")
    lines.append(f"")
    stage_node_counts = {}
    for n in nodes:
        sc = n["stage_code"]
        stage_node_counts[sc] = stage_node_counts.get(sc, 0) + 1

    lines.append(f"| 阶段 | 阶段码 | 节点数 |")
    lines.append(f"|------|--------|--------|")
    if stages:
        for sname, sinfo in stages.items():
            sc = sinfo.get("code", "?")
            cnt = stage_node_counts.get(sc, 0)
            lines.append(f"| {sname} | {sc} | {cnt} |")
    else:
        for sc, cnt in stage_node_counts.items():
            lines.append(f"| {sc} | {sc} | {cnt} |")
    lines.append(f"")

    # Node tree — group by stage, then by WBS level
    lines.append(f"## 节点树")
    lines.append(f"")

    current_stage = None
    for n in nodes:
        if n["stage_code"] != current_stage:
            current_stage = n["stage_code"]
            stage_name = n.get("stage_name", current_stage)
            lines.append(f"### {current_stage}")
            lines.append(f"")

        # Indent based on WBS level
        indent = "  " * (n["wbs_level"] - 1)
        milestone_marker = " 🔑" if n["is_milestone"] else ""
        id_str = f"`{n['node_id']}`"
        name_str = n["node_name"]
        owner_str = f" — *{n['owner']}*" if n.get("owner") else ""
        date_str = ""
        if n.get("planned_start") and n.get("planned_end"):
            date_str = f" ({n['planned_start']} → {n['planned_end']})"

        lines.append(f"{indent}- {id_str} {name_str}{milestone_marker}{owner_str}{date_str}")

        if n.get("criteria"):
            lines.append(f"{indent}  > 完成标准：{n['criteria'][:120]}")

    lines.append(f"")

    # Dependencies
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## 依赖关系")
    lines.append(f"")
    lines.append(f"| 下游节点 | 上游节点 | 权重 | 类型 | 原因 |")
    lines.append(f"|----------|----------|------|------|------|")
    for dep in dependencies:
        req_str = "强" if dep["required"] else "弱"
        lines.append(
            f"| `{dep['from_node_id']}` | `{dep['to_node_id']}` | {dep['weight']} | {req_str} | {dep['reason']} |"
        )
    lines.append(f"")

    # Key milestones
    if milestones:
        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## 关键里程碑（签确版）")
        lines.append(f"")
        lines.append(f"| 阶段 | 序号 | 节点名称 | 责任部门 | 完成标准 |")
        lines.append(f"|------|------|----------|----------|----------|")
        for m in milestones:
            lines.append(
                f"| {m['stage_name']} | {m['seq']} | {m['node_name']} | {m['owner']} | {m['criteria'][:60]} |"
            )
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## 数据质量检查")
    lines.append(f"")

    # Check for common issues
    warnings = []
    nodes_with_empty_criteria = [n for n in nodes if not n.get("criteria")]
    if nodes_with_empty_criteria:
        warnings.append(f"- ⚠️ {len(nodes_with_empty_criteria)} 个节点缺少完成标准")
    nodes_without_owner = [n for n in nodes if not n.get("owner")]
    if nodes_without_owner:
        warnings.append(f"- ⚠️ {len(nodes_without_owner)} 个节点缺少责任部门")
    nodes_without_dates = [n for n in nodes if not n.get("planned_start") and not n.get("planned_end")]
    if nodes_without_dates:
        warnings.append(f"- ⚠️ {len(nodes_without_dates)} 个节点缺少计划时间")
    # Duplicate names
    names = [n["node_name"] for n in nodes]
    dupes = [name for name in set(names) if names.count(name) > 1]
    if dupes:
        warnings.append(f"- ⚠️ {len(dupes)} 个节点名称重复: {', '.join(dupes[:5])}")

    if warnings:
        lines.extend(warnings)
    else:
        lines.append(f"✅ 数据质量检查通过，无异常。")
    lines.append(f"")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Database writer
# ══════════════════════════════════════════════════════════════════════════════

def write_to_db(nodes: list[dict], dependencies: list[dict], stages: OrderedDict, reset: bool = False):
    """Write parsed nodes and dependencies to PostgreSQL."""
    from emily_core.infrastructure.database.session import init_db, get_session
    from emily_core.repositories.sm_node_repo import SMNodeRepository
    from emily_core.repositories.sm_stage_repo import SMStageRepository

    print("  Initializing database...")
    init_db()

    if reset:
        print("  [RESET] Clearing existing state machine tables...")
        with get_session() as s:
            from sqlalchemy import text
            s.execute(text("DELETE FROM sm_node_deliverables"))
            s.execute(text("DELETE FROM sm_node_dependencies"))
            s.execute(text("DELETE FROM sm_status_history"))
            s.execute(text("DELETE FROM sm_audit_logs"))
            s.execute(text("DELETE FROM sm_nodes"))
            s.execute(text("DELETE FROM sm_stages"))
            s.commit()
        print("  Cleared.")

    # Write stages
    print(f"  Writing {len(stages)} stages...")
    stage_order = 1
    for sname, sinfo in stages.items():
        existing = SMStageRepository.get_by_id(stage_order)
        if existing is None:
            SMStageRepository.create(
                stage_id=stage_order,
                stage_name=sname,
                boundary_start="",
                boundary_end="",
            )
        stage_order += 1

    # Write nodes
    print(f"  Writing {len(nodes)} nodes...")
    imported = 0
    for n in nodes:
        # Check if node exists
        with get_session() as sess:
            existing = SMNodeRepository.get_by_node_id(n["node_id"], session=sess)
            if existing:
                print(f"    [SKIP] {n['node_id']} already exists")
                continue

        SMNodeRepository.create(
            node_id=n["node_id"],
            node_name=n["node_name"],
            stage_id=n.get("stage_order", 1),
            parent_section=n.get("parent_name", "") or "",
            node_type="standard",
            owner=n.get("owner", ""),
            is_milestone=n.get("is_milestone", False),
            sort_order=n.get("row", 0),
        )

        # Write planned dates if present
        if n.get("planned_start") or n.get("planned_end"):
            with get_session() as sess:
                node = SMNodeRepository.get_by_node_id(n["node_id"], session=sess)
                if node:
                    node.planned_start_date = n.get("planned_start", "")
                    node.planned_end_date = n.get("planned_end", "")

        imported += 1
        if imported % 50 == 0:
            print(f"    {imported}/{len(nodes)}...")

    # Write dependencies
    print(f"  Writing {len(dependencies)} dependencies...")
    dep_count = 0
    for dep in dependencies:
        SMNodeRepository.create_dependency(
            from_node_id=dep["from_node_id"],
            to_node_id=dep["to_node_id"],
            weight=dep.get("weight", 1.0),
            required=dep.get("required", True),
        )
        dep_count += 1
        if dep_count % 100 == 0:
            print(f"    {dep_count}/{len(dependencies)}...")

    print(f"  Done. Imported: {imported} nodes, {dep_count} dependencies across {len(stages)} stages.")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="全景节点计划解析器：Excel → Markdown 节点树 → PostgreSQL"
    )
    parser.add_argument("--file", type=str, required=True,
                        help="Excel 文件路径")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="仅解析并输出 Markdown，不写入 DB（默认）")
    parser.add_argument("--write", action="store_true",
                        help="写入 PostgreSQL")
    parser.add_argument("--reset", action="store_true",
                        help="清空所有状态机表后重新导入（需配合 --write）")
    parser.add_argument("--output", type=str, default=None,
                        help="Markdown 输出目录（默认：源文件同目录）")
    parser.add_argument("--sheet", type=str, default=None,
                        help="指定节点计划 sheet 名称（默认自动检测）")
    args = parser.parse_args()

    # If --write is used, disable dry-run
    if args.write:
        args.dry_run = False

    if not os.path.exists(args.file):
        print(f"ERROR: File not found: {args.file}")
        sys.exit(1)

    print(f"Reading: {args.file}")
    import pandas as pd
    xls = pd.ExcelFile(args.file)
    all_sheets = {name: pd.read_excel(xls, sheet_name=name, header=None) for name in xls.sheet_names}
    print(f"  Sheets: {xls.sheet_names}")

    # Auto-detect sheets
    sheet_map = detect_sheets(all_sheets)
    print(f"  Detected: signoff={sheet_map['signoff']}, detail={sheet_map['detail']}")

    # Parse signoff sheet for stages and milestones
    stages = OrderedDict()
    milestones = []
    if sheet_map["signoff"] and sheet_map["signoff"] in all_sheets:
        milestones, stages = parse_signoff_sheet(all_sheets[sheet_map["signoff"]])
        if milestones:
            print(f"  Parsed {len(milestones)} milestones from signoff sheet")
        if stages:
            print(f"  Stages: {', '.join(stages.keys())}")

    # Parse detail sheet for node tree
    detail_sheet = args.sheet or sheet_map["detail"]
    if detail_sheet is None:
        print("ERROR: Could not detect detail sheet. Use --sheet to specify.")
        sys.exit(1)
    if detail_sheet not in all_sheets:
        print(f"ERROR: Sheet '{detail_sheet}' not found. Available: {list(all_sheets.keys())}")
        sys.exit(1)

    detail_df = all_sheets[detail_sheet]
    print(f"  Parsing detail sheet '{detail_sheet}': {detail_df.shape[0]} rows")

    nodes = parse_detail_sheet(detail_df, stages=stages)
    print(f"  Parsed {len(nodes)} nodes")

    # If no stages from signoff, infer from 一级 nodes
    if not stages:
        for n in nodes:
            if n["wbs_level"] == 1:
                sc = n["stage_code"]
                if sc not in stages:
                    stages[sc] = {"code": sc, "order": len(stages) + 1, "milestones": []}

    # Generate node IDs
    nodes = generate_node_ids(nodes)

    # Infer dependencies
    dependencies = infer_dependencies(nodes)
    print(f"  Inferred {len(dependencies)} dependencies")

    # Build sheet stats
    sheet_stats = f"签确版={sheet_map['signoff']}, 节点计划={detail_sheet}"

    # Generate markdown
    md_content = generate_markdown(
        nodes, dependencies, stages, milestones,
        source_file=os.path.basename(args.file),
        sheet_stats=sheet_stats,
    )

    # Output directory
    output_dir = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.file)),
        "..",
        "解析结果",
    )
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(args.file))[0]
    output_path = os.path.join(output_dir, f"{base_name}-节点树.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n  Markdown written: {output_path}")
    print(f"  Nodes: {len(nodes)}, Dependencies: {len(dependencies)}")

    # Write to DB
    if args.write:
        write_to_db(nodes, dependencies, stages, reset=args.reset)
    else:
        print(f"\n  [DRY RUN] To write to DB, add --write flag.")
        print(f"  Review the Markdown output first, then:")
        print(f"    uv run python scripts/parse_nodes.py --file \"{args.file}\" --write")


if __name__ == "__main__":
    main()
