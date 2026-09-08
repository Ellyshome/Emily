# Emily RAG 改造技术对标清单

> 基准结论：不抄 Cherry Studio 的代码，抄它的设计；用「现状 vs Cherry 参考 vs 落到本栈形态」三层对照，作为范围化改造的执行基线。
> 参照仓库：`D:\app\charrystudio\cherry-studio`（AGPL-3.0，仅取设计思路，不引入代码/依赖）。

---

## 一、定调：Emily RAG「简陋」的准确界定

Emily 现有 RAG 不是能力残缺，而是停留在最朴素的**向量检索原型**，管线只有三段：

```
切块 → 向量化 → 余弦 top_k
```

对照工程化 RAG 应有的完整管线，缺了 8 项能力。其中 7 项是**通用工程能力**（Cherry 有、可直接对标设计），1 项是 **Emily 特有的范围能力**（Cherry 反而没有，必须自建）。

关键判断：**Emily 缺的是「通用工程能力」，Cherry 缺的是「权限范围维度」——两者补的方向正交，不能互相替代。**

---

## 二、八维逐项对照

| # | 能力 | Emily 现状 | Cherry 参考实现 | 落到 Emily 本栈的形态 |
|---|---|---|---|---|
| 1 | 文档解析 | 仅 UTF-8 纯文本；二进制 PDF 直接跳过（`file_tool.py` 入库逻辑） | 17 种格式；anydoc 原生 + 降级链；扫描 PDF 走 Mineru/OCR | Python 侧接 `pymupdf`/`unstructured`/`docling` 等，按文件类型路由解析器 |
| 2 | 分块 | 固定长度或按 `\n\n` 粗切（`ingest_knowledge.py`） | 结构断点打分 + token 级二次精修 + 精确 offset 不变量（`splitter.ts`/`tokenLimit.ts`） | 重写同算法：标题/段落/代码围栏断点 + token 预算二分 + 偏移守恒 |
| 3 | 去重 | 无 | content / embedding 双 sha256，重复向量不重复计费（`hashing.ts`） | 文件/片段级加哈希列，入库前查重 |
| 4 | 向量化 | 手动调 embedding（Qwen3-Embedding-0.6B，1024 维，本机已具备） | 统一 `AiService.embedMany`，维度与 base 绑定 | 抽取统一 embedding 服务，缓存向量、批量调用 |
| 5 | 检索 | 纯余弦 top_k，无过滤（`knowledge_chunk_repo.search_dense`） | **BM25(FTS5 trigram) + 向量 + RRF 混合**（`KnowledgeIndexStore.search`） | pgvector 余弦 + `pg_trgm`/PG 全文 + RRF 融合（算法照搬，存储换 PostgreSQL） |
| 6 | 重排 | 无 | 可选 rerank 模型，失败静默降级（`rerank.ts`） | 可选 rerank 服务；无则跳过，不阻断主流程 |
| 7 | 入库可靠性 | 一次性脚本，无状态、无恢复 | 5 类 job + 状态机 + 每库队列锁 + 崩溃恢复 + 原子 rebuild | 接入 Emily 现有作业/计划任务基建，做幂等 + 失败重试 + 状态机 |
| 8 | 引用溯源 | 无 | kb_* 输出 `[cite:id]`，前端还原 citation | `knowledge_search` 工具返回来源 id + 标题，供 LLM 引用 |

---

## 三、两个正交维度（最重要的结论）

### A. 通用工程能力（1~8，抄 Cherry 设计）

Cherry 的引擎完整度远超 Emily，值得逐条对标补齐。这些是「RAG 该长什么样」的参考答案。

### B. 范围能力（两者当前都缺，Emily 计划新增）

Cherry 的范围只到「知识库 base 级」，全仓库**没有 user/tenant/ACL/文件级/节点级权限**，原话：

> 「文件/节点粒度上不存在 ACL、不存在按 item 的检索过滤入参，这是评估范围控制时最关键的结论。」

Emily 的 RAG 通道**当前同样没有范围过滤**（`search_dense` 全库检索、无 doc 过滤，见第二节第 5 项）。区别仅在于：这是 Emily 本次要**计划新增**的目标，而非已有能力：

```
【目标形态，待实现】
可见文件集合 = 用户所属企业参与的「全景节点」的共享文件并集
             （node_participant_companies → node_accessible_files → files）
访客 = 未关联任何节点 → 可见集为空 + 白名单(explicit)叠加
```

**这一层必须自建、且优先于 A**（Cherry 代码里没有可抄对象）。它决定了检索过滤必须发生在「向量排序之前」（SQL WHERE 层），否则范围限制无从谈起。

> 补充：Emily 在**结构化文件检索**（`query_files`/`query_data`）一侧已有可见性基座（`session_accessible_files` + `FileManager.query_visible_files`），但它**未接入 RAG 通道**，且快照同步点有缺口（`sync_for_user` 仅在手动脚本被调用）。因此「范围能力」对 RAG 而言是从零接入，不能算作已具备。

---

## 四、落地顺序建议

范围化是主线，通用工程能力是旁路补齐；但**第 0 步（doc_id 归一）是两者的共同地基**，不做则范围过滤自欺欺人。

```
0. doc_id 归一：所有入 RAG 文档必须能反查到 files.id（当前目录扫描入库的 doc_id 是 uuid4，断链）
1. 范围解析服务：可见文件集合 = 节点共享清单并集（本需求三大基础函数）
2. 检索前过滤：provider/repo 增加 doc allowlist（WHERE doc_id IN 可见集），排序前过滤
3. 工具层注入：knowledge_search 接入当前 actor 的 user_id → 可见集
4. 访客白名单：可见集为空 + explicit 授权，且仅放行受限 RAG 问答
5. 通用能力补齐：按第二节 1~8 逐项对标（分块/混合检索/去重/重排/状态机/溯源）
```

> 注：第 0~4 步（范围化）与第 5 步（通用能力）可并行，但 0~4 是需求核心，第 5 步是质量提升；建议先保范围正确，再提召回质量。

---

## 五、已定约定（范围化落地口径）

1. **doc_id 归一**：所有入 RAG 文档/切片锚定 `files.id`；切片只含 `file_id`（doc_id），**不携带节点/scope 信息**。可见范围经「用户参与节点 → node_accessible_files → files.id」推导。
2. **过滤位置**：检索前 SQL WHERE 过滤（`doc_id IN 可见集`），排序前过滤；宁少答、不泄露。
3. **最小范围单元 = 文件**：用 `doc_id IN 可见文件集`，切片不冗余 scope 标签（无需改 chunk 表加列）。
4. **公共文档归属**：无全员节点、无公共节点；所有文件初始**零可见**（不属任何节点），允许不被全员所见，风险靠后台治理。
5. **上传者可见**：可见文件集合 = 节点可见集 ∪ `files.uploaded_by = user_id` 的自有文档（前期固定加入）。
6. **零可见文件治理**：照常入库，后台定期扫描「零可见文件治理清单」做下架/补授权。
7. **范围外应答**：非本次范围。RAG 只负责产出可见范围内的 chunk 素材，语言组织归 session-agent。

> 注：第 5 条为「前期」口径——先固定叠加上传者自有文档，后续如需收紧再单独评估。

---
