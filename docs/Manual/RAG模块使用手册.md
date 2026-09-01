# RAG 模块使用手册

> 版本：Emily V1.0 · 最后更新：2026-07-25

---

## 1. 概述

Emily 的 RAG（Retrieval-Augmented Generation）模块负责在 LLM 执行企业业务流程时，实时检索项目知识库中的领域知识，注入上下文辅助推理。

**双层 Provider 架构**：

```
用户消息 → SessionAgent → WorkItem → Node3 execute → knowledge_search tool
                                                          │
                                            ┌─────────────┴─────────────┐
                                            │   PgVectorRagProvider      │  ← 主路径
                                            │   TEI + pgvector HNSW      │
                                            │   (BGE-m3, 1024d)           │
                                            └─────────────┬─────────────┘
                                          不可用时自动降级
                                            ┌─────────────┴─────────────┐
                                            │   LocalFileRagProvider     │  ← 兜底
                                            │   本地 TF 关键词搜索        │
                                            │   (项目资料/*.md)           │
                                            └───────────────────────────┘
```

- **主路径**：基于语义向量检索，TEI 容器加载 BGE-m3 模型生成 query embedding，PostgreSQL pgvector 做 HNSW 相似度查询
- **兜底路径**：零依赖的本地 TF 关键词搜索，在 `项目资料/` 目录下扫描 `.md`/`.txt` 文件，按标题分块后做词频评分

---

## 2. 检索链路

完整调用链路：

```
LLM 提取参数 → BusinessFlowTool.handler(params)
  → handle_knowledge_search(params, rag_provider)
    → rag_provider.is_available()           # 连通性检查
    → rag_provider.search(query, top_k, stage, role)
      → PgVectorRagProvider: TEI /embed → pgvector cosine 相似度 → metadata 过滤
      → LocalFileRagProvider: TF 分词 → 词频打分 → stage/role 过滤
    → RagSearchResponse { query, results[], context_text, provider_name }
  → handler 格式化返回 { success, reply, rag_results_data }
→ 结果注入 WorkItem 上下文 → LLM 基于检索结果生成回复
```

**关键参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 自然语言查询，LLM 从用户消息中提取 |
| `top_k` | int | 否 | 返回条数，默认 5，最大 10 |
| `stage` | string | 否 | 按项目阶段过滤：`投资决策` / `专项审查` / `规划报建` / `招标采购` / `施工建设` / `竣工验收` / `交付售后` |
| `role` | string | 否 | 按岗位过滤：`工程部经理` / `设计部经理` / `规划报建专员` 等 |

---

## 3. 配置

### 3.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EMILY_KB_ENABLED` | `false` | RAG 总开关，设为 `true` 启用 |
| `EMILY_TEI_URL` | `http://tei:80` | TEI embedding 服务地址 |
| `EMILY_KB_TOP_K` | `5` | 检索返回最大条数 |
| `EMILY_RAG_SIMILARITY_THRESHOLD` | `0.3` | pgvector 相似度阈值 (0-1) |
| `EMILY_KB_LOCAL_FALLBACK_DIR` | `项目资料/` | 本地兜底检索目录 |

### 3.2 docker-compose 配置

```yaml
# emily-core 服务环境变量
emily-core:
  environment:
    - EMILY_KB_ENABLED=true
    - EMILY_TEI_URL=http://emily-embed:8000

# Embedding 服务（BGE-m3）
emily-embed:
  image: emily-core:latest
  command: ["python", "-m", "emily_core.infrastructure.embedding.server"]
  ports:
    - "127.0.0.1:8082:8000"
  environment:
    - EMBEDDING_MODEL_ID=BAAI/bge-m3
    - EMBEDDING_PORT=8000
```

### 3.3 core_config.json

```json
{
  "kb_enabled": true,
  "tei_url": "http://emily-embed:8000",
  "rag_similarity_threshold": 0.3,
  "kb_top_k": 5
}
```

---

## 4. 数据入库

### 4.1 knowledge_chunks 表结构

知识库数据存储在 `knowledge_chunks` 表，每行是一个文档分块：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | UUID | 主键 |
| `doc_id` | string | 源文档 ID，同文档多 chunk 共享 |
| `doc_name` | string | 源文档名 |
| `chunk_index` | int | chunk 序号 |
| `chunk_text` | text | 分块文本内容 |
| `embedding` | vector(1024) | BGE-m3 密集向量 (1024维) |
| `metadata` | jsonb | 文档元数据：stage, role, keywords 等 |

### 4.2 批量写入（Python API）

```python
from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
from emily_core.infrastructure.embedding.tei_client import TeiClient

repo = KnowledgeChunkRepo()
tei = TeiClient("http://emily-embed:8000")

# 1. 准备 chunks 和 embeddings
chunks = [
    {"text": "放线验收标准：轴线偏差 ≤ 5mm...", "index": 0},
    {"text": "混凝土浇筑温度控制：5°C ~ 35°C...", "index": 1},
]
embeddings = await tei.embed([c["text"] for c in chunks])

# 2. 批量写入
doc_meta = {
    "doc_id": "DOC-2026-001",
    "doc_name": "施工质量标准.md",
    "stage": "施工建设",
    "role": "工程部经理",
}
repo.batch_insert(chunks, embeddings, doc_meta)
```

### 4.3 删除文档

```python
# 按 doc_id 删除文档的所有 chunks
repo.delete_by_doc("DOC-2026-001")
```

### 4.4 直接 SQL 写入

```sql
-- 创建向量扩展（首次）
CREATE EXTENSION IF NOT EXISTS vector;

-- 写入 chunk（需用 pgvector 函数将数组转为向量）
INSERT INTO knowledge_chunks (id, doc_id, doc_name, chunk_index, chunk_text, embedding, metadata)
VALUES (
  'uuid-here',
  'DOC-001',
  '施工标准.md',
  0,
  '放线验收标准：轴线偏差 ≤ 5mm...',
  '[0.012, -0.034, 0.056, ...]'::vector,
  '{"stage": "施工建设", "role": "工程部经理"}'
);
```

---

## 5. Provider 详解

### 5.1 PgVectorRagProvider（主路径）

**检索流程**：
1. 调用 TEI `/embed` 接口生成 query embedding（BGE-m3, 1024维）
2. PostgreSQL 内执行 cosine 相似度查询（`<=>` 运算符，HNSW 索引）
3. 过滤 `similarity >= threshold` 的结果
4. 按 metadata 字段做 stage/role 二次过滤
5. 返回 top_k 条，附带相似度分数和文档名

**依赖**：
- `emily-embed` 容器（TEI API 兼容）
- `emily-postgres` 容器（pgvector 扩展）
- `knowledge_chunks` 表中有已入库数据

**可用条件**：TEI `/health` 返回 200

### 5.2 LocalFileRagProvider（兜底路径）

**检索流程**：
1. 启动时扫描 `项目资料/` 目录下所有 `.md`/`.txt` 文件
2. 按 Markdown 标题（#/##/###）分块
3. 从文件名、标题、正文前 200 字符提取 stage/role/keywords 元数据
4. 查询时做中文分词 + 英文单词切分，TF 词频评分
5. 按 stage/role 过滤后返回 top_k

**零依赖**：纯 Python 标准库实现（pathlib + re），无需外部服务。

**默认目录**：项目根目录下的 `项目资料/` 文件夹，可通过 `EMILY_KB_LOCAL_FALLBACK_DIR` 自定义。

---

## 6. 工具与 SOP 调用

### 6.1 knowledge_search 工具

`knowledge_search` 作为**基座能力**注册在 `BusinessFlowToolRegistry` 中，对所有 SOP 开放。

**工具 schema**：
```json
{
  "query": "自然语言查询（必填）",
  "top_k": "返回条数，默认5，最大10",
  "stage": "项目阶段过滤（可选）",
  "role": "岗位过滤（可选）"
}
```

**调用方式**：LLM 通过 `chat_json` 提取结构化参数，框架直接调用 `handle_knowledge_search(params, rag_provider)`，**不经过 ReAct function calling**。

### 6.2 在 SOP 中引用

Skill YAML 声明中引用 knowledge_search：

```yaml
# skills/query/sop-005-qry.skill.yaml
name: query_knowledge
tools:
  - knowledge_search    # 声明使用该工具
steps:
  - name: search_kb
    tool: knowledge_search
    description: "搜索知识库获取相关规范标准"
```

---

## 7. 测试与验证

### 7.1 rag_dry_run 脚本

直接测试 RAG 检索，绕过 LLM 和管道：

```bash
# 基础检索
uv run python scripts/rag_dry_run.py "放线验收标准"

# 指定 top_k
uv run python scripts/rag_dry_run.py "施工工艺" --top-k 8

# 带阶段和岗位过滤
uv run python scripts/rag_dry_run.py "钢筋间距" --stage 施工建设 --role 工程部经理

# 诊断模式（仅检查配置和连通性，不发检索）
uv run python scripts/rag_dry_run.py --probe
```

输出为 JSON，包含 `dry_run_meta`（配置/连通性诊断）和 `handler_result`（检索结果）。

### 7.2 手工 SQL 验证

```bash
# 检查 knowledge_chunks 表数据量
docker exec emily-postgres psql -U emily -d emily -c \
  "SELECT doc_name, COUNT(*) AS chunks FROM knowledge_chunks GROUP BY doc_name;"

# 检查 pgvector 扩展
docker exec emily-postgres psql -U emily -d emily -c \
  "SELECT * FROM pg_extension WHERE extname = 'vector';"

# 检查 TEI 服务
curl http://localhost:8082/health
```

---

## 8. 日志与追踪

### 8.1 检索日志

每次 RAG 检索自动写入 `rag_retrieval_logs` 表：

| 字段 | 说明 |
|------|------|
| `query_text` | 查询文本 |
| `provider` | 提供者名称（pgvector / LocalFile） |
| `hit_count` | 命中条数 |
| `top_score` / `avg_score` | 相似度统计 |
| `was_used_by_llm` | 是否被 LLM 使用 |
| `latency_ms` | 检索耗时（毫秒） |
| `pipeline_run_id` | 关联管道运行 ID |

### 8.2 应用日志

```
# 成功
[INFO] emily.rag.pgvector: pgvector search: '放线验收标准' → 5 results (42ms)

# 降级
[WARNING] emily.rag.pgvector: pgvector search: TEI embed failed: Connection refused
[INFO] emily.rag.local: LocalFileRag: 已加载 128 个文本块 (目录: 项目资料/)
```

---

## 9. 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `knowledge_search` 返回"知识库暂不可用" | RAG provider 未初始化或 TEI 不通 | 检查 `EMILY_KB_ENABLED=true`，`docker compose ps emily-embed` |
| 检索结果为空 | `knowledge_chunks` 表无数据 或 相似度阈值过高 | 检查表中数据，适当调低 `rag_similarity_threshold` |
| 返回"知识库服务暂未就绪" | kb_enabled=false 时注册了 stub handler | 设 `EMILY_KB_ENABLED=true` |
| embedding server 启动失败 | BGE-m3 模型未下载或显存不足 | 检查 `HF_HOME` 目录，确保有 ~2GB 可用空间和 ≥2GB 内存 |
| pgvector 查询慢 | `knowledge_chunks` 表无索引 | 创建 HNSW 索引：`CREATE INDEX ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);` |
