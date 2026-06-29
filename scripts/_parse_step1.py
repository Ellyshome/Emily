"""Step 1: Read Excel -> structured JSON intermediate format."""
import pandas as pd, json, os, re, sys
from datetime import datetime, timedelta
from collections import OrderedDict

xls_path = sys.argv[1] if len(sys.argv) > 1 else r"H:\工作经验\全生命周期计划\生态城26#地.xlsx"
xls = pd.ExcelFile(xls_path)
df_sig = pd.read_excel(xls, sheet_name=4, header=None)
df_detail = pd.read_excel(xls, sheet_name=5, header=None)
print(f"Signoff: {df_sig.shape}, Detail: {df_detail.shape}")

STAGE_MAP = {"前期":"QQ","设计":"SJ","成本":"CB","工程":"SG","营销":"YX","市场":"YX","交付":"JF","交付一期":"JF","交付二期":"JF2"}

stages = OrderedDict()
for i in range(len(df_sig)):
    v0 = str(df_sig.iloc[i, 0]) if pd.notna(df_sig.iloc[i, 0]) else ""
    if not v0: continue
    is_sig = any(kw in v0 for kw in ["签","经理","总经","董事","审批","条线","计划"])
    if is_sig or len(v0) >= 10: continue
    for k, code in STAGE_MAP.items():
        if k in v0 and v0 not in stages:
            stages[v0] = {"code": code, "order": len(stages) + 1}
            break
print("Stages:", {s: stages[s]["code"] for s in stages})


def excel_date(val):
    if val is None or (hasattr(val, "__iter__") and pd.isna(val)):
        return ""
    try:
        if hasattr(val, "strftime"):
            return val.strftime("%Y-%m-%d")
        n = float(str(val))
        if n > 30000:
            return (datetime(1899, 12, 30) + timedelta(days=n)).strftime("%Y-%m-%d")
        return str(int(n))
    except Exception:
        return str(val)[:10]


level_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
nodes = []
wbs_stack = [None] * 10
wbs_counters = [0] * 10
stage_name_list = list(stages.keys())
stage_seq = 0
cur_sname = stage_name_list[0] if stage_name_list else "Unknown"
cur_scode = stages[cur_sname]["code"] if cur_sname in stages else "XX"

for i in range(len(df_detail)):
    v1 = str(df_detail.iloc[i, 1]) if pd.notna(df_detail.iloc[i, 1]) else ""
    v2 = str(df_detail.iloc[i, 2]) if pd.notna(df_detail.iloc[i, 2]) else ""
    v3 = str(df_detail.iloc[i, 3]) if pd.notna(df_detail.iloc[i, 3]) else ""
    v4 = str(df_detail.iloc[i, 4]) if pd.notna(df_detail.iloc[i, 4]) else ""
    if not v2 or v2 in ["计划节点名称", "节点名称"]:
        continue

    wbs = 2
    for cn, lv in level_map.items():
        if f"{cn}级" in v1:
            wbs = lv
            break

    import re as _re
    name = _re.sub(r"[（(][^)）]*[)）]\s*$", "", v2).strip()

    if wbs == 1:
        stage_seq += 1
        if stage_seq <= len(stage_name_list):
            cur_sname = stage_name_list[stage_seq - 1]
            cur_scode = stages[cur_sname]["code"]

    wbs_stack[wbs] = name
    for lv in range(wbs + 1, 10):
        wbs_stack[lv] = None
    wbs_counters[wbs] += 1
    for lv in range(wbs + 1, 10):
        wbs_counters[lv] = 0

    parent = None
    for lv in range(wbs - 1, 0, -1):
        if wbs_stack[lv]:
            parent = wbs_stack[lv]
            break

    s = excel_date(df_detail.iloc[i, 6])
    e = excel_date(df_detail.iloc[i, 8])

    nodes.append({
        "wbs": wbs,
        "name": name,
        "criteria": v3.strip(),
        "owner": v4.strip(),
        "parent": parent,
        "stage_name": cur_sname,
        "stage_code": cur_scode,
        "start": s,
        "end": e,
        "is_milestone": wbs <= 2,
        "plan_level": "一级" if wbs == 1 else "二级",
        "row": i,
    })

# Generate node IDs
stage_counters = {}
for n in nodes:
    sc = n["stage_code"]
    stage_counters[sc] = stage_counters.get(sc, 0) + 1
    n["node_id"] = f"{sc}-{stage_counters[sc]:03d}"

# Infer dependencies
deps = []
prev_sib = None
cur_parent = None
for n in nodes:
    if n["parent"] != cur_parent:
        cur_parent = n["parent"]
        prev_sib = None
    if prev_sib and prev_sib["parent"] == cur_parent:
        r = prev_sib["name"][:15] + " -> " + n["name"][:15]
        deps.append({
            "from": n["node_id"],
            "to": prev_sib["node_id"],
            "weight": 0.5,
            "required": False,
            "reason": r,
        })
    prev_sib = n

# Stage-to-stage deps
stages_ordered = []
seen = set()
for n in nodes:
    if n["wbs"] == 1 and n["stage_code"] not in seen:
        stages_ordered.append(n)
        seen.add(n["stage_code"])
for i in range(1, len(stages_ordered)):
    a, b = stages_ordered[i], stages_ordered[i - 1]
    reason = b["name"][:15] + " -> " + a["name"][:15]
    deps.append({
        "from": a["node_id"],
        "to": b["node_id"],
        "weight": 1.0,
        "required": True,
        "reason": reason,
    })

print(f"Parsed: {len(nodes)} nodes, {len(deps)} deps, {len(stages)} stages")

stage_counts = {}
for n in nodes:
    stage_counts[n["stage_code"]] = stage_counts.get(n["stage_code"], 0) + 1
for sname in stages:
    sc = stages[sname]["code"]
    print(f"  {sname} ({sc}): {stage_counts.get(sc, 0)} nodes")

# Save intermediate data
outdir = "需求文件/全景节点图-解析结果/生态城26#地/_data"
os.makedirs(outdir, exist_ok=True)
with open(f"{outdir}/nodes.json", "w", encoding="utf-8") as f:
    json.dump({
        "project_name": "生态城26#地",
        "source_file": os.path.basename(xls_path),
        "parsed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stages": list(stages.keys()),
        "stage_codes": {s: stages[s]["code"] for s in stages},
        "nodes": nodes,
        "deps": deps,
        "stage_counts": stage_counts,
        "total_nodes": len(nodes),
        "total_deps": len(deps),
        "milestone_count": len([n for n in nodes if n["is_milestone"]]),
        "level1_count": len([n for n in nodes if n["wbs"] == 1]),
    }, f, ensure_ascii=False, indent=2)
print("Intermediate data saved to _data/nodes.json")
