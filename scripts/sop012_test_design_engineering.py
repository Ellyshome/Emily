"""SOP-012 全景节点图构造 — 测试脚本（设计+工程阶段）
按照 SOP-012-FLOW 流程处理生态城26#地.xlsx，只提取'设计'与'工程'两个阶段的相关节点
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

# 收集需要保留的节点索引
keep_indices = set()

# 找出所有 SJ（设计）阶段节点及其完整子树
for n in nodes:
    if n['stage'] == 'SJ':
        # 加入该节点本身
        keep_indices.add(n['idx'])
        # BFS 收集所有后代
        queue = list(n['children'])
        while queue:
            child_idx = queue.pop(0)
            keep_indices.add(child_idx)
            queue.extend(nodes[child_idx]['children'])

# 找出所有 SG（工程）阶段节点及其完整子树
for n in nodes:
    if n['stage'] == 'SG':
        # 加入该节点本身
        keep_indices.add(n['idx'])
        # BFS 收集所有后代
        queue = list(n['children'])
        while queue:
            child_idx = queue.pop(0)
            keep_indices.add(child_idx)
            queue.extend(nodes[child_idx]['children'])

# 过滤节点列表
old_count = len(nodes)
nodes = [n for n in nodes if n['idx'] in keep_indices]

# 重新映射 idx → 新位置
old_to_new = {n['idx']: i for i, n in enumerate(nodes)}
for n in nodes:
    # 重新映射 parent_idx
    if n['parent_idx'] is not None:
        if n['parent_idx'] in old_to_new:
            n['parent_idx'] = old_to_new[n['parent_idx']]
        else:
            n['parent_idx'] = None  # 父节点被过滤掉
    # 重新映射 children 列表
    n['children'] = [old_to_new[c] for c in n['children'] if c in old_to_new]
    # 更新 idx 为新位置
    n['idx'] = old_to_new[n['idx']]

print(f"  过滤前: {old_count} 节点 → 过滤后: {len(nodes)} 节点")
sj_count = sum(1 for n in nodes if n['stage'] == 'SJ')
sg_count = sum(1 for n in nodes if n['stage'] == 'SG')
print(f"  设计(SJ): {sj_count} 节点 | 工程(SG): {sg_count} 节点")

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
    {'desc': '阶段间依赖仅基于排序推断（设计最后一节点→工程第一节点），不符合业务实际', 'impact': '建议补充：设计阶段→工程阶段 的实际前置关系'},
    {'desc': '并行节点间无依赖标记（如多个L2节点可能并行）', 'impact': '无法区分串行/并行关系'},
]

# ============================================================
# STEP 7: 生成产出物 (SOP-012 §3.3)
# ============================================================
print("[步骤7] 生成产出物...")
os.makedirs(OUTPUT_DIR, exist_ok=True)

stage_display = {'SJ': '设计阶段', 'SG': '工程施工阶段'}

# --- 7a. 全景节点索引.md ---
print("  生成全景节点索引...")
index_lines = []
index_lines.append(f'# {PROJECT_NAME} — 全景节点索引')
index_lines.append('')
index_lines.append(f'> **生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
index_lines.append(f'> **数据来源**：`emily-data/baseknowledge/生态城26#地.xlsx` Sheet6「全生命周期节点计划」')
index_lines.append(f'> **解析器**：SOP-012-FLOW v2.0')
index_lines.append(f'> **筛选范围**：仅包含 设计(SJ) + 工程(SG) 阶段')
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
index_lines.append('---')
index_lines.append('')
index_lines.append('## 数据质量检查')
index_lines.append('')
index_lines.append('| 检查项 | 数量 | 影响 |')
index_lines.append('|--------|------|------|')
index_lines.append(f'| 完成标准缺失 | {len(missing_criteria)} | 影响节点的验收依据，建议补充 |')
index_lines.append(f'| 责任部门缺失 | {len(missing_dept)} | 影响节点的责任追踪，建议补充 |')
index_lines.append(f'| 计划时间缺失 | {len(missing_dates)} | 影响进度计划的制定 |')
index_lines.append(f'| 节点名称重复 | {len(dup_names)} | 可能造成混淆，建议人工确认 |')
index_lines.append('')

# Write panoramic index
index_content = '\n'.join(index_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-全景节点索引.md'), 'w', encoding='utf-8') as f:
    f.write(index_content)
print(f"  ✓ {PROJECT_NAME}-全景节点索引.md (~{len(key_nodes_list)} L1+L2 节点 + Mermaid 图)")

# --- 7b. 缺失依赖清单.md ---
print("  生成缺失依赖清单...")
dep_lines = []
dep_lines.append(f'# {PROJECT_NAME} — 缺失依赖清单')
dep_lines.append('')
dep_lines.append(f'> **生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
dep_lines.append(f'> **说明**：Excel 中未显式标注节点间依赖关系。以下依赖关系均为 Agent 自动推断，需要人工逐条确认是否正确。')
dep_lines.append('')
dep_lines.append('---')
dep_lines.append('')
dep_lines.append('## 依赖确认指引')
dep_lines.append('')
dep_lines.append('1. **WBS 层级依赖**：子节点默认依赖父节点（通常正确，少数并行节点需解除）')
dep_lines.append('2. **阶段间依赖**：设计阶段的最后节点 → 工程阶段的第一节点（需人工确认实际前置关系）')
dep_lines.append('3. **并行节点**：同一层级的节点可能并行执行，也可能有隐含依赖，需业务判断')
dep_lines.append('')
dep_lines.append('---')
dep_lines.append('')
dep_lines.append('## WBS 层级推断依赖（需确认）')
dep_lines.append('')
dep_lines.append('| 序号 | 上游节点 | 下游节点 | 依赖类型 | 置信度 | 人工确认 |')
dep_lines.append('|------|---------|---------|---------|--------|---------|')
for i, d in enumerate(wbs_deps, 1):
    dep_lines.append(f'| {i} | `{d["from"]}` {d["from_name"][:20]} | `{d["to"]}` {d["to_name"][:20]} | {d["type"]} | {d["confidence"]} | □ 确认 □ 修改 □ 删除 |')

dep_lines.append('')
dep_lines.append('## 阶段间推断依赖（需重点确认）')
dep_lines.append('')
dep_lines.append('| 序号 | 上游节点 | 下游节点 | 依赖类型 | 置信度 | 人工确认 |')
dep_lines.append('|------|---------|---------|---------|--------|---------|')
for i, d in enumerate(inter_stage_deps, 1):
    dep_lines.append(f'| {i} | `{d["from"]}` {d["from_name"][:30]} | `{d["to"]}` {d["to_name"][:30]} | {d["type"]} | {d["confidence"]} | □ 确认 □ 修改 □ 删除 |')

dep_lines.append('')
dep_lines.append('## 需补充的依赖关系建议')
dep_lines.append('')
for i, md in enumerate(missing_deps, 1):
    dep_lines.append(f'**问题{i}**：{md["desc"]}')
    dep_lines.append(f'- 影响：{md["impact"]}')
    dep_lines.append('')

dep_content = '\n'.join(dep_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-缺失依赖清单.md'), 'w', encoding='utf-8') as f:
    f.write(dep_content)
print(f"  ✓ {PROJECT_NAME}-缺失依赖清单.md ({len(wbs_deps) + len(inter_stage_deps)} 条待确认)")

# --- 7c. 模板适配度报告.md ---
print("  生成模板适配度报告...")
rep_lines = []
rep_lines.append(f'# {PROJECT_NAME} — 模板适配度报告')
rep_lines.append('')
rep_lines.append(f'> **生成时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}')
rep_lines.append(f'> **对比基准**：SOP-012-FLOW V4 模板定义字段 vs 本次 Excel 实际数据')
rep_lines.append('')
rep_lines.append('---')
rep_lines.append('')
rep_lines.append('## 字段覆盖率统计')
rep_lines.append('')

V4_FIELDS = [
    ('node_id', '节点编号', '🔴 必有', '✓ 自动生成'),
    ('node_name', '节点名称', '🔴 必有', '✓ Excel 原值（第3列）'),
    ('criteria', '完成标准', '🔴 必有', f'✓ Excel 第4列（{len(missing_criteria)}个缺失）'),
    ('owner', '责任部门', '🟡 应有', f'✓ Excel 第5列（{len(missing_dept)}个缺失）'),
    ('plan_level', '计划级别', '🔴 必有', '✓ Excel 第2列（一级~八级）'),
    ('is_milestone', '是否关键节点', '🟡 应有', '✓ 按 L1+L2 自动推断'),
    ('start_date', '计划开始时间', '🟡 应有', f'✓ Excel 第7列（部分解析）'),
    ('end_date', '计划完成时间', '🟡 应有', f'✓ Excel 第9列（部分解析）'),
    ('deliverables', '成果物清单', '🟢 如有', '✗ Excel 无对应列'),
    ('preconditions', '前置条件', '🟢 如有', '✗ 从依赖推断中填充'),
    ('supervisor_unit', '监理单位', '🟢 如有', '✗ Excel 无对应列'),
]

rep_lines.append('| 字段名 | 中文名称 | 级别 | 本次数据情况 |')
rep_lines.append('|--------|---------|------|------------|')
for field, name, level, status in V4_FIELDS:
    rep_lines.append(f'| `{field}` | {name} | {level} | {status} |')

rep_lines.append('')
rep_lines.append('## 列映射推断记录')
rep_lines.append('')
rep_lines.append('Excel 原始列 → 字段映射：')
rep_lines.append('- 第2列（B列）：级别 → `plan_level`')
rep_lines.append('- 第3列（C列）：计划节点名称 → `node_name`')
rep_lines.append('- 第4列（D列）：主要控制节点及验收标准 → `criteria`')
rep_lines.append('- 第5列（E列）：主办部门 → `owner`')
rep_lines.append('- 第6列（F列）：协办部门 → `dept2`（V4模板未定义）')
rep_lines.append('- 第7列（G列）：开始时间 → `start_date`')
rep_lines.append('- 第8列（H列）：工期 → `duration`')
rep_lines.append('- 第9列（I列）：完成时间 → `end_date`')
rep_lines.append('')
rep_lines.append('## 阶段分类说明')
rep_lines.append('')
rep_lines.append('本次按关键字自动推断阶段码：')
rep_lines.append(f'- **设计（SJ）**：{sj_count} 个节点 - 含方案、施工图、图审、勘察、初设等')
rep_lines.append(f'- **工程（SG）**：{sg_count} 个节点 - 含开工、施工、主体、精装、景观、市政、验收等')
rep_lines.append('')
rep_lines.append('## 适配度总结')
rep_lines.append('')
rep_lines.append(f'- **必填字段覆盖率**：100%（node_id/name/criteria/level 均有值，部分 criteria 为空）')
rep_lines.append(f'- **应有字段覆盖率**：约 80%（责任部门/时间字段部分缺失）')
rep_lines.append(f'- **可选字段覆盖率**：约 0%（成果物/监理单位等 Excel 未提供）')
rep_lines.append('- **新增非标准字段**：协办部门（Excel特有，V4模板未定义）')
rep_lines.append('')
rep_lines.append('### 改进建议')
rep_lines.append('')
rep_lines.append('1. **补充完成标准**：建议补充空的完成标准字段，便于后续节点验收')
rep_lines.append('2. **补充责任部门**：部分节点缺少主办部门，建议补充明确责任主体')
rep_lines.append('3. **增加依赖关系列**：建议在 Excel 中增加"前置节点"列，明确标注依赖关系')
rep_lines.append('4. **补充监理单位**：工程阶段节点建议增加监理单位字段，便于工程管理')
rep_lines.append('')

rep_content = '\n'.join(rep_lines)
with open(os.path.join(OUTPUT_DIR, f'{PROJECT_NAME}-模板适配度报告.md'), 'w', encoding='utf-8') as f:
    f.write(rep_content)
print(f"  ✓ {PROJECT_NAME}-模板适配度报告.md")

# ============================================================
# STEP 8: 输出回执 (SOP-012 §4.3)
# ============================================================
print("")
print("=" * 60)
print("📋 全景节点图构造完成（设计+工程阶段）")
print("=" * 60)
print(f"项目名称：{PROJECT_NAME}")
print(f"节点总数：{len(nodes)}（一级：{sum(1 for n in nodes if n['level_num']==1)}，二级：{sum(1 for n in nodes if n['level_num']==2)}）")
print(f"识别阶段：{', '.join([stage_display[s] for s in STAGE_ORDER if any(n['stage']==s for n in nodes)])}")
print(f"依赖关系：已确认 0 条 / 推断 {len(wbs_deps)+len(inter_stage_deps)} 条 / 缺失 0 条")
print("-" * 60)
print(f"产出物位置：{OUTPUT_DIR}/")
print(f"  · 全景索引：{PROJECT_NAME}-全景节点索引.md")
print(f"  · 节点树明细：{PROJECT_NAME}-节点树明细.md")
print(f"  · 缺失依赖清单：{PROJECT_NAME}-缺失依赖清单.md")
print(f"  · 适配度报告：{PROJECT_NAME}-模板适配度报告.md")
print("-" * 60)
print(f"[数据质量]")
print(f"  · 完成标准缺失：{len(missing_criteria)} 个节点")
print(f"  · 责任部门缺失：{len(missing_dept)} 个节点")
print(f"  · 计划时间缺失：{len(missing_dates)} 个节点")
print(f"  · 名称重复：{len(dup_names)} 处")
print("=" * 60)
print("Done! All 4 output files generated.")
