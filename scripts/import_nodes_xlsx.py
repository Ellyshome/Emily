#!/usr/bin/env python3
"""全景节点图 V2 xlsx 批量导入工具 —— 需求文档 §5.3。

用法：
    uv run python scripts/import_nodes_xlsx.py <xlsx文件路径> --project-id <项目ID> [--creator-id <创建人ID>]

xlsx 格式要求（Sheet1）：
    | 节点编号 | 节点名称 | 父节点编号 | 阶段 | 截止时间 | 主责条线 | 关联单位 | 权重 | 成果名称 | 成果目标量 | 成果单位 | 依赖成果ID | 依赖权重 |
    |---------|---------|-----------|------|---------|---------|---------|------|---------|-----------|---------|-----------|---------|
    | SG-001  | 景观工程 |           | 3    | 2026-10-15 | dept-eng | comp-a  | 1.0  | 施工图  | 1         | 份      | SJ-003-DELV-001 | 0.4 |

支持父子层级通过编号识别：子节点编号格式为 {父节点编号}-NN（如 SG-001-01 是 SG-001 的子节点）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 xlsx 批量导入")
    parser.add_argument("file", type=str, help="xlsx 文件路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="import-script", help="创建人ID")
    parser.add_argument("--no-deps", action="store_true", help="跳过依赖创建")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    parser.add_argument("--max-rows", type=int, default=500, help="最大导入行数")
    return parser.parse_args()


def parse_xlsx(filepath: str, max_rows: int = 500) -> tuple[list[dict], list[dict]]:
    """解析 xlsx 文件为节点数据列表。"""
    try:
        import openpyxl
    except ImportError:
        print("错误：需要 openpyxl 库。请执行: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(min_row=2, values_only=True))  # 跳过表头
    wb.close()

    nodes: dict[str, dict] = {}
    deps: list[dict] = []

    for i, row in enumerate(rows[:max_rows]):
        if not row or not row[0]:
            continue

        node_id = str(row[0]).strip() if row[0] else ""
        node_name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        parent_prefix = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        stage_id = int(row[3]) if len(row) > 3 and row[3] else 0
        deadline = str(row[4]).strip() if len(row) > 4 and row[4] else ""
        owner = str(row[5]).strip() if len(row) > 5 and row[5] else "项目总"
        company = str(row[6]).strip() if len(row) > 6 and row[6] else "建设单位"
        weight = float(row[7]) if len(row) > 7 and row[7] else 1.0
        deliv_name = str(row[8]).strip() if len(row) > 8 and row[8] else ""
        deliv_target = float(row[9]) if len(row) > 9 and row[9] else 1.0
        deliv_unit = str(row[10]).strip() if len(row) > 10 and row[10] else "份"
        dep_deliv_id = str(row[11]).strip() if len(row) > 11 and row[11] else ""
        dep_weight = float(row[12]) if len(row) > 12 and row[12] else 1.0

        if not node_id or not node_name:
            print(f"跳过第 {i+2} 行：节点编号或名称为空")
            continue

        nodes[node_id] = {
            "node_id": node_id,
            "node_name": node_name,
            "parent_node_id": parent_prefix,
            "stage_id": stage_id,
            "deadline": _normalize_deadline(deadline),
            "owner_dept_id": owner,
            "related_company_id": company,
            "child_weight": weight,
        }

        # 收集成果信息
        if deliv_name:
            if "deliverables" not in nodes[node_id]:
                nodes[node_id]["deliverables"] = []
            nodes[node_id]["deliverables"].append({
                "deliverable_name": deliv_name,
                "target_amount": deliv_target,
                "unit": deliv_unit,
            })

        # 收集依赖信息
        if dep_deliv_id:
            deps.append({
                "node_id": node_id,
                "depends_on_deliverable_id": dep_deliv_id,
                "weight": dep_weight,
            })

    return list(nodes.values()), deps


def _normalize_deadline(deadline: str) -> str:
    """标准化截止时间格式。"""
    if not deadline:
        return ""
    # 尝试解析常见格式
    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"]:
        try:
            dt = datetime.strptime(deadline.strip(), fmt)
            return dt.replace(tzinfo=BEIJING_TZ).isoformat()
        except ValueError:
            continue
    return deadline  # 无法解析则原样返回


async def import_nodes(nodes: list[dict], deps: list[dict],
                       project_id: str, creator_id: str,
                       dry_run: bool = False, skip_deps: bool = False):
    """执行导入。"""
    # 动态 import 避免在非 emily-core 环境报错
    from emily_core.services.node_commands import (
        CreateNodeCommand, CreateDeliverableCommand, AddDependencyCommand,
    )
    from emily_core.services.node_service import NodeService

    svc = NodeService()
    created = 0
    created_ids: set[str] = set()
    failed: list[tuple[str, str]] = []

    for nd in nodes:
        try:
            if dry_run:
                print(f"[DRY-RUN] 将创建节点: {nd['node_id']} - {nd['node_name']}")
                created += 1
                created_ids.add(nd["node_id"])
                continue

            # 创建节点
            cmd = CreateNodeCommand(
                project_id=project_id,
                node_id=nd["node_id"],
                node_name=nd["node_name"],
                owner_dept_id=nd.get("owner_dept_id", "项目总"),
                related_company_id=nd.get("related_company_id", "建设单位"),
                deadline=nd.get("deadline", ""),
                creator_id=creator_id,
                parent_node_id=nd.get("parent_node_id", ""),
                stage_id=nd.get("stage_id", 0),
                child_weight=nd.get("child_weight", 1.0),
            )
            result = await svc.create_node(cmd)
            if not result.success:
                failed.append((nd["node_id"], result.message))
                continue

            created += 1
            created_ids.add(nd["node_id"])
            print(f"[OK] {nd['node_id']}: {nd['node_name']} (status={result.status})")

            # 创建成果
            for deliv in nd.get("deliverables", []):
                dcmd = CreateDeliverableCommand(
                    node_id=nd["node_id"],
                    deliverable_name=deliv["deliverable_name"],
                    target_amount=deliv["target_amount"],
                    unit=deliv["unit"],
                    operator_id=creator_id,
                )
                await svc.create_deliverable(dcmd)

        except Exception as e:
            failed.append((nd.get("node_id", "?"), str(e)))
            print(f"[FAIL] {nd.get('node_id', '?')}: {e}")

    # 创建依赖（第二阶段——等所有节点创建完成后再建立依赖）
    if not dry_run and not skip_deps:
        dep_created = 0
        for dep in deps:
            try:
                dcmd = AddDependencyCommand(
                    node_id=dep["node_id"],
                    depends_on_deliverable_id=dep["depends_on_deliverable_id"],
                    weight=dep.get("weight", 1.0),
                    operator_id=creator_id,
                )
                result = await svc.add_dependency(dcmd)
                if result.success:
                    dep_created += 1
                else:
                    print(f"[DEP-FAIL] {dep['node_id']} -> {dep['depends_on_deliverable_id']}: {result.message}")
            except Exception as e:
                print(f"[DEP-FAIL] {dep['node_id']}: {e}")

    # 批量建立父子关系（第三阶段——利用编号层级自动推断）
    if not dry_run:
        parent_count = 0
        for nd in nodes:
            parent_id = nd.get("parent_node_id", "")
            if parent_id and parent_id in created_ids:
                try:
                    from emily_core.services.node_commands import MountChildCommand
                    mcmd = MountChildCommand(
                        parent_node_id=parent_id,
                        child_node_id=nd["node_id"],
                        child_weight=nd.get("child_weight", 1.0),
                        operator_id=creator_id,
                    )
                    result = await svc.mount_child(mcmd)
                    if result.success:
                        parent_count += 1
                except Exception as e:
                    print(f"[MOUNT-FAIL] {parent_id} -> {nd['node_id']}: {e}")
        if parent_count:
            print(f"[OK] 已挂载 {parent_count} 个子节点")

    # 汇总
    print(f"\n导入完成：成功 {created}/{len(nodes)} 个节点，失败 {len(failed)} 个")
    if failed and not dry_run:
        print("失败列表：")
        for nid, reason in failed:
            print(f"  - {nid}: {reason}")


def main():
    args = parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"错误：文件不存在: {args.file}")
        sys.exit(1)

    ext = filepath.suffix.lower()
    if ext not in (".xlsx", ".xlsm"):
        print(f"错误：不支持的文件格式: {ext}（仅支持 .xlsx）")
        sys.exit(1)

    print(f"解析文件: {args.file}")
    nodes, deps = parse_xlsx(str(filepath), args.max_rows)

    if not nodes:
        print("未解析到任何节点数据，请检查文件格式。")
        sys.exit(1)

    print(f"解析到 {len(nodes)} 个节点，{len(deps)} 条依赖关系")

    asyncio.run(import_nodes(
        nodes, deps,
        project_id=args.project_id,
        creator_id=args.creator_id,
        dry_run=args.dry_run,
        skip_deps=args.no_deps,
    ))


if __name__ == "__main__":
    main()
