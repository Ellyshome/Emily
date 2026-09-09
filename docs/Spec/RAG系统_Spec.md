# Emily RAG 系统 — 技术规格说明（Spec）

> 版本：1.0　更新：2026-09-09
> 范围：描述 emily-core 当前**已实现**的 RAG（检索增强生成）知识库子系统，作为维护与后续开发的事实基准。
> 说明：本文件描述"现状实现"，不描述需求愿景；与实现不一致的历史文档以本文件为准。

---

## 1. 系统定位

Emily 的 RAG 子系统解决"**在对话/SOP 执行时，从知识库检索与当前用户可见且相关的领域知识**"。

知识分两类来源（工具描述口径）：
1. **项目资料**：规范标准、施工工艺、政策法规、项目文档等（锚定项目，受可见性约束）。
2. **公司规章制度**：管理办法、制度、操作规程等。

核心设计原则（代码 docstring 反复出现）：
- **可见性是关系而非属性**：检索范围 = 实时解析的"用户可见文件集合"，不使用会话快照，避免过期越权。
- **宁少答不泄露**：可见文件集是检索前置的唯一权威过滤。
- **doc_id 必须锚定 files.id**：`batch_insert` 拒绝无 `doc_id` 的写入（拒绝回退 uuid4），保证 chunk 可溯源到文件。

---

## 2. 总体架构与分层

```
┌─────────────────────────────────────────────────────────────┐
│ 会话层  SessionAgent / SessionScheduler / SessionFactory    │
│   SessionContext.create() = 全量知识灌注（权限/记忆/世界书/    │
│   rag_available 标记 —— 注意：不预灌 knowledge_chunks）       │
└──────────────────────────┬──────────────────────────────────┘
                           │ SOP WorkItem 驱动（RealExecutor）
┌──────────────────────────▼──────────────────────────────────┐
│ 工具层  knowledge_search（基座能力，注册 BusinessFlowTool    │
│         Registry，不走 LLM function calling）               │
│         parse_document / chunk_text / embed_and_index       │
└──────────────────────────┬──────────────────────────────────┘
┌──────────────────────────▼──────────────────────────────────┐
│ 服务/守卫层  PermissionService → VisibleFileSetResolver      │
│              （可见文件集合实时计算，唯一权威）               │
└──────────────────────────┬──────────────────────────────────┘
┌──────────────────────────▼──────────────────────────────────┐
│ Provider 层  RagProvider 抽象                                │
│    ├─ PgVectorRagProvider（pgvector + TEI/远程 Embedding）   │
│    └─ LocalFileRagProvider（无向量服务时的关键词兜底）        │
└──────────────────────────┬──────────────────────────────────┘
┌──────────────────────────▼──────────────────────────────────┐
│ 存储/写侧   KnowledgeChunkRepo（向量入库/检索）              │
│            DocumentParser → StructuralChunker → DedupChecker│
│            TeiClient / RemoteEmbeddingClient                │
└─────────────────────────────────────────────────────────────┘
```

核心文件路径（相对 emily-core/emily_core）：
- 工具：[tools/knowledge_search_tool.py](d:/app/Emily/emily-core/emily_core/tools/knowledge_search_tool.py)
- 可见性：[services/visible_file_set_resolver.py](d:/app/Emily/emily-core/emily_core/services/visible_file_set_resolver.py)、[services/permission_service.py](d:/app/Emily/emily-core/emily_core/services/permission_service.py)
- 检索 Provider：[providers/rag/pgvector_provider.py](d:/app/Emily/emily-core/emily_core/providers/rag/pgvector_provider.py)、[providers/rag/local_fallback.py](d:/app/Emily/emily-core/emily_core/providers/rag/local_fallback.py)、[providers/rag/base.py](d:/app/Emily/emily-core/emily_core/providers/rag/base.py)
- Repository：[repositories/knowledge_chunk_repo.py](d:/app/Emily/emily-core/emily_core/repositories/knowledge_chunk_repo.py)
- 装配：[bootstrap.py](d:/app/Emily/emily-core/emily_core/bootstrap.py)、[config.py](d:/app/Emily/emily-core/emily_core/config.py)

---

## 3. 数据模型

### 3.1 `knowledge_chunks`（KnowledgeChunk，[models.py](d:/app/Emily/emily-core/emily_core/infrastructure/database/models.py)）

| 列 | 类型 | 说明 |
|---|---|---|
| id | UUID String | 主键 |
| doc_id | String (indexed) | **锚定 files.id**；同一文档多 chunk 共用 |
| doc_name | String | 源文档名 |
| chunk_index | Integer | chunk 序号 |
| chunk_text | Text | chunk 原文 |
| embedding | Vector(1024)（pgvector 不可用则 Text） | BGE-m3 密集向量 |
| metadata_（物理列 `metadata`） | Text JSON | 文档级元数据（含 doc_name/file_no/collection/stage/role 等） |
| content_hash | String(64) | chunk 原文 sha256（去重用） |
| ingest_status | String(20) | pending/parsing/embedding/indexed/failed（状态机） |
| created_at | String | 创建时间 |

> 现状注：**没有** `retrieval_count`、`file_no` 列（file_no 只存在于 File 表与 metadata JSON）；全库未建 HNSW/ivfflat 向量索引，检索为顺序扫描 + cosine 排序。

### 3.2 `files`（File）
RAG 相关关键列：
- `file_no`（唯一）、`project_id`、`filename`、`storage_path`、`uploaded_by`
- `confidentiality`：0=公开 1=内部 2=机密 3=绝密
- `rag_indexed`：是否已入知识库；`rag_collection`：general_reference / project_<id>
- `is_deleted`、`file_category`、`source_module_type`、`purpose`/`purpose_confirmed`

### 3.3 可见性关系表
- `project_nodes.visibility_mode`：`specific`（按 node_accessible_files 绑定）/ `all_project_files`（全项目文件）
- `node_accessible_files`：(node_id, file_id) —— specific 绑定
- `node_participant_companies`：(node_id, company_id) —— "企业参与节点"来源
- `session_accessible_files`：user_id+file_id+access_type（project_scope / node_linked / explicit）；RAG 检索只用 `explicit`

### 3.4 `rag_retrieval_logs`（RAGRetrievalLog）
检索日志：pipeline_run_id、conversation_id、user_id、query_text、provider、hit_count、top_score、avg_score、results_summary、was_used_by_llm、latency_ms、error_summary、created_at。

---

## 4. 入库（写侧）管线

### 4.1 模块职责
| 阶段 | 实现 | 说明 |
|---|---|---|
| 解析 | [services/document_parser.py](d:/app/Emily/emily-core/emily_core/services/document_parser.py) | md/txt/pdf(pymupdf)/docx(python-docx)，异常降级直读 |
| 分块 | [services/structural_chunker.py](d:/app/Emily/emily-core/emily_core/services/structural_chunker.py) | 按 `#/##/###` 标题切段，超长按换行/句号断开，overlap=64，返回 {text,index,heading} |
| 去重 | [services/dedup_checker.py](d:/app/Emily/emily-core/emily_core/services/dedup_checker.py) | `content_hash`=sha256；`is_duplicate` 对**全表**查重；批内另用 seen_hashes |
| Embedding | [infrastructure/embedding/tei_client.py](d:/app/Emily/emily-core/emily_core/infrastructure/embedding/tei_client.py) / remote_client.py | TEI（BGE-m3 1024 维）或远程 OpenAI 兼容 API；无内部截断/batch，由调用方分批 |
| 入库 | [repositories/knowledge_chunk_repo.py](d:/app/Emily/emily-core/emily_core/repositories/knowledge_chunk_repo.py) `batch_insert` | 强制 doc_id 锚定 files.id，直接置 `ingest_status=indexed` |

### 4.2 三条入库链路
1. **文件工具自动入库**：[tools/file_tool.py](d:/app/Emily/emily-core/emily_core/tools/file_tool.py) `handle_record_file` 当 `purpose=="REFERENCE"` 异步 `_index_reference_file` → 读文件文本按段落切分 → `handle_embed_and_index` → `FileManager.set_rag_indexed(file_id, True, "general_reference")`。
2. **原子工具流水线**：[tools/parse_document_tool.py](d:/app/Emily/emily-core/emily_core/tools/parse_document_tool.py)（docling/MarkItDown）→ [tools/chunk_tool.py](d:/app/Emily/emily-core/emily_core/tools/chunk_tool.py)（langchain 分块）→ [tools/embed_tool.py](d:/app/Emily/emily-core/emily_core/tools/embed_tool.py) `handle_embed_and_index` → `repo.batch_insert`。
3. **调度状态机（M7）**：[scheduler/jobs/ingest.py](d:/app/Emily/emily-core/emily_core/scheduler/jobs/ingest.py)（action_type=rag_ingest）：failed→pending 恢复 → 逐 chunk parsing→embedding→indexed，失败置 failed。
4. **CLI/脚本批量**（非运行时）：见 §7。

> 线上另有每日文件解析 `daily_file_parse`（生成 content_summary 摘要，**不写 knowledge_chunks**）—— 与向量入库是两条独立流水线，勿混淆。

---

## 5. 检索可见性（越权封堵核心）

### 5.1 信息密级推导（[permission_service.py](d:/app/Emily/emily-core/emily_core/services/permission_service.py)）
```
_derive_info_level(level, is_management_unit):
    level >= 5                 → confidential
    is_management_unit(公司is_admin) → confidential
    level >= 2                 → internal
    else                       → public
```
密级映射：`public:0 / internal:1 / confidential:2 / secret:3`（`_INFO_LEVEL_MAP`）。
> 结论：推导最高只到 **confidential(2)**；**绝密(3)** 文件除 ①自传 或 ④显式授权 外，永不进入任何用户可见集。

### 5.2 可见文件集合公式（[visible_file_set_resolver.py](d:/app/Emily/emily-core/emily_core/services/visible_file_set_resolver.py)）
```
可见文件 = ① 上传者自有 ∪ ② 公开文件 ∪ ③ 节点可见∩密级 ∪ ④ 显式授权
```
- ① `uploaded_by == user_id`（自传永可见，不受密级）
- ② `confidentiality == 0`
- ③ 仅当 company_id 非空：参与节点（`node_participant_companies.company_id`）
  - `specific` → `node_accessible_files` 绑定文件
  - `all_project_files` → 该节点 project 全项目文件
  - 均需 `confidentiality <= max_conf`
- ④ `session_accessible_files.access_type=='explicit'`（显式授权，不受密级）
返回惰性 SQLAlchemy Select，由 `search_dense` 在**排序前**以 `doc_id IN (子查询)` 过滤。

### 5.3 调用链
`knowledge_search` handler（[knowledge_search_tool.py](d:/app/Emily/emily-core/emily_core/tools/knowledge_search_tool.py) `_resolve_scoped_doc_ids`）：
`PermissionService.build_permission_dict(user_id)` → `VisibleFileSetResolver.resolve_visible_file_ids(...)` → `scoped_doc_ids` 下传 provider。
> `session_accessible_files` 快照（FileManager 用）与 RAG 实时解析两套并存：RAG 用实时解析避免过期。

---

## 6. 检索执行

### 6.1 RagProvider 抽象（[providers/rag/base.py](d:/app/Emily/emily-core/emily_core/providers/rag/base.py)）
- `search(query, top_k=5, stage, role, scoped_doc_ids, rerank)` → `RagSearchResponse(query, results, context_text, total, provider_name)`
- `SearchResult(content, score, source_document, source_kb, metadata, source_file_id, source_title)`
- `is_available()`

### 6.2 PgVectorRagProvider（[providers/rag/pgvector_provider.py](d:/app/Emily/emily-core/emily_core/providers/rag/pgvector_provider.py)）
```
search(query):
    qvec = tei.embed([query])                  # 失败→空结果
    try: repo.search_hybrid(query, qvec, top_k, doc_ids=scoped_doc_ids)
    except: repo.search_dense(qvec, threshold=0.3, doc_ids=...)
    → metadata stage/role 精确过滤 → rerank(占位) → top_k → 结果映射
```
- 默认 `similarity=0.3`、`top_k=5`（工具最大 10）。
- **混合检索（M6）**（repo.search_hybrid）：dense(top_k*3, threshold=0.0) + keyword（pg_trgm similarity>0.05，降级 ILIKE）→ **RRF 融合**（k=60，`score=Σ1/(k+rank+1)`），两分支都先做 doc_ids allowlist。
- **rerank 为占位**，不改变排序。
- 命中结果 context_text 拼 `"[doc_name]\nchunk_text"`。

### 6.3 检索日志
`RAGRetrievalLogger`（[infrastructure/logging/rag_logger.py](d:/app/Emily/emily-core/emily_core/infrastructure/logging/rag_logger.py)）成功/失败均写 `rag_retrieval_logs`（失败写 error_summary、hit_count=0）；供 `evolution_repo.aggregate_rag_logs` + `scripts/evolution_metrics.py` 统计。写入失败不阻断主流程。**无 chunk 级 retrieval_count 回写。**

### 6.4 Provider 装配优先级（[bootstrap.py](d:/app/Emily/emily-core/emily_core/bootstrap.py)）
1. `kb_enabled` 开启且未注入 provider：
   - 远程 Embedding API（embedding_api_url/key/model）优先，次选本地 TeiClient(tei_url)
   - 二者其一可用 → `PgVectorRagProvider`
2. 都不可用 → `LocalFileRagProvider`（目录：/app/baseknowledge、/app/company_policies 或 emily-data 对应目录；关键词兜底，无可见性 doc_id 语义）
3. 配置项（[config.py](d:/app/Emily/emily-core/emily_core/config.py)）：`kb_enabled=False` 默认、`tei_url=http://tei:80`、`rag_similarity_threshold=0.3`

---

## 7. 运行入口 / 工具 / 测试脚本

### 7.1 工具（registry）
- `knowledge_search`：基座能力，SOP 引导 Agent 调用（RealExecutor 直接调用，不走 LLM function calling）。schema：query(必填)/top_k(≤10)/stage/role。
- `parse_document`、`chunk_text`、`embed_and_index`：文档→chunk→向量入库原子工具（embed_and_index 为 write 权限）。
- LLM 不可用或 provider 缺失时注册 stub（友好提示"知识库服务暂未就绪"）。

### 7.2 Session 编排
- [adapters/session/session_factory.py](d:/app/Emily/emily-core/emily_core/adapters/session/session_factory.py) `SessionFactory.create`：组装依赖 + 委托 `SessionContext.create()` 全量灌注后创建 `SessionAgent`。
- [session/session_context.py](d:/app/Emily/emily-core/emily_core/session/session_context.py)：灌注用户/项目/记忆/权限/可见文件摘要/rag_available/rag_collections；**只灌注标记，不预灌 knowledge_chunks 内容**。
- `SessionDataFetcher`（fetchers/fetch_rag_info.py）探测 provider 可用性 + collections 名注入 prompt；真正检索由 SOP 触发 `knowledge_search`。
- 最终回复合成把 WorkItem structured_result（含 rag_sources）注入 session_reply prompt（`_synthesize_final_reply`），附 M8 引用 `[cite:doc_id]`。

### 7.3 脚本（[scripts/](d:/app/Emily/scripts/)）
| 脚本 | 用途 |
|---|---|
| ingest_knowledge.py | 知识目录批量入库（--dir/--collection/--backend/--dry-run/--uploaded-by/--confidentiality） |
| rag_test_harness.py | RAG 改造验收执行器；独立模式（自建 18 文件 TC-01~17）/ `--env` 模式（env-test EMERALD 环境建库+验收；`--setup` 仅建库、`--rebuild` 重建） |
| rag_dry_run.py | 绕过 LLM 直调生产入口 handle_knowledge_search（测试入口=生产入口） |
| rag_batch_test.py | 批量入库+回归查询 |
| rag_visible_check.py | 手动校验可见集 + 命中均在可见集 |
| rag_governance.py | 零可见文件治理清单 |
| backfill_doc_id.py | 孤儿 chunk（doc_id 无对应 files）检测报告 |
| evolution_metrics.py | rag_retrieval_logs 聚合统计 |

### 7.4 测试环境（.claude/tool/env-test、.claude/skills/emy-test）
- `setup_test_env.ps1`：重置+种子+RAG 库（`Invoke-SeedRAGEnv`：014 访客用户 + `rag_test_harness.py --env --setup --rebuild`；`-SkipRAG` 跳过）。
- `014_seed_rag_visitor.sql`：无公司 L1 访客（TC-03/TC-10 访客落点：③ 为空，仅 ①∪②）。
- `008_seed_emerald_nodes.yaml`：EMERALD-01 节点树（manage_nodes.py 建树）。
- `010_seed_runtime_data.sql`：含 rag_retrieval_logs 模拟记录。
- 内置 baseknowledge（emily-data/baseknowledge）**不自动预灌**：仅当用 LocalFileRagProvider 启动扫描，或显式跑 ingest_knowledge.py / rag_test_harness.py。

---

## 8. 关键现状事实（易误解点）

1. **无 HNSW/ivfflat 索引**：pgvector 检索是顺序扫描 + cosine_distance 排序（Vector 列注释与实现不符）。
2. **info_level 推导最高 confidential**：绝密(3) 仅 ①自传 / ④explicit 可见。
3. **混合检索的 0.3 阈值不生效**（threshold=0.0），0.3 仅用于 hybrid 异常降级纯 dense 的路径。
4. **rerank 占位**，pgvector 与 LocalFile 均无自有 reranker。
5. **chunk 无检索计数回写**；命中统计仅落在 rag_retrieval_logs。
6. **session_factory.py 确实存在**（adapters/session/），"全量知识灌注"= SessionContext.create() 的数据灌注（不含 knowledge_chunks 向量内容预载）。
7. **world book / 规则书 / 系统描述**（Session prompt 文本注入）与 **knowledge_chunks**（RAG 向量检索）是两条不同知识管线。
8. RAG 可见性 = **实时关系计算**（File+节点+参与企业+explicit），独立于 FileManager 的快照表。

---

## 9. 快速定位清单

- 检索工具入口：tools/knowledge_search_tool.py
- 可见性守卫：services/visible_file_set_resolver.py、services/permission_service.py
- Provider：providers/rag/{base,pgvector_provider,local_fallback}.py
- 入库 repo：repositories/knowledge_chunk_repo.py
- 解析/分块/去重：services/{document_parser,structural_chunker,dedup_checker}.py
- Embedding：infrastructure/embedding/{tei_client,remote_client}.py
- 装配：bootstrap.py、config.py
- 调度入库：scheduler/jobs/ingest.py
- 文件 REFERENCE 入库：tools/file_tool.py、services/file_manager.py
- 会话灌注：adapters/session/session_factory.py、session/session_context.py、session/session_data_fetcher.py
- 测试脚本：scripts/rag_*.py、scripts/ingest_knowledge.py
- 环境工具：.claude/tool/env-test/、.claude/skills/emy-test/
