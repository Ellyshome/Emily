#!/usr/bin/env python3
"""全景节点图 V2 xlsx 批量导入工具 —— 适配蓝城伟业 8 列格式。

实际 xlsx 格式（8 列）：
    | 序号 | 指标级别 | 节点名称 | 主责条线 | 关联单位 | 成果 | 时间要求 | 备注 |

通过 HTTP API 直调 emily-core 创建节点。

用法：
    uv run python scripts/import_nodes_xlsx_v2.py <xlsx文件路径> --project-id <项目ID> [--creator-id <创建人ID>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


API_BASE = "http://localhost:18080/api/v1"


def api_post(path: str, data: dict) -> dict:
    """POST/PATCH 到 emily-core API。"""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()}


def api_patch(path: str, data: dict) -> dict:
    """PATCH 到 emily-core API。"""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    req.get_method = lambda: "PATCH"
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()}


# 指标级别 → 阶段 ID 映射（蓝城伟业内控计划）
LEVEL_TO_STAGE = {
    "关键节点": 0,  # 贯穿全周期
    "一级节点": 1,
    "二级节点": 2,
    "三级节点": 3,
}


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 xlsx 批量导入（V2 8列适配）")
    parser.add_argument("file", type=str, help="xlsx 文件路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="import-v2", help="创建人ID")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    parser.add_argument("--max-rows", type=int, default=500, help="最大导入行数")
    return parser.parse_args()


def parse_xlsx_v2(filepath: str, max_rows: int = 500) -> list[dict]:
    """解析 8 列格式 xlsx 为节点数据列表。"""
    try:
        import openpyxl
    except ImportError:
        print("错误：需要 openpyxl 库。pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(min_row=4, values_only=True))  # 跳过3行标题
    wb.close()

    nodes: list[dict] = []

    for row in rows[:max_rows]:
        if not row or not row[0]:
            continue

        seq = str(int(row[0])).strip() if row[0] and str(row[0]).replace(".", "").isdigit() else str(row[0]).strip() if row[0] else ""
        if not seq:
            continue

        level = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        node_name = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        owner_dept = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        company = str(row[4]).strip() if len(row) > 4 and row[4] else ""
        deliverable_raw = str(row[5]).strip() if len(row) > 5 and row[5] else ""
        deadline_raw = str(row[6]).strip() if len(row) > 6 and row[6] else ""
        remark = str(row[7]).strip() if len(row) > 7 and row[7] else ""

        if not node_name:
            continue

        # 生成节点 ID
        node_id = f"XA-{seq.zfill(3)}"

        # 阶段 ID
        stage_id = LEVEL_TO_STAGE.get(level, 0)

        # 截止时间标准化
        deadline = ""
        if deadline_raw and deadline_raw != "/" and deadline_raw != "None":
            deadline = _normalize_deadline(deadline_raw)

        node = {
            "project_id": "",  # 运行时填充
            "node_id": node_id,
            "node_name": node_name,
            "owner_dept_id": owner_dept or "未指定",
            "related_company_id": company or "蓝城伟业",
            "stage_id": stage_id,
            "deadline": deadline,
            "creator_id": "",  # 运行时填充
            "indicator_level": level,
            "remark": remark[:500] if remark else "",
            "source_row": int(seq),
            "deliverables": [],
        }

        # 成果信息
        if deliverable_raw and deliverable_raw != "/" and deliverable_raw != "None":
            node["deliverables"].append({
                "deliverable_name": deliverable_raw,
                "target_amount": 1,
                "unit": "份",
                "is_required": True,
            })

        nodes.append(node)

    return nodes


def _normalize_deadline(deadline: str) -> str:
    """标准化截止时间格式。"""
    if not deadline:
        return ""
    from datetime import timezone, timedelta
    beijing_tz = timezone(timedelta(hours=8))
    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d",
    ]:
        try:
            dt = datetime.strptime(deadline.strip(), fmt)
            return dt.replace(tzinfo=beijing_tz).isoformat()
        except ValueError:
            continue
    return deadline


def main():
    args = parse_args()

    filepath = Path(args.file) if Path(args.file).exists() else None
    if not filepath:
        # 尝试在 host 路径检查
        alt = Path("/app/attachments") / Path(args.file).name
        if alt.exists():
            filepath = alt
        else:
            print(f"错误：文件不存在: {args.file}")
            sys.exit(1)

    print(f"解析文件: {filepath}")
    nodes = parse_xlsx_v2(str(filepath), args.max_rows)

    if not nodes:
        print("未解析到任何节点数据。")
        sys.exit(1)

    # 统计
    from collections import Counter
    levels = Counter(n["indicator_level"] for n in nodes)
    has_deliv = sum(1 for n in nodes if n["deliverables"])
    has_deadline = sum(1 for n in nodes if n["deadline"])
    print(f"解析到 {len(nodes)} 个节点")
    print(f"  指标级别: {dict(levels)}")
    print(f"  含成果: {has_deliv}, 含截止时间: {has_deadline}")

    if args.dry_run:
        print("\n[Dry-Run 模式] 将导入以下节点（前 20 个）：")
        for n in nodes[:20]:
            deliv_str = f"→ 成果: {n['deliverables'][0]['deliverable_name']}" if n["deliverables"] else ""
            ddl_str = f"截止: {n['deadline'][:20]}" if n["deadline"] else ""
            print(f"  {n['node_id']} [{n['indicator_level']}] {n['node_name'][:30]} | "
                  f"部门={n['owner_dept_id']} | {ddl_str} {deliv_str}")
        if len(nodes) > 20:
            print(f"  ... 还有 {len(nodes) - 20} 个")
        return

    # === 执行导入 ===
    project_id = args.project_id
    creator_id = args.creator_id

    created = 0
    failed: list[tuple[str, str]] = []

    for nd in nodes:
        try:
            # Step 1: 创建节点
            payload = {
                "project_id": project_id,
                "node_id": nd["node_id"],
                "node_name": nd["node_name"],
                "owner_dept_id": nd["owner_dept_id"],
                "related_company_id": nd["related_company_id"],
                "stage_id": nd["stage_id"],
                "deadline": nd["deadline"],
                "creator_id": creator_id,
                "remark": nd.get("remark", ""),
            }
            r = api_post("/project-nodes", payload)
            if r.get("error"):
                failed.append((nd["node_id"], f"API error {r['error']}: {r.get('detail','')[:100]}"))
                continue

            created += 1
            ddl_info = f", ddl={nd['deadline'][:16]}" if nd["deadline"] else ""
            print(f"[OK] {nd['node_id']}: {nd['node_name'][:35]} ({nd['indicator_level']}{ddl_info})")

            # Step 2: 创建成果（如果有）
            for deliv in nd.get("deliverables", []):
                d_result = api_post(f"/project-nodes/{nd['node_id']}/deliverables", {
                    "deliverable_name": deliv["deliverable_name"],
                    "target_amount": deliv["target_amount"],
                    "unit": deliv["unit"],
                    "is_required": deliv.get("is_required", True),
                })
                if d_result.get("error"):
                    print(f"  [DELIV-FAIL] {nd['node_id']}: {d_result.get('detail','')[:80]}")
                else:
                    print(f"  [DELIV] {nd['node_id']}: {deliv['deliverable_name'][:30]}")

        except Exception as e:
            failed.append((nd.get("node_id", "?"), str(e)))
            print(f"[FAIL] {nd.get('node_id', '?')}: {e}")

    # 汇总
    print(f"\n{'='*60}")
    print(f"导入完成：成功 {created}/{len(nodes)}，失败 {len(failed)}")
    if failed:
        print("失败列表：")
        for nid, reason in failed:
            print(f"  - {nid}: {reason}")


if __name__ == "__main__":
    main()
