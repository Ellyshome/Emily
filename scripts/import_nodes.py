#!/usr/bin/env python3
"""导入全景节点图到 sm_nodes / sm_dependencies / sm_deliverables / sm_stages 表。

解析 `emily-data/baseknowledge/全景节点.md` → 提取节点、依赖、成果物、阶段 → 写入 PostgreSQL。

用法:
    python scripts/import_nodes.py                          # 导入全部节点
    python scripts/import_nodes.py --dry-run                # 仅解析并打印，不写入
    python scripts/import_nodes.py --reset                  # 先清空所有状态机表再导入

前置条件: emily-postgres 容器已启动，数据库 emily 已存在。
"""

import re
import sys
import os
import argparse

# Ensure emily-core is on path (works both in-container and from host)
_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, "..", "emily-core"))
sys.path.insert(0, os.path.join(_script_dir, ".."))  # container: /app
sys.path.insert(0, "/app")  # container: /app directly

from emily_core.infrastructure.database.session import init_db, get_session
from emily_core.repositories.sm_node_repo import SMNodeRepository
from emily_core.repositories.sm_stage_repo import SMStageRepository

STAGE_MAP_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}

# Node ID regex: matches ### N.N.N Name, #### N.N.N Name, ### N.N Na, #### N.N Na
NODE_HEADER_RE = re.compile(r"^#{2,4}\s+(\d+\.\d+(?:\.\d+)?)\s+(.+)")
# Dependency reference: N.N.N-《Name》
DEP_REF_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)-《(.+?)》")
# Deliverable: 《Name》
DLV_RE = re.compile(r"《(.+?)》")


def parse_panorama(filepath: str, verbose: bool = True) -> dict:
    """Parse the panorama node diagram line by line, handling ### and #### node headers.

    State machine:
        idle       → waiting for stage/part header or node header
        in_node    → reading fields after a node header (max 7 lines)
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    stages = {}
    nodes = []
    current_stage = 0
    current_section = ""

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # --- Detect stage: ## 阶段X：... ---
        if line.startswith("## 阶段") and "：" in line:
            for cn, sid in STAGE_MAP_CN.items():
                if f"阶段{cn}" in line:
                    current_stage = sid
                    name_part = line.split("：", 1)[-1].strip()
                    s_name = f"阶段{cn}：{name_part}"
                    stages[sid] = {"name": s_name, "entry": "", "exit": ""}
                    if verbose:
                        print(f"  Stage {sid}: {s_name}")
                    # Read boundary from next line
                    if i + 1 < len(lines) and "阶段边界" in lines[i + 1]:
                        boundary = lines[i + 1].strip()
                        m = re.search(r"\*\*阶段边界\*\*：(.*?)→(.*)", boundary)
                        if m:
                            stages[sid]["entry"] = m.group(1).strip()
                            stages[sid]["exit"] = m.group(2).strip()
                    break

        # --- Detect section header: ### 第X部分：... ---
        if (line.startswith("### 第") or line.startswith("## 第")) and "部分" in line:
            current_section = re.sub(r"^#+\s+", "", line)
            if verbose:
                print(f"    Section: {current_section}")

        # --- Detect node: ### N.N Name  or  #### N.N.N Name ---
        node_match = NODE_HEADER_RE.match(line)
        if node_match and current_stage > 0:
            # Skip false positives: headers that are section titles, not node IDs
            node_id = node_match.group(1)
            node_name = node_match.group(2).strip()

            # Validate it looks like a node ID (starts with digit, contains dots)
            if not re.match(r"^\d+\.\d+", node_id):
                i += 1
                continue

            # Read next ~8 lines for fields
            owner = ""
            dep_text = ""
            deliverables = ""
            associated = ""
            for j in range(i + 1, min(i + 9, len(lines))):
                field = lines[j].strip()
                if field.startswith("- **主责部门**"):
                    # Extract after the last colon
                    parts = re.split(r"[:：]", field.replace("**主责部门**", "").replace("- **", ""), maxsplit=1)
                    if len(parts) >= 2:
                        owner = parts[1].strip()
                elif field.startswith("- **关联单位**"):
                    associated = field.split("**：")[-1].strip() if "**：" in field else ""
                elif field.startswith("- **前置条件**"):
                    dep_text = field.split("**：")[-1].strip() if "**：" in field else ""
                elif field.startswith("- **任务成果**"):
                    deliverables = field.split("**：")[-1].strip() if "**：" in field else ""

            # Classify type
            node_type = _classify_node(node_name)

            nodes.append({
                "node_id": node_id,
                "node_name": node_name,
                "stage_id": current_stage,
                "parent_section": current_section,
                "node_type": node_type,
                "owner": owner,
                "dep_text": dep_text,
                "deliverables": deliverables,
            })

            if verbose:
                deps_preview = dep_text[:60] + "..." if len(dep_text) > 60 else dep_text
                print(f"    {node_id}: {node_name[:40]} (s={current_stage}, t={node_type})")

        i += 1

    # --- Post-process: resolve stages from panorama if regex extraction missed some ---
    # The nested nodes (stage 2/5 sections) may not have had their stage detected properly.
    # We now re-scan: any node with stage_id=0 gets assigned based on the node_id prefix.
    for node in nodes:
        if node["stage_id"] == 0:
            prefix = node["node_id"].split(".")[0]
            try:
                node["stage_id"] = int(prefix)
            except ValueError:
                pass

    # --- Parse dependencies ---
    for node in nodes:
        deps = []
        for m in DEP_REF_RE.finditer(node["dep_text"]):
            deps.append({"to_node_id": m.group(1), "deliverable": m.group(2)})
        node["dependencies"] = deps

    # --- Parse deliverables ---
    for node in nodes:
        dlvs = []
        for m in DLV_RE.finditer(node["deliverables"]):
            dlvs.append(m.group(1))
        node["deliverable_list"] = dlvs

    return {"stages": stages, "nodes": nodes}


def _classify_node(node_name: str) -> str:
    """Classify a node by its name keywords. Order matters — first match wins."""
    name = node_name
    if any(kw in name for kw in ["方案", "施工图", "设计"]):
        return "design"
    if any(kw in name for kw in ["招标", "合同", "采购"]):
        return "procurement"
    if any(kw in name for kw in ["施工", "安装", "工程"]):
        return "construction"
    if any(kw in name for kw in ["验收", "检测", "审查", "检验"]):
        return "inspection"
    if any(kw in name for kw in ["测绘", "实测"]):
        return "survey"
    if any(kw in name for kw in ["办理", "备案", "审批", "许可证", "证书"]):
        return "license"
    if any(kw in name for kw in ["结算", "预算", "控制价", "成本"]):
        return "cost"
    if any(kw in name for kw in ["交付"]):
        return "delivery"
    return "standard"


def import_to_db(parsed: dict, dry_run: bool = False, reset: bool = False) -> None:
    stages = parsed["stages"]
    nodes = parsed["nodes"]

    if reset:
        print("  [RESET] Clearing existing state machine tables...")
        if not dry_run:
            with get_session() as s:
                from sqlalchemy import text
                s.execute(text("DELETE FROM sm_node_deliverables"))
                s.execute(text("DELETE FROM sm_node_dependencies"))
                s.execute(text("DELETE FROM sm_status_history"))
                s.execute(text("DELETE FROM sm_audit_logs"))
                s.execute(text("DELETE FROM sm_nodes"))
                s.execute(text("DELETE FROM sm_stages"))
                s.commit()

    # Import stages
    print(f"\n  Importing {len(stages)} stages...")
    for sid in sorted(stages.keys()):
        info = stages[sid]
        if dry_run:
            print(f"    [DRY] Stage {sid}: {info['name']}")
        else:
            SMStageRepository.create(
                stage_id=sid,
                stage_name=info["name"],
                boundary_start=info.get("entry", ""),
                boundary_end=info.get("exit", ""),
            )

    # Import nodes
    print(f"  Importing {len(nodes)} nodes...")
    imported = 0
    for node in nodes:
        if dry_run:
            print(f"    [DRY] {node['node_id']}: {node['node_name']} (s={node['stage_id']})")
        else:
            SMNodeRepository.create(
                node_id=node["node_id"],
                node_name=node["node_name"],
                stage_id=node["stage_id"],
                parent_section=node["parent_section"],
                node_type=node["node_type"],
                owner=node["owner"],
            )
            for dep in node["dependencies"]:
                SMNodeRepository.create_dependency(
                    from_node_id=node["node_id"],
                    to_node_id=dep["to_node_id"],
                )
            for dlv in node["deliverable_list"]:
                SMNodeRepository.create_deliverable(
                    node_id=node["node_id"],
                    deliverable_name=dlv,
                )
            imported += 1

    # Update stage progress
    if not dry_run:
        print(f"\n  Updating stage progress...")
        for sid in range(1, 8):
            with get_session() as s:
                staged = SMNodeRepository.list_by_stage(sid, session=s)
                completed = sum(1 for n in staged if n.status == "COMPLETED")
                SMStageRepository.update_progress(sid, len(staged), completed, session=s)

    if dry_run:
        print(f"\n  [DRY RUN] Would import: {len(stages)} stages, {len(nodes)} nodes")
    else:
        print(f"\n  Done. Imported: {imported} nodes across {len(stages)} stages.")


def main():
    parser = argparse.ArgumentParser(description="Import panorama node diagram into state machine tables")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    parser.add_argument("--reset", action="store_true", help="Clear all SM tables before import")
    parser.add_argument("--file", type=str, default=None, help="Path to panorama md file")
    parser.add_argument("--verbose", "-v", action="store_true", default=True, help="Show each parsed node")
    args = parser.parse_args()

    filepath = args.file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "emily-data", "baseknowledge", "全景节点.md"
    )

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    print(f"Parsing: {filepath}")
    parsed = parse_panorama(filepath, verbose=args.verbose)

    if not args.dry_run:
        print("Initializing database...")
        init_db()

    import_to_db(parsed, dry_run=args.dry_run, reset=args.reset)


if __name__ == "__main__":
    main()
