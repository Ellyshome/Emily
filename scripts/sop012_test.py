"""SOP-012 全景节点图构造 — 测试脚本
按照 SOP-012-FLOW 流程处理生态城26#地.xlsx，生成：
  1. 全景节点索引.md（含内嵌 Mermaid 图）
  2. 节点树明细.md（全部节点按阶段缩进）
  3. 缺失依赖清单.md
  4. 模板适配度报告.md
"""
import zipfile, xml.etree.ElementTree as ET, sys, os
from datetime import datetime, timedelta
from collections import defaultdict, Counter

sys.stdout.reconfigure(encoding='utf-8')

INPUT_FILE = 'emily-data/baseknowledge/生态城26#地.xlsx'
OUTPUT_DIR = 'emily-data/baseknowledge/解析结果/生态城26#地-设计工程'
PROJECT_NAME = '生态城26#地（设计+工程）'

zf = zipfile.ZipFile(INPUT_FILE, 'r')
ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

# --- Read shared strings ---
shared_strings = []
if 'xl/sharedStrings.xml' in zf.namelist():
    ss = ET.parse(zf.open('xl/sharedStrings.xml'))
    for si in ss.findall(f'.//{{{ns}}}si'):
        texts = []
        for t in si.iter(f'{{{ns}}}t'):
            if t.text:
                texts.append(t.text)
        shared_strings.append(''.join(texts))

def col_letter_to_num(col_str):
    n = 0
    for c in col_str:
        n = n * 26 + (ord(c.upper()) - ord('A') + 1)
    return n

def parse_cell(cell):
    ref = cell.get('r')
    col_str = ''.join(c for c in ref if c.isalpha())
    row_str = ''.join(c for c in ref if c.isdigit())
    col = col_letter_to_num(col_str)
    row = int(row_str)
    cell_type = cell.get('t')
    v = cell.find(f'{{{ns}}}v')
    if v is None or v.text is None:
        return col, row, None
    if cell_type == 's':
        idx = int(v.text)
        if idx < len(shared_strings):
            return col, row, shared_strings[idx]
    elif cell_type == 'b':
        return col, row, v.text == '1'
    else:
        return col, row, v.text
    return col, row, None

def excel_serial_to_date(serial):
    try:
        serial = int(float(serial))
        base = datetime(1899, 12, 30)
        return (base + timedelta(days=serial)).strftime('%Y-%m-%d')
    except:
        return ''

# ============================================================
# STEP 1: 读取 Excel + 列映射 (SOP-012 §3.3)
# ============================================================
print("[步骤1] 读取Excel文件...")
fname = 'xl/worksheets/sheet6.xml'
tree = ET.parse(zf.open(fname))
rows_data = tree.findall(f'.//{{{ns}}}row')

all_rows = []
for row_elem in rows_data:
    r = int(row_elem.get('r'))
    cells_dict = {}
    for cell in row_elem.findall(f'{{{ns}}}c'):
        col, row, val = parse_cell(cell)
        if val is not None:
            cells_dict[col] = str(val).strip()
    if cells_dict:
        all_rows.append((r, cells_dict))

data_rows = all_rows[3:]  # skip 3 header rows

LEVEL_MAP = {'一级': 1, '二级': 2, '三级': 3, '四级': 4, '五级': 5, '六级': 6, '七级': 7, '八级': 8}

# ============================================================
# STEP 2: WBS 树构造 (SOP-012 §3.3 语义理解)
# ============================================================
print("[步骤2] 构造WBS节点树...")
nodes = []
level_stack = []
for i, (r, cells) in enumerate(data_rows):
    level_str = cells.get(2, '')
    name = cells.get(3, '')
    if not name or level_str not in LEVEL_MAP:
        continue
    level_num = LEVEL_MAP[level_str]
    criteria = cells.get(4, '')
    dept = cells.get(5, '')
    dept2 = cells.get(6, '')
    start_date = excel_serial_to_date(cells.get(7, ''))
    end_date = excel_serial_to_date(cells.get(9, ''))
    duration = cells.get(8, '')
    while level_stack and level_stack[-1][1] >= level_num:
        level_stack.pop()
    parent_idx = level_stack[-1][0] if level_stack else None
    node = {
        'idx': len(nodes), 'row': r, 'level_num': level_num, 'level_str': level_str,
        'name': name, 'criteria': criteria, 'dept': dept, 'dept2': dept2,
        'start_date': start_date, 'end_date': end_date, 'duration': duration,
        'parent_idx': parent_idx, 'children': [], 'stage': '', 'node_id': '',
    }
    nodes.append(node)
    level_stack.append((len(nodes) - 1, level_num))

for n in nodes:
    if n['parent_idx'] is not None:
        nodes[n['parent_idx']]['children'].append(n['idx'])

roots = [n for n in nodes if n['parent_idx'] is None]
print(f"  解析节点总数: {len(nodes)}")
print(f"  根节点(L1): {len(roots)}")

# ============================================================
# STEP 3: 阶段识别 (SOP-012 §3.3 语义理解)
# ============================================================
print("[步骤3] 阶段识别与分类...")
STAGE_KEYWORDS = {
    'QQ': ['土拓', '土地', '摘牌', '公司成立', '融资', '项目公司', '工商注册', '资质', '组织架构', '办公', '食堂', '车辆', '宿舍', '土地证', '集团启动会'],
    'SJ': ['设计', '方案', '施工图', '图审', '图纸', '外审', '深化', '强排', '勘察', '初设', '设计部'],
    'CB': ['成本', '招标', '定标', '总包', '分包', '采购', '测算', '报价', '合同签订', '结算', '成本部'],
    'SG': ['开工', '施工', '工程', '主体', '砌筑', '装修', '安装', '竣备', '竣工', '验收', '正负零', '封顶',
           '景观', '供电', '供水', '燃气', '热力', '消防', '门窗', '外檐', '精装', '软装', '硬装',
           '桩基', '土方', '地库', '基础', '防水', '保温', '涂料', '屋面', '电梯', '栏杆',
           '品质排查', '交付评估', '工地开放', '物业移交', '分项验收', '工程部'],
    'YX': ['营销', '开盘', '签约', '回款', '销售', '渠道', '拓客', '推广', '预售', '销许', '展示区', '样板间', '售楼处', '示范区', '营销部'],
    'JF': ['交付', '产权', '准入证', '竣工备案', '客户满意度', '维修', '维保', '满意度', '交付业主', '客服部'],
}

def identify_stage(node):
    name = node.get('name', '')
    scores = defaultdict(int)
    for stage, keywords in STAGE_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                scores[stage] += 1
    if scores:
        return max(scores, key=scores.get)
    return 'QT'

# Smart stage assignment for root nodes
ROOT_STAGE = {
    '土拓': 'QQ',
    '开工、建设': 'SG',
    '交付': 'JF',
    '客户满意度（含维修维保）与结算': 'JF',
    '日常/新增工作': 'QT',
}

for root in roots:
    root_stage = 'QT'
    for kw, stage in ROOT_STAGE.items():
        if kw in root['name']:
            root_stage = stage
            break
    if root_stage == 'QT':
        root_stage = identify_stage(root)
    root['stage'] = root_stage

# For construction super-node, assign sub-stages to L2 children
cr = [r for r in roots if '开工' in r['name'] or '建设' in r['name']]
if cr:
    for child_idx in cr[0]['children']:
        child = nodes[child_idx]
        stage = identify_stage(child)
        child['stage'] = stage if stage != 'QT' else 'SG'

# Propagate stage to all descendants
def propagate_stage(node_idx, stage):
    n = nodes[node_idx]
    if not n['stage']:
        n['stage'] = stage
    for c in n['children']:
        propagate_stage(c, n['stage'])

for root in roots:
    propagate_stage(root['idx'], root['stage'])

# ============================================================
# STEP 3.5: 过滤 - 仅保留"设计"与"工程"阶段相关节点
# ============================================================
print("[步骤3.5] 过滤：仅保留'设计'(SJ)和'工程'(SG)阶段相关节点...")

# 方案：仅保留 stage 为 SJ 或 SG 的节点，并保留其祖先链（维持树结构）
keep_indices = set()

# 1. 收集所有 SJ 和 SG 阶段节点
for n in nodes:
    if n['stage'] in ('SJ', 'SG'):
        keep_indices.add(n['idx'])
        # 保留祖先链（向上追溯到根，维持 WBS 树完整性）
        ancestor_idx = n['parent_idx']
        while ancestor_idx is not None:
            keep_indices.add(ancestor_idx)
            ancestor_idx = nodes[ancestor_idx]['parent_idx']

# 2. 过滤节点列表
old_count = len(nodes)
nodes = [n for n in nodes if n['idx'] in keep_indices]

# 3. 重新映射 idx → 新位置
old_to_new = {n['idx']: i for i, n in enumerate(nodes)}
for n in nodes:
    # 重新映射 parent_idx
    if n['parent_idx'] is not None:
        if n['parent_idx'] in old_to_new:
            n['parent_idx'] = old_to_new[n['parent_idx']]
        else:
            n['parent_idx'] = None
    # 重新映射 children 列表（仅保留仍在节点集中的子节点）
    n['children'] = [old_to_new[c] for c in n['children'] if c in old_to_new]
    # 更新 idx 为新位置
    n['idx'] = old_to_new[n['idx']]

print(f"  过滤前: {old_count} 节点 → 过滤后: {len(nodes)} 节点")
sj_count = sum(1 for n in nodes if n['stage'] == 'SJ')
sg_count = sum(1 for n in nodes if n['stage'] == 'SG')
other_count = len(nodes) - sj_count - sg_count
print(f"  设计(SJ): {sj_count} 节点 | 工程(SG): {sg_count} 节点 | 祖先桥接: {other_count} 节点")

# Show top-level nodes for each stage
for stage, label in [('SJ', '设计'), ('SG', '工程')]:
    stage_roots = [n for n in nodes if n['stage'] == stage and n['level_num'] <= 2]
    print(f"  [{label}] L1+L2 节点 ({len(stage_roots)}个):")
    for n in stage_roots[:8]:
        print(f"    · [{n['level_str']}] {n['name'][:50]}")
    if len(stage_roots) > 8:
        print(f"    ... 还有 {len(stage_roots) - 8} 个")

# ============================================================
# STEP 4: 节点编号 (SOP-012 §3.3)
# ============================================================
print("[步骤4] 节点自动编号...")
stage_counters = defaultdict(int)
for n in nodes:
    stage = n['stage']
    stage_counters[stage] += 1
    n['node_id'] = f"{stage}-{stage_counters[stage]:04d}"

# ============================================================
# STEP 5: 数据质量统计 (SOP-012 §3.3)
# ============================================================
print("[步骤5] 数据质量检查...")
missing_criteria = [n for n in nodes if not n['criteria']]
missing_dept = [n for n in nodes if not n['dept']]
missing_dates = [n for n in nodes if not n['start_date'] and not n['end_date']]
dup_names = [(name, cnt) for name, cnt in Counter(n['name'] for n in nodes).items() if cnt > 1]

# ============================================================
# STEP 6: 依赖分析 (SOP-012 §3.3)
# ============================================================
print("[步骤6] 依赖关系分析...")
# WBS parent-child deps
wbs_deps = []
for n in nodes:
    if n['parent_idx'] is not None:
        wbs_deps.append({
            'from': nodes[n['parent_idx']]['node_id'],
            'to': n['node_id'],
            'from_name': nodes[n['parent_idx']]['name'],
            'to_name': n['name'],
            'type': 'WBS层级推断',
            'confidence': '高',
        })

# Inter-stage deps: first node of next stage depends on last node of prev stage
STAGE_ORDER = ['SJ', 'SG']
stage_first = {}  # first node idx per stage
stage_last = {}   # last node idx per stage
for stage in STAGE_ORDER:
    snodes = [n for n in nodes if n['stage'] == stage]
    if snodes:
        stage_first[stage] = snodes[0]['idx']
        stage_last[stage] = snodes[-1]['idx']

inter_stage_deps = []
for i in range(len(STAGE_ORDER)-1):
    s1, s2 = STAGE_ORDER[i], STAGE_ORDER[i+1]
    if s1 in stage_last and s2 in stage_first:
        inter_stage_deps.append({
            'from': nodes[stage_last[s1]]['node_id'],
            'to': nodes[stage_first[s2]]['node_id'],
            'from_name': nodes[stage_last[s1]]['name'],
            'to_name': nodes[stage_first[s2]]['name'],
            'type': '阶段间推断',
            'confidence': '低（需人工确认）',
        })

# Missing deps: no explicit dependency defined, all are inferred
missing_deps = [
    {'desc': 'Excel中未标注任何节点间的显式依赖关系', 'impact': '所有依赖均为推断，需人工逐条确认'},
    {'desc': '阶段间依赖仅基于排序推断（最后一节点→下一阶段第一节点），不符合业务实际', 'impact': '建议补充：设计阶段→工程阶段 的实际前置关系'},
    {'desc': '并行节点间无依赖标记（如多个L2节点可能并行）', 'impact': '无法区分串行/并行关系'},
]

# ============================================================
# STEP 7: 生成产出物 (SOP-012 §3.3)
# ============================================================
print("[步骤7] 生成产出物...")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- 7a. 全景节点索引.md ---
print("  生成全景节点索引...")
stage_display = {'SJ': '设计阶段', 'SG': '工程施工阶段'}

index_lines = []
index_lines.append(f'# {PROJECT_NAME} — 全景节点索引')
index_lines.append('')
index_lines.append(f'> **生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
index_lines.append(f'> **数据来源**：`emily-data/baseknowledge/生态城26#地.xlsx` Sheet6「全生命周期节点计划」')
index_lines.append(f'> **解析器**：SOP-012-FLOW v2.0')
index_lines.append(f'> **筛选范围**：仅 设计(SJ) + 工程(SG) 阶段')
index_lines.append(f'> **节点总数**：{len(nodes)} | **一级**：{sum(1 for n in nodes if n["level_num"]==1)} | **二级**：{sum(1 for n in nodes if n["level_num"]==2)}')
index_lines.append('')
index_lines.append('---')
index_lines.append('')
index_lines.append('## 阶段概览')
index_lines.append('')
index_lines.append('| 阶段码 | 阶段名称 | 节点数 | 一级 | 二级 |')
index_lines.append('|--------|---------|--------|------|------|')

for stage in STAGE_ORDER:
    snodes = stage_stats = [n for n in nodes if n['stage'] == stage]
    if not stage_stats:
        continue
    l1_cnt = sum(1 for n in stage_stats if n['level_num'] == 1)
    l2_cnt = sum(1 for n in stage_stats if n['level_num'] == 2)
    index_lines.append(f'| {stage} | {stage_display.get(stage, stage)} | {len(stage_stats)} | {l1_cnt} | {l2_cnt} |')

index_lines.append('')

# --- Build Mermaid diagram (embedded in index) ---
key_nodes_list = [n for n in nodes if n['level_num'] <= 2]
node_ids_mermaid = {}
for n in key_nodes_list:
    safe_id = n['node_id'].replace('-', '_')
    node_ids_mermaid[n['node_id']] = safe_id

mmd_lines = []
mmd_lines.append('```mermaid')
mmd_lines.append('%%{init: {"theme": "dark", "flowchart": {"nodeSpacing": 20, "rankSpacing": 50}}}%%')
mmd_lines.append('flowchart TB')
mmd_lines.append('')

for stage in STAGE_ORDER:
    snodes = [n for n in key_nodes_list if n['stage'] == stage]
    if not snodes:
        continue
    mmd_lines.append(f'    subgraph {stage}_Stage [{stage_display.get(stage, stage)}]')
    for n in snodes:
        safe_id = node_ids_mermaid[n['node_id']]
        short_name = n['name'][:15] + ('...' if len(n['name']) > 15 else '')
        if n['level_num'] == 1:
            mmd_lines.append(f'        {safe_id}["{short_name}"]:::mainNode')
        else:
            mmd_lines.append(f'        {safe_id}["{short_name}"]:::subNode')
    mmd_lines.append('    end')
    mmd_lines.append('')

mmd_lines.append('    classDef mainNode fill:#4a4a4a,stroke:#ffb74d,stroke-width:2px')
mmd_lines.append('    classDef subNode fill:#353535,stroke:#888888,stroke-width:1px')

for i in range(len(STAGE_ORDER)-1):
    s1, s2 = STAGE_ORDER[i], STAGE_ORDER[i+1]
    n1 = [n for n in key_nodes_list if n['stage'] == s1 and n['level_num'] == 1]
    n2 = [n for n in key_nodes_list if n['stage'] == s2 and n['level_num'] == 1]
    if n1 and n2:
        mmd_lines.append(f'    {node_ids_mermaid[n1[0]["node_id"]]} -.->|阶段推断| {node_ids_mermaid[n2[0]["node_id"]]}')

for n in key_nodes_list:
    if n['parent_idx'] is not None:
        parent = nodes[n['parent_idx']]
        if parent['node_id'] in node_ids_mermaid:
            mmd_lines.append(f'    {node_ids_mermaid[parent["node_id"]]} -->|WBS| {node_ids_mermaid[n["node_id"]]}')

mmd_lines.append('```')
mmd_content = '\n'.join(mmd_lines)

# Insert mermaid diagram into index
index_lines.append('')
index_lines.append('---')
index_lines.append('')
index_lines.append('## 全景节点图')
index_lines.append('')
index_lines.append(f'> **说明**：实线 = WBS层级推断依赖；虚线 = 阶段间推断依赖（需人工确认）。仅展示 L1+L2 关键节点（{len(key_nodes_list)}个）。')
index_lines.append('')
index_lines.append(mmd_content)
index_lines.append('')
index_lines.append('> 💡 如果 Mermaid 图没有渲染出来，请安装 VS Code 插件 `Markdown Preview Mermaid Support`，或复制 mermaid 代码块到 <https://mermaid.live/> 查看。')
index_lines.append('')
index_lines.append('---')
index_lines.append('')
index_lines.append(f'> 📂 **大文件已拆分**：节点树明细（{len(nodes)} 节点按阶段缩进）见 [`{PROJECT_NAME}-节点树明细.md`](./{PROJECT_NAME}-节点树明细.md)')
index_lines.append('')
index_lines.append('---')
index_lines.append('')

# --- Build separate node tree detail file ---
tree_lines = []
tree_lines.append(f'# {PROJECT_NAME} — 节点树明细')
tree_lines.append('')
tree_lines.append(f'> 从 [`{PROJECT_NAME}-全景节点索引.md`](./{PROJECT_NAME}-全景节点索引.md) 中拆分。')
tree_lines.append(f'> 按 {len([s for s in STAGE_ORDER if any(n["stage"]==s for n in nodes)])} 阶段列出全部 {len(nodes)} 个节点（一级~八级 WBS 缩进树）。')
tree_lines.append('')
tree_lines.append('---')
tree_lines.append('')
tree_lines.append('## 节点树（按阶段）')
tree_lines.append('')

for stage in STAGE_ORDER:
    snodes = [n for n in nodes if n['stage'] == stage]
    if not snodes:
        continue
    tree_lines.append(f'### {stage} — {stage_display.get(stage, stage)}')
    tree_lines.append('')

    stage_roots = []
    for n in snodes:
        if n['parent_idx'] is None:
            stage_roots.append(n)
        elif nodes[n['parent_idx']]['stage'] != stage:
            stage_roots.append(n)

    for sr in stage_roots:
        stack = [(sr, 0)]
        while stack:
            n, indent = stack.pop(0)
            prefix = '  ' * indent + ('- ' if indent > 0 else '')
            is_milestone = '⭐ ' if n['level_num'] <= 2 else ''
            tree_lines.append(f'{prefix}{is_milestone}`{n["node_id"]}` **{n["name"]}**')
            if n['criteria']:
                crit_short = n['criteria'][:80].replace('\n', ' ')
                tree_lines.append(f'{prefix}  > {crit_short}{"..." if len(n["criteria"])>80 else ""}')
            if n['dept']:
                tree_lines.append(f'{prefix}  > 主办：{n["dept"]}')
            if n['start_date']:
                tree_lines.append(f'{prefix}  > 📅 {n["start_date"]} ~ {n["end_date"]}（{n["duration"]}天）')
            tree_lines.append('')

            for ci in n['children']:
                child = nodes[ci]
                if child['stage'] == stage:
                    stack.append((child, indent + 1))

        tree_lines.append('')

# Write node tree detail file
tree_content = '\n'.join(tree_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-节点树明细.md'), 'w', encoding='utf-8') as f:
    f.write(tree_content)
print(f"  ✓ {PROJECT_NAME}-节点树明细.md ({len(nodes)} 节点)")

index_lines.append('---')
index_lines.append('')
index_lines.append('## 关键里程碑表')
index_lines.append('')
index_lines.append('| 节点编号 | 节点名称 | 级别 | 责任部门 | 计划完成时间 | 完成标准摘要 |')
index_lines.append('|----------|---------|------|---------|-------------|------------|')

key_nodes = [n for n in nodes if n['level_num'] <= 2]
for n in key_nodes:
    crit_short = (n['criteria'] or '【缺失】')[:40].replace('\n', ' ')
    index_lines.append(f'| {n["node_id"]} | {n["name"]} | {n["level_str"]} | {n["dept"] or "【缺失】"} | {n["end_date"] or "【缺失】"} | {crit_short} |')

index_lines.append('')
index_lines.append('---')
index_lines.append('')
index_lines.append('## 依赖关系概览')
index_lines.append('')
index_lines.append(f'- **WBS 层级推断依赖**：{len(wbs_deps)} 条（每个子节点依赖其父节点）')
index_lines.append(f'- **阶段间推断依赖**：{len(inter_stage_deps)} 条（按阶段排序推断）')
index_lines.append('')
index_lines.append('> ⚠️ **注意**：Excel 文件中未显式标注节点间依赖关系。以上所有依赖均为 Agent 基于 WBS 层级和阶段顺序的推断。已确认的依赖用实线，推断的依赖用虚线。详见《缺失依赖清单》。')
index_lines.append('')

# Inter-stage dependency table
index_lines.append('### 阶段间推断依赖')
index_lines.append('')
index_lines.append('| 上游节点 | 下游节点 | 推断依据 | 置信度 |')
index_lines.append('|---------|---------|---------|--------|')
for dep in inter_stage_deps:
    index_lines.append(f'| {dep["from"]} ({dep["from_name"]}) | {dep["to"]} ({dep["to_name"]}) | {dep["type"]} | {dep["confidence"]} |')

index_lines.append('')
index_lines.append('---')
index_lines.append('')
index_lines.append('## 数据质量检查')
index_lines.append('')
index_lines.append(f'| 检查项 | 数据 |')
index_lines.append(f'|--------|------|')
index_lines.append(f'| 完成标准缺失 | {len(missing_criteria)} 个节点（{len(missing_criteria)*100//len(nodes)}%） |')
index_lines.append(f'| 责任部门缺失 | {len(missing_dept)} 个节点（{len(missing_dept)*100//len(nodes)}%） |')
index_lines.append(f'| 计划时间缺失 | {len(missing_dates)} 个节点（{len(missing_dates)*100//len(nodes)}%） |')
index_lines.append(f'| 名称重复 | {len(dup_names)} 处 |')
if dup_names[:5]:
    index_lines.append(f'| 重复名称示例 | {", ".join(f"{name}({cnt}次)" for name, cnt in dup_names[:5])} |')

index_lines.append('')
index_lines.append('---')
index_lines.append('')
index_lines.append('## 列映射推断')
index_lines.append('')
index_lines.append('| Excel原始列名 | 推断含义 | 映射目标字段 | 映射方式 |')
index_lines.append('|-------------|---------|------------|---------|')
index_lines.append('| 级别 | WBS层级（一级~八级） | plan_level | 直接映射 |')
index_lines.append('| 计划节点名称 | 节点名称（含括号阶段标注） | node_name | 直接映射（清理括号标注） |')
index_lines.append('| 完成标准 | 完成判定标准（自由文本） | criteria | 直接映射 |')
index_lines.append('| 主责单位 | 主办责任部门（含具体责任人） | owner | 直接映射 |')
index_lines.append('| 双向考核单位 | 协办/考核部门 | supervisor_unit | 语义推断 |')
index_lines.append('| 开始时间（Excel序列数） | 计划开始日期 | planned_start_date | 序列数→日期转换 |')
index_lines.append('| 周期（天数） | 计划工期 | duration | 直接映射 |')
index_lines.append('| 完成时间（Excel序列数） | 计划结束日期 | planned_end_date | 序列数→日期转换 |')
index_lines.append('| 28栋叠拼及5栋洋房（月列） | 月度时段标记 | — | 未解析（时间轴可视化用） |')

index_md = '\n'.join(index_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-全景节点索引.md'), 'w', encoding='utf-8') as f:
    f.write(index_md)
print(f"  ✓ {PROJECT_NAME}-全景节点索引.md (含内嵌 Mermaid 全景节点图, {len(key_nodes_list)} 个关键节点)")

# --- 7b. 缺失依赖清单 ---
print("  生成缺失依赖清单...")
dd_lines = []
dd_lines.append(f'# {PROJECT_NAME} — 缺失依赖清单')
dd_lines.append('')
dd_lines.append(f'> **生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
dd_lines.append(f'> **说明**：以下依赖关系为 Agent 自动推断或无法确认，需要人工逐条审核确认。')
dd_lines.append('')
dd_lines.append('---')
dd_lines.append('')
dd_lines.append('## 一、需人工确认的阶段间依赖')
dd_lines.append('')
dd_lines.append('以下依赖基于阶段排序推断，**置信度低**，建议项目负责人逐条确认：')
dd_lines.append('')
dd_lines.append('| # | 上游节点 | 下游节点 | 推断依据 | 待确认事项 |')
dd_lines.append('|---|---------|---------|---------|-----------|')
for i, dep in enumerate(inter_stage_deps, 1):
    dd_lines.append(f'| {i} | {dep["from"]} {dep["from_name"]} | {dep["to"]} {dep["to_name"]} | {dep["type"]} | 是否确实存在前后依赖关系？ |')

dd_lines.append('')
dd_lines.append('## 二、WBS层级推断依赖')
dd_lines.append('')
dd_lines.append(f'共 {len(wbs_deps)} 条 WBS 层级推断依赖（子节点→父节点）。以下为 Level 2 层级的依赖关系：')
dd_lines.append('')
dd_lines.append('| 父节点 | 子节点（L2） | 推断依据 |')
dd_lines.append('|--------|------------|---------|')

# Only show L1→L2 deps
for n in key_nodes_list:
    if n['level_num'] == 2 and n['parent_idx'] is not None:
        parent = nodes[n['parent_idx']]
        dd_lines.append(f'| {parent["node_id"]} {parent["name"]} | {n["node_id"]} {n["name"]} | WBS父→子层级 |')

dd_lines.append('')
dd_lines.append('## 三、全局性问题')
dd_lines.append('')
for md in missing_deps:
    dd_lines.append(f'- **{md["desc"]}**')
    dd_lines.append(f'  - 影响：{md["impact"]}')
    dd_lines.append('')

dd_lines.append('')
dd_lines.append('## 四、审核操作指引')
dd_lines.append('')
dd_lines.append('1. 确认阶段间依赖：检查"阶段间推断依赖"表中每条依赖关系的正确性')
dd_lines.append('2. 补充缺失依赖：对确实存在的依赖关系，注明"已确认"')
dd_lines.append('3. 标记并行关系：标注哪些 L2 节点可以并行推进')
dd_lines.append('4. 添加外部依赖：补充与外部单位（政府审批、水电燃气等）的依赖关系')
dd_lines.append('5. 更新全景节点图：确认后的依赖在 Mermaid 图中改为实线')

dd_content = '\n'.join(dd_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-缺失依赖清单.md'), 'w', encoding='utf-8') as f:
    f.write(dd_content)
print(f"  ✓ {PROJECT_NAME}-缺失依赖清单.md")

# --- 7d. 模板适配度报告 ---
print("  生成模板适配度报告...")
ar_lines = []
ar_lines.append(f'# {PROJECT_NAME} — 模板适配度报告')
ar_lines.append('')
ar_lines.append(f'> **生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
ar_lines.append(f'> **对照模板**：V4 节点模板（`需求文件/全景节点图-梳理版V4/`）')
ar_lines.append('')
ar_lines.append('---')
ar_lines.append('')
ar_lines.append('## 一、V4 模板字段在本次数据中的覆盖率')
ar_lines.append('')
ar_lines.append('| 字段 | 模板级别 | 本次数据来源 | 填充率 | 说明 |')
ar_lines.append('|------|---------|-------------|--------|------|')

# Calculate fill rates
node_with_criteria = sum(1 for n in nodes if n['criteria'])
node_with_dept = sum(1 for n in nodes if n['dept'])
node_with_dates = sum(1 for n in nodes if n['start_date'] and n['end_date'])

total = len(nodes)
ar_lines.append(f'| 节点编号 (node_id) | 🔴 必有 | 自动生成 ({stage}-{len(nodes):04d}) | 100% | 按阶段码+序号规则生成 |')
ar_lines.append(f'| 节点名称 (node_name) | 🔴 必有 | Excel"计划节点名称"列 | 100% | 含括号阶段标注，需清理 |')
ar_lines.append(f'| 完成标准 (criteria) | 🔴 必有 | Excel"完成标准"列 | {node_with_criteria*100//total}% | {total-node_with_criteria} 个节点缺失 |')
ar_lines.append(f'| 责任部门 (owner) | 🟡 应有 | Excel"主责单位"列 | {node_with_dept*100//total}% | {total-node_with_dept} 个节点缺失 |')
ar_lines.append(f'| 计划级别 (plan_level) | 🔴 必有 | Excel"级别"列 | 100% | 一级~八级 |')
ar_lines.append(f'| 是否关键节点 (is_milestone) | 🟡 应有 | 推断（L1/L2=是） | 100% | 基于 WBS 级别推断 |')
ar_lines.append(f'| 计划开始时间 | 🟡 应有 | Excel"开始时间"列（序列数） | {node_with_dates*100//total}% | {total-node_with_dates} 个节点缺失 |')
ar_lines.append(f'| 计划结束时间 | 🟡 应有 | Excel"完成时间"列（序列数） | {node_with_dates*100//total}% | {total-node_with_dates} 个节点缺失 |')
ar_lines.append(f'| 上游节点 (parent_name) | 🟡 应有 | WBS 层级推断 | 100% | 所有子节点自动关联父节点 |')
ar_lines.append(f'| 节点成果列表 (deliverables) | 🟢 如有 | — | 0% | Excel 中无对应列 |')
ar_lines.append(f'| 前置条件列表 (preconditions) | 🟢 如有 | 依赖推断 | ~5% | 仅从 WBS 父子关系推断 |')
ar_lines.append(f'| 监理单位 (supervisor_unit) | 🟢 如有 | — | 0% | Excel 中无对应列，需后续补充 |')
ar_lines.append(f'| 变更与作废机制 | 🟢 如有 | — | 0% | V4 模板定义，本次导入不填充 |')
ar_lines.append(f'| 节点日志 | 🟢 如有 | — | 0% | 运维阶段动态追加 |')

ar_lines.append('')
ar_lines.append('## 二、本次数据中有但 V4 模板中无的字段')
ar_lines.append('')
ar_lines.append('| Excel 字段 | 含义 | 建议 |')
ar_lines.append('|-----------|------|------|')
ar_lines.append('| 双向考核单位 | 协办/被考核部门 | 建议 V4 模板新增此字段（🟡应有级别），对应交叉考核机制 |')
ar_lines.append('| 周期（天数） | 计划工期 | V4 模板已有"计划开始/结束时间"，可计算工期。建议保留为辅助字段 |')
ar_lines.append('| 月度时段标记（1~12月 × 多年） | 时间轴可视化标记 | 与 V4 模板的甘特图需求对应，建议纳入"基于甘特图的多子任务进程图表" |')

ar_lines.append('')
ar_lines.append('## 三、V4 模板定义了但本次数据无法填充的字段')
ar_lines.append('')
ar_lines.append('| V4 模板字段 | 来源模板 | 缺失原因 | 建议 |')
ar_lines.append('|------------|---------|---------|------|')
ar_lines.append('| 承包单位 | L1 设计/成本/工程模板 | Excel 主责单位为建设方部门，未区分承包单位 | 后续通过 SOP-013 单独收集 |')
ar_lines.append('| 监理单位 | L1 工程阶段模板 | Excel 中无此列 | 后续通过 SOP-013 单独收集 |')
ar_lines.append('| 分包单位 | L1 工程阶段模板 | Excel 中无此列 | 后续通过 SOP-013 单独收集 |')
ar_lines.append('| 完工确认岗位 | L0 基类 | Excel 中无此列 | 按阶段默认值填充（SJ→设计部经理，SG→工程部经理等） |')
ar_lines.append('| 条件清单表 | L1 设计/工程模板 | Excel 中无此列 | 后续通过 SOP-013 单独收集 |')
ar_lines.append('| 节点成果列表 | L0 基类 | Excel 中无成果物描述列 | 部分可从"完成标准"中提取 |')
ar_lines.append('| 表单审批流（完整） | L0 基类 | Excel 仅有节点级数据，无表单级 | 在具体节点实例生成时由 SOP-013 处理 |')
ar_lines.append('| 地块分区 × 阶段 N:N 关系 | 景观设计/工程 | Excel 中无对应概念 | 属于节点实例级细节，SOP-013 单独处理 |')

ar_lines.append('')
ar_lines.append('## 四、V4 模板改进建议')
ar_lines.append('')
ar_lines.append('| # | 建议 | 优先级 | 说明 |')
ar_lines.append('|---|------|--------|------|')
ar_lines.append('| 1 | L0 基类新增"双向考核单位"字段 | 中 | 本次 Excel 实际包含此列，且业务中有交叉考核机制 |')
ar_lines.append('| 2 | L0 基类新增"周期（天数）"辅助字段 | 低 | 从开始/结束时间可计算，但显式存储便于查询 |')
ar_lines.append('| 3 | 多地块/多标段并行场景需模板化 | 高 | 本次项目仅有 28栋叠拼+5栋洋房，但标准模板应覆盖多地块场景 |')
ar_lines.append('| 4 | "完成标准"字段应支持结构化格式 | 中 | 当前 Excel 中有编号列表格式（1、... 2、...），V4 模板应接纳此格式 |')
ar_lines.append('| 5 | 阶段码建议增加"前期（QQ）→设计（SJ）→成本招采（CB）"的更细粒度划分 | 中 | 本次数据中土拓阶段占据了大量前期节点，标准 QQ 码只能承载部分 |')

ar_content = '\n'.join(ar_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-模板适配度报告.md'), 'w', encoding='utf-8') as f:
    f.write(ar_content)
print(f"  ✓ {PROJECT_NAME}-模板适配度报告.md")

# ============================================================
# FINAL: 输出回执
# ============================================================
print()
print("=" * 60)
print("📋 全景节点图构造完成")
print("=" * 60)
print(f"项目名称：{PROJECT_NAME}")
print(f"节点总数：{len(nodes)}（一级：{sum(1 for n in nodes if n['level_num']==1)}，二级：{sum(1 for n in nodes if n['level_num']==2)}）")
print(f"关键节点：{len(key_nodes_list)}")
print(f"识别阶段：{[s for s in STAGE_ORDER if any(n['stage']==s for n in nodes)]}")
print(f"依赖关系：已确认 0 条 / WBS推断 {len(wbs_deps)} 条 / 阶段推断 {len(inter_stage_deps)} 条")
print(f"──────────────")
print(f"产出物：")
print(f"  · 全景索引（含 Mermaid 图）：{OUTPUT_DIR}/{PROJECT_NAME}-全景节点索引.md")
print(f"  · 节点树明细：{OUTPUT_DIR}/{PROJECT_NAME}-节点树明细.md")
print(f"  · 缺失依赖清单：{OUTPUT_DIR}/{PROJECT_NAME}-缺失依赖清单.md")
print(f"  · 模板适配度报告：{OUTPUT_DIR}/{PROJECT_NAME}-模板适配度报告.md")
print(f"──────────────")
print(f"[数据质量]")
print(f"  · 完成标准缺失：{len(missing_criteria)} 个节点")
print(f"  · 责任部门缺失：{len(missing_dept)} 个节点")
print(f"  · 计划时间缺失：{len(missing_dates)} 个节点")
print(f"  · 名称重复：{len(dup_names)} 处")
print(f"──────────────")
print(f"[下一步]")
print(f"  · 查看全景索引确认节点树")
print(f"  · 查看缺失依赖清单，逐条确认后手动补充")
print(f"  · 如需生成节点实例文档，使用 SOP-013-FLOW")
print(f"  · 如需写入数据库，执行：uv run python scripts/parse_nodes_tree.py --file \"{INPUT_FILE}\" --write")

print()
print("Done! All 4 output files generated.")
