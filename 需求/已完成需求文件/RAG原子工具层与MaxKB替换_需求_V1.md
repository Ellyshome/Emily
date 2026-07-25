# RAG 原子工具层与 MaxKB 替换 — 需求文档

> **版本**：V1.0
> **编制日期**：2026-07-25
> **背景来源**：emy-test 排查中发现 MaxKB admin 登录持续失败 → 架构错配分析 → 决定用原子工具层替换 MaxKB
> **关联**：
> - [docs/技术踩坑备忘录.md](../docs/技术踩坑备忘录.md) 6.1–6.6（MaxKB 踩坑）、9.1/9.2（LLM 模型选型）
> - [需求/silicon-ocr/](silicon-ocr/) （LLM-VLM OCR 参考实现）
> - [emily-core/emily_core/providers/rag/base.py](../emily-core/emily_core/providers/rag/base.py)（RagProvider 抽象，保留）

---

## 1. 问题背景

### 1.1 现象

emy-test 实测期间，emily-core 容器日志反复出现：

```
[WARNING] emily.rag.maxkb: MaxKB admin login error: 'NoneType' object has no attribute 'get'
```

`knowledge_search` 工具全部返回"知识库暂不可用"，RAG 链路自部署以来实际从未可用。

### 1.2 根因

| 层 | 问题 |
|----|------|
| **密码错** | [docker-compose-napcat.yml:45](../docker-compose-napcat.yml#L45) 配 `EMILY_MAXKB_ADMIN_PASSWORD=maxkb123`（官方默认），但本项目 MaxKB 实例 admin 密码已被修改，`check_password('maxkb123')=False`，登录返回 `{"code":500,"message":"用户名或密码不正确","data":null}` |
| **代码 bug** | [maxkb_provider.py:85](../emily-core/emily_core/providers/rag/maxkb_provider.py#L85) `data.get("data", {}).get("token")` 在 `data:null` 时崩溃（`None.get`），掩盖真实错误（踩坑 6.4） |
| **架构错配** | MaxKB 是"知识库问答应用"，Emily 只用其底层向量检索；admin 登录 + 验证码机制对内部服务调用过重；hit_test 为非文档化内部 API（踩坑 6.2） |

### 1.3 架构错配的深层问题

MaxKB 打包了**两类能力**，Emily 的需求其实只要其中一半：

| 能力 | 作用侧 | Emily 需求 | MaxKB 黑盒的问题 |
|------|--------|------------|------------------|
| 多格式解析 / 分片 / 乱码处理 | 录入侧 | 上传文档 → 结构化录入 | 只转为检索 chunks，录入拿不到结构 |
| 扫描件 OCR | 录入侧 | 图纸 → 文字 → 录入+检索 | 只为检索做 OCR，录入流程用不上 |
| 表格 / 图表提取 | 录入侧 | 工程量清单 → 结构化数据 | 表格被拍平成文本，**丢失行列结构** |
| 文本分块 | 索引侧 | 知识库建索引 | 策略锁死 |
| embedding + 向量检索 | 查询侧 | RAG 检索 | 唯一适合 Emily 的部分 |

**结论**：Emily 需要的是**原子化的录入侧工具**（OCR、表格提取、文档解析）+ **自主的检索基座**（pgvector），而不是 MaxKB 这个黑盒。Emily 的 [RagProvider 抽象](../emily-core/emily_core/providers/rag/base.py)已设计好接口，替换实现不影响上层。

---

## 2. 目标

### 2.1 替换 MaxKB

- 完全移除 maxkb 容器及其全部代码、配置、文档引用
- 自建 `PgVectorRagProvider` 实现检索，复用 emily-postgres + pgvector
- RAG 链路恢复可用，无 admin 登录依赖

### 2.2 自建原子工具层

把 MaxKB 黑盒里的能力拆成 Emily 的原子工具（遵循现有 BusinessFlowToolRegistry 模式），SOP 可独立调用、可组合：

- 录入侧：`ocr_document` / `parse_document` / `extract_table` / `chunk_text`
- 索引侧：`embed_and_index`
- 查询侧：`knowledge_search`（已有，底层换实现）

### 2.3 指标

| 指标 | 当前 | 目标 |
|------|------|------|
| RAG 可用性 | 不可用（登录失败） | 可用，`is_available()=True` |
| 认证依赖 | admin 登录 + 验证码 | 无（容器内部直连） |
| 容器数 | 6（含 maxkb） | 6（maxkb 换成 tei） |
| knowledge_search 调用 | 返回 stub | 返回真实检索结果 |
| OCR 能力 | 困在 MaxKB 黑盒 | `ocr_document` 原子工具，SOP 可独立调用 |
| 文档解析能力 | 困在 MaxKB 黑盒 | `parse_document` / `extract_table` 原子工具 |

---

## 3. 技术栈选型

### 3.1 选型矩阵

| 能力 | 选型 | 形态 | 理由 |
|------|------|------|------|
| **embedding** | BGE-m3（BAAI/bge-m3） | TEI 独立容器（推荐）或 sentence-transformers 内嵌 | 中文工程文档最强；支持密集+稀疏+多向量混合检索。备选 Qwen3-Embedding-0.6B（与现 MaxKB 一致，更小） |
| **向量检索** | pgvector（已有） | 复用 emily-postgres | 零迁移；HNSW 索引；稀疏向量用 `tsvector` 存 |
| **PDF 解析** | docling（IBM，2024） | pip 内嵌 | 版面分析 + 阅读顺序 + 表格识别一体 |
| **Office 解析** | MarkItDown（微软，2024） | pip 内嵌 | Word/Excel/PPT 统一转 Markdown，轻量 |
| **OCR** | **VLM 视觉大模型**（见 3.2） | API 调用 | 参考 silicon-ocr，保留表格结构，可定制 prompt |
| **PDF 表格** | camelot | pip 内嵌 | stream/lattice 双模式 |
| **Excel 表格** | openpyxl | pip 内嵌 | 原生 .xlsx 行列结构 |
| **分块** | langchain-text-splitters | pip 单独包 | 只要 `MarkdownHeaderTextSplitter` + `RecursiveCharacterTextSplitter`，不引入 langchain 主包 |

**原则**：不引入 langchain / llama-index 编排层，只裁剪叶子组件。Emily 已有 SOP/WorkItem 编排，不重复造轮子。

### 3.2 OCR：VLM 视觉大模型方案（参考 silicon-ocr）

#### 3.2.1 方案原理

OCR 不用传统 OCR 引擎（PaddleOCR/Tesseract），改用**视觉大模型（VLM）+ 结构化 prompt**：

1. 图片转 base64
2. 调用 VLM API（OpenAI 兼容 chat/completions，`content` 含 `image_url`）
3. prompt 要求"原样抄写文字，表格用 `|` 分隔，保留标题层级，输出 Markdown"
4. 返回结构化 Markdown 文本

#### 3.2.2 参考实现

[需求/silicon-ocr/main.py](silicon-ocr/main.py) 已验证此方案，核心调用：

```python
data = {
    "model": model,  # Qwen/Qwen3-VL-8B-Instruct 或 qianfan-ocr-fast
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "原样抄写图片中所有文字，表格用 | 分隔，保留结构..."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
        ],
    }],
    "max_tokens": 4096,
}
response = requests.post(api_url, headers={"Authorization": f"Bearer {api_key}"}, json=data)
```

#### 3.2.3 VLM 选型

silicon-ocr 已尝试多个模型，均可通过 config.json 切换：

| 模型 | 提供方 | 特点 | 适用 |
|------|--------|------|------|
| `Qwen/Qwen3-VL-8B-Instruct` | SiliconFlow | 通用 VLM，Markdown 结构保留好 | 主选，通用文档 |
| `Qwen/Qwen3-VL-8B-Thinking` | SiliconFlow | 含思维链，复杂版面更准 | 复杂图纸 |
| `qianfan-ocr-fast` | 百度千帆 | 专用 OCR，速度快 | 纯文字识别 |

**推荐主选 SiliconFlow Qwen3-VL-8B-Instruct**（与 silicon-ocr SKILL.md 一致），通过环境变量 `EMILY_VLM_API_URL` / `EMILY_VLM_API_KEY` / `EMILY_VLM_MODEL` 配置，可在不改正代码的前提下切换模型。

#### 3.2.4 优势

| 维度 | 传统 OCR（PaddleOCR） | VLM-OCR（本方案） |
|------|----------------------|-------------------|
| 表格结构 | 需单独版面分析 | prompt 直接要求 Markdown 表格 |
| 标题层级 | 需后处理 | VLM 天然理解 |
| 版面阅读顺序 | 需版面分析模型 | VLM 直接输出 |
| 可定制 | 改引擎代码 | 改 prompt |
| 部署 | 本地模型 + 依赖 | API 调用，零本地依赖 |
| 中文质量 | 强 | 强（Qwen3-VL 中文训练充分） |

---

## 3.3 部署拓扑

```
┌─ emily-core ─────────────────────────────┐
│  pip 内嵌：                               │
│   - docling (PDF 解析)                    │
│   - MarkItDown (Office 解析)              │
│   - camelot + openpyxl (表格)             │
│   - langchain-text-splitters (分块)       │
│   - PgVectorRagProvider (自建检索)        │
│   - VlmOcrClient (调 VLM API)             │
└──────┬───────────────────────────────────┘
       │ HTTP embedding          │ HTTP VLM
       ▼                         ▼
┌─ tei (BGE-m3) ─┐       ┌─ SiliconFlow / 千帆 ─┐
│  独立容器       │       │  外部 API（零部署）   │
└──────┬─────────┘       └──────────────────────┘
       │ SQL + pgvector
       ▼
┌─ emily-postgres (已有) ──────────────────┐
│  knowledge_chunks 表 (新增)               │
└──────────────────────────────────────────┘
```

- **新增容器**：`tei`（embedding 服务，BGE-m3）
- **移除容器**：`maxkb`
- **外部 API**：VLM（SiliconFlow/千帆），零部署
- **pip 内嵌**：其余所有解析/表格/分块工具

> 备选：若不想加 tei 容器，embedding 可用 `sentence-transformers` 内嵌 emily-core（BGE-m3 常驻内存 ~2GB），或退用 Qwen3-Embedding-0.6B（更小）。

---

## 4. 原子工具设计

所有工具遵循 M14 handler 风格（参考 [knowledge_search_tool.py](../emily-core/emily_core/tools/knowledge_search_tool.py)），注册到 BusinessFlowToolRegistry，由 SkillExecutor 校验白名单后框架直调。

### 4.1 工具清单

| 工具 | 作用侧 | 输入 | 输出 | 依赖 |
|------|--------|------|------|------|
| `ocr_document` | 录入 | `file_path`, `prompt?` | `{text, markdown, pages[]}` | VLM API |
| `parse_document` | 录入 | `file_path` | `{sections[], tables[], metadata}` | docling + MarkItDown |
| `extract_table` | 录入 | `file_path`, `sheet?` | `{rows[][], headers[], format}` | camelot(PDF) / openpyxl(Excel) |
| `chunk_text` | 索引 | `text`, `strategy?`, `chunk_size?` | `{chunks[]}` | langchain-text-splitters |
| `embed_and_index` | 索引 | `chunks[]`, `doc_metadata` | `{indexed_ids[], count}` | TEI + pgvector |
| `knowledge_search` | 查询 | `query`, `top_k?`, `stage?`, `role?` | `RagSearchResponse` | pgvector（底层换实现） |

### 4.2 关键接口定义

#### 4.2.1 ocr_document

```python
async def handle_ocr_document(params: dict, vlm_client: VlmOcrClient) -> dict:
    """VLM 视觉大模型 OCR。

    Args:
        params: {file_path, prompt?}
            - file_path: 图片路径（jpg/png/bmp/tiff）或 PDF（自动按页转图）
            - prompt: 可选，定制识别要求；默认用"原样抄写+表格|分隔+保留结构"prompt
        vlm_client: VLM API 客户端（SiliconFlow/千帆）

    Returns:
        {success, text, markdown, pages[{page_no, text}], model, elapsed_ms}
    """
```

默认 prompt（参考 silicon-ocr/main.py:69-79）：

```
请识别并原样抄写图片中所有文字，要求如下：
1. 不要用代码块（```）包裹输出内容；
2. 按照从上到下、从左到右的顺序输出；
3. 不论文字位于何处（正文、色块、边框、表格、页眉、页脚等），一律原样收录；
4. 表格内容按行输出，单元格之间用 | 分隔；
5. 保留标题层级（用 # 标记）、列表（用 - 标记）；
6. 不对内容做归类、总结或结构调整，忠实抄写原文。
```

支持多页并发（参考 silicon-ocr 的 `ThreadPoolExecutor`，默认 6 并发）。

#### 4.2.2 PgVectorRagProvider

替换 [MaxKBRagProvider](../emily-core/emily_core/providers/rag/maxkb_provider.py)，实现同一个 [RagProvider](../emily-core/emily_core/providers/rag/base.py) 接口：

```python
class PgVectorRagProvider(RagProvider):
    """pgvector 向量检索提供者。

    通过 TEI 服务生成 embedding，存入/查询 emily-postgres 的 pgvector。
    支持密集检索 + 稀疏检索（BGE-m3）的 RRF 融合。
    """

    def __init__(self, db_url: str, tei_url: str,
                 similarity: float = 0.3, top_k: int = 5):
        ...

    async def is_available(self) -> bool:
        """检查 TEI + pgvector 连通性（无需登录）。"""

    async def search(self, query, top_k=5, stage=None, role=None) -> RagSearchResponse:
        """query → TEI embedding → pgvector 相似度查询 → top_k chunks。"""
```

### 4.3 数据模型

新增 `knowledge_chunks` 表（models.py）：

```python
class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id = Column(String, primary_key=True)           # UUID
    doc_id = Column(String, index=True)             # 文档 ID（同文档多 chunk）
    doc_name = Column(String)                       # 源文档名
    chunk_index = Column(Integer)                   # chunk 序号
    chunk_text = Column(Text)                       # chunk 原文
    embedding = Column(Vector(1024))                # BGE-m3 密集向量（pgvector）
    sparse_vector = Column(TSVECTOR)                # BGE-m3 稀疏向量（可选）
    metadata = Column(JSON, default={})             # {stage, role, doc_type, ...}
    created_at = Column(String)                     # ISO8601
```

索引：`HNSW` on `embedding`（密集检索）、`GIN` on `sparse_vector`（稀疏检索）。

---

## 5. MaxKB 彻底清理清单

### 5.1 代码

| 文件 | 操作 | 说明 |
|------|------|------|
| `emily-core/emily_core/providers/rag/maxkb_provider.py` | **删除** | 整个文件移除 |
| `emily-core/emily_core/providers/rag/__init__.py` | 改 | 移除 `MaxKBRagProvider` / `get_maxkb_provider` 导出，新增 `PgVectorRagProvider` |
| `emily-core/emily_core/providers/rag/pgvector_provider.py` | **新增** | PgVectorRagProvider 实现 |
| `emily-core/emily_core/bootstrap.py` | 改 | RAG 初始化从 MaxKB 改为 PgVector（L122-133） |
| `emily-core/emily_core/config.py` | 改 | 移除 `maxkb_*` 字段（L102-132），新增 `tei_url` / `vlm_*` 字段；修复 L52 注释残留 `maxkb:5432/team_brain` |
| `emily-core/emily_core/infrastructure/database/models.py` | 改 | L1360 `rag_query_logs.provider` 注释 `maxkb` → `pgvector`；新增 `KnowledgeChunk` 表 |
| `emily-core/emily_core/tools/ocr_tool.py` | **新增** | `handle_ocr_document` handler |
| `emily-core/emily_core/tools/parse_document_tool.py` | **新增** | `handle_parse_document` handler |
| `emily-core/emily_core/tools/extract_table_tool.py` | **新增** | `handle_extract_table` handler |
| `emily-core/emily_core/infrastructure/vlm/client.py` | **新增** | VlmOcrClient（封装 SiliconFlow/千帆 API） |
| `scripts/rag_dry_run.py` | 改或删除 | 改为对 PgVectorRagProvider 的 dry-run，或删除 |

### 5.2 配置

| 文件 | 操作 | 说明 |
|------|------|------|
| `docker-compose-napcat.yml` | 改 | 删除 `maxkb` 服务（L88-102）；emily-core 移除 `EMILY_MAXKB_URL` / `EMILY_MAXKB_ADMIN_PASSWORD` / `EMILY_KB_ENABLED`（L43-45）、`depends_on: maxkb`（L86）、`NO_PROXY` 里的 `maxkb`（L58）；新增 `tei` 服务 + `EMILY_TEI_URL` / `EMILY_VLM_*` 环境变量 |
| `emily-data/config/core_config.json` | 改 | 移除 `kb_enabled` / `maxkb_*` 字段，新增 `tei_url` / `vlm_*` |
| `.env.example` | 改 | 移除 `EMILY_MAXKB_*`，新增 `EMILY_TEI_URL` / `EMILY_VLM_API_URL` / `EMILY_VLM_API_KEY` / `EMILY_VLM_MODEL` |

### 5.3 DB / 种子数据

| 文件 | 操作 | 说明 |
|------|------|------|
| `.claude/tool/env-test/010_seed_runtime_data.sql` | 检查 | 若含 maxkb 种子数据则清理 |
| `emily-core/emily_core/infrastructure/database/session.py` | 检查 | `_PENDING_COLUMNS` 是否需补 `knowledge_chunks` 表 |

### 5.4 文档

| 文件 | 操作 | 说明 |
|------|------|------|
| `CLAUDE.md` | 改 | 技术栈表 RAG 行改为 pgvector + TEI + VLM；容器拓扑表移除 maxkb、加 tei；日常命令移除 maxkb 相关 |
| `docs/技术踩坑备忘录.md` | 改 | 6.1–6.6 标注"已废弃（MaxKB 已移除）"；新增本次替换的踩坑条目 |
| `docs/开发记录.md` | 改 | 记录此次架构决策（ADR） |
| `docs/业务模块与运转全景.md` | 改 | RAG 模块描述从 MaxKB 改为 pgvector |
| `docs/代码文件目录.md` | 改 | 移除 `maxkb_provider.py`，新增 `pgvector_provider.py` / `ocr_tool.py` 等 |
| `docs/接口协议与调用约定.md` | 改 | RAG 接口描述更新 |
| `docs/脚本工具目录.md` | 改 | `rag_dry_run.py` 条目更新 |
| `docs/各模块职能记录.md` | 改 | RAG 模块职能更新 |
| `docs/软著类/程序鉴别材料_完整版.md` | 改 | env_map 等处移除 `EMILY_MAXKB_*` |

---

## 6. 迁移路径（分步独立可验证）

| 步骤 | 动作 | 产出 | 风险 |
|------|------|------|------|
| **0** | docker-compose `EMILY_KB_ENABLED=false` + 停 maxkb 容器 | 报错消失，RAG 暂停 | 无 |
| **1** | 加 tei 容器（BGE-m3）；emily-postgres 建 `knowledge_chunks` 表 + HNSW/GIN 索引 | embedding 服务就绪 | 低 |
| **2** | 实现 `PgVectorRagProvider`（密集检索先行）；bootstrap 注入 | 替换 MaxKBRagProvider，RAG 恢复 | 低（接口已抽象） |
| **3** | 加稀疏检索 + RRF 融合 | 检索质量提升 | 中 |
| **4** | 实现 `embed_and_index` 工具 + 索引 SOP（把现有 SOP/规则书 md 灌进去） | 知识库有内容 | 低 |
| **5** | 实现 `VlmOcrClient` + `ocr_document` 工具（参考 silicon-ocr） | OCR 录入能力独立 | 低 |
| **6** | 实现 `extract_table`（工程量清单）、`parse_document`（规范文档） | 录入能力完整 | 中 |
| **7** | 删除 `maxkb_provider.py`；maxkb 容器从 docker-compose 移除；清理全部文档引用 | 架构清爽 | 无 |

**第 0-2 步**即可恢复 RAG 并消除报错；第 5-6 步按业务优先级逐步建录入侧原子工具；第 7 步收尾清理。

---

## 7. 验收标准

### 7.1 RAG 检索

- [ ] `docker compose ps` 无 maxkb 容器，有 tei 容器
- [ ] emily-core 启动日志无 `MaxKB admin login error`
- [ ] `PgVectorRagProvider.is_available()` 返回 True
- [ ] `knowledge_search` 工具返回真实检索结果（非 stub）
- [ ] emy-test 发"查一下施工规范对层高的要求"，命中知识库 chunks

### 7.2 OCR 原子工具

- [ ] `ocr_document` 工具注册到 BusinessFlowToolRegistry
- [ ] 输入施工图纸图片，返回 Markdown 文本（表格用 `|` 分隔）
- [ ] VLM 模型可通过环境变量切换（SiliconFlow ↔ 千帆）
- [ ] 多页文档并发识别，按页码排序

### 7.3 MaxKB 清理

- [ ] 全项目 `grep -ri maxkb` 无业务代码引用（仅 docs 历史踩坑记录允许保留并标注"已废弃"）
- [ ] `docker-compose-napcat.yml` 无 maxkb 服务
- [ ] `config.py` 无 `maxkb_*` 字段
- [ ] `.env.example` 无 `EMILY_MAXKB_*`
- [ ] CLAUDE.md 容器拓扑表无 maxkb

### 7.4 录入侧原子工具

- [ ] `parse_document` / `extract_table` / `chunk_text` / `embed_and_index` 均注册为原子工具
- [ ] 至少一个 SOP 演示"上传扫描件 → OCR → 结构化录入"全流程

---

## 8. 风险与备选

### 8.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| TEI 容器内存占用（BGE-m3 ~2GB） | emily-core 主机内存压力 | 备选 sentence-transformers 内嵌，或换 Qwen3-Embedding-0.6B（0.6B） |
| VLM API 付费 + 网络依赖 | OCR 成本 + 延迟 | 千帆 qianfan-ocr-fast 便宜快速；批量图片并发控制 |
| docling 依赖较重 | emily-core 镜像变大 | 备选 PyMuPDF + MarkItDown（纯文本提取，无版面分析） |
| pgvector 混合检索复杂度 | 实现成本 | 第 2 步先做密集检索，第 3 步再加稀疏 + RRF |

### 8.2 备选方案

- **embedding**：BGE-m3（推荐）→ Qwen3-Embedding-0.6B（轻量）→ DeepSeek embedding API（若有）
- **VLM**：SiliconFlow Qwen3-VL-8B-Instruct（推荐）→ 百度千帆 qianfan-ocr-fast → 本地部署 Qwen3-VL
- **PDF 解析**：docling（推荐）→ unstructured → PyMuPDF + MarkItDown
- **部署**：TEI 独立容器（推荐）→ sentence-transformers 内嵌 → 外部 embedding API

---

## 9. 后续动作

1. **本文档评审**：确认技术栈选型与清理范围
2. **拆分实施计划**：按第 6 节迁移路径，每步独立出实施计划文档（参考 `对话流优化_计划_V1.md` 风格）
3. **第 0 步立即执行**：关闭 maxkb（`EMILY_KB_ENABLED=false`），消除当前报错
4. **第 1-2 步优先**：恢复 RAG 基础能力
5. **第 5 步**：OCR 工具（业务价值最高，施工图纸场景最痛）
