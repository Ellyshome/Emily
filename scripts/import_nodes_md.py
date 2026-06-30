#!/usr/bin/env python3
"""全景节点图 V2 Markdown 节点定义解析工具 —— 需求文档 §5.1。

支持的 Markdown 格式示例：

# 景观工程 (SG-001)
- 阶段: 3
- 截止: 2026-10-15T18:00:00+08:00
- 主责: dept-eng
- 单位: comp-landscape

## 成果
- 景观施工图: 1 份 [必需]
- 苗木清单: 1 份 [可选]

## 依赖
- SJ-003-DELV-001: 0.4 (景观施工图)
- CB-002-DELV-001: 0.3 (工程合同)

## 子节点
- SG-001-01: 钢筋绑扎 (权重0.4)
- SG-001-02: 模板支设 (权重0.3)
- SG-001-03: 混凝土浇筑 (权重0.3)

用法：
    uv run python scripts/import_nodes_md.py <md文件路径> --project-id <项目ID>
    uv run python scripts/import_nodes_md.py <目录路径> --project-id <项目ID>  # 批量导入目录
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 Markdown 批量导入")
    parser.add_argument("path", type=str, help="md 文件路径或目录路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="import-script", help="创建人ID")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    return parser.parse_args()


def parse_md_node(filepath: str) -> dict | None:
    """解析单个 Markdown 文件为节点数据。

    格式约定：
      - 第一行 H1 (# 开头) = 节点名称 + 可选编号
      - 元数据行 "- key: value"
      - "## 成果" 区域 → deliverables
      - "## 依赖" 区域 → dependencies
      - "## 子节点" 区域 → children
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.strip().split("\n")

    node: dict = {
        "node_id": "",
        "node_name": "",
        "stage_id": 0,
        "deadline": "",
        "owner_dept_id": "项目总",
        "related_company_id": "建设单位",
        "child_weight": 1.0,
        "deliverables": [],
        "dependencies": [],
        "children": [],
    }

    section = "header"

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 标题行
        if line.startswith("# "):
            title = line[2:].strip()
            # 尝试提取编号：景观工程 (SG-001) → name + node_id
            m = re.match(r"(.+?)\s*\((\S+)\)\s*$", title)
            if m:
                node["node_name"] = m.group(1).strip()
                node["node_id"] = m.group(2).strip()
            else:
                node["node_name"] = title
                node["node_id"] = ""
            continue

        # 区域标记
        if line.startswith("## "):
            section_name = line[3:].strip().lower()
            if "成果" in section_name:
                section = "deliverables"
            elif "依赖" in section_name:
                section = "dependencies"
            elif "子节点" in section_name:
                section = "children"
            else:
                section = "header"
            continue

        # 元数据行
        if section == "header" and line.startswith("- "):
            meta = line[2:]
            if ":" in meta:
                key, _, value = meta.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key in ("阶段", "stage"):
                    try:
                        node["stage_id"] = int(value)
                    except ValueError:
                        pass
                elif key in ("截止", "deadline"):
                    node["deadline"] = value
                elif key in ("主责", "owner"):
                    node["owner_dept_id"] = value
                elif key in ("单位", "company"):
                    node["related_company_id"] = value
            continue

        # 成果行: - 成果名: 目标量 单位 [必需/可选]
        if section == "deliverables" and line.startswith("- "):
            item = line[2:]
            m = re.match(r"(.+?)\s*:\s*([\d.]+)\s*(\S+)?\s*(\[必需\])?(\[可选\])?", item)
            if m:
                node["deliverables"].append({
                    "deliverable_name": m.group(1).strip(),
                    "target_amount": float(m.group(2)),
                    "unit": m.group(3) or "份",
                    "is_required": "可选" not in (m.group(5) or ""),
                })
            continue

        # 依赖行: - DELIVERABLE_ID: 权重 (说明)
        if section == "dependencies" and line.startswith("- "):
            item = line[2:]
            m = re.match(r"(\S+)\s*:\s*([\d.]+)", item)
            if m:
                node["dependencies"].append({
                    "depends_on_deliverable_id": m.group(1),
                    "weight": float(m.group(2)),
                })
            continue

        # 子节点行: - NODE_ID: 名称 (权重X.X)
        if section == "children" and line.startswith("- "):
            item = line[2:]
            m = re.match(r"(\S+)\s*:\s*(.+?)\s*\(权重\s*([\d.]+)\)", item)
            if m:
                node["children"].append({
                    "child_node_id": m.group(1),
                    "child_name": m.group(2).strip(),
                    "child_weight": float(m.group(3)),
                })
            continue

    if not node["node_name"]:
        return None

    return node


async def import_from_md(nodes: list[dict], project_id: str, creator_id: str,
                          dry_run: bool = False):
    """执行导入。"""
    from emily_core.services.node_commands import (
        CreateNodeCommand, CreateDeliverableCommand,
        AddDependencyCommand, MountChildCommand,
    )
    from emily_core.services.node_service import NodeService

    svc = NodeService()
    created_ids: set[str] = set()

    for nd in nodes:
        if dry_run:
            print(f"[DRY-RUN] {nd['node_id'] or '?'}: {nd['node_name']} "
                  f"({len(nd['deliverables'])} 成果, {len(nd['dependencies'])} 依赖, "
                  f"{len(nd['children'])} 子节点)")
            created_ids.add(nd["node_id"])
            continue

        try:
            cmd = CreateNodeCommand(
                project_id=project_id,
                node_id=nd["node_id"],
                node_name=nd["node_name"],
                owner_dept_id=nd["owner_dept_id"],
                related_company_id=nd["related_company_id"],
                deadline=nd["deadline"],
                stage_id=nd["stage_id"],
                creator_id=creator_id,
            )
            result = await svc.create_node(cmd)
            if not result.success:
                print(f"[FAIL] {nd['node_id']}: {result.message}")
                continue

            print(f"[OK] {nd['node_id']}: {nd['node_name']}")
            created_ids.add(nd["node_id"])

            # 创建成果
            for d in nd["deliverables"]:
                await svc.create_deliverable(CreateDeliverableCommand(
                    node_id=nd["node_id"],
                    deliverable_name=d["deliverable_name"],
                    target_amount=d["target_amount"],
                    unit=d["unit"],
                    is_required=d["is_required"],
                    operator_id=creator_id,
                ))

            # 创建子节点（递归）
            for child in nd["children"]:
                child_cmd = CreateNodeCommand(
                    project_id=project_id,
                    node_id=child["child_node_id"],
                    node_name=child["child_name"],
                    deadline=nd["deadline"],
                    creator_id=creator_id,
                    stage_id=nd["stage_id"],
                )
                await svc.create_node(child_cmd)
                created_ids.add(child["child_node_id"])

                # 挂载子节点
                await svc.mount_child(MountChildCommand(
                    parent_node_id=nd["node_id"],
                    child_node_id=child["child_node_id"],
                    child_weight=child["child_weight"],
                    operator_id=creator_id,
                ))

        except Exception as e:
            print(f"[FAIL] {nd.get('node_id', '?')}: {e}")

    # 建立依赖（第二阶段）
    if not dry_run:
        dep_count = 0
        for nd in nodes:
            for dep in nd["dependencies"]:
                try:
                    result = await svc.add_dependency(AddDependencyCommand(
                        node_id=nd["node_id"],
                        depends_on_deliverable_id=dep["depends_on_deliverable_id"],
                        weight=dep["weight"],
                        operator_id=creator_id,
                    ))
                    if result.success:
                        dep_count += 1
                except Exception as e:
                    print(f"[DEP-FAIL] {nd['node_id']}: {e}")
        if dep_count:
            print(f"[OK] 已创建 {dep_count} 条依赖")

    print(f"\n导入完成：{len(nodes)} 个节点")


def main():
    args = parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"错误：路径不存在: {args.path}")
        sys.exit(1)

    # 收集 md 文件
    if path.is_dir():
        md_files = sorted(path.glob("*.md"))
        print(f"发现 {len(md_files)} 个 md 文件")
    else:
        if path.suffix.lower() != ".md":
            print(f"错误：不支持的文件格式: {path.suffix}（仅支持 .md）")
            sys.exit(1)
        md_files = [path]

    # 解析所有文件
    nodes = []
    for md_file in md_files:
        print(f"解析: {md_file}")
        node = parse_md_node(str(md_file))
        if node:
            nodes.append(node)
            print(f"  -> {node['node_id']}: {node['node_name']}")
        else:
            print(f"  -> 解析失败（跳过）")

    if not nodes:
        print("未解析到任何节点数据。")
        sys.exit(1)

    asyncio.run(import_from_md(
        nodes,
        project_id=args.project_id,
        creator_id=args.creator_id,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
