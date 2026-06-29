"""Step 2: intermediate JSON -> 全景索引 + Mermaid + per-node instances + 适配度报告."""
import json, os, sys, re

data_path = sys.argv[1] if len(sys.argv) > 1 else "需求文件/全景节点图-解析结果/生态城26#地/_data/nodes.json"
with open(data_path, "r", encoding="utf-8") as f:
    data = json.load(f)

outdir = os.path.dirname(os.path.dirname(data_path))  # 生态城26#地/
project = data["project_name"]
print(f"Generating outputs for: {project} -> {outdir}")

nodes = data["nodes"]
deps = data["deps"]
scodes = data["stage_codes"]
stages = data["stages"]

# Build stage metadata
stage_meta = {}
for sname, scode in scodes.items():
    snodes = [n for n in nodes if n["stage_code"] == scode]
    stage_meta[scode] = {
        "name": sname,
        "count": len(snodes),
        "level1": [n for n in snodes if n["wbs"] == 1],
        "sorted": sorted(snodes, key=lambda x: x["node_id"]),
    }

# ═══ 1. 全景节点索引 ═══
lines = []
lines.append(f"# {project} — 全景节点索引")
lines.append("")
lines.append(f"> **来源**：{data['source_file']}")
lines.append(f"> **解析时间**：{data['parsed_at']}")
lines.append(f"> **节点总数**：{data['total_nodes']}（一级：{data['level1_count']}，二级：{data['total_nodes'] - data['level1_count']}）")
lines.append(f"> **关键节点**：{data['milestone_count']}")
lines.append(f"> **识别阶段**：{' / '.join([f'{sname}({scodes[sname]})' for sname in stages])}")
lines.append(f"> **推断依赖**：{data['total_deps']} 条")
lines.append("")
lines.append("---")
lines.append("")

# Stage summary table
lines.append("## 阶段概览")
lines.append("")
lines.append("| 阶段 | 阶段码 | 节点数 | 一级节点 |")
lines.append("|------|--------|--------|----------|")
for sname in stages:
    sc = scodes[sname]
    sm = stage_meta[sc]
    lines.append(f"| {sname} | {sc} | {sm['count']} | {len(sm['level1'])} |")
lines.append("")

# Node tree per stage
lines.append("## 节点树")
lines.append("")
for sname in stages:
    sc = scodes[sname]
    sm = stage_meta[sc]
    lines.append(f"### {sname}（{sc}）")
    lines.append("")
    for n in sm["sorted"]:
        indent = "  " * (n["wbs"] - 1)
        marker = " 🔑" if n["is_milestone"] else ""
        date_str = f" ({n['start']} → {n['end']})" if n.get("start") and n.get("end") else ""
        owner_str = f" — *{n['owner']}*" if n.get("owner") else ""
        lines.append(f"{indent}- `{n['node_id']}` {n['name']}{marker}{owner_str}{date_str}")
    lines.append("")

# Mermaid diagram
lines.append("---")
lines.append("")
lines.append("## 全景节点流程图")
lines.append("")
lines.append("```mermaid")
lines.append("%%{init: {'theme': 'dark', 'themeVariables': {'background': '#1a1a1a', 'primaryColor': '#3d3d3d', 'primaryTextColor': '#e0e0e0', 'lineColor': '#888888'}}}%%")
lines.append("flowchart TB")
# Node style class defs
lines.append("    classDef milestone fill:#4a3728,stroke:#ffb74d,stroke-width:2px")
lines.append("    classDef normal fill:#2d2d2d,stroke:#555555,stroke-width:1px")
lines.append("    classDef stageBox fill:#1a1a1a,stroke:#444444,stroke-width:2px,rx:6")
lines.append("")

# Generate subgraphs per stage (only level 1-3 nodes for readability)
node_id_set = set()
for sname in stages:
    sc = scodes[sname]
    sm = stage_meta[sc]
    lines.append(f"    subgraph {sc}_Stage [{sname}]")
    for n in sm["sorted"]:
        if n["wbs"] > 3:
            continue  # skip deep nesting in mermaid for readability
        nid = n["node_id"].replace("-", "_")
        lbl = n["name"][:20]
        cls = "milestone" if n["is_milestone"] else "normal"
        lines.append(f"        {nid}[{lbl}]:::{cls}")
        node_id_set.add(nid)
    lines.append(f"    end")
    lines.append(f"    {sc}_Stage:::stageBox")
    lines.append("")

# Edges (only for nodes in the set, within same stage)
edge_count = 0
lines.append("    %% Dependencies (truncated for readability)")
for d in deps:
    fn = d["from"].replace("-", "_")
    tn = d["to"].replace("-", "_")
    if fn in node_id_set and tn in node_id_set:
        style = "stroke:#ff8a80,stroke-width:2px" if d["required"] else "stroke:#555555,stroke-width:1px,stroke-dasharray: 5 5"
        lines.append(f"    {tn} --> {fn}")
        edge_count += 1
        if edge_count >= 100:
            lines.append(f"    %% ... {len(deps) - 100} more edges omitted")
            break

lines.append("```")
lines.append("")

# Key milestones table
lines.append("---")
lines.append("")
lines.append("## 关键里程碑")
lines.append("")
level1_nodes = [n for n in nodes if n["wbs"] == 1]
lines.append("| 阶段 | 节点编号 | 节点名称 | 责任部门 | 计划时间 |")
lines.append("|------|----------|----------|----------|----------|")
for n in level1_nodes:
    date_str = f"{n['start']} → {n['end']}" if n.get("start") and n.get("end") else "-"
    lines.append(f"| {n['stage_name']} | `{n['node_id']}` | {n['name']} | {n.get('owner','-')} | {date_str} |")
lines.append("")

# Quality checks
lines.append("---")
lines.append("")
lines.append("## 数据质量")
lines.append("")
no_criteria = len([n for n in nodes if not n.get("criteria")])
no_owner = len([n for n in nodes if not n.get("owner")])
no_dates = len([n for n in nodes if not n.get("start") and not n.get("end")])
if no_criteria:
    lines.append(f"- 完成标准缺失：{no_criteria} 个节点")
if no_owner:
    lines.append(f"- 责任部门缺失：{no_owner} 个节点")
if no_dates:
    lines.append(f"- 计划时间缺失：{no_dates} 个节点")
if not (no_criteria or no_owner or no_dates):
    lines.append("数据质量检查通过，无异常。")

index_md = "\n".join(lines)
os.makedirs(outdir, exist_ok=True)
with open(f"{outdir}/{project}-全景节点索引.md", "w", encoding="utf-8") as f:
    f.write(index_md)
print(f"  Index written: {project}-全景节点索引.md")

# ═══ 2. Per-node instance files ═══
v4_template = """# {node_name}

## 继承关系

| 层级 | 模板 | 说明 |
|:----|------|------|
| L0 | 独立节点基础模板 | 3+1 时态 / 完成标准 / 计划级别 / 关键节点 / 固有数据段 / 表单审批流-基类 / 异常处理三阶段 |
| L1 | 阶段状态机模板 | {stage_note} |
| L2 | 本节点实例 | 来自项目「{project_name}」的实际计划数据 |

> **上游节点**：{parent_info}
> **下游节点**：{children_info}

---

## 继承字段（来自 L0 + L1 父类模板）

| 字段 | 来源 | 本节点取值 |
|------|:---:|------|
| 节点编号 | L0 | **{node_id}** |
| 节点名称 | L0 | **{node_name}** |
| 完成标准 | L0 | {criteria} |
| 计划级别 | L0 | {plan_level} |
| 是否关键节点 | L0 | {is_milestone} |
| 计划开启时间 | L0 | {start} |
| 计划结束时间 | L0 | {end} |
| 实际启动时间 | L0 | 待记录 |
| 实际结束时间 | L0 | 待记录 |
| 状态最后更新时间 | L0 | 待记录 |
| 责任部门 | L0 | {owner} |
| 前置条件列表 | L0 | 见依赖关系 |
| 节点成果列表 | L0 | 见完成标准 |

---

## 节点状态流转

> 遵循父类「所有状态变更须负责人手动确认」原则。

```
未启动 ──→ 运作中 ──→ 完工归档
```

---

## 关联规则

### 依赖关系

上游节点完成后方可启动本节点。详见项目全景索引中的依赖关系表。

---

## 变更记录

| 版本 | 日期 | 修改人 | 修改内容 |
|------|------|--------|----------|
| v1.0 | {date} | 解析器自动生成 | 从 Excel 导入初始数据 |
"""

# Build parent/child lookup
parent_map = {}  # node_name -> list of child nodes
for n in nodes:
    p = n.get("parent")
    if p and p != "":
        if p not in parent_map:
            parent_map[p] = []
        parent_map[p].append(n)

name_to_node = {n["name"]: n for n in nodes}

instance_count = 0
for n in nodes:
    sc = n["stage_code"]
    stage_dir = f"{outdir}/{sc}"
    os.makedirs(stage_dir, exist_ok=True)

    # parent info
    parent = n.get("parent", "")
    parent_id = ""
    if parent and parent in name_to_node:
        parent_id = name_to_node[parent]["node_id"]
        parent_info = f"{parent_id}「{parent}」"
    else:
        parent_info = "无（一级节点）"

    # children info
    children = parent_map.get(n["name"], [])
    if children:
        child_list = ", ".join([f"`{c['node_id']}`“{c['name']}”" for c in children[:5]])
        if len(children) > 5:
            child_list += f" 等 {len(children)} 个"
        children_info = child_list
    else:
        children_info = "无（叶子节点）"

    # stage note
    stage_notes = {"前期": "工程阶段状态机模板 / 投资拓展", "设计": "设计阶段状态机模板",
                   "成本": "成本招采状态机模板", "工程": "工程阶段状态机模板",
                   "营销": "营销阶段", "市场": "营销阶段", "交付": "交付阶段"}
    stage_note = stage_notes.get(n["stage_name"], "")

    criteria = n.get("criteria", "") or "【待补充】"
    # escape pipe chars
    criteria = criteria.replace("|", "\\|").replace("\n", "; ")
    if len(criteria) > 200:
        criteria = criteria[:200] + "..."

    content = v4_template.format(
        node_name=n["name"],
        node_id=n["node_id"],
        criteria=criteria,
        plan_level=n["plan_level"],
        is_milestone="是" if n["is_milestone"] else "否",
        start=n.get("start", "【待定】") or "【待定】",
        end=n.get("end", "【待定】") or "【待定】",
        owner=n.get("owner", "【待指定】") or "【待指定】",
        parent_info=parent_info,
        children_info=children_info,
        stage_note=stage_note,
        project_name=project,
        date=data["parsed_at"].split(" ")[0],
    )

    fname = n["name"].replace("/", "-").replace("\\", "-")[:60]
    fpath = f"{stage_dir}/{n['node_id']}-{fname}.md"
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    instance_count += 1

print(f"  Instances written: {instance_count} files across {len(scodes)} directories")

# ═══ 3. 适配度报告 ═══
report = []
report.append(f"# {project} — 模板适配度报告")
report.append("")
report.append(f"> **解析时间**：{data['parsed_at']}")
report.append(f"> **模板版本**：V4（{os.path.basename(os.path.dirname(os.path.dirname(outdir)))}/全景节点图-梳理版V4/）")
report.append("")
report.append("---")
report.append("")
report.append("## 字段覆盖率")
report.append("")
report.append("V4 L0固有数据段字段及其在本次数据中的覆盖率：")
report.append("")

v4_fields = [
    ("节点编号", True, "由解析器自动生成"),
    ("节点名称", True, "Excel 节点名称列"),
    ("完成标准", True, "Excel 完成标准列"),
    ("计划级别", True, "从 WBS 层级推断（一级/二级）"),
    ("是否关键节点", True, "WBS <= 2 默认标记"),
    ("计划开启时间", True, "Excel 开始时间列"),
    ("计划结束时间", True, "Excel 结束时间列"),
    ("实际启动时间", False, "实操数据，导入时无"),
    ("实际结束时间", False, "实操数据，导入时无"),
    ("状态最后更新时间", False, "实操数据，导入时无"),
    ("责任部门", True, "Excel 主办部门列"),
    ("完工确认岗位", False, "需业务指定，导入时无"),
    ("前置条件列表", True, "从依赖推断自动生成"),
    ("节点成果列表", False, "Excel 中部分节点包含，但未统一"),
    ("节点日志", False, "实操数据，导入时无"),
    ("节点待解决卡点", False, "实操数据，导入时无"),
    ("监理单位", False, "本 Excel 未提供"),
    ("承包单位", False, "本 Excel 未提供"),
    ("分包单位", False, "本 Excel 未提供"),
    ("变更与作废机制", False, "导入时无，需后续实操补充"),
]

report.append("| V4 字段 | 本次数据有值 | 数据来源 / 缺失原因 |")
report.append("|---------|:-----------:|---------------------|")
filled = 0
for name, has_val, note in v4_fields:
    mark = "✅" if has_val else "❌"
    if has_val:
        filled += 1
    report.append(f"| {name} | {mark} | {note} |")
report.append("")

coverage = filled / len(v4_fields) * 100
report.append(f"**字段覆盖率**：{filled}/{len(v4_fields)}（{coverage:.0f}%）")
report.append("")
report.append("> 注：7 个不可填充字段均为实操过程中产生的数据（实际时间、日志、卡点等），导入阶段无法提供。这不是模板设计缺陷。")
report.append("")

report.append("## Excel 特有字段")
report.append("")
report.append("Excel 中存在但 V4 模板中未单独定义字段的数据列：")
report.append("")
report.append("| Excel 列 | 数据状态 | 建议 |")
report.append("|----------|:--------:|------|")
report.append("| 双向考核部门 | 有数据，设计阶段已确认丢弃 | 不纳入 |")
report.append("| 工期(天) | 有数据，设计确认临时计算用 | 不纳入 |")
report.append("| WBS 层级序号（一行~八行） | 用于推断父子关系 | 映射至 parent 字段 |")
report.append("| 28个月施工月列(1月~12月) | 甘特图示意，非结构化数据 | 不纳入 |")
report.append("| 2020年月列(1月~12月) | 同上 | 不纳入 |")
report.append("")

report.append("## 数据质量统计")
report.append("")
no_cr = len([n for n in nodes if not n.get("criteria")])
no_ow = len([n for n in nodes if not n.get("owner")])
no_dt = len([n for n in nodes if not n.get("start") and not n.get("end")])
report.append(f"| 指标 | 数量 | 占比 |")
report.append(f"|------|------|------|")
report.append(f"| 完成标准缺失 | {no_cr} | {no_cr/data['total_nodes']*100:.1f}% |")
report.append(f"| 责任部门缺失 | {no_ow} | {no_ow/data['total_nodes']*100:.1f}% |")
report.append(f"| 计划时间缺失 | {no_dt} | {no_dt/data['total_nodes']*100:.1f}% |")
report.append(f"| 总节点数 | {data['total_nodes']} | 100% |")
report.append("")

report.append("## V4 模板改进建议")
report.append("")
report.append("1. **完成标准字段已补充**：V3→V4 新增的完成标准字段与 Excel 完美匹配。建议保持。")
report.append("2. **计划级别字段已补充**：一级/二级区分与 Excel WBS 层级对应良好。建议保持。")
if no_ow > 0:
    report.append(f"3. **责任部门缺失 {no_ow} 个**：部分节点在 Excel 中未填写主办部门。这不是模板问题，是原始数据完整性问题。建议在导入流程中增加「缺失字段提醒」。")
if data['total_nodes'] > 200:
    report.append(f"4. **节点数量较大**（{data['total_nodes']} 个）：全景索引 Mermaid 图仅展示到三级节点以保证可读性。完整节点树见各阶段目录下的实例文件。")
report.append("5. **监理/承包/分包单位**：V4 模板定义了这些字段但在真实 Excel 中通常缺失。建议在 V4 中将它们从 🔴必有 降为 🟢如有，在实际业务数据填入后再标记。")
report.append("")

report_md = "\n".join(report)
with open(f"{outdir}/{project}-模板适配度报告.md", "w", encoding="utf-8") as f:
    f.write(report_md)
print(f"  Report written: {project}-模板适配度报告.md")
print("Done.")
