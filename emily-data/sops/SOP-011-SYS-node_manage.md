# 全景节点管理 — 业务流服务手册

## 1. 业务流版本信息

| 字段 | 值 |
|------|-----|
| 业务流编号 | SOP-011-SYS-node_manage |
| 版本 | v1.2 |
| 业务名称 | 全景节点管理 |
| 业务类型 | SYS |
| 权限控制 | `admin`（仅管理员 L5+；成果提交等子流程为 L3+，见 §3.5） |

## 2. 触发条件

### 2.1 触发语义特征

**必须条件**：
- 消息内容涉及项目节点/工作项的创建、查询
- 涉及项目进度的更新、成果提交
- 涉及工作项之间的依赖关系管理
- 涉及父子节点的层级组装

**否定条件**：
- 纯闲聊问候
- 不涉及项目工作项/节点的内容

### 2.2 示例对话

**应触发此业务流的用户说法：**

> 「帮我创建一个节点：编号SG-001，名称是景观施工图设计」

> 「查看节点SG-001的详细信息和当前进度」

> 「节点SG-001的成果SG-001-DELV-001已经完成了，更新进度到100%」

> 「节点SG-002依赖于SG-001-DELV-001这个成果」

> 「把SG-001-01挂载为SG-001的子节点」

**不应触发此业务流的用户说法：**

> 「今天天气怎么样」 → 闲聊

> 「帮我查一下昨天的会议记录」 → 应触发 SOP-005-QRY 数据查询

> 「帮我给张工发个消息」 → 不属于节点操作

## 3. 任务执行协议

### 3.1 前置合规理念

全景节点与任务执行存在不可拆分的合规关联：

- **成果上报必须来自已分配任务，任务必须挂载于执行中的全景节点。** 这是不可断开的合规链。
- 全景节点是任务分配的唯一合法来源。未启用（`NOT_ACTIVATED` / `CONDITIONS_NOT_MET`）的节点不得挂载任何任务。
- 当任务挂载到非 IN_PROGRESS 状态的节点时，系统应阻断并引导用户联系有权限的负责人先启动节点。
- 任务通过 `create_task_node` 创建 TASK 类型叶子节点，通过 `submit_node_deliverable` 提交成果。

### 3.2 处理流程

1. **意图识别**：分析用户消息中的操作意图（创建/查询/更新进度/添加依赖/挂载子节点）
2. **参数提取**：从自然语言中提取节点编号、名称、截止时间、成果编号等结构化字段
3. **业务校验**：检查节点是否存在、权限是否足够、依赖是否合法（循环检测）、**节点状态是否允许挂载任务**
4. **执行操作**：调用对应的节点工具完成操作
5. **结果反馈**：返回操作结果 + 当前状态（进度/流转情况/受影响的祖先节点）

### 3.3 可调用工具

**① 节点管理核心工具（8 个；`category=project` / `permission_flag=admin` → 仅 L5+ 可见）**

| 工具名 | 用途 | 关键参数（`*` = 必填） |
|--------|------|----------------------|
| `create_node` | 创建全景节点（单节点 / 批量） | `project_id`*、`node_id`*、`node_name`*、`deadline`*、`remark`、`nodes[]`（批量模式） |
| `query_node` | 查询节点详情（状态/进度/成果/依赖） | `node_id`* |
| `update_node_progress` | 更新成果进度（**自动触发状态机重算**） | `deliverable_id`*、`current_amount`*、`file_id` |
| `add_node_dependency` | 添加前置依赖（BFS 循环检测 + 权重阻塞） | `node_id`*、`depends_on_deliverable_id`*、`weight` |
| `mount_child_node` | 挂载父子节点关系（深度上限 3 层检测） | `parent_node_id`*、`child_node_id`*、`child_weight` |
| `update_nodes` | 批量更新节点字段 | `updates`* |
| `acknowledge_nodes` | 批量签认节点 | `node_ids`*、`remark` |
| `discard_nodes` | 批量废弃节点（**软删除**，非物理删除） | `node_ids`* |

**② 任务 / 成果子流程工具（5 个；`category=business` / `permission_flag=write` → L3+ 可见）**

| 工具名 | 用途 | 关键参数（`*` = 必填） |
|--------|------|----------------------|
| `create_task_node` | 创建 TASK 类型叶子节点（挂载到父节点） | `project_id`*、`title`*（或 `node_name`）、`executor_id` / `responsible_user_id`、`deadline_at`、`parent_node_id` / `node_id` |
| `submit_node_deliverable` | 提交节点成果（PENDING → SUBMITTED） | `content`*、`deliverable_id`、`file_url` / `attachment_file_id` |
| `confirm_node_deliverable` | 确认成果（SUBMITTED → CONFIRMED，触发进度重算） | `deliverable_id`*、`reason` |
| `return_node_deliverable` | 退回成果（SUBMITTED → RETURNED） | `deliverable_id`*、`reason`* |
| `query_my_nodes` | 查询我负责 / 参与的节点 | `project_id`、`node_type`、`limit` |

### 3.4 状态流转规则

四态状态机（定义见 `node_state_machine.py` 的 `VALID_TRANSITIONS`）：

```
节点创建（入库即生效）→ CONDITIONS_NOT_MET（条件不足）
          ↑                                   │
          │ 阻塞回退                  前置依赖满足 + 成果推进
          │                                   ↓
   （NOT_ACTIVATED 历史态）           IN_PROGRESS（进行中）
   新流程不再进入，仅存量数据可能停留           │ 所有必需成果 100%
                                             ↓
                                       COMPLETED（已完成·终态）
```

- **创建即 `CONDITIONS_NOT_MET`**：节点入库即生效，不存在"待审批阻断"；`NOT_ACTIVATED` 为历史态。
- 叶子节点进入 `IN_PROGRESS` 的条件：**前置条件满足度 ≥ 1.0 且 必需成果完成度 ≥ 1.0**（均按权重累计）。
- 父节点状态由子节点集体决定：全部未满足 → `CONDITIONS_NOT_MET`；全部完成 → `COMPLETED`；其余 → `IN_PROGRESS`。
- 阻塞场景：添加 `weight ≥ 999` 的依赖 → 自动回退至 `CONDITIONS_NOT_MET`。

### 3.5 权限映射

> **口径以代码为准**：工具可见性来自 `tool_registry` 表（`ToolRegistryRepo.get_available`），服务层校验来自 `node_service.py`。

**A. 经 IM / SOP 调用（LLM 工具，受「工具可见性」约束）**

| 操作 | 工具 | 最低可见级别 |
|------|------|------------|
| 创建 / 查询（全量）/ 更新进度 / 添加依赖 / 挂载子节点 / 批量更新 / 签认 / 废弃 | 8 个核心节点工具（`category=project`） | **L5+（管理员）** |
| 创建任务节点 / 提交·确认·退回成果 / 查询我的节点 | 5 个任务工具（`category=business`） | **L3+** |

**B. 经 REST / CLI 调用（不受工具可见性约束，受服务层校验）**

| 操作 | 服务层校验 |
|------|-----------|
| 创建节点 | 仅建设单位人员（`company_type = 建设单位`）可创建；L5+ 管理员不受限 |
| 废弃节点 | `discard_node` 无等级校验（由调用方 / 上层把关） |

> **失败表现**：低等级用户经 IM 触发本 SOP 时，核心节点工具不在其可见工具集内（模型看不到、不会调用），回复退化为"基础状态 + 需更高权限"的精简结果；若强行引用越权工具，则返回"该操作无法执行，您可能没有相应权限"。**成果提交类操作（L3+）不受此限**。

**C. 调用通道差异（兜底白名单）**

核心节点工具中，`query_node` / `query_my_nodes` 在兜底只读白名单内，可被**直接调用**；而 `create_node` / `update_nodes` / `acknowledge_nodes` / `discard_nodes` 等**不在**兜底白名单——**直接调用会被"该操作在当前档位不可用，请走对应标准流程或联系管理员"拦截**，必须经本 SOP 能力（`SOP-011-SYS`）执行。这正是"走对应标准流程"的含义，排障时勿误判为权限不足。

### 3.6 Agent 调用指引（L3 agent loop）

1. **工具选择**：根据操作类型选择对应工具（create_node / query_node / update_node_progress 等），节点工具 schema 含完整参数约束
2. **工具失败自纠**：调用返回错误时，分析 tool_result 中的 error 信息，调整参数后重试
3. **信息不足**：操作需要 project_id 时若只有项目名称，先调 `resolve_project` 解析
4. **完成回复**：操作成功后简洁确认结果，包含关键节点编号

## 4. 输出规范

操作完成后返回：
- `success`: 操作是否成功
- `node_id`: 操作的节点编号
- `status`: 当前状态（CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED）
- `progress`: 当前进度百分比
- `message`: 中文提示信息
- `affected_ancestors`: 受影响的祖先节点列表（进度更新时）
