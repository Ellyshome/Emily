# RAG 系统化改造 — 概要设计（SD）

> **基于需求**：[RAG系统化改造PRD.md](./RAG系统化改造PRD.md)
> **设计版本**：v1.0
> **级别**：System Design（概要设计）
> **目标**：将 Emily RAG 从「无范围过滤的三段向量原型」改造为「范围正确 + 工程能力对标」的系统化检索，范围化与通用能力同步设计、分层落地。

---

## 你的角色

你作为 **Emily开发者资深架构师** + **系统设计师** + **数据库架构师**，严格按以下模块顺序设计，逐模块验收，验证不通过不进入下一个模块。具体代码实现在编码阶段落地，本设计给出接口契约和实现约束。

---

## 硬约束（违反即失败）

1. **禁止修改已有接口签名**：除非设计明确标注「修改接口签名」，否则只能在已有类中新增方法，不修改现有方法签名。本设计唯一允许修改签名的是 `RagProvider.search()`（M3），需显式标注。
2. **分层不可跳**：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`。范围解析放 Repository/Service 层，检索集成放 Tool 层，不得在 Tool 层直连 DB。
3. **Sync Repo + `asyncio.to_thread`**：Repository 全 sync；async 调用方用 `asyncio.to_thread()` 包裹。可见集解析、`search_dense` 均为 sync。
4. **`emily_core` 不 import 任何 `astrbot.*` 包**。
5. **功能注册接入（元原则）**：新 Provider/脚本/调度作业必须走注册通道（业务工具→`tools/registry.py`；调度→`JobHandlerRegistry`；脚本→`scripts_registry.yaml`），禁止裸调用/孤儿代码。
6. **工具必须带参数 schema**：修改 `knowledge_search` 的入参时，schema 常量（`_KNOWLEDGE_SEARCH_SCHEMA`）与注册处（`registry.py`）与一致性校验（`tools_consistency.py` 的 `TOOL_SCHEMA_MAP`）三处同步。
7. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告。
8. **遵循接口契约**：所有跨模块调用必须通过约定接口，不允许绕过接口直接操作对方内部数据。

---

## 前置设计决策（需用户确认，默认按推荐方案落地）

| # | 决策点 | 现状 | 推荐方案 | 影响 |
|---|--------|------|---------|------|
| D1 | 节点可见集③的节点来源 | `permission_service.authorized_node_ids` 由 `company.function_scope`（职能映射）推导；PRD 公式③写的是 `node_participant_companies`（企业真实参与） | **以 `node_participant_companies` 为可见范围的节点来源**（符合 PRD「可见性是关系」语义）；`function_scope`/`authorized_node_ids` 属职能授权维度，不用于文件可见范围 | 决定 M2 的③查询源 |
| D2 | 可见集计算方式 | 已有 `session_accessible_files` 快照（`sync_for_user`），但当前未含①上传者/②公开两个来源 | **新增实时解析器（M2），每次检索实时算 ①∪②∪③∪④ 并返回 SQL 子查询**；不依赖快照，避免过期 | 决定 M2 的实现形态与 M3 的过滤参数类型 |
| D3 | 节点可见模式 | `project_nodes.visibility_mode` 存在 `specific`（经 `node_accessible_files`）与 `all_project_files`（全项目文件）两种 | **③需同时覆盖两种模式**：`specific` 走 `node_accessible_files`；`all_project_files` 走该节点 `project_id` 全项目文件（均受密级约束） | M2 的③需分支处理 |

> 若 D1 有异议，M2 的③查询源可替换为 `authorized_node_ids`，其余模块不受影响。

---

## 系统架构概览

### 架构图

```
┌────────────────────────────────────────────────────────────────────┐
│                          emily-core (FastAPI)                        │
│                                                                      │
│  Tool 层  ┌────────────────────────────────────────────────────┐    │
│           │ knowledge_search_tool (handle_knowledge_search)      │    │
│           │  【M4 改造】注入 user_id → 调 M2 解析可见集 → search │    │
│           └───────────────┬────────────────────────────────────┘    │
│                           │                                          │
│  Provider 层 ┌────────────▼─────────────────────────────────────┐   │
│              │ PgVectorRagProvider (search)                      │   │
│              │  【M3 改造】透传 scoped_doc_ids                   │   │
│              │  【M6 增强】混合检索 + rerank                     │   │
│              └───────────────┬────────────────────────────────────┘   │
│                           │                                          │
│  Service 层  ┌────────────▼─────────────────────────────────────┐   │
│              │ 【M2 新增】VisibleFileSetResolver                 │   │
│              │   resolve_visible_file_ids(user_id, ctx)          │   │
│              └───────────────┬────────────────────────────────────┘   │
│                           │                                          │
│  Repository 层 ┌───────────▼────────────────────────────────────┐   │
│                │ KnowledgeChunkRepo.search_dense(+doc_ids)       │   │
│                │  【M3 改造】加 doc_id allowlist                 │   │
│                │  【M1 改造】batch_insert 强制 doc_id 锚定       │   │
│                └───────────────┬────────────────────────────────────┘   │
│                           │                                          │
│  DB           ┌─────────────▼─────────────────────────────────────┐ │
│               │ files / knowledge_chunks / project_nodes /        │ │
│               │ node_participant_companies / node_accessible_files│ │
│               │ session_accessible_files                          │ │
│               └──────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘

入库管线（独立脚本 / 工具）
  scripts/ingest_knowledge.py  ──【M1 改造】doc_id=files.id
  tools/embed_tool.py          ──【M1 校验】doc_id 必填
  tools/file_tool.py           ──【M1 已正确，仅校验】
  【M5 增强】解析路由 + 结构分块 + 哈希去重
  【M7 新增】入库作业状态机（JobHandlerRegistry）
```

### 分层关系

| 新/改模块 | 所在分层 | 上层依赖 | 下层被依赖 |
|-----------|---------|----------|-----------|
| M1 doc_id 归一 | Repository + 脚本 | 无 | M3 / M5 |
| M2 可见范围解析器 | Service / Repository | 无 | M4 |
| M3 检索范围过滤 | Repository + Provider | M1 | M4 / M6 |
| M4 检索链集成 | Tool + Registry | M2, M3 | M8 |
| M5 解析/分块/去重 | Service + 脚本 | M1 | M7 |
| M6 混合检索/重排 | Repository + Provider | M3 | — |
| M7 入库状态机 | Scheduler/Job | M5 | — |
| M8 引用溯源 | Tool + Provider | M4 | — |

---

## 数据流设计

### 检索链（范围化后的核心流程）

```
用户消息 → SessionAgent → BusinessFlowToolRegistry
  → knowledge_search handler(params, user_id)
      → VisibleFileSetResolver.resolve_visible_file_ids(user_id, ctx)   [M2]
            └─ ① uploaded_by ∪ ② confidentiality=0 ∪ ③ node_visible ∩ info_level ∪ ④ explicit
      → rag_provider.search(query, top_k, stage, role, scoped_doc_ids)  [M3]
            └─ KnowledgeChunkRepo.search_dense(..., doc_ids=scoped)      [M3]
                  └─ WHERE 1 - cosine_distance(embedding) >= threshold
                     AND doc_id IN (可见集)   ← 排序前过滤
```

### 核心流程

| 流程 | 触发条件 | 参与者 | 数据流向 | 异常路径 |
|------|---------|--------|---------|---------|
| 检索（范围化） | SOP 命中 `knowledge_search` | M4→M2→M3→Repo→DB | user_id → 可见 file_id 集 → doc_id IN 集 → top_k chunk | 权限快照缺失→空集（宁少答不泄露）；DB 异常→空结果 |
| 入库（doc_id 归一） | 脚本/工具触发 | 脚本→Repo→DB | 源文件 → 建/查 files 记录 → files.id 作 doc_id → 写 chunk | 无 files 记录→先建；重复→查重跳过 |
| 入库（状态机） | 文件上传/定时 | Job→Service→Repo→DB | job 状态 5 态流转 → 崩溃恢复重跑 | 失败→FAILED 可重试 |

---

## 模块依赖图

```
M1(doc_id 归一) ──────→ M3(检索范围过滤) ──────→ M4(检索链集成) ──────→ M8(引用溯源)
M2(可见范围解析器) ─────────────────────────────→ M4
M1 ──→ M5(解析/分块/去重) ──→ M7(入库状态机)
M3 ──→ M6(混合检索/重排)
```

无循环依赖。构建顺序：M1、M2（并行）→ M3、M5（并行）→ M4、M6（并行）→ M7、M8（并行）。

> 里程碑映射：**里程碑1（正确性）= M1~M4**；**里程碑2（质量）= M5~M6**；**里程碑3（体验/可靠性）= M7~M8**。可先落地里程碑1 完成越权封堵，再逐里程碑推进。

---

## 交付物总览

| 模块 | 交付物类型 | 新增/修改 | 核心接口/类/表 |
|------|-----------|----------|---------------|
| M1 | Repository + 脚本改造 | 修改 | `KnowledgeChunkRepo.batch_insert`、`scripts/ingest_knowledge.py` |
| M2 | Service + Repository | 新增 | `VisibleFileSetResolver`、`resolve_visible_file_ids()` |
| M3 | Repository + Provider 改造 | 修改 | `KnowledgeChunkRepo.search_dense()`、`RagProvider.search()`、`PgVectorRagProvider` |
| M4 | Tool + Registry 改造 | 修改 | `handle_knowledge_search()`、`registry._register_base()`、`_KNOWLEDGE_SEARCH_SCHEMA` |
| M5 | Service + 脚本增强 | 新增/修改 | 解析路由、分块器、去重器 |
| M6 | Repository + Provider 增强 | 修改 | `search_hybrid()`、rerank 降级 |
| M7 | Job 状态机 | 新增 | 入库作业状态机（接入 `JobHandlerRegistry`） |
| M8 | Tool + Provider 增强 | 修改 | `SearchResult` 来源字段、citation 组装 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily_core/repositories/knowledge_chunk_repo.py` | 修改 | `batch_insert` 强制 doc_id 必填（M1）；`search_dense` 增加 `doc_ids` allowlist 参数（M3）；新增 `search_hybrid`（M6） |
| `emily_core/providers/rag/base.py` | 修改 | `SearchResult` 增来源字段（M8）；`RagProvider.search()` 增 `scoped_doc_ids`（M3，标注改签名） |
| `emily_core/providers/rag/pgvector_provider.py` | 修改 | `search/_search_impl` 透传 `scoped_doc_ids`（M3）；混合检索/rerank（M6） |
| `emily_core/tools/knowledge_search_tool.py` | 修改 | `handle_knowledge_search` 增 user_id/可见集入参（M4）；citation 组装（M8） |
| `emily_core/tools/registry.py` | 修改 | `_register_base` 中 `_rag` 注入 user_id（M4） |
| `emily_core/tools/embed_tool.py` | 修改 | `handle_embed_and_index` 校验 doc_id 必填（M1） |
| `emily_core/tools/file_tool.py` | 校验 | `_index_reference_file` 已锚定 doc_id=file_id，仅补校验（M1） |
| `scripts/ingest_knowledge.py` | 修改 | 目录扫描前建/查 `files` 记录，用 `files.id` 作 doc_id（M1） |
| `emily_core/infrastructure/database/models.py` | 修改 | `knowledge_chunks` 增去重哈希/状态字段（M5/M7，见各模块数据模型） |
| `emily_core/services/permission_service.py` | 不变 | —（仅消费其 `build_permission_dict` 的 `company_id`/`info_level`） |

---

## 独立脚本架构设计

### 独立脚本清单

| # | 脚本（建议命名） | 职责 | 关键参数 | `--dry-run` 行为 |
|---|----------------|------|---------|------------------|
| 1 | `scripts/ingest_knowledge.py`（改造） | 目录扫描入库，doc_id 锚定 files.id | `--dir` `--collection` `--backend` `--uploaded-by` `--confidentiality` `--dry-run` | 预览将建/匹配的 files 记录 + chunk 数，不写库 |
| 2 | `scripts/backfill_doc_id.py`（新增） | 回填/清理历史断链 chunk | `--dry-run` `--mark-orphan` | 列出断链 chunk 数量与样例，不写库 |
| 3 | `scripts/rag_visible_check.py`（新增） | 手动校验某用户可见文件集与检索结果 | `--user-id` `--query` | 打印可见集统计与命中，不写库 |

### 聚合薄壳

| # | 脚本（建议命名） | 串联逻辑 |
|---|----------------|---------|
| 1 | `scripts/rag_governance.py`（新增） | 零可见治理：扫描无可见来源文件 → 生成「零可见治理清单」→ 汇总/通知 |

### 脚本交互关系

```
scripts/ingest_knowledge.py（改造）
  ├── 建/查 files 记录      → files.id 作为 doc_id
  ├── 解析/分块/去重（M5）   → chunks
  └── KnowledgeChunkRepo.batch_insert

scripts/rag_governance.py（新增）
  └── VisibleFileSetResolver 反查 → 零可见文件 → 治理清单

EmilyCore / Scheduler 集成
  └── import 脚本核心函数 run()  → dict（系统调用通道）
```

---

## M1: doc_id 归一（入库管线锚定 files.id）

**依赖**：无

**层级**：Repository + 脚本

**职责**：统一所有入库路径 `knowledge_chunks.doc_id = files.id`，消除目录扫描的 uuid4 断链，使范围过滤的 `doc_id IN 可见文件id集` 语义成立。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `KnowledgeChunkRepo.batch_insert` | 方法 | `batch_insert(chunks, embeddings, doc_meta) -> list[str]` | **行为收紧**：`doc_meta["doc_id"]` 必填；缺失时不再回退 uuid4，改为抛 `ValueError`（标注「修改行为」） |
| `ensure_file_record(filename, *, uploaded_by, confidentiality, project_id=None) -> File` | 新增方法 | 输入文件信息，返回已存在或新建的 `File` | 目录扫描入库前建/查 files 记录 |
| `backfill_doc_id(dry_run=True) -> dict` | 新增脚本函数 | 返回 `{orphan_count, sample_doc_ids}` | 回填/清理历史断链 chunk |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `File`（ORM） | `models.py` | 建/查 files 记录作为 doc_id 锚点 |

### 数据模型

#### 已有表改动

| 表名 | 改动 | 新增字段/索引 |
|------|------|-------------|
| `knowledge_chunks` | 无结构变更（M1 仅语义收紧） | —（去重/状态字段在 M5/M7 加入） |

### 模块验收检测

```bash
# 验收 1：batch_insert 拒绝缺 doc_id
uv run python -c "from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo; KnowledgeChunkRepo().batch_insert([{'text':'x','index':0}], [[0.0]*1024], {})"
→ 预期输出：抛出 ValueError（"doc_id is required"）

# 验收 2：目录扫描入库后 doc_id 指向 files 记录
uv run python scripts/ingest_knowledge.py --dir <测试目录> --uploaded-by <真实user_uuid> --confidentiality 1 --dry-run
→ 预期输出：预览中每个文件对应一条 files 记录（file_no 生成），chunk 的 doc_id = 该 files.id

# 验收 3：历史断链数据可被识别
uv run python scripts/backfill_doc_id.py --dry-run
→ 预期输出：orphan_count ≥ 0，sample_doc_ids 列出 doc_id 无对应 files 记录的 chunk
```

**失败处理**：若验收 1 未抛异常，说明回退逻辑未移除，检查 `batch_insert` 的 `doc_meta.get("doc_id", str(uuid.uuid4()))` 是否已改为必填校验；若验收 2 预览 doc_id 非 files.id，检查 `ingest_knowledge.py` 是否在 `batch_insert` 前调用了 `ensure_file_record`。

---

## M2: 可见文件范围解析器（VisibleFileSetResolver）

**依赖**：无

**层级**：Service / Repository

**职责**：给定 user_id 及其权限上下文，实时计算可见文件集合 = ①上传者自有集 ∪ ②公开文件集 ∪ ③节点可见集 ∩ 密级约束 ∪ ④显式授权，作为 RAG 检索的前置过滤器。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `VisibleFileSetResolver` | 类 | — | 可见集唯一权威来源 |
| `resolve_visible_file_ids(user_id: str, *, company_id: str, info_level: str, explicit_ok: bool = True) -> Select` | 方法 | 输入用户与权限上下文，返回 SQLAlchemy 子查询（file_id 集合） | 实时计算 ①∪②∪③∪④ |
| `resolve_visible_file_list(user_id: str, **ctx) -> list[str]` | 方法 | 同上，物化为 list（供小集合/调试） | 与上者同源，仅物化 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `PermissionService.build_permission_dict(user_id)` | `permission_service.py` | 取 `company_id`、`info_level` |
| `File` / `NodeParticipantCompany` / `NodeAccessibleFile` / `ProjectNode` / `SessionAccessibleFile`（ORM） | `models.py` | 三来源 + 显式授权的查询基表 |

### 核心算法/策略

| 算法/策略 | 用途 | 选型理由 | 备选方案 |
|----------|------|---------|---------|
| 实时计算 + SQL 子查询 | 生成可见 file_id 集合 | 「可见性是关系」，实时算避免快照过期；子查询避免超大 IN-list 的 Python 内存/ SQL 长度问题 | 复用 `session_accessible_files` 快照（需扩展①/②来源并保证同步，弃用） |
| ③节点来源 = `node_participant_companies` | 判定企业参与节点 | 符合 PRD「可见性是关系」语义，企业真实参与为准 | `function_scope`/`authorized_node_ids`（职能授权，语义不符，见 D1） |
| 密级映射 `{public:0, internal:1, confidential:2, secret:3}` | ③密级约束 | 与现有 `sync_for_user` 的映射一致 | — |

### 数据模型

无数据模型变更（纯查询，复用现有 `files` / `node_participant_companies` / `node_accessible_files` / `project_nodes` / `session_accessible_files` 表）。

### 模块验收检测

```bash
# 验收 1：上传者自有集（①）不受密级约束
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT id, uploaded_by, confidentiality FROM files WHERE uploaded_by='<user_uuid>' AND is_deleted=false LIMIT 5;"
uv run python scripts/rag_visible_check.py --user-id <user_uuid>
→ 预期输出：可见集含该 user 所有上传文件（含 confidentiality=3）

# 验收 2：公开文件集（②）对任意用户可见
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT id, confidentiality FROM files WHERE confidentiality=0 AND is_deleted=false LIMIT 5;"
uv run python scripts/rag_visible_check.py --user-id <任一访客uuid>
→ 预期输出：可见集含全部 confidentiality=0 文件

# 验收 3：节点可见集（③）按企业参与 + 密级约束
# 构造：某节点经 node_participant_companies 绑定公司 C，经 node_accessible_files 绑定文件 F（confidentiality=2）
uv run python scripts/rag_visible_check.py --user-id <公司C成员uuid>
→ 预期输出：info_level≥confidential 的用户可见 F；info_level=public 的用户不可见 F
```

**失败处理**：若①未覆盖高密级自传文件，检查①分支是否漏加 `is_deleted=false` 或是否误加了密级条件；若③不生效，核对 D1 决策（节点来源是否用了 `node_participant_companies`）与 `visibility_mode` 两种模式是否都覆盖。

---

## M3: RAG 检索范围过滤（search_dense allowlist + provider 透传）

**依赖**：M1

**层级**：Repository + Provider

**职责**：让向量检索在「排序前」按 `doc_id IN 可见集` 过滤，实现宁少答、不泄露。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `KnowledgeChunkRepo.search_dense` | 方法（修改） | `search_dense(embedding, top_k=5, threshold=0.3, doc_ids: Select | list[str] | None = None) -> list[dict]` | 新增 `doc_ids` 参数，SQL 加 `doc_id IN (doc_ids)` |
| `RagProvider.search` | 抽象方法（**标注：修改接口签名**） | `search(query, top_k=5, stage=None, role=None, scoped_doc_ids: Select | list[str] | None = None) -> RagSearchResponse` | 新增可选 `scoped_doc_ids` |
| `PgVectorRagProvider._search_impl` | 方法（修改） | 透传 `scoped_doc_ids` 至 `search_dense` | — |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `VisibleFileSetResolver.resolve_visible_file_ids()` | M2 | 生成可见集子查询（由 M4 传入） |

### 数据模型

无数据模型变更。

### 模块验收检测

```bash
# 验收 1：search_dense 带 doc_ids 过滤
uv run python -c "
from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
# 用一份已知可见 file_id 集，断言返回结果的 doc_id 全部 ∈ 集合
rows = KnowledgeChunkRepo().search_dense([0.0]*1024, top_k=10, doc_ids=['<file_id_a>','<file_id_b>'])
assert all(r['doc_id'] in {'<file_id_a>','<file_id_b>'} for r in rows)
print('OK', len(rows))
"
→ 预期输出：OK <n>，且无越界 doc_id

# 验收 2：doc_ids=None 时行为向后兼容（不过滤）
uv run python -c "from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo; print(len(KnowledgeChunkRepo().search_dense([0.0]*1024, top_k=5)))"
→ 预期输出：返回 ≤5 条，不抛异常
```

**失败处理**：若验收 1 出现越界 doc_id，检查 SQL 过滤条件是否加在 `.filter()` 中且用 `KnowledgeChunk.doc_id.in_(doc_ids)`；若验收 2 抛异常，检查 `doc_ids` 为 None 时是否仍拼了空 IN 子句。

---

## M4: 检索链集成（knowledge_search 注入 user_id + 可见集）

**依赖**：M2, M3

**层级**：Tool + Registry

**职责**：在 `knowledge_search` 调用链中注入当前用户身份，解析可见集并下传到检索，堵住越权入口（F1~F5）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `handle_knowledge_search` | 方法（修改） | `handle_knowledge_search(params: dict, rag_provider: RagProvider, user_id: str | None = None, resolver: VisibleFileSetResolver | None = None) -> dict` | 新增 user_id/resolver 入参 |
| `_rag(params, **kw)` | 闭包（修改） | 从框架上下文取 user_id → 解析可见集 → 调 handler | `registry._register_base()` 内 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `PermissionService.build_permission_dict(user_id)` | `permission_service.py` | 取 `company_id`/`info_level` |
| `VisibleFileSetResolver.resolve_visible_file_ids()` | M2 | 生成可见集 |
| `rag_provider.search(..., scoped_doc_ids=...)` | M3 | 下传范围 |

### 数据模型

无数据模型变更。

### 模块验收检测

```bash
# 验收 1：F4 越权封堵 —— 两个不同用户检索结果落在各自可见集内
uv run python scripts/rag_visible_check.py --user-id <user_A> --query "施工规范"
uv run python scripts/rag_visible_check.py --user-id <user_B> --query "施工规范"
→ 预期输出：两者命中 doc 的 doc_id 均分别 ∈ 各自可见集，且 B 无法命中仅 A 可见的机密文件

# 验收 2：F1~F5 功能链 —— 用户可见文件数 > 0 且检索走过滤
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT count(*) FROM session_accessible_files WHERE user_id='<user_A>';"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查知识库里关于施工工艺的资料" --sender "<真实用户名>"
→ 预期输出：回复内容仅来自该用户可见文件，无越权内容

# 验收 3：访客落点 —— 无节点/企业用户可见 = ①∪②
uv run python scripts/rag_visible_check.py --user-id <访客uuid>
→ 预期输出：可见集 = 自传文件 + 公开文件，③为空
```

**失败处理**：若验收 1 中 B 仍命中 A 的机密文件，说明 user_id 未正确注入或 resolver 返回了未过滤的全集，检查 `registry._register_base` 的 `_rag` 闭包是否取到 user_id、handler 是否把 `scoped_doc_ids` 传给了 `search`。

---

## M5: 文档解析 + 结构分块 + 去重（入库管线增强）

**依赖**：M1

**层级**：Service + 脚本

**职责**：对标 Cherry 的能力 1~3：多格式解析路由、结构断点分块、content/embedding 哈希去重，替换当前「UTF-8 纯文本 + 段落粗切 + 无去重」。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `DocumentParser.parse(local_path: Path) -> str` | 新增方法 | 按扩展名路由（md/txt 直读；pdf/docx 走 `pymupdf`/`unstructured`），失败降级 | 解析路由 |
| `StructuralChunker.chunk(text: str, *, max_len: int = 512, overlap: int = 64) -> list[dict]` | 新增方法 | 结构断点打分 + token 精修，返回 `[{text, index, heading}]` | 分块 |
| `DedupChecker.is_duplicate(content_hash: str) -> bool` | 新增方法 | 按 content sha256 判断重复 | 去重 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `KnowledgeChunkRepo.batch_insert` | M1 | 写入（doc_id 已锚定） |
| `TeiClient.embed` | 现有 | embedding 用于 embedding 级去重 |

### 数据模型

#### 已有表改动

| 表名 | 改动 | 新增字段/索引 |
|------|------|-------------|
| `knowledge_chunks` | 新增字段 | `content_hash VARCHAR(64) DEFAULT ''`（content sha256） |
| `knowledge_chunks` | 新增索引 | `idx_kc_content_hash`（`content_hash`） |

### 模块验收检测

```bash
# 验收 1：多格式解析降级
uv run python -c "from emily_core.services.document_parser import DocumentParser; print(DocumentParser().parse(Path('<样例.pdf>'))[:50])"
→ 预期输出：返回非空文本；若 pdf 解析失败则降级到可读文本或明确报错

# 验收 2：结构分块 offset 不变量
uv run python -c "from emily_core.services.structural_chunker import StructuralChunker; cs=StructuralChunker().chunk('<长文本>'); print(len(cs), cs[0]['heading'])"
→ 预期输出：chunk 数 > 1，首 chunk 含 heading 标记

# 验收 3：去重
uv run python scripts/ingest_knowledge.py --dir <含重复文件目录> --dry-run
→ 预期输出：预览中重复文件被标记 skip，chunk_count 不含重复
```

**失败处理**：若解析失败无降级，检查 `DocumentParser.parse` 是否实现失败回退分支；若分块 offset 断裂，检查 chunk 边界是否按 heading 断点而非硬切。

---

## M6: 混合检索 + 重排（检索增强）

**依赖**：M3

**层级**：Repository + Provider

**职责**：对标 Cherry 能力 5~6：BM25 + 向量 + RRF 混合，可选 rerank 失败降级，提升召回与排序质量（复用 M3 的 allowlist）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `KnowledgeChunkRepo.search_hybrid` | 新增方法 | `search_hybrid(query, embedding, top_k=5, doc_ids=None) -> list[dict]` | BM25（pg_trgm/tsvector）+ 向量 + RRF 融合，内部复用 `doc_ids` 过滤 |
| `RagProvider.search` | 方法（修改） | 增加可选 `rerank: bool = False`（标注改签名） | 触发 rerank |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `KnowledgeChunkRepo.search_dense` | M3 | 向量分支（含 allowlist） |
| 可选 rerank 服务 | 新增/现有 | 重排，失败降级到向量分数 |

### 核心算法/策略

| 算法/策略 | 用途 | 选型理由 | 备选方案 |
|----------|------|---------|---------|
| RRF（Reciprocal Rank Fusion） | 融合 BM25 与向量 | 无需调参、对分数尺度不敏感，业界标准 | 加权求和（需调参，弃用） |
| BM25 用 pg_trgm | 关键词召回 | 复用 PostgreSQL，避免引入 ES | 自建倒排（过重，弃用） |
| rerank 可选 + 降级 | 精排 | 质量与延迟平衡，失败不阻断 | 强制 rerank（延迟高，弃用） |

### 数据模型

无数据模型变更（复用 `knowledge_chunks` 与 pg 内置能力）。

### 模块验收检测

```bash
# 验收 1：混合检索命中不越界
uv run python -c "
from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
rows = KnowledgeChunkRepo().search_hybrid('施工', [0.0]*1024, top_k=10, doc_ids=['<file_id_a>'])
assert all(r['doc_id']=='<file_id_a>' for r in rows)
print('OK', len(rows))
"
→ 预期输出：OK <n>，doc_id 均 ∈ 允许集

# 验收 2：rerank 失败降级
uv run python scripts/rag_visible_check.py --user-id <user> --query "施工规范" --rerank
→ 预期输出：rerank 不可用/失败时仍返回向量结果，不报错
```

**失败处理**：若混合检索越界，检查 `search_hybrid` 是否把 `doc_ids` 下传到 BM25 与向量两个分支；若 rerank 失败阻断，检查是否实现 try/except 降级路径。

---

## M7: 入库状态机（作业可靠性）

**依赖**：M5

**层级**：Scheduler / Job

**职责**：对标 Cherry 能力 7：入库作业 5 态状态机 + 崩溃恢复，接入 `JobHandlerRegistry` + `scheduler_config.json`。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `IngestJobHandler` | 新增类 | 实现 `JobHandlerRegistry` 协议 | 入库作业处理器 |
| `run(job_ctx) -> dict` | 方法 | 输入作业上下文，输出 `{status, indexed, failed, next_state}` | 状态机主流程 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `JobHandlerRegistry` | `scheduler` | 注册入库作业 |
| `DocumentParser` / `StructuralChunker` / `DedupChecker` | M5 | 解析/分块/去重 |
| `KnowledgeChunkRepo.batch_insert` | M1 | 写入 |

### 数据模型

#### 已有表改动

| 表名 | 改动 | 新增字段/索引 |
|------|------|-------------|
| `knowledge_chunks` | 新增字段 | `ingest_status VARCHAR(20) DEFAULT 'pending'`（pending/parsing/embedding/indexed/failed） |
| `knowledge_chunks` | 新增索引 | `idx_kc_ingest_status`（`ingest_status`） |

#### 状态机

| 当前状态 | 允许转换到 | 触发条件 |
|---------|-----------|---------|
| `pending` | `parsing` | 作业领取 |
| `parsing` | `embedding`, `failed` | 解析成功/失败 |
| `embedding` | `indexed`, `failed` | 向量化+写入成功/失败 |
| `indexed` | — | 终态 |
| `failed` | `pending` | 崩溃恢复/手动重试 |

### 模块验收检测

```bash
# 验收 1：作业注册成功
uv run python scripts/scriptmgr.py check
→ 预期输出：入库作业 handler 被列出，无 stub 告警

# 验收 2：失败重试恢复
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT ingest_status, count(*) FROM knowledge_chunks GROUP BY ingest_status;"
→ 预期输出：failed 记录可通过重跑作业转入 pending/indexed
```

**失败处理**：若作业未注册，检查是否在 `JobHandlerRegistry` + `scheduler_config.json` 双处登记；若失败态无法恢复，检查状态机是否缺少 `failed→pending` 转换。

---

## M8: 引用溯源（citation）

**依赖**：M4

**层级**：Tool + Provider

**职责**：对标 Cherry 能力 8：检索结果携带来源 id + 标题，工具层组装 `[cite:id]` 溯源，供 session-agent 组织回复（不改变范围外应答归属）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `SearchResult` | 数据类（修改） | 新增 `source_file_id: str = ""`、`source_title: str = ""` | 来源信息 |
| `PgVectorRagProvider._search_impl` | 方法（修改） | 组装结果时填充 `source_file_id = row["doc_id"]`、`source_title = row["doc_name"]` | — |
| `handle_knowledge_search` | 方法（修改） | 返回 `chunks[]` 增 `cite_id`、`title`；`reply` 附 `[cite:{doc_id}]` | citation 组装 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `rag_provider.search()` | M4 | 取带来源的结果 |

### 数据模型

无数据模型变更（`doc_id`/`doc_name` 已存在）。

### 模块验收检测

```bash
# 验收 1：检索结果携带来源
uv run python scripts/rag_visible_check.py --user-id <user> --query "施工规范"
→ 预期输出：每条 chunk 含 cite_id（=files.id）与 title（=file_no）

# 验收 2：reply 含 citation 标记
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查施工工艺" --sender "<真实用户名>"
→ 预期输出：回复素材中出现 [cite:<file_id>] 与来源标题
```

**失败处理**：若 chunk 无 cite_id，检查 `_search_impl` 是否从 `row["doc_id"]`/`row["doc_name"]` 填充了 `SearchResult` 新字段；若 reply 无标记，检查 handler 是否按 `chunks[]` 重新组装了 reply 文本。

---

## 组装验证

所有模块完成后，运行端到端组装验证：

| 验证项 | 验证方式 | 预期结果 |
|--------|---------|---------|
| 数据层正确 | SQL 查 `knowledge_chunks.doc_id` 均对应 `files.id` | 无断链孤儿 |
| 接口契约正确 | `search_dense`/`search_hybrid` 带 doc_ids 调用 | 返回结果 doc_id ∈ 允许集 |
| 核心流程正确 | 两个不同用户检索同一 query | 命中互不越权 |
| 异常路径正确 | 访客/无权限用户检索 | 可见=①∪②，机密文件不可见 |

```bash
# 端到端组装验证命令
uv run python scripts/rag_visible_check.py --user-id <user_A> --query "施工规范"
uv run python scripts/rag_visible_check.py --user-id <访客> --query "施工规范"
docker exec emily-postgres psql -U emily -d emily -c \
"SELECT k.doc_id, f.confidentiality, f.uploaded_by FROM knowledge_chunks k LEFT JOIN files f ON k.doc_id=f.id WHERE f.id IS NULL LIMIT 5;"
→ 预期输出：两次检索结果均落在各自可见集内；最后一条 SQL 返回 0 行（无断链）
```

---

## 阶段反思指令

每完成一个模块的设计，在进入下一个模块之前，执行以下反思：

1. **检查设计完整性**：本模块的接口契约、数据模型、验收检测是否完整。
2. **检查设计偏差**：是否有与 PRD 不符的设计？记录差异。
3. **判断是否继续**：
   - 偏差 ≤ 1 个接口调整 → 直接修改设计文档对应模块，继续。
   - 偏差 2-4 个接口或模块职责调整 → 在设计文档末尾追加 "v1.1 修订记录"，继续。
   - 偏差 > 4 个接口或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成设计。

---

*本设计为概要设计（SD），由 req-plan 技能生成。*
