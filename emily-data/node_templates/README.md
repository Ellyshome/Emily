# 模板字段 → 数据库字段 映射关系

> 模板是 LLM 可读的"行业参考蓝图"，数据库是项目运行时的结构化存储。
> 从模板创建节点时，按本文档的映射规则将模板转换为数据库记录。

---

## 一、总览：一道映射、三类去向

```
模板文件 (.md)
    │
    ├──→ ProjectNode 表      (1 行)
    ├──→ NodeDeliverable 表  (N 行，来自 ## 产物清单)
    └──→ NodeDependency 表   (M 行，来自 ## 前置条件)
```

创建时需额外注入的**项目上下文参数**（模板中不存在）：
- `project_id` — 目标项目
- `creator_id` — 操作人 UUID
- `deadline` — 截止日期
- `parent_node_id` — 父节点（如有）

---

## 二、模板 → ProjectNode 主表

| # | 模板字段 | DB 字段 | 映射类型 | 说明 |
|---|---|---|---|---|
| 1 | `ref_id` | — | **模板专用** | 不入库。仅用于 Registry 索引和跨模板引用 |
| 2 | `node_name` | `node_name` | **直接映射** | |
| 3 | `node_type` | `node_type` | **直接映射** | MILESTONE / WORK_PACKAGE / TASK |
| 4 | `stage_id` | `stage_id` | **直接映射** | 0=立项 1=规划设计 2=工程施工 3=交付结算 |
| 5 | `## 节点说明` | `remark` | **摘要映射** | 模板正文通常 2-5 句。DB remark 取首句摘要（≤200 字），完整说明不入库 |
| 6 | `## 流程位置` | — | **模板专用** | 不入库。供 LLM 推理用，帮助理解节点在全局流程中的位置 |
| 7 | — | `node_id` | **自动生成** | 规则：`NODE-{hash4(node_name + project_id)}`，与 `node_batch.generate_node_id()` 一致 |
| 8 | — | `project_id` | **上下文注入** | 创建时由调用方传入 |
| 9 | — | `owner_dept_id` | **上下文注入** | 创建时由调用方传入（或从项目默认值取） |
| 10 | — | `related_company_id` | **上下文注入** | 创建时由调用方传入（或从项目默认值取） |
| 11 | — | `deadline` | **上下文注入** | 创建时由调用方传入 |
| 12 | — | `creator_id` | **上下文注入** | 取当前操作人 UUID |
| 13 | — | `responsible_user_id` | **自动填充** | 默认 = `creator_id` |
| 14 | — | `sort_order` | **自动填充** | 默认 0，调用方可覆盖 |
| 15 | — | `status` | **自动填充** | 创建时默认 `NOT_ACTIVATED`；若管理员创建则可直接 `COMPLETED`（补录场景） |
| 16 | — | `parent_node_id` | **上下文注入** | 按项目实际层级结构指定 |

---

## 三、模板「产物清单」→ NodeDeliverable 表

模板中 `## 产物清单` 下的每个 `### 产物名称` 对应 1 条 `node_deliverables` 记录。

| # | 模板字段 | DB 字段 | 映射类型 | 说明 |
|---|---|---|---|---|
| 1 | `### 产物名称` | `deliverable_name` | **直接映射** | |
| 2 | `**性质**：必需/可选` | `is_required` | **直接映射** | "必需"→true，"可选"→false |
| 3 | `目标 N 份/套` | `target_amount` | **直接映射** | 从正文提取数字 |
| 4 | — | `current_amount` | **自动填充** | 创建时默认 `"0.00"`；补录已完成节点时置为 `target_amount` |
| 5 | 正文中的单位（份/套/张） | `unit` | **直接映射** | |
| 6 | `**文件识别特征**` | — | **模板专用** | 不入库。LLM 匹配产物的核心依据 |
| 7 | `**典型文件名**` | — | **模板专用** | 不入库。辅助 LLM 参考 |
| 8 | — | `deliverable_id` | **自动生成** | 规则：`{node_id}-DELV-{序号}`，如 `NODE-A3F2-DELV-001` |
| 9 | — | `node_id` | **自动填充** | 取本条 ProjectNode 的 `node_id` |
| 10 | — | `file_id` | **自动填充** | 补录场景中关联已上传的文件 ID |
| 11 | — | `completed_at` | **自动填充** | 补录已完成时取文件的 `created_at` 或当前时间 |

---

## 四、模板「前置条件」→ NodeDependency 表

模板中的 `## 前置条件` 是自然语言描述（如"方案设计已获甲方批复"），转换为 `node_dependencies` 时需要做**语义解析**：

```
前置条件："方案设计已获甲方批复"
    ↓ LLM 或规则解析
├── depends_on_node_id:   "ECO-SJ-01"      (方案设计节点的 node_id)
└── depends_on_deliverable_id: "ECO-SJ-01-DELV-001"  (方案设计文本的 deliverable_id)
```

| # | 模板字段 | DB 字段 | 映射类型 | 说明 |
|---|---|---|---|---|
| 1 | `- 前置条件描述` | `depends_on_deliverable_id` | **需要解析** | 将自然语言描述匹配到目标项目的实际 deliverable |
| 2 | — | `depends_on_node_id` | **自动填充** | 取 deliverable 所属的 node_id（冗余字段） |
| 3 | — | `node_id` | **自动填充** | 取本条 ProjectNode 的 `node_id` |
| 4 | — | `dependency_type` | **自动填充** | 固定 `"DELIVERABLE"` |
| 5 | — | `weight` | **自动填充** | 固定 `"1.0000"` |

**注意**：前置条件的解析是创建流程中的难点。当前 `node_batch.py` 已支持按 `deliverable_name` 模糊匹配依赖（`_resolve_deliverable_id()`），此能力可复用。

---

## 五、LLM 专用字段（不入库）

这些字段是模板独有的，核心用途是让 LLM 能判断"某个上传的文件是否属于这个节点的产物"。

| 字段 | 位置 | 用途 |
|---|---|---|
| `## 节点说明` 全文 | 模板正文 | LLM 理解节点含义和工作内容 |
| `**文件识别特征**` | 每个产物下 | LLM 匹配文件的核心材料——文件长什么样、有什么关键信息 |
| `**典型文件名**` | 每个产物下 | LLM 辅助参考——glob 模式，非硬规则 |
| `## 流程位置` | 模板正文 | LLM 判断节点在项目全局中的时序位置 |
| `## 前置条件` 原文 | 模板正文 | LLM 理解该节点与其他节点的依赖关系 |

---

## 六、创建节点时的两类场景

| | 日常创建（前瞻） | 补录创建（回溯） |
|---|---|---|
| 触发 | 管理员规划项目节点树 | 文件上传匹配到缺失节点 |
| `status` | `NOT_ACTIVATED` → 之后审批激活 | 直接 `COMPLETED` |
| `current_amount` | `"0.00"` | `= target_amount`（全部已完成） |
| `completed_at` | 空 | 取匹配文件的 `created_at` |
| `file_id` (deliverable) | 空 | 关联匹配到的文件 |
| 前置依赖 | 正常解析 | 放宽：前置条件不满足也可创建（因是补录） |

---

## 七、映射汇总图

```
 模板文件                           数据库
 ────────                           ──────
 ref_id: REF-CONST-DG-001  ──→     (不入库)
 node_name: 施工图设计完成   ──→     project_nodes.node_name
 node_type: MILESTONE       ──→     project_nodes.node_type
 stage_id: 1                ──→     project_nodes.stage_id
                                    
 ## 节点说明                 ──→     project_nodes.remark (摘要)
 ## 流程位置                 ──→     (不入库)
                                    
 ## 产物清单                           
   ### 全套施工图设计文件      ──→     node_deliverables (row 1)
     性质: 必需               ──→       .is_required = true
     目标 1 套                ──→       .target_amount = "1.00"
     文件识别特征              ──→     (不入库)
     
   ### 审查合格证              ──→     node_deliverables (row 2)
   ### 评审通过文件            ──→     node_deliverables (row 3)
                                    
 ## 前置条件                           
   - 规划许可证已取得          ──→     node_dependencies (row 1)
   - 方案设计已批复            ──→     node_dependencies (row 2)
         (需解析为 deliverable_id)
                                    
 (无)                        ←──     project_nodes.project_id (注入)
 (无)                        ←──     project_nodes.deadline (注入)
 (无)                        ←──     project_nodes.owner_dept_id (注入)
```

---

## 八、映射实现位置

| 层级 | 文件 | 职责 |
|---|---|---|
| 解析 | `infrastructure/node_template/parser.py` | 读 .md → frontmatter + body → `NodeTemplate` dataclass |
| 映射 | `infrastructure/node_template/mapper.py` | `NodeTemplate` + 项目上下文 → `CreateNodeCommand` + `CreateDeliverableCommand` 列表 |
| 写入 | `services/node_batch.py`（已有） | 复用 `create_node_tree()` 执行写入 |
