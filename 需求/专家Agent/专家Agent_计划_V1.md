# 专家 Agent — 概要设计（SD）

> **基于需求**：[专家Agent_PRD_V1.md](专家Agent_PRD_V1.md)
> **设计版本**：v1.0
> **级别**：System Design（概要设计）
> **目标**：在 LangGraph WorkItem 图中新增可选 `expert_review` 节点 + 专家库管理工具，实现窄域业务"单次 LLM 调用 + 集中注意力"的专家评审能力。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **系统设计师**，严格按以下模块顺序设计，逐模块验收，验证不通过不进入下一个模块。具体代码实现在编码阶段落地，本设计给出接口契约和实现约束。

---

## 硬约束（违反即失败）

1. **业务内核独立**：`emily_core` 不 import 任何 `astrbot.*` 包（CLAUDE.md 约束 #1）
2. **分层不跳**：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`（约束 #2）
3. **Sync repo + `asyncio.to_thread`**：Repository 全 sync，async Service/节点用 `asyncio.to_thread()` 包裹（约束 #6）
4. **工具必须带 params schema**：所有 LLM 业务工具注册时必须提供 JSON Schema，经 `_reg_biz(reg, name, desc, handler, params=_SCHEMA)` 传入；CI `check_tools_consistency.py` 的 `TOOL_SCHEMA_MAP` 需同步添加映射（约束 #11）
5. **State 纯可序列化**：LangGraph `AgentLoopState` 只含基础类型，专家评审结果通过 `BusContext.work_item`（contextvars）传递，**不进 State**（BUG-04 教训）
6. **不改已有接口签名**：`LLMClient.chat_json` 签名不变（已支持 `model=` 参数），专家调用直接传 `model="deepseek-chat"`；`WorkItem` dataclass 只新增字段不改已有字段
7. **`create_all()` 不 ALTER 已有表**：新表 `experts`/`expert_approvals` 由 `create_all()` 创建；若后续需对已有表加列，必须在 `_PENDING_COLUMNS` 注册
8. **节点工厂模式**：新节点 `make_expert_review` 参照 `make_summarizing`/`make_executing` 的工厂签名（`hook_adapter, *, llm_client, config`），hook 三态（before/after/error）必须走通
9. **专家模型固定 `deepseek-chat`**（决策 #1），**不支持 ask_user**（决策 #3），**单专家**（决策 #4）
10. **手册路径**：`emily-data/files/Expert Work Manual/`（决策 #2），参照 `RuleBookLoader` 多级 fallback 路径查找模式

---

## 系统架构概览

### 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                          EmilyCore                               │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            LangGraph StateGraph（现有，修改）              │   │
│  │                                                            │   │
│  │  START→created→routing ──route_after_routing──┐           │   │
│  │                        │                      │           │   │
│  │                ┌───────┴────────┐             │           │   │
│  │                ↓                ↓             │           │   │
│  │          executing         【expert_review】  │           │   │
│  │          (agent loop)       （新增节点）       │           │   │
│  │                │                │             │           │   │
│  │                ↓                ↓             │           │   │
│  │            summarizing ←────────┘             │           │   │
│  │                ↓                                            │   │
│  │          quality_gate→END                                   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │【M3-意图接入】│  │【M4-评审节点】│  │ 【M5-专家库管理工具】 │   │
│  │ SessionAgent │  │expert_agent  │  │ expert_manage_tool   │   │
│  │ 设置 expert_ │  │ prompt_build │  │ 4 工具+schema        │   │
│  │ id/required  │  │ 文件加载     │  │ create/approve/      │   │
│  └──────┬───────┘  └──────┬───────┘  │ toggle/query         │   │
│         │                 │          └──────────┬───────────┘   │
│         │                 ↓                     │               │
│         │          ┌──────────────┐             │               │
│         │          │【M2-手册加载】│             │               │
│         │          │ManualLoader  │             │               │
│         │          │(多级fallback)│             │               │
│         │          └──────┬───────┘             │               │
│         │                 │                     │               │
│         │          ┌──────┴────────────────────┴──────────┐    │
│         │          │      【M1-数据层】                     │    │
│         │          │  ExpertRepository (sync)              │    │
│         │          │  ExpertApprovalRepository (sync)      │    │
│         │          │  Expert / ExpertApproval ORM          │    │
│         │          └──────────────────┬────────────────────┘    │
│         │                             │                          │
│         │                    ┌────────┴────────┐                 │
│         │                    │ emily-postgres  │                 │
│         │                    │ experts 表      │                 │
│         │                    │ expert_approvals│                 │
│         │                    └─────────────────┘                 │
│         │                                                        │
│  ┌──────┴───────────────────────────────────────────────────┐   │
│  │ emily-data/files/Expert Work Manual/  （磁盘手册文件）     │   │
│  │   EXP-xxx.md (职能手册)  EXP-xxx-任务.md (任务手册)        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 分层关系

| 新模块 | 所在分层 | 上层依赖 | 下层被依赖 |
|--------|---------|----------|-----------|
| M1-数据层 | Repository + ORM | 无 | M2, M4, M5 |
| M2-手册加载 | Service（工具类） | M1（路径来自 experts 表） | M4 |
| M3-意图接入 | Session（SessionAgent） | M1（查专家 by sop_id） | 无 |
| M4-评审节点 | WorkItem LangGraph node | M1, M2, LLMClient | 组装进 graph.py |
| M5-专家库管理工具 | Tools（BusinessFlowTool） | M1 | 注册进 registry.py |

---

## 数据流设计

### 核心流程

```
【流程 A：专家评审执行】
用户消息+附件 → SessionAgent 意图识别
   → 查 ExpertRepository.get_by_sop_id(wi.sop_id) 命中专家
   → 设置 wi.expert_id / wi.expert_required=True
   → WorkItem 入图 created→routing
   → route_after_routing: expert_required && expert_id 非空 → expert_review
   → expert_review 节点:
       M1.get_by_id(expert_id) → Expert 定义
       M2.load_manual(manual_path) → 职能手册全文
       M2.load_manual(task_manual_path) → 任务手册全文
       M2.load_review_files(wi attachments) → 待审文件文本
       build_expert_prompt(手册+任务+文件+要求) → system_prompt
       LLMClient.chat_json(system_prompt, user_input, model="deepseek-chat")
       → _normalize_expert_result → wi.expert_review_result
   → summarizing: 从 expert_review_result 构造 StructuredResult
   → quality_gate → done → SessionAgent 组织回复

【流程 B：专家库管理】
管理员消息 → SessionAgent 意图识别 → 匹配 SOP-012-SYS-expert_manage
   → agent loop 调 create_expert/approve_expert/toggle_expert/query_experts 工具
   → tool_node 执行 handler（权限校验 + ExpertRepository CRUD）
   → complete_work 返回结果
```

| 流程 | 触发条件 | 参与者 | 数据流向 | 异常路径 |
|------|---------|--------|---------|---------|
| 专家评审 | SOP 声明专家 或 用户显式请求 | M3→M4→M2→M1→DB, M4→LLM | 专家定义+手册+文件→prompt→LLM→评审成果 | LLM 异常→partial 降级；专家未激活→回退 executing |
| 新建专家 | 管理单位员工发"新建专家" | M5→M1→DB | 工具参数→ExpertRepository.create→PENDING 记录 | 权限不足→工具返回拒绝 |
| 审批专家 | L5+ 发"审批 EXP-xxx" | M5→M1→DB | expert_id+action→ExpertRepository.approve→ACTIVE | 状态非法→返回错误 |

---

## 模块依赖图

```
M1(数据层: ORM+Repository)
  │
  ├──→ M2(手册加载: ManualLoader)          ──┐
  │                                          │
  ├──→ M5(专家库管理工具: 4 BusinessFlowTool) │
  │                                          │
  └──→ M3(意图接入: SessionAgent 扩展)       │
                                             │
                              M4(评审节点) ───┘  ← 依赖 M1+M2+LLMClient
                                │
                                ↓
                          graph.py 装配（组装点）
```

**构建顺序**：M1 → M2 → M5 → M4 → M3 → 组装验证

理由：M1 是所有模块的数据基础；M2 依赖 M1 的路径字段；M5 依赖 M1 的 CRUD 且独立于评审节点可先验收；M4 依赖 M1+M2；M3 最后接入（需 M1 查专家 + M4 节点已就位）。

---

## 交付物总览

| 模块 | 交付物类型 | 新增/修改 | 核心接口/类/表 |
|------|-----------|----------|---------------|
| M1 | 数据模型+Repository | 新增 | `experts` 表, `expert_approvals` 表, `ExpertRepository`, `ExpertApprovalRepository`, `Expert`/`ExpertApproval` ORM |
| M2 | Service（工具类） | 新增 | `ExpertManualLoader` 类（多级 fallback 路径加载） |
| M3 | Session 扩展 | 修改 | `SessionAgent` 意图识别段新增专家匹配逻辑；`WorkItem` 新增 3 字段 |
| M4 | LangGraph 节点 | 新增+修改 | `make_expert_review` 节点工厂；`build_expert_prompt`；`graph.py` 装配 `route_after_routing`；`make_summarizing` 适配专家成果 |
| M5 | 业务工具 | 新增+修改 | `create_expert`/`approve_expert`/`toggle_expert`/`query_experts` 4 工具 + schema；`registry.py` 注册；`check_tools_consistency.py` 映射 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/workitem/workitem.py` | 修改 | `WorkItem` dataclass 新增 `expert_id`/`expert_required`/`expert_review_result` 3 字段（默认空值） |
| `emily-core/emily_core/workitem/langgraph_engine/graph.py` | 修改 | `build_workitem_graph` 注册 `expert_review` 节点；删除 `routing→executing` 直连边，改为 `routing→route_after_routing→{executing\|expert_review}`；新增 `route_after_routing` 函数 |
| `emily-core/emily_core/workitem/langgraph_engine/nodes.py` | 修改 | 新增 `make_expert_review` 工厂函数；`make_summarizing` 新增 `expert_review_result` 分支构造 `StructuredResult` |
| `emily-core/emily_core/infrastructure/database/models.py` | 修改 | 新增 `Expert`/`ExpertApproval` ORM 类 |
| `emily-core/emily_core/tools/registry.py` | 修改 | `_register_business` 末尾注册 4 个专家管理工具（`_reg_biz` + `params=`） |
| `emily-core/emily_core/tools/manager.py` | 不变 | — |
| `emily-core/emily_core/config.py` | 修改 | 新增 `expert_review_enabled`/`llm_expert_max_tokens`/`expert_model` 3 配置项 |
| `emily-core/emily_core/session/session_agent.py` | 修改 | 意图识别段（设置 `wi.sop_id` 附近）新增：查 `ExpertRepository.get_by_sop_id`，命中则设 `wi.expert_id`+`wi.expert_required=True` |
| `emily-core/emily_core/bootstrap.py` | 不变 | `init_db()` 已含 `create_all()`+`_ensure_columns()`，新表自动创建 |
| `emily-core/emily_core/infrastructure/llm/client.py` | 不变 | `chat_json(system_prompt, user_message, model=)` 签名已满足，无需改 |
| `scripts/check_tools_consistency.py` | 修改 | `TOOL_SCHEMA_MAP` 添加 4 个专家工具→schema 常量映射 |
| `emily-core/emily_core/__init__.py` | 不变 | `EmilyCore` 已持 `llm_client`，`build_workitem_graph` 调用处自动传入 |

---

## 独立脚本架构设计

> 本需求以运行时节点 + 工具为主，不涉及数据处理流水线脚本。手册文件为人工编写的 `.md`，无需脚本生成。
> 验收阶段的 DB 预埋/查询通过现有 `docker exec emily-postgres psql` 命令完成，不新增独立脚本。

---

## M1: 数据层（ORM + Repository）

**依赖**：无（首建模块）

**层级**：Repository + Infrastructure(ORM)

**职责**：定义专家库持久化模型与 sync 数据访问接口，供 M2/M3/M4/M5 调用。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `ExpertRepository` | 静态方法类 | — | 专家库 CRUD（全 sync） |
| `create(*, expert_no, name, function_desc, manual_path, task_manual_path, review_schema, sop_id, creator_id) -> Expert` | 静态方法 | 创建 PENDING 专家记录 | 参照 `ProjectNodeRepo.create(**kwargs)` 模式 |
| `get_by_id(expert_id: str) -> Expert \| None` | 静态方法 | 按 UUID 查 | 参照 `ProjectNodeRepo.get_by_id` |
| `get_by_expert_no(expert_no: str) -> Expert \| None` | 静态方法 | 按业务编号查 | 参照 `ProjectNodeRepo.get_by_node_id` |
| `get_by_sop_id(sop_id: str) -> Expert \| None` | 静态方法 | 按 SOP 查首个 ACTIVE 专家 | M3 意图接入用 |
| `list_by_status(status: str) -> list[Expert]` | 静态方法 | 按状态列表 | M5 query_experts 用 |
| `list_active() -> list[Expert]` | 静态方法 | 全部 ACTIVE 专家 | M5 query_experts 用 |
| `update_status(expert_id: str, new_status: str, approver_id: str) -> Expert \| None` | 静态方法 | 状态机流转 + 写 approver_id/approved_at | 参照 `ProjectNodeRepo.update_status` |
| `generate_expert_no() -> str` | 静态方法 | 生成 `EXP-001` 递增编号 | 参照 `NodeDeliverableRepo.generate_deliverable_id` |
| `ExpertApprovalRepository` | 静态方法类 | — | 审批记录追加（只增不删） |
| `create(*, expert_id, action, operator_id, reason) -> ExpertApproval` | 静态方法 | 记录一次审批/启停操作 | — |
| `list_by_expert(expert_id: str) -> list[ExpertApproval]` | 静态方法 | 查专家操作历史 | M5 query_experts 可选附带 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `get_session()` | `infrastructure/database/session.py` | 获取 DB session（`with get_session() as session:` 模式） |
| `_new_uuid()` | `infrastructure/database/models.py` | 生成 UUID 主键 |
| `datetime.now(timezone.utc).isoformat()` | 标准库 | 时间戳（参照现有 repo 的 `created_at` ISO 字符串风格） |

### 数据模型

#### 新增表：`experts`

| 字段 | 类型 | 必填 | 默认值 | 说明 | 业务规则 |
|------|------|------|--------|------|---------|
| `id` | String(UUID) PK | ✓ | `_new_uuid()` | 主键 | — |
| `expert_no` | String(32) UNIQUE | ✓ | — | 业务编号 `EXP-001` | 全局唯一 |
| `name` | String(100) | ✓ | — | 专家名称 | — |
| `function_desc` | String(200) | ✓ | — | 一句话职能描述 | — |
| `manual_path` | String(500) | ✓ | — | 职能手册文件名（相对手册目录） | 文件须存在 |
| `task_manual_path` | String(500) | ✓ | — | 任务手册文件名 | 文件须存在 |
| `review_schema` | Text(JSON) | ✓ | `'{}'` | 评审成果 JSON schema（注入 prompt） | 合法 JSON |
| `sop_id` | String(64) | ✗ | `''` | 绑定 SOP ID | 可空=通用专家 |
| `status` | String(16) | ✓ | `'PENDING'` | 状态 | 枚举：PENDING/ACTIVE/REJECTED/DISABLED |
| `creator_id` | String(UUID) | ✓ | — | 创建人 FK→users.id | 不加 FK 约束（参照现有表风格，软关联） |
| `approver_id` | String(UUID) | ✗ | `''` | 审批人 | PENDING 时空 |
| `created_at` | String(50) | ✓ | `now_iso()` | 创建时间 ISO | — |
| `approved_at` | String(50) | ✗ | `''` | 审批时间 | — |
| `updated_at` | String(50) | ✓ | `now_iso()` | 更新时间 | — |

**索引**：`idx_experts_status(status)`, `idx_experts_sop_id(sop_id)`, `unique(expert_no)`

#### 新增表：`expert_approvals`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `id` | String(UUID) PK | ✓ | `_new_uuid()` | 主键 |
| `expert_id` | String(UUID) | ✓ | — | 关联专家（软关联） |
| `action` | String(16) | ✓ | — | APPROVE/REJECT/ENABLE/DISABLE |
| `operator_id` | String(UUID) | ✓ | — | 操作人 |
| `reason` | Text | ✗ | `''` | 操作理由 |
| `created_at` | String(50) | ✓ | `now_iso()` | 操作时间 |

**索引**：`idx_expert_approvals_expert_id(expert_id)`

#### 状态机（experts.status）

| 当前状态 | 允许转换到 | 触发 | 操作人权限 |
|---------|-----------|------|-----------|
| `PENDING` | `ACTIVE` | `approve_expert(action=APPROVE)` | L5+ |
| `PENDING` | `REJECTED` | `approve_expert(action=REJECT)` | L5+ |
| `ACTIVE` | `DISABLED` | `toggle_expert(action=DISABLE)` | L5+ |
| `DISABLED` | `ACTIVE` | `toggle_expert(action=ENABLE)` | L5+ |
| `REJECTED` | — | 终态 | — |

#### 已有表改动

无。`experts`/`expert_approvals` 为新表，由 `Base.metadata.create_all()` 自动创建。

### 模块验收检测

```bash
# 验收 1：ORM 类定义正确，表已创建
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-postgres psql -U emily -d emily -c "\dt experts"
→ 预期输出：列出 experts 表

# 验收 2：字段与索引完整
docker exec emily-postgres psql -U emily -d emily -c "\d experts"
→ 预期输出：含 id/expert_no/name/function_desc/manual_path/task_manual_path/review_schema/sop_id/status/creator_id/approver_id/created_at/approved_at/updated_at；expert_no 有 UNIQUE 约束

# 验收 3：expert_approvals 表已创建
docker exec emily-postgres psql -U emily -d emily -c "\dt expert_approvals"
→ 预期输出：列出 expert_approvals 表

# 验收 4：Repository 基础 CRUD（通过 python -c 快速验证）
uv run python -c "from emily_core.repositories.expert_repo import ExpertRepository; e=ExpertRepository.create(expert_no='EXP-TEST', name='测试专家', function_desc='测试', manual_path='t.md', task_manual_path='t-task.md', review_schema={}, sop_id='', creator_id='test'); print(e.expert_no, e.status); print(ExpertRepository.get_by_expert_no('EXP-TEST').name)"
→ 预期输出：EXP-TEST PENDING \n 测试专家
```

**失败处理**：若表未创建，检查 `models.py` 中 `Expert`/`ExpertApproval` 类是否继承 `Base` 且在 `init_db()` 调用前已 import；若 UNIQUE 约束缺失，检查 `expert_no` 列定义。

---

## M2: 手册加载（ExpertManualLoader）

**依赖**：M1（`experts.manual_path`/`task_manual_path` 字段）

**层级**：Service（无状态工具类）

**职责**：从磁盘加载专家手册全文 + 待审文件文本，多级 fallback 路径查找，参照 `RuleBookLoader` 模式。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `ExpertManualLoader` | 静态方法类 | — | 手册+文件加载工具 |
| `load_manual(filename: str) -> str` | 静态方法 | 加载手册全文；找不到返回空串+警告 | 多级 fallback：容器 `/app/files/Expert Work Manual/` → env `EMILY_EXPERT_MANUAL_DIR` → 开发路径 `emily-data/files/Expert Work Manual/` |
| `load_review_files(attachments: list[dict], user_input: str) -> str` | 静态方法 | 加载待审文件文本 | 优先解析附件（复用 `handle_parse_document`），无附件时从 `user_input` 提取文件路径；失败降级返回提示串 |
| `resolve_manual_dir() -> Path` | 静态方法 | 解析手册根目录（多级 fallback） | 参照 `RuleBookLoader.load` 的候选路径逻辑 |

#### DTO / 返回结构

| 返回 | 字段 | 用途 |
|------|------|------|
| `load_manual` 返回 `str` | 手册全文 | 注入专家 prompt |
| `load_review_files` 返回 `str` | 拼接的文件文本（多文件用 `---` 分隔） | 注入专家 prompt 的"待审文件内容"段 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `handle_parse_document({file_path})` | `tools/parse_document_tool.py` | 解析 PDF/Word/PPT 为 sections+tables 文本 |
| `RuleBookLoader` 路径 fallback 模式 | `services/rule_book_loader.py` | 参照实现手册目录解析 |

### 核心策略

| 策略 | 用途 | 选型理由 | 备选 |
|------|------|---------|------|
| 多级路径 fallback | 手册目录解析 | 与 `RuleBookLoader`/`prompt_loader._find_prompt_path` 一致，容器/开发环境兼容 | 硬编码单路径（不可移植） |
| 复用 `handle_parse_document` | 文件解析 | 已支持 PDF(docling)/Office(MarkItDown)，无需重造 | 新写解析器（违反 DRY） |
| 文件文本截断 | 防超 LLM 上下文 | 单文件截断到 ~12K 字符（约 16K token，留余量给手册+输出） | 全量灌入（可能超限） |

### 模块验收检测

```bash
# 验收 1：手册目录解析
uv run python -c "from emily_core.services.expert_manual_loader import ExpertManualLoader; p=ExpertManualLoader.resolve_manual_dir(); print(p)"
→ 预期输出：emily-data/files/Expert Work Manual 的绝对路径（开发环境）

# 验收 2：加载手册全文（先放一份样板手册）
uv run python -c "from emily_core.services.expert_manual_loader import ExpertManualLoader; t=ExpertManualLoader.load_manual('EXP-001-景观苗木审核.md'); print(len(t), t[:30])"
→ 预期输出：字符数 + 手册开头内容

# 验收 3：文件不存在的降级
uv run python -c "from emily_core.services.expert_manual_loader import ExpertManualLoader; print(repr(ExpertManualLoader.load_manual('not-exist.md')))"
→ 预期输出：'' （空串，不抛异常）
```

**失败处理**：若目录解析失败，检查 `emily-data/files/Expert Work Manual/` 是否存在；Windows 路径含空格需用 `Path` 而非字符串拼接。

---

## M3: 意图接入（SessionAgent 扩展）

**依赖**：M1（`ExpertRepository.get_by_sop_id`）

**层级**：Session

**职责**：在 `SessionAgent` 意图识别阶段，当 SOP 匹配到绑定了专家的 SOP 时，设置 `wi.expert_id` + `wi.expert_required=True`，使 WorkItem 入图后走 `expert_review` 路径。

### 接口契约

#### 对外接口

无新增类。修改 `SessionAgent` 现有方法：

| 修改点 | 方法 | 改动 |
|--------|------|------|
| 意图识别段（设置 `wi.sop_id` 之后） | `SessionAgent._route_or_dispatch`（或同等设置 sop_id 的方法） | 新增：`expert = ExpertRepository.get_by_sop_id(wi.sop_id)`；若 `expert and expert.status == "ACTIVE"`：`wi.expert_id = expert.id; wi.expert_required = True` |

#### WorkItem 字段扩展（M3 的数据载体）

在 `WorkItem` dataclass（`workitem/workitem.py`）新增：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `expert_id` | `str` | `""` | 匹配到的专家 UUID（非空时 routing 后走 expert_review） |
| `expert_required` | `bool` | `False` | 是否需要专家评审 |
| `expert_review_result` | `dict` | `field(default_factory=dict)` | 专家评审成果，供 summarizing 构造 StructuredResult |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `ExpertRepository.get_by_sop_id(sop_id)` | M1 | 查 SOP 是否绑定专家 |

### 核心策略

| 策略 | 用途 | 选型理由 |
|------|------|---------|
| 仅 ACTIVE 专家触发 | 避免 PENDING/DISABLED 误触发 | `_run_expert_agent` 二次校验 status==ACTIVE，双重保险 |
| `expert_required` 与 `expert_id` 双标记 | 路由判定明确 | `route_after_routing` 检查 `expert_required and expert_id`，避免单字段歧义 |
| SOP 绑定为主路径 | 自动化 | 用户无需显式指定专家；SOP 声明即路由 |

### 模块验收检测

```bash
# 验收 1：WorkItem 新字段存在
uv run python -c "from emily_core.workitem.workitem import WorkItem; w=WorkItem(); print(w.expert_id, w.expert_required, w.expert_review_result)"
→ 预期输出： False {}

# 验收 2：SOP 绑定专家后触发（需 M1+M5 就绪，预埋一条 ACTIVE 专家绑定 SOP-XXX）
# 通过 emy-test 发送该 SOP 触发消息，检查 emily-core 日志
docker logs --tail 50 emily-core 2>&1 | grep "expert_required"
→ 预期输出：含 expert_required=True / expert_id=... 的日志
```

**失败处理**：若 `wi.expert_id` 始终为空，检查 `ExpertRepository.get_by_sop_id` 返回的 `status` 是否为 ACTIVE；确认 SessionAgent 改动点在 `wi.sop_id` 赋值之后。

---

## M4: 评审节点（expert_review）

**依赖**：M1（ExpertRepository）、M2（ExpertManualLoader）、LLMClient

**层级**：WorkItem LangGraph node

**职责**：替代通用 agent loop，对匹配专家的 WorkItem 执行单次 `chat_json` 调用，产出 `ExpertReviewResult` 写入 `wi.expert_review_result`。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `make_expert_review(hook_adapter, *, llm_client, config)` | 工厂函数 | 返回 `async def expert_review(state) -> dict` | 参照 `make_summarizing` 签名模式 |
| `route_after_routing(state: dict) -> str` | 路由函数 | 返回 `"executing"` 或 `"expert_review"` | graph.py 条件边用 |
| `build_expert_prompt(*, manual_text, task_manual_text, file_text, review_requirement, review_schema) -> str` | 函数 | 构建窄域 system prompt | 参照 `build_system_prompt` 但无工具表/无 agent loop 规则 |
| `_run_expert_agent(wi, ctx, llm_client, config) -> dict` | 内部函数 | 专家 Agent 核心：加载→prompt→chat_json→normalize | M4 内部调用 |

#### 节点返回值约定

| 返回字段 | 值 | 说明 |
|---------|-----|------|
| `wi_state` | `"summarizing"` | 成功/降级都进 summarizing |
| `wi_state` | `"failed"` | before hook 阻断时 |
| 副作用 | `wi.expert_review_result = {...}` | 供 summarizing 构造 StructuredResult |
| 副作用 | `wi.add_warning(...)` | 降级时记警告 |
| 副作用 | `ctx.set("prompt_info_expert_review", {...})` | 供 ArchiveHook 渲染 |

#### ExpertReviewResult 数据结构（dict）

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `str` | success/partial/failed |
| `score` | `float` | 0-100 量化打分 |
| `score_dimensions` | `dict` | 分维度打分（如 `{"completeness": 80}`） |
| `issues` | `list[dict]` | `[{"location","standard","severity","description"}]` |
| `conclusion` | `str` | 总体结论 |
| `recommendation` | `str` | 处置建议（通过/退回/重做） |
| `elapsed_ms` | `int` | LLM 耗时 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `ExpertRepository.get_by_id(expert_id)` | M1 | 取专家定义（manual_path 等） |
| `ExpertManualLoader.load_manual/load_review_files` | M2 | 加载手册+文件 |
| `LLMClient.chat_json(system_prompt, user_message, model="deepseek-chat")` | `infrastructure/llm/client.py` | 单次 LLM 调用（**不改签名**，直接传 model） |
| `hook_adapter.fire_before/fire_after/fire_error` | `langgraph_engine/hook_adapter.py` | hook 三态 |
| `_enter_stage`/`_exit_stage`/`_get_context` | `nodes.py` 模块级 | 阶段计时 + BusContext 获取（参照其他节点） |
| `StructuredResult` | `pipeline/interfaces/execution.py` | summarizing 构造用 |

### graph.py 装配改动

| 改动 | 位置 | 内容 |
|------|------|------|
| 注册节点 | `build_workitem_graph` 内 | `gs.add_node("expert_review", make_expert_review(hook_adapter, llm_client=llm_client, config=config))` |
| 删除直连边 | `build_workitem_graph` 内 | 删除 `gs.add_edge("routing", "executing")` |
| 新增条件边 | `build_workitem_graph` 内 | `gs.add_conditional_edges("routing", route_after_routing, {"executing":"executing", "expert_review":"expert_review"})` |
| expert_review 出边 | `build_workitem_graph` 内 | `gs.add_edge("expert_review", "summarizing")` |

### make_summarizing 适配改动

在 `make_summarizing` 的 `if wi.structured_result is None:` 分支前，新增专家成果优先构造：

```
if wi.structured_result is None and wi.expert_review_result:
    er = wi.expert_review_result
    wi.structured_result = StructuredResult(
        status=er.get("status","success"),
        intent="expert_review",
        sop_id=wi.sop_id or "",
        risk_level=getattr(wi, "risk_level", "L2"),
        data={"score": er.get("score",0), "details": er.get("score_dimensions",{})},
        summary_facts=[er.get("conclusion","")],
        issues=er.get("issues",[]),
        ...
    )
```

### 核心策略

| 策略 | 用途 | 选型理由 | 备选 |
|------|------|---------|------|
| 单次 `chat_json` 无 tools | 省算力 | 专家任务无需工具调用，1 次 LLM 完成 | 走 agent loop（浪费） |
| 固定 `model="deepseek-chat"` | 降低成本/延迟（决策 #1） | chat 类模型支持 temperature + json_mode；reasoner 贵且慢 | reasoner（违背初衷） |
| 不支持 ask_user（决策 #3） | 简单跑起来 | 信息不足直接 partial，用户重发 | interrupt 机制（复杂） |
| LLM 异常→partial 降级 | 不阻断主流程 | 参照现有节点的 fail-open 哲学 | 抛错失败（体验差） |
| 专家未激活→回退 executing | 兜底 | 避免停用专家仍被引用 | 直接失败 |
| prompt 不含工具表/agent loop 规则 | 集中注意力 | 与 `build_system_prompt` 区分，窄域 prompt | 复用通用 prompt（注意力分散） |
| review_schema 注入 prompt 而非 API 参数 | `chat_json` 不接收 schema 参数 | 靠 prompt 描述 + `json_mode=True` 强制 JSON | 改 `chat_json` 签名（违反硬约束 #6） |

### 模块验收检测

```bash
# 验收 1：节点工厂可构造
uv run python -c "from emily_core.workitem.langgraph_engine.nodes import make_expert_review; print(callable(make_expert_review(None, llm_client=None, config=None)))"
→ 预期输出：True（或构造时不抛错）

# 验收 2：图装配含 expert_review 节点
uv run python -c "from emily_core.workitem.langgraph_engine.graph import build_workitem_graph; g=build_workitem_graph(hook_adapter=None, llm_client=None, business_tools=None, resolvers=None, config=None); print('expert_review' in g.nodes)"
→ 预期输出：True

# 验收 3：端到端——预埋 ACTIVE 专家 + SOP 绑定，emy-test 发审核消息
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "审核这份景观施工图苗木表" --sender "真实用户名"
docker logs --tail 100 emily-core 2>&1 | grep -E "expert_review|ExpertReviewResult"
→ 预期输出：含 expert_review 节点执行日志 + 评审成果 score/issues

# 验收 4：非专家任务不受影响（回归）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：段放线完成" --sender "真实用户名"
docker logs --tail 50 emily-core 2>&1 | grep "route_after_routing"
→ 预期输出：路由到 executing（agent loop），不走 expert_review
```

**失败处理**：若节点未触发，检查 `route_after_routing` 中 `wi.expert_required and wi.expert_id` 条件；若 LLM 返回非 JSON，检查 prompt 中 schema 描述是否清晰 + `chat_json` 的 `json_mode=True` 是否生效（`chat_messages` 内部已设 `response_format`）。

---

## M5: 专家库管理工具

**依赖**：M1（ExpertRepository + ExpertApprovalRepository）

**层级**：Tools（BusinessFlowTool）

**职责**：提供 4 个 LLM 可调工具，实现专家库的创建/审批/启停/查询，权限校验在 handler 内完成。

### 接口契约

#### 对外接口（4 个 BusinessFlowTool）

| 工具名 | handler 签名 | params schema 常量 | 权限校验 | category | permission_flag |
|--------|-------------|-------------------|---------|----------|-----------------|
| `create_expert` | `async def handle_create_expert(params, user_id) -> dict` | `_EXPERT_CREATE_SCHEMA` | `is_management_unit or can_access(level,4)` | `business` | `write` |
| `approve_expert` | `async def handle_approve_expert(params, user_id) -> dict` | `_EXPERT_APPROVE_SCHEMA` | `is_admin(level)`（L5+） | `project` | `admin` |
| `toggle_expert` | `async def handle_toggle_expert(params, user_id) -> dict` | `_EXPERT_TOGGLE_SCHEMA` | `is_admin(level)` | `project` | `admin` |
| `query_experts` | `async def handle_query_experts(params) -> dict` | `_EXPERT_QUERY_SCHEMA` | L1+（无特殊校验） | `base` | `all` |

#### handler 返回约定（统一）

```python
{"success": bool, "reply": str, "expert_no": str (可选), ...}
```

#### params schema 定义（4 个常量）

| 常量 | 关键字段 | required |
|------|---------|----------|
| `_EXPERT_CREATE_SCHEMA` | `name`(str), `function_desc`(str), `manual_path`(str), `task_manual_path`(str), `review_schema`(object), `sop_id`(str, 可选) | name, function_desc, manual_path, task_manual_path |
| `_EXPERT_APPROVE_SCHEMA` | `expert_no`(str), `action`("APPROVE"\|"REJECT"), `reason`(str) | expert_no, action |
| `_EXPERT_TOGGLE_SCHEMA` | `expert_no`(str), `action`("ENABLE"\|"DISABLE"), `reason`(str) | expert_no, action |
| `_EXPERT_QUERY_SCHEMA` | `status`(str, 可选: PENDING/ACTIVE/DISABLED/REJECTED), `sop_id`(str, 可选) | 无 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `ExpertRepository.*` | M1 | CRUD |
| `ExpertApprovalRepository.create` | M1 | 审批记录 |
| `is_admin(user_level)` | `permission/level.py` | 审批/启停权限 |
| `can_access(user_level, 4)` | `permission/level.py` | 创建权限 |
| `is_management_unit` | session_ctx（`perm_dict["is_management_unit"]`） | 创建权限 |
| `_reg_biz(reg, name, desc, handler, params=, category=, permission_flag=)` | `tools/registry.py` | 工具注册 |
| `_h(mod, fn)` | `tools/registry.py` | handler 引用 |

### 注册改动

在 `tools/registry.py` 的 `_register_business(core, reg)` 末尾追加：

```
from .expert_manage_tool import (
    handle_create_expert, handle_approve_expert,
    handle_toggle_expert, handle_query_experts,
    _EXPERT_CREATE_SCHEMA, _EXPERT_APPROVE_SCHEMA,
    _EXPERT_TOGGLE_SCHEMA, _EXPERT_QUERY_SCHEMA,
)
_buc += _reg_biz(reg, "create_expert", "新建专家（需管理员审批）",
                 partial(_h("expert_manage_tool","handle_create_expert")),
                 params=_EXPERT_CREATE_SCHEMA, category="business", permission_flag="write")
# ... approve/toggle/query 同理
```

### CI 一致性改动

`scripts/check_tools_consistency.py` 的 `TOOL_SCHEMA_MAP` 新增 4 条：

```
"create_expert": "_EXPERT_CREATE_SCHEMA",
"approve_expert": "_EXPERT_APPROVE_SCHEMA",
"toggle_expert": "_EXPERT_TOGGLE_SCHEMA",
"query_experts": "_EXPERT_QUERY_SCHEMA",
```

### 核心策略

| 策略 | 用途 | 选型理由 |
|------|------|---------|
| handler 内做权限校验 | fail-closed | 参照 `tool_node` 的 `_session_api_ids` 权限检查模式；专家工具额外做 level 校验 |
| `create_expert` 创建为 PENDING | 审批流 | 需求"需要项目管理员确认后方可生效" |
| `expert_no` 自动生成 | 避免冲突 | `ExpertRepository.generate_expert_no()` 递增 |
| `review_schema` 存 DB | 运行时注入 prompt | M4 从 `expert.review_schema` 取值注入 prompt |
| `permission_flag=admin` for approve/toggle | 工具表过滤 | `ToolRegistryRepo.get_available` 中 `admin` 需 L5+ |

### 模块验收检测

```bash
# 验收 1：4 工具已注册且带 schema
uv run python -c "from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry; from emily_core.tools.registry import register_all; reg=BusinessFlowToolRegistry(); register_all(None, reg); [print(t.name, bool(t.parameters.get('properties'))) for t in reg._tools.values() if 'expert' in t.name]"
→ 预期输出：create_expert True / approve_expert True / toggle_expert True / query_experts True

# 验收 2：CI 一致性检查通过
uv run python scripts/check_tools_consistency.py
→ 预期输出：无 error，4 个专家工具 schema 映射命中

# 验收 3：emy-test 创建专家（管理单位员工）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "新建专家：景观苗木审核专家，职能是审核景观施工图苗木设计" --sender "管理单位用户名"
docker exec emily-postgres psql -U emily -d emily -c "SELECT expert_no, name, status FROM experts;"
→ 预期输出：新增一行 status=PENDING

# 验收 4：审批流转（L5 用户）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "审批通过 EXP-001" --sender "管理员用户名"
docker exec emily-postgres psql -U emily -d emily -c "SELECT expert_no, status, approver_id FROM experts WHERE expert_no='EXP-001';"
→ 预期输出：status=ACTIVE, approver_id 非空

# 验收 5：权限不足拒绝（L2 用户尝试审批）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "审批通过 EXP-001" --sender "普通员工用户名"
→ 预期输出：回复含"权限不足"
```

**失败处理**：若工具未注册，检查 `_register_business` 是否调用；若 SchemaGuard WARNING，检查 `_reg_biz` 是否传 `params=`；若 CI 报 error，检查 `TOOL_SCHEMA_MAP` 映射常量名是否与 tool 源文件一致。

---

## 组装验证

所有模块完成后，运行端到端组装验证：

| 验证项 | 验证方式 | 预期结果 |
|--------|---------|---------|
| 数据层正确 | `psql \d experts` / `\d expert_approvals` | 表结构/索引与设计一致 |
| 工具注册正确 | `register_all` 后查 4 专家工具 + schema | 4 工具均有 properties |
| CI 一致性 | `check_tools_consistency.py` | 无 error |
| 图装配正确 | `build_workitem_graph` 后查 nodes | 含 expert_review 节点 |
| 非专家任务回归 | emy-test 发普通事件录入 | 走 executing，不走 expert_review |
| 专家评审端到端 | 预埋专家+SOP 绑定，emy-test 发审核消息 | expert_review 节点执行，产出 score/issues |
| 降级路径 | 断开 LLM 或传无效 expert_id | partial 降级，不阻断 |
| 权限校验 | L2 用户尝试审批 | 拒绝 |

```bash
# 端到端组装验证命令（预埋 ACTIVE 专家 + SOP 绑定后）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "审核这份景观施工图苗木表" --sender "真实用户名"
docker logs --tail 200 emily-core 2>&1 | grep -E "route_after_routing|expert_review|ExpertReviewResult|summarizing"
→ 预期输出：routing→expert_review→summarizing 完整链路，含评审成果

# 降级验证（临时把 expert_model 改成无效值或停 LLM）
docker logs --tail 50 emily-core 2>&1 | grep -E "expert_review failed|partial"
→ 预期输出：含降级日志，wi_state 仍进 summarizing
```

---

## 阶段反思指令

每完成一个模块的设计，在进入下一个模块之前，执行以下反思：

1. **检查设计完整性**：本模块的接口契约、数据模型、验收检测是否完整
2. **检查设计偏差**：是否有与需求/PRD 决策不符的设计？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个接口调整 → 直接修改设计文档对应模块，继续
   - 如果偏差 2-4 个接口或模块职责调整 → 在设计文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个接口或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成设计

---

## 配套文档同步（实施完成后）

实施阶段完成后，按 CLAUDE.md §10 维护约定同步以下文档：

| 文档 | 更新内容 |
|------|---------|
| `docs/代码文件目录.md` | 新增 expert_repo / expert_manual_loader / expert_manage_tool / expert_review 节点条目 |
| `docs/业务模块与运转全景.md` | WorkItem 图新增 expert_review 节点 + 专家库模块清单 + SOP-012 |
| `docs/接口协议与调用约定.md` | 新增 ExpertReviewResult 数据结构 + 4 工具 schema |
| `docs/数据库设计.md` | 新增 experts + expert_approvals 两表（55 表→55 表，视现有计数） |
| `docs/技术踩坑备忘录.md` | 记录实施中遇到的新坑（如有） |

---

*本设计为概要设计（SD），由 req-plan 技能生成。*
