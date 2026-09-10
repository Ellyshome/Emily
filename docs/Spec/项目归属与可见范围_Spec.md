# 项目归属与可见范围 — 技术规格说明（Spec）

> 版本：1.0　更新：2026-09-10
> 范围：描述 emily-core 中**项目归属判定**与**节点/态势可见范围**的权威口径，以及项目态势书（世界书）的载入规则。
> 说明：本文件描述"已确定的现状口径"，是后续开发与测试环境搭建的事实基准；与实现不一致的历史文档以本文件为准。

---

## 1. 定位

解决三个问题：

1. **一个人属于哪些项目？**（项目归属）
2. **一个人能看到项目里的哪些节点？**（可见范围）
3. **新会话该载入哪本项目态势书？**（态势书载入）

三者的关系：**2 由 1 推导，3 也由 1 推导**——归属是唯一的源头口径。

---

## 2. 核心口径（三条权威定义）

### 2.1 项目归属：人员不带项目字段，由"企业参与节点"推导

> **人员不应有用于表达项目归属的字段。其参与项目，通过其归属的企业、企业参与了某个项目的节点来判断。**

推导链：

```
users.company
   └─→ company_info.id
        └─→ node_participant_companies.company_id   （企业参与节点）
             └─→ node_participant_companies.node_id
                  └─→ project_nodes.project_id      （节点所属项目）
```

- **参与项目集合** = 上述链路的 `project_id` 去重（排除 `project_nodes.is_discarded = true`）。
- **`users.project_id` 已废弃并删除**（ORM 模型 + 数据库列均已移除）。任何新增代码不得再引入"人员项目字段"。

### 2.2 可见节点集合

| 用户类型 | 可见节点集合 |
|---|---|
| **管理单位**（`company_info.is_admin = true`） | **本项目全部节点**（该项目下 `is_discarded = false` 的全部 `node_id`） |
| 其他单位 | **其企业参与过的节点**（`node_participant_companies` 中的真实 `node_id`） |
| 无企业 / 无参与记录 | **空集**（fail-closed：不授予任何节点） |

- 可见节点是**真实节点编号**（如 `EMR-GH-01-01`），不是行业/条线关键词。
- 管理单位取"全项目"是为沿用既有权限体系语义（`permission/row_security.py` 中管理单位本就按"项目全参建单位"处理）。

### 2.3 项目态势书（世界书）载入

| 规则 | 说明 |
|---|---|
| **每项目唯一一本** | `project_world_books.project_id` 有唯一约束 |
| **按"参与项目集合"载入** | 见 2.1；与 `users.project_id` 无关 |
| **参与多个项目 → 合并载入多个** | 多本态势书逐本裁剪渲染后合并展示；不合并原始 JSON |
| **无参与项目 → 不载入任何态势书内容** | 返回空串；不得以"会话创建者"或其它身份兜底载入 |

---

## 3. 态势信息的分层展示规则

同一本态势书在不同权限下展示粒度不同，**必须分两层**，不可一刀切：

| 层次 | 内容 | 可见性 |
|---|---|---|
| **项目级聚合**（不含节点名） | 项目名、编号、阶段；节点总数、完成/进行中/逾期计数；整体进度 | **所有项目参与者可见** |
| **节点级明细**（含节点名） | 「我可见节点」、`🔴 逾期：…`、`7天内到期`、`阻塞链`、「节点明细」 | **按 2.2 的可见节点集合过滤**；过滤后为空则该行/该段**整段不输出**（不输出"无"） |
| **【近期事件】** | 项目近期事件列表 | **仅管理单位展示**；非管理单位**不展示该段** |

> **【近期事件】为何特殊**：`events` 表**只有 `project_id`、没有节点关联**，数据层面无法安全裁剪，故按 fail-closed 处理——宁可不对非管理单位展示。
> 若后续要求对非管理单位开放该段，需先给 `events` 增加节点关联（属独立需求）。

---

## 4. 实现落点

### 4.1 查询单点

[repositories/participation_repo.py](d:/app/Emily/emily-core/emily_core/repositories/participation_repo.py) —— **唯一**的"企业—节点"关系查询实现，禁止在他处另立口径：

| 方法 | 用途 |
|---|---|
| `node_ids_of_company(company_id)` | 企业参与节点（真实 node_id） |
| `project_ids_of_company(company_id)` | 企业参与节点所归属的项目 |
| `all_node_ids_of_projects(project_ids)` | 项目全部节点（管理单位口径） |
| `company_ids_of_projects(project_ids)` | 项目下全部参与单位（反查） |
| `users_of_projects(project_ids)` | 项目下全部人员（反查） |

### 4.2 口径消费方

| 位置 | 作用 |
|---|---|
| [services/permission_service.py](d:/app/Emily/emily-core/emily_core/services/permission_service.py) | `_derive_project_ids` / `_derive_authorized_nodes`：产出快照的 `project_ids` 与 `authorized_node_ids` |
| [session/session_data_fetcher.py](d:/app/Emily/emily-core/emily_core/session/session_data_fetcher.py) | 按 `project_ids` 载入态势书（多项目合并）；快照写入 `is_management_unit` |
| [session/session_context.py](d:/app/Emily/emily-core/emily_core/session/session_context.py) | `_brief_world` 逐本渲染合并；按 `is_management_unit` 决定是否展示事件段 |
| [session/fetchers/fetch_world_book.py](d:/app/Emily/emily-core/emily_core/session/fetchers/fetch_world_book.py) | `_visible()` **统一过滤出口**；`render_brief` / `render_full` 的 `include_events` 开关 |
| [services/world_book_builder.py](d:/app/Emily/emily-core/emily_core/services/world_book_builder.py) | 生成态势书；节点级条目（逾期/7天内到期/阻塞）**必须带 `node_id`** 以支撑求交 |
| [tools/meta_cognition_tool.py](d:/app/Emily/emily-core/emily_core/tools/meta_cognition_tool.py) | 按需查阅三书全文（world 多项目合并 + 事件段按管理单位开关） |
| [services/cognition_drift_detector.py](d:/app/Emily/emily-core/emily_core/services/cognition_drift_detector.py)、[services/initialization_checker.py](d:/app/Emily/emily-core/emily_core/services/initialization_checker.py)、[permission/row_security.py](d:/app/Emily/emily-core/emily_core/permission/row_security.py) | 反向查询"项目→企业/人员"，一律走 ParticipationRepo |

### 4.3 数据模型要点

| 表 | 关键列 | 说明 |
|---|---|---|
| `users` | `company` → `company_info.id` | **无 `project_id`（已删除）** |
| `company_info` | `is_admin` | `true` = 管理单位 |
| `node_participant_companies` | `node_id`, `company_id` | **归属口径的唯一事实源** |
| `project_nodes` | `node_id`, `project_id`, `is_discarded` | 节点→项目映射 |
| `node_participants` | `node_id`, `user_id` | 用户级参与人，**不是归属口径**（另有用途：查询"我的节点"等） |
| `project_world_books` | `project_id`（唯一） | 每项目一本态势书 |

---

## 5. 不变量（Invariants）

1. **归属单一源头**：任何"某人属于哪些项目 / 能看到哪些节点"的判断，只能经 ParticipationRepo；禁止引入人员项目字段或关键词推导。
2. **fail-closed**：无企业、无参与记录、数据缺失 → 可见集合为空，不授予任何节点，不载入任何态势书。
3. **单一过滤出口**：态势书中所有**含节点名**的段落必须经 `_visible()` 判定；新增段落一并接入，不得各自实现。
4. **为空则不输出**：节点级段落过滤后为空 → 整行/整段省略，不输出"无"这类占位。
5. **聚合与明细分离**：项目级聚合信息可对全体项目参与者展示；节点级明细一律按可见集合裁剪。

---

## 6. 已确认的设计决策（决策记录）

| 决策 | 结论 |
|---|---|
| 管理单位是否可见全项目节点 | **是**（沿用既有权限体系语义） |
| 用户企业参与多个项目时态势书怎么载 | **参与几个就合并载入几个** |
| 【近期事件】拿不到节点关联时非管理单位怎么办 | **不展示该段**（fail-closed） |
| `users.project_id` 是否保留 | **删除**（模型 + 数据库列），"宁少勿多" |
| 是否把"本人负责的节点"计入可见范围 | **否**——归属只由企业判定，不混入个人负责关系 |
| 节点"关联单位"字段（`related_company_id`）的语义 | **`company_info.id`**；中文名称/类型只作输入写法，落库前由 `CompanyResolver` 解析 |
| 建节点时参与单位的兜底顺序 | **显式 `participant_company_ids` → 解析后的 `related_company_id` → 创建人所属企业** |
| 按名称反查节点（批量导入） | **唯一命中才采纳**；同名/模糊多候选一律拒绝（返回 None 并告警） |
| 存量中文 `related_company_id` | **已归一**为 `company_info.id`（脚本 `scripts/backfill_node_company_and_participants.py`） |

---

## 7. 与历史文档的差异

| 历史描述 | 现行口径 |
|---|---|
| 以 `users.project_id` 表示人员所属项目 | **已废弃**，改由"企业→参与节点→项目"推导；该列已从模型与数据库移除 |
| 由 `company_info.function_scope` / `company.scope` 推导可见节点（`design`/`construction` 等关键词） | **已废弃**，改为 `node_participant_companies` 中的**真实节点编号** |
| 态势书单项目载入 | 支持**多项目合并载入**（按参与项目集合） |
| 节点级段落仅"节点明细"做裁剪 | **全部**含节点名的段落统一裁剪（逾期/7天内到期/阻塞/明细），走同一出口 |
| 测试环境按用户级 `node_participants` 播种参与关系 | 归属需播种**企业级** `node_participant_companies`（见 `.claude/tool/env-test/013_seed_node_participants.sql`） |
| `related_company_id` 存中文标签（"建设单位"/"总包"…），DDL 与 ORM 默认值即中文 | 语义为 `company_info.id`；默认值改为空；中文仅作输入，由 `CompanyResolver` 转换后落库 |
| 批量导入按节点名"精确 → 包含"取**第一个**匹配 | **唯一命中才采纳**；多候选拒绝并告警（见 `node_batch._resolve_node_id_by_name`） |
| `update_node` **静默忽略** `related_company_id` | 已支持更新；无法解析则明确报错，不再静默丢弃 |

---

## 8. 标识与名称口径（概念级不变量）

> 本节对应一次**概念级 spec 不匹配**的收口：早期把"企业业务范围关键词"当作"节点授权标识"，
> 并用中文标签充当单位 ID，导致权限裁剪**静默失效**（不报错，只是恒为空）。以下为固化规则。

### 8.1 规则

| 规则 | 说明 |
|---|---|
| **标识用 ID** | 跨表引用、权限求交、唯一性判断，一律用 `node_id` / `company_id` / `user_id` / `project_id` |
| **名称仅展示** | `node_name` / `company_name` / `type` 不参与匹配与判定 |
| **名称可用于"输入侧定位"** | 对话或批量导入可用名称定位目标，但必须：① 限定 `project_id`；② **唯一命中才采纳**；③ 多候选 → 拒绝并告警（不得静默取第一个） |
| **禁止一列混装** | 一个字段只承载一种语义（ID 就是 ID）；可读写法只在**入口**出现，落库前完成转换 |

### 8.2 入口解析（`CompanyResolver`）

[services/company_resolver.py](d:/app/Emily/emily-core/emily_core/services/company_resolver.py) 是"单位引用 → `company_info.id`"的唯一解析实现：

1. 已是合法 `company_info.id` → 原样返回
2. 项目参建单位范围内按 `type` → `company_name` 顺序匹配
3. 未命中则退到全局范围重复 2)（兼容存量/种子里的中文写法）
4. **多候选 / 无命中 → 返回 None 并告警**（fail-closed）

### 8.3 本次修正的反例

| 反例 | 修正 |
|---|---|
| 用 `company.scope` 推导"可见节点"（产出 `design`/`construction` 关键词，与 `EMR-*` 编号永不相等） | 改为 `node_participant_companies.node_id`（真实编号） |
| `related_company_id` 存中文标签（"建设单位"等），与"ID 语义"冲突 | 默认值清空；入口由 `CompanyResolver` 解析；存量经回填脚本归一 |
| 批量导入按名称"精确 → 包含"取**第一个**匹配 | 唯一命中才采纳，多候选拒绝并告警 |
| `update_node` 静默忽略 `related_company_id` | 支持更新；解析失败明确报错 |

### 8.4 运维护栏（防回潮）

启动自检（`bootstrap`）新增两条 fail-open 告警：

1. **参与单位登记不全** —— `ParticipationRepo.projects_with_unregistered_nodes()`：存在未登记参与单位的节点（这些节点对非管理单位不可见）
2. **单位标识不规范** —— `CompanyResolver.find_invalid_node_company_refs()`：`related_company_id` 存了非 `company_info.id` 的内容（空值视为"未设置"，不算违规）

> 恢复手段：`uv run python scripts/backfill_node_company_and_participants.py [--dry-run]`（幂等，可重复执行）

### 8.5 已知同类项（**本次未处理**，待产品/设计定夺）

| 字段 | 现状 | 风险 |
|---|---|---|
| `project_nodes.owner_dept_id` | 命名是 `_id`，实际存**中文部门名**（如"项目总"）；被 `ProjectNodeRepo.find_by_owner` / `find_pending_approval` 用于**过滤**，并被 `NodeService._check_approver_permission` 用于**审批权限判定**（与 `user.department` 的中文名比较） | 与 8.1"标识用 ID"冲突；部门改名即断；是"用名称当标识"的同类问题 |

> 处理它需要先确定"部门"的权威表达（是否存在部门表 / 统一 ID），属独立议题；本 Spec 先登记，避免遗漏。
