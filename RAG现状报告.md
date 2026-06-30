# Emily 项目 RAG（知识库检索）现状报告

> 生成时间：2026-06-30 | 基于生产环境（Docker Compose）实际探查

---

## 一、存量（数据现状）

MaxKB 容器当前已索引完成，可检索。

### 知识库

| 属性 | 值 |
|------|-----|
| 名称 | `Emily仓库` |
| ID | `019ef8e0-e431-7ea1-8a62-eb75b599d210` |
| 类型 | 0（通用型） |
| scope | WORKSPACE |
| 工作区 | default |

### 文档（6 篇，均已嵌入完成 status=nnn2）

| 文档 | 原文件大小 | 字符数 |
|------|----------|--------|
| 材料文件.md | — | 27,219 |
| 生命周期.md | — | 23,478 |
| 组织管理体系.md | — | 6,145 |
| 消防验收指南.md | — | 3,126 |
| TCSES 77-2022《城市景观水体水质提升技术指南》.pdf | 7.1 MB | 290 |
| 城市绿化工程竣工验收服务指南（2024版）.docx | 25.7 KB | 770 |

### 向量数据

| 指标 | 数量 |
|------|------|
| 分段（paragraph） | 151 |
| 向量（embedding） | 328 |
| 向量维度 | 1024 |
| 索引类型 | HNSW（cosine） |

### 检索引擎

| 属性 | 值 |
|------|-----|
| Embedding 模型 | Qwen3-Embedding-0.6B |
| 部署方式 | 本地模型（model_local_provider），文件挂载到 maxkb 容器 |
| 模型路径 | `/opt/maxkb-app/model/embedding/Qwen3-Embedding-0.6B` |
| 检索后端 | pgvector（HNSW 索引，cosine 相似度） |

---

## 二、存储架构

```
┌─ 宿主机文件系统
│   emily-data/baseknowledge/                    ← 原始文档（bind-mount 到 maxkb 只读）
│   ├─ 生命周期.md
│   ├─ 消防验收指南.md
│   ├─ 材料文件.md
│   ├─ 组织管理体系.md
│   ├─ TCSES 77-2022…指南.pdf
│   └─ 城市绿化工程竣工验收服务指南.docx
│
│   D:\app\Qwen\Qwen3-Embedding-0___6B/          ← Embedding 模型文件（bind-mount 只读）
│
└─ Docker 容器层

┌─ maxkb 容器（1panel/maxkb:latest）
│   ├─ /opt/maxkb-app/data/emily/                ← 文档挂载点（只读）
│   ├─ Django ORM + REST API                     ← 导入文档 → 分段 → 向量化
│   ├─ Celery 异步向量化任务
│   ├─ Qwen3-Embedding-0.6B 本地推理             ← embedding 生成
│   └─ 内置 PostgreSQL（maxkb 库，用户 root）      ← pgvector 存储
│       ├─ document    — 6 rows
│       ├─ paragraph   — 151 rows（分段结果 + 原文）
│       ├─ embedding   — 328 rows（向量 + search_vector）
│       └─ model       — 1 row（maxkb-embedding / EMBEDDING）
│
├─ emily-postgres 容器（postgres:16-alpine）
│   └─ emily 库                                   ← 业务数据，不含 RAG 向量
│
└─ emily-core 容器
    ├─ providers/rag/maxkb_provider.py            ← MaxKB hit_test HTTP 客户端
    └─ providers/rag/local_fallback.py            ← 本地 TF-IDF 关键词回退
```

**关键点**：MaxKB 使用**独立的 PostgreSQL**（内嵌在 maxkb 容器内），不是 `emily-postgres` 容器中的 `emily` 库。两边数据完全隔离。

---

## 三、RAG 使用路径（代码调用链）

```
用户消息 → SessionAgent（意图路由）
  │
  └─ SOP 匹配（如 SOP-005 / SOP-999 兜底）
       │
       ▼
     Planner（_llm_plan()）
       │  system prompt 动态注入 available_tools 列表
       │  SOP 第3条规则："需要查询领域知识时，用 knowledge_search"
       │
       ▼
     PlanStep(tool_name="knowledge_search",
              tool_params={query, top_k, stage, role})
       │
       ▼
     node3_execute() → _real_execute(plan, context)
       │  tool_name in BusinessFlowToolRegistry["knowledge_search"]
       │
       ▼
     handle_knowledge_search(params, rag_provider)
       │  ① 检查可用性：is_available()
       │         → POST /admin/api/user/login → 获取 Bearer token
       │  ② 执行检索：rag_provider.search(query, top_k, stage, role)
       │         → POST /admin/api/workspace/default/
       │            knowledge/{knowledge_id}/hit_test
       │            { query_text, top_number, similarity, search_mode }
       │
       ▼
     MaxKB Admin hit_test API（非公开接口，但稳定可用）
       │  pgvector HNSW cosine 检索引擎
       │  返回命中段落 + 相似度分数（不经过 LLM）
       │
       ▼
     RagSearchResponse
       ├─ results: [SearchResult(content, score, source_document)]
       └─ context_text: markdown 格式检索结果摘要

     ← 返回 { success, reply, rag_results_data: {query, provider, chunks[], hit_count, elapsed_ms} }

       │
       ▼
     RealExecutor 构建 StepResult
       ├─ ToolCallRecord(tool_name="knowledge_search", ...)    ← 工具调用可追踪
       ├─ RagResult(RagChunk(content, score, doc_name)[])      ← 正规 dataclass
       └─ StepResult(rag_results=[...], tool_calls=[...])

       │
       ▼
     node4_summary()
       │  若 rag_hits > 0：
       │   "根据知识库检索，找到以下相关信息：
       │    根据《消防验收指南.md》：消防验收需提交：..."
       │
       ▼
     Guardian审核 → 出站回复（SSE → AstrBot → QQ）
```

### Provider 双实现

| Provider | 检索方式 | 依赖 | 使用条件 |
|----------|---------|------|---------|
| `MaxKBRagProvider` | MaxKB hit_test HTTP API（pgvector HNSW） | aiohttp | `kb_enabled=true` + 凭据配置 |
| `LocalFileRagProvider` | 本地 TF-IDF 关键词（Python 标准库） | 无 | 配置文件目录存在即可，当前未激活 |

---

## 四、当前配置状态

| 配置项 | 值 | 位置 |
|--------|-----|------|
| `kb_enabled` | `false` | `core_config.json` + 无环境变量覆盖 |
| `maxkb_url` | `http://maxkb:8080`（默认） | `config.py` |
| `maxkb_admin_password` | 未配置 | 需通过环境变量注入 |
| `maxkb_knowledge_id` | 未配置 | 需通过环境变量注入 |
| `maxkb_search_mode` | `embedding` | `core_config.json` |
| `kb_top_k` | `5` | `core_config.json` |
| `maxkb_similarity_threshold` | `0.3`（默认） | `config.py` |
| MaxKB 容器 | 运行中（8080 端口可达） | Docker Compose |
| 知识库文档 | 6 篇，全部 status=nnn2（可检索） | maxkb PgSQL |
| Embedding 向量 | 328 条，HNSW 索引就绪 | maxkb PgSQL |

---

## 五、代码侧状态

| 组件 | 状态 |
|------|------|
| `MaxKBRagProvider` | ✅ 完整实现（264 行），含登录、token 缓存、自动重试 |
| `LocalFileRagProvider` | ✅ 完整实现（309 行），零依赖 TF-IDF 回退 |
| `RagProvider` 抽象基类 | ✅ 含 `RagSearchResponse` + `SearchResult` dataclass |
| `handle_knowledge_search` | ✅ M14 handler，注册到 `BusinessFlowToolRegistry` |
| `BusinessFlowToolRegistry` 条件注册 | ✅ `rag_provider is not None` 时自动注册为第 6 个基座工具 |
| Planner prompt 动态注入 | ✅ `_llm_plan()` 从 `BusinessFlowToolRegistry.list_names()` 构建 `{available_tools}` |
| RealExecutor 分发器 RAG 支持 | ✅ 检测 `handler_dict["rag_results_data"]` 自动构建 `RagResult`/`RagChunk` |
| `StepResult.rag_results` | ✅ Pipeline 接口已定义 `RagResult`/`RagChunk` dataclass |
| `node4_summary` RAG 回复组装 | ✅ `rag_hits > 0` 时格式化引用来源 |
| `bootstrap.py` RAG 初始化 | ✅ `kb_enabled=true` 时创建 `MaxKBRagProvider` 并注入 |

---

## 六、如何启用

在 Docker Compose 环境变量中补充以下 3 项，重启 `emily-core` 即可：

```yaml
emily-core:
  environment:
    - EMILY_KB_ENABLED=true
    - EMILY_MAXKB_ADMIN_PASSWORD=<MaxKB管理员密码>
    - EMILY_MAXKB_KNOWLEDGE_ID=019ef8e0-e431-7ea1-8a62-eb75b599d210
```

`bootstrap.py` 第 98-110 行会在 `kb_enabled=true` 且凭据有效时自动创建 `MaxKBRagProvider`。`_init_phase_c_deps()` 检测到 `rag_provider is not None` 后会将 `knowledge_search` 注册到 `BusinessFlowToolRegistry`。Planner 会在 prompt 中看到该工具，SOP 引导 Agent 在需要时调用。无需改任何代码。

---

## 七、相关文件索引

| 文件 | 角色 |
|------|------|
| `emily-core/emily_core/providers/rag/base.py` | `RagProvider` ABC + `SearchResult` + `RagSearchResponse` |
| `emily-core/emily_core/providers/rag/maxkb_provider.py` | MaxKB hit_test HTTP 客户端（264 行） |
| `emily-core/emily_core/providers/rag/local_fallback.py` | 本地 TF-IDF 关键词回退（309 行） |
| `emily-core/emily_core/providers/rag/__init__.py` | Provider 导出 + 懒加载 |
| `emily-core/emily_core/tools/knowledge_search_tool.py` | M14 handler + schema + description |
| `emily-core/emily_core/__init__.py` | 条件注册到 `BusinessFlowToolRegistry`（225-243 行） |
| `emily-core/emily_core/bootstrap.py` | RAG Provider 创建（98-110 行） |
| `emily-core/emily_core/config.py` | RAG 相关配置字段（105-135 行） |
| `emily-core/emily_core/workitem/workitem_agent.py` | Planner 动态注入 + 分发器 RAG 构建 + node4 组装 |
| `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` | `RagResult` / `RagChunk` dataclass |
| `emily-core/emily_core/workitem/pipeline/mocks/mock_execution.py` | Mock RAG 结果（MockWorkAgentQuery） |
| `emily-data/config/core_config.json` | 运行时配置（`kb_enabled: false`） |
| `emily-data/baseknowledge/` | 原始文档目录（bind-mount 到 maxkb） |
| `docker-compose-napcat.yml` | maxkb 容器定义 + 挂载配置 |
