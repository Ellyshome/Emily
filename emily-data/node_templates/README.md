# 全景节点参考模板库

> 模板是**人工维护的行业参考蓝图**，不是项目运行时数据。从模板建节点时，按本文档的映射规则
> 把模板转换为一组创建入参（节点 + 成果 + 参与单位 + 参与人 + 共享文件 + 依赖）。
>
> **系统不自动制作模板**：模板库只能由人工维护与补齐；未命中模板时按人的要求走人工建节点路径。

---

## 一、模板单元形态（固定）

一个模板 = 一个**自洽单元目录**，固定命名规则：

```
emily-data/node_templates/{ref_id}/           ← 目录名 = 模板编号
  ├── template.yaml                          ← 固定命名的结构化清单入口（机器可读）
  └── <附件…>                                 ← 任意格式，仅登记不解析，给人看/给人下载
```

| 项 | 约定 |
|----|------|
| `{ref_id}` | 模板编号，前缀 `REF-CONST-*`；目录名必须与清单内 `ref_id` 一致（不一致会告警） |
| `template.yaml` | **唯一结构化入口**，承载入库所需字段；任意文本格式只开放给附件 |
| 附件 | 参考范例（xlsx）、成果约定、说明文档等；二进制附件**不进入解析链路** |
| 索引 | `index.yaml` 由脚本生成（勿手改），登记编号/名称/类型/阶段/摘要/入口/附件清单 |

模板目录在各部署变体中以 **只读**（`:ro`）挂入容器 `/app/data/node_templates`。
因此**索引写入只能在宿主机执行**；容器内只做 `--check` 只读校验。

---

## 二、`template.yaml` 字段

| # | 字段 | 类型 | 必填 | 说明 |
|---|------|------|------|------|
| 1 | `ref_id` | str | ✓ | 模板编号（模板专用，不入库；用于索引与跨模板引用） |
| 2 | `node_name` | str | ✓ | 节点名称 |
| 3 | `node_type` | enum | ✓ | `MILESTONE` / `TASK`。**由声明决定，不随结构变化** |
| 4 | `stage_id` | int | — | 模板侧阶段分组（**仅展示层用，不入库**） |
| 5 | `summary` | str | ✓ | 摘要（入库为节点备注的摘要口径） |
| 6 | `deliverables` | list | ✓ | 成果清单（见第三节） |
| 7 | `preconditions` | list[str] | — | 前置条件**文字描述**；当 `declarations.pre_conditions` 缺省时按文字匹配 |
| 8 | `declarations` | map | — | **对象声明区**（见第四节）；不进索引，随内容按需读取 |

### 成果条目字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 成果名称 → `node_deliverables.deliverable_name` |
| `target_amount` | float | 目标量（默认 1.0） |
| `unit` | str | 量纲（份 / 套 / 张…，默认"份"） |
| `is_required` | bool | 是否必需成果（默认 true） |
| `typical_filenames` | str | 典型文件名特征（**模板专用，不入库**，供识别参考） |

---

## 三、字段 → 数据库映射

```
模板单元                            数据库
────────                            ──────
template.yaml
  ref_id                     ──→    （不入库；索引与溯源用）
  node_name                  ──→    project_nodes.node_name
  node_type                  ──→    project_nodes.node_type（由声明决定）
  stage_id                   ──→    （不入库，仅展示分组）
  summary                    ──→    project_nodes.remark（摘要口径）
  deliverables[].name        ──→    node_deliverables.deliverable_name
  deliverables[].target_amount →    node_deliverables.target_amount
  deliverables[].unit        ──→    node_deliverables.unit
  deliverables[].is_required ──→    node_deliverables.is_required
  declarations.pre_conditions ─→    node_dependencies（只指向项目内**已存在**成果）
  （无）                     ←──    project_nodes.project_id / deadline / creator_id（上下文注入）
  （无）                     ←──    project_nodes.responsible_user_id（默认 = creator_id）
  （无）                     ←──    project_nodes.status（恒为 CONDITIONS_NOT_MET，由成果齐备自证推进）
  （无）                     ←──    project_nodes.node_id（自动生成）
```

**口径说明**

- **节点类型由声明决定**：模板里写 `MILESTONE` 就是里程碑，不因有无子节点而改变。
- **阶段不入库**：`stage_id` 只存在于模板侧与展示层分组。
- **成果必备**：业务节点创建时至少需一条 `is_required=true` 的成果（否则拒绝，`40003`）；
  **容器节点**（临时里程碑 / 临时任务 / 未归类收容节点）豁免此项。
- **完成判定**：节点是否完结只由**该节点自身必需成果**齐备决定，不向上聚合、无签认环节。
- **编号由服务端解析**：模板只提供名称/编号来源，任何编号都不由 LLM 生成。

---

## 四、对象声明区（`declarations`）

按对象类型分节；每条含「描述 / 匹配条件 / 是否必需」。**匹配条件是声明式**（非脚本、非脚本混合）。

```yaml
declarations:
  participant_companies:            # 参与单位候选
    - desc: 本项目建设单位
      match:
        type_norm: 建设单位          # 类型归一后的标准类型（越出词表 → 告警）
        scope_keywords: []          # 承包范围关键词（包含匹配）
        name_keywords: []           # 单位名称关键词（包含匹配）
      required: true
  participant_users:                # 成员候选（来源单位为本模板已声明的类型）
    - desc: 建设单位项目负责人
      match: {from_company: 建设单位, role: participant}
      required: false
  shared_files:                     # 共享文件候选（受操作人可见文件集合约束）
    - desc: 全景计划参考范例
      match: {name_keywords: [全景计划], type_keywords: [xlsx]}
      required: false
  pre_conditions:                   # 前置成果候选（只指向项目内已有成果）
    - desc: 立项批复文件
      match: {deliverable_name_keywords: [立项批复]}
      required: true
```

| 对象类型 | 匹配口径 |
|---------|---------|
| `participant_companies` | 项目内参建单位优先 → 未命中退全局企业；判据 = 类型归一**等值**匹配 **且** 范围/名称关键词**包含**匹配 |
| `participant_users` | 由已确定参与单位推出该单位**在职人员**；默认候选 = 该企业全员 |
| `shared_files` | 文件名 / 类型关键词包含匹配；**范围必须限定在操作人可见文件集合内** |
| `pre_conditions` | 项目内已有成果名匹配：整句优先 → 无命中退主体词；双向子串匹配；多命中取名称最长者 |

**声明错误一律告警，不静默忽略**：语法错误 / 类型越出词表 / `from_company` 引用不存在的来源单位
→ 装配草稿的 `warnings[]` 列出条目索引与原因。

**未解析项**：声明了但零候选 → 草稿 `unresolved[]` 单列回显（附原因），不静默丢弃。
未声明某类对象 → 该类不产出候选，也**不**记为未解析项。

---

## 五、装配产物：只读草稿

对同一模板的装配产出**候选集合**（非单值）+ 每条候选的**依据**（依据模板哪一条声明、匹配到什么）：

| 草稿字段 | 说明 |
|---------|------|
| `candidates{companies,users,files,dependencies}` | 四类候选集合，供人增删 |
| `basis`（每条候选内） | 声明条目索引 + 命中字段 + 命中值 |
| `unresolved[]` | 声明了但零候选的条目及原因 |
| `warnings[]` | 声明校验告警 |

装配链路**全程只读**：不写库、可反复调用（重复调用结果稳定）；未经确认不落库。

---

## 六、日常运行

```bash
# 模板库变更后（宿主机执行；容器内模板目录只读）
uv run python scripts/node_template_release.py

# 单步：迁移存量顶层单文件模板 → 模板单元目录
uv run python scripts/migrate_node_templates.py --dry-run
uv run python scripts/migrate_node_templates.py
uv run python scripts/migrate_node_templates.py --verify   # 四项一致性断言

# 单步：重建索引 / 只读校验（校验可在容器内执行）
uv run python scripts/maintain_node_template_index.py
uv run python scripts/maintain_node_template_index.py --check
```

---

## 七、实现位置

| 层级 | 文件 | 职责 |
|------|------|------|
| 索引生成 | `scripts/maintain_node_template_index.py` | 扫描模板单元 → `index.yaml`（含附件清单） |
| 形态迁移 | `scripts/migrate_node_templates.py` | 顶层单文件 → 模板单元目录 + 四项一致性断言 |
| 读取 | `emily-core/emily_core/services/node_template_loader.py` | 只读检索：索引 → 清单 → 附件清单 → 对象声明原文 |
| 声明解析与装配 | `emily-core/emily_core/services/node_assembly_service.py` | 声明校验 + 四类采集器 → 只读草稿（候选 + 依据 + 未解析项） |
| 写入 | `emily-core/emily_core/services/node_service.py` | `create_node` 按依赖序一次性落库六类对象 |
| 门禁 | `emily-core/emily_core/services/node_template_gate.py` | 文件触发的自动补建必须命中模板编号（不降级匹配） |
