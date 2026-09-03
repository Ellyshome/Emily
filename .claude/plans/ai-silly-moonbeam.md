# 代码审核报告修复计划

> 来源：`代码审核报告.md`（审核日期 2026-09-03）
> 执行者：AI 开发工具
> 编制日期：2026-09-03
> 状态：待执行

## 执行方式

本计划将**复制保存到项目根目录** `d:\app\Emily\修复计划.md`（用户指定位置），由后续 AI 工具按"执行顺序与依赖"逐项执行。本 plans 目录文件为只读副本，以项目根目录版本为准。

---

## Context（背景与目标）

`代码审核报告.md` 对 Emily 项目（emily-core、插件、scripts、docker 配置）做了功能完整性、断头代码、孤儿代码的审核，识别出 P0~P3 共 14 项问题。

本计划已**逐条用 CodeGraph/Grep/Read 对照当前代码库复核**。复核结论：报告中绝大多数结论属实，但有几处需修正：

- **§2.1 #5/#6（tools/base/、tools/business/ "零导入者"）不准确**：二者实际被 `tools/__init__.py:19-20,23-26` 导入。但 `tools/__init__.py` 本身无任何外部导入者（全库无人 `from emily_core.tools import`），三者构成一条**自洽的孤儿重导出链**，应整体清理（连同 §6.1 的 `tools/__init__.py __all__` bug）。
- **§5.3 napcat.yml 硬编码路径**比报告描述更严重：第 127 行 `D:\app\Emily\emily-data\db_seeds:/var/lib/postgresql/data` 实际把 `db_seeds`（**不是 seed 目录，而是当前真实运行的 PG 数据目录**，含 `postmaster.pid`/`base/`/`pg_wal/`，今日仍在写入）挂为 PG 数据卷。命名误导 + 硬编码绝对路径，但**当前能跑**（因为数据真在那里）。minimal.yml 挂 `./emily-data/postgres_data`（目录不存在 → 空库）。**两者 PG 数据目录路径不一致**：napcat 用 `db_seeds`（有数据），minimal 用 `postgres_data`（不存在）。修复须统一为 `postgres_data` 并把现有数据迁移过去，否则 minimal 启动得空库、napcat 改名后丢数据。
- **§4.1 #4 morning_report 严重度被高估**：handler 并非纯占位——`morning_report.py:28-50` 已查询 EvolutionRepo 并追加进化摘要、`52-60` 已 publish。它只是 TODO 行未删除 + 用占位前导文本，且 `enabled: false`。属 P2 清理而非 P1。
- **§4.1 #3 session_cleanup 有更优解**：`SessionPoolManager` 自身已实现 `_sweep_loop` + `sweep_expired()`（`session_pool.py:74-83,152-179`），路由时自动启动。调度 handler 是冗余的——硬接 SessionPool 会与池自带的 sweeper **双重并发清理**。正确做法是禁用调度任务（池自带清理），handler 改为只读巡检/上报 `get_status()`，或直接删除该 job 配置。

**目标**：系统性消除审核报告中的断头/孤儿/假成功/配置缺陷，恢复调度器可信度与部署可移植性，遵循 CLAUDE.md 约束 #0（根治而非迁就）。

---

## 修复项总览（按执行顺序，P0→P3）

| 序 | 优先级 | 修复项 | 类型 |
|----|--------|--------|------|
| 1 | P0 | 统一并修复 PG 数据目录路径（minimal.yml + napcat.yml） | 部署配置 |
| 2 | P0 | session_cleanup：禁用冗余调度任务（池自带清理） | 调度器假成功 |
| 3 | P0 | data_sync / webhook：禁用假成功 stub 或标注 disabled | 调度器假成功 |
| 4 | P1 | voice_entry：删除孤儿实现 + 从 registry 摘除 stub | 断头代码 |
| 5 | P1 | rag_logger：在 knowledge_search_tool 接入写入 | 断头代码 |
| 6 | P1 | morning_report：删除 TODO 占位文本，已实现逻辑保留 | 清理（降级） |
| 7 | P2 | 删除孤儿文件 9 处（含 tools/base、business、tools/__init__.py 整链） | 孤儿清理 |
| 8 | P2 | 删除 `_workitem_agent` 死属性 + 同步 CLAUDE.md | 残留引用 |
| 9 | P2 | 同步插件 message.py 的 event_id 字段到核心版；删插件 3 个不使用副本 | 副本分歧 |
| 10 | P2 | 去重 docker-compose-remote.yml / wecom.yml | 冗余 |
| 11 | P3 | 注册遗漏脚本到 scripts_registry.yaml | 完善 |
| 12 | P3 | 补全 scheduler/jobs/__init__.py 的 __all__ | 完善 |
| 13 | P3 | 删除 tools/__init__.py（随 #7 整链删除，此项并入 #7） | 完善 |
| 14 | P3 | 注释/清理 workitem_agent.py 残留注释引用 | 清理 |

---

## 详细修复方案

### 修复 1（P0）— 统一 PG 数据目录路径

**问题**：`docker-compose-napcat.yml:127` 硬编码 `D:\app\Emily\emily-data\db_seeds:/var/lib/postgresql/data`（绝对路径 + 误导命名）；`docker-compose-minimal.yml:72` 用 `./emily-data/postgres_data:/var/lib/postgresql/data`（目录不存在）。两者数据目录不一致，minimal 启动得空库，napcat 的真实数据被错误命名为 `db_seeds`。

**根治方案**（遵循 #0 根治原则，而非迁就 db_seeds 现状）：
1. 将现有 `emily-data/db_seeds` 中的 **PG 数据文件**（`PG_VERSION`、`base/`、`global/`、`pg_wal/`、`pg_*` 等——**不含任何 `.sql` seed 文件**，经核查 db_seeds 目录无 seed SQL，全是 PG 运行数据）移动/重命名为 `emily-data/postgres_data`。
   - ⚠ **执行前必须先 `docker compose -f docker-compose-napcat.yml stop emily-postgres`**，避免移动正在运行的 PG 数据目录导致损坏。
   - 移动后核对 `postgres_data/PG_VERSION` 存在且 `base/` 非空。
2. 改 `docker-compose-napcat.yml:127`：`D:\app\Emily\emily-data\db_seeds:/var/lib/postgresql/data` → `./emily-data/postgres_data:/var/lib/postgresql/data`
3. `docker-compose-minimal.yml:72` 保持 `./emily-data/postgres_data` 不变（修复 1 后目录已存在）。
4. 核查是否还有其他 compose 文件引用 `db_seeds` 作为数据卷（非 `/app/db_seeds:ro` 的只读挂载，只读挂载 `./emily-data/db_seeds:/app/db_seeds:ro` 已不再需要，因 db_seeds 改名后该挂载应一并删除或改为指向新的 seed 来源——但当前 db_seeds 无 seed 文件，故 `/app/db_seeds:ro` 挂载失去意义，一并从两个 compose 文件删除该行）。

**关键文件**：
- `docker-compose-napcat.yml:76,127`
- `docker-compose-minimal.yml:42,72`
- 文件系统：`emily-data/db_seeds` → `emily-data/postgres_data`（数据迁移）

**验证**：`docker compose -f docker-compose-napcat.yml up -d emily-postgres` → `docker exec emily-postgres psql -U emily -d emily -c "SELECT count(*) FROM users;"` 应返回非零（数据已迁移），而非空库。

---

### 修复 2（P0）— session_cleanup 调度任务禁用

**问题**：`scheduler_config.json:23-30` 的 "Session 超时清理" `enabled: true`，但 `session_cleanup.py:27` 是 TODO stub，永远报告"已关闭 0 个"。同时 `SessionPoolManager` 已自带 `_sweep_loop`（`session_pool.py:74-83`，路由时经 `_ensure_sweeper()` 自动启动）做 TTL 清理。调度 handler 若硬接 `sweep_expired()` 会与池自带 sweeper **双重并发**。

**根治方案**（二选一，推荐 A）：
- **A（推荐）禁用调度任务**：`scheduler_config.json` 将 "Session 超时清理" 改为 `"enabled": false`，并加注释说明"Session TTL 清理由 SessionPoolManager.sweep_expired 内部 sweeper 自动执行，无需调度任务"。理由：池自带清理已覆盖，调度任务冗余。
- **B（备选）改 handler 为只读巡检**：`session_cleanup.py` 删除 TODO，改为调用 `self._session_pool.get_status()` 上报当前活跃 session 数与最长空闲时长（不触发清理，避免与 sweeper 竞争），返回 `JobResult(success=True, summary=f"当前活跃 {status['total']} 个 session")`。

**关键文件**：
- `emily-data/config/scheduler_config.json`（"Session 超时清理" 条目）
- `emily-core/emily_core/scheduler/jobs/session_cleanup.py`（若选 B）

**验证**：`docker compose restart emily-core` → 查日志无"已关闭 0 个超时 session"假成功记录；确认 SessionPool sweeper 仍在工作（发一条消息后查日志 `SessionPool sweeper`）。

---

### 修复 3（P0）— data_sync / webhook 假成功 stub

**问题**：`data_sync.py:25`、`webhook.py:25` 是 TODO stub，返回 `success=True`，调度器误判。

**根治方案**：二者当前未在 `scheduler_config.json` 注册 job 条目（核查 config 只有 4 个 job：晨报/节点截止/Session清理/健康检查，data_sync 和 webhook **无 job 配置**，仅 handler 类被注册到 registry）。因此：
1. handler 的 `execute` 返回 `success=False` + 明确 summary"功能未实现"，避免任何未来误注册后假成功。
   - `data_sync.py:28` 改 `return JobResult(success=False, summary=f"数据同步未实现（类型：{sync_type}）")`
   - `webhook.py:28` 改 `return JobResult(success=False, summary=f"Webhook 未实现: {method} {url}")`
2. 保留 TODO 注释，标注"接入前不得在 scheduler_config.json 启用"。

**关键文件**：
- `emily-core/emily_core/scheduler/jobs/data_sync.py:22-28`
- `emily-core/emily_core/scheduler/jobs/webhook.py:18-28`

**验证**：handler 单测或手动调用应返回 `success=False`；核查 `scheduler_config.json` 确认二者无 job 条目。

---

### 修复 4（P1）— voice_entry 断头

**问题**：`registry.py:513-516` 注册的 `handle_voice_entry` 来自 `tools/project/__init__.py:43-45`，是返回 `{"success": False, "message": "voice_entry 工具待接入..."}` 的硬编码 stub；真正的实现 `tools/node_voice_entry_tool.py` 是孤儿（零导入）且自身 `from ..providers.llm_client` 指向不存在的模块（`providers/` 下无 `llm_client.py`，只有 `email/`、`rag/`）。

**根治方案**（遵循约束 #11 提醒义务——孤儿实现无人调用，stub 误导）：
1. 删除孤儿实现 `emily-core/emily_core/tools/node_voice_entry_tool.py`（坏导入，不可用）。
2. 从 `tools/project/__init__.py` 摘除 `handle_voice_entry` 的 stub 导出与注册（删除 `handle_voice_entry` 函数定义 + `_VOICE_ENTRY_SCHEMA`/`_VOICE_ENTRY_DESCRIPTION` 若存在）。
3. 从 `tools/__init__.py` 的 import 块（`from .project import ... handle_voice_entry`）和 `__all__` 移除 `handle_voice_entry`、`_VOICE_ENTRY_SCHEMA`、`_VOICE_ENTRY_DESCRIPTION`。
4. 从 `tools/registry.py` 注册逻辑摘除 voice_entry 注册（`registry.py:513-516`）。
5. 若 `tools_consistency.py` 的 `TOOL_SCHEMA_MAP` 有 voice_entry 条目，一并移除。
6. 若 SOP `.md` 中有 voice_entry 声明，需评估——但 voice_entry 当前是 stub，SOP 不可能成功调用它，故 SOP 侧大概率未使用，删除前 grep `sops/` 确认。

**关键文件**：
- `emily-core/emily_core/tools/node_voice_entry_tool.py`（删除）
- `emily-core/emily_core/tools/project/__init__.py`（移除 stub）
- `emily-core/emily_core/tools/__init__.py:34,46,70`（移除导入与 __all__ 条目）
- `emily-core/emily_core/tools/registry.py:513-516`（移除注册）

**验证**：`docker compose restart emily-core` → 启动日志无 voice_entry 注册；`uv run python scripts/check_tools_consistency.py` 通过；`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +` 后重启确认无 ImportError。

---

### 修复 5（P1）— rag_logger 写入接入

**问题**：`infrastructure/logging/rag_logger.py` 的 `RAGRetrievalLogger.log(...)` 零导入，写入端断裂；读端 `evolution_repo.py:536-564` 查 `rag_retrieval_logs` 表，模型 `models.py:1451` 存在 → evolution RAG 统计永远为空。读端仅被 `scripts/evolution_metrics.py`（CLI）消费，非 HTTP 可见，属遥测缺口。

**根治方案**：在唯一生产调用点 `tools/knowledge_search_tool.py:96` 的 `handle_knowledge_search` 内接入日志写入（该处已有 `elapsed_ms`、`response.total`、`response.results`（含 score）、异常上下文）。

在 `knowledge_search_tool.py` 成功分支（`response` 取得后，约 line 100-110）插入：
```python
try:
    from emily_core.infrastructure.logging.rag_logger import RAGRetrievalLogger
    scores = [r.score for r in response.results]
    await RAGRetrievalLogger.log(
        query_text=response.query or params.get("query", ""),
        provider=response.provider_name,
        hit_count=response.total,
        top_score=max(scores) if scores else 0.0,
        avg_score=(sum(scores) / len(scores)) if scores else 0.0,
        latency_ms=elapsed_ms,
        was_used_by_llm=True,
        # pipeline_run_id/conversation_id/user_id 暂留默认 ""（handler 签名无这些，v1 接受空 ID）
    )
except Exception as log_err:
    logger.warning("RAG retrieval log write failed: %s", log_err)
```
异常分支（`except Exception as e`，约 line 126）也记一条 `error_summary=str(e)`、`hit_count=0` 的失败日志。

**关键文件**：
- `emily-core/emily_core/tools/knowledge_search_tool.py:60-127`（接入点）
- `emily-core/emily_core/infrastructure/logging/rag_logger.py`（已存在，无需改）

**验证**：触发一次知识库检索（emy-test 发"查一下XX知识"命中 SOP knowledge_search）→ `docker exec emily-postgres psql -U emily -d emily -c "SELECT query_text, provider, hit_count, top_score, latency_ms FROM rag_retrieval_logs ORDER BY created_at DESC LIMIT 5;"` 应有新行。

---

### 修复 6（P1，实为 P2）— morning_report 清理

**问题**：`morning_report.py:25-26` 有 TODO + 占位前导文本 "🌅 {push_group} 晨报（待接入完整逻辑）"，但 `28-60` 已有真实进化摘要查询与 publish。`enabled: false`。现有 `MorningReportBuilder`（`services/evolution/morning_report_builder.py`）存在但未被调用——它是 per-user 个性化晨报，与 handler 的群发晨报定位不同，**不宜强行接入**（接入需遍历 active 用户逐个 build+publish，是另一项功能开发，超出"清理 stub"范畴）。

**方案**：
1. 删除 `morning_report.py:25` 的 TODO 行与 `26` 的占位前导文本，改为用 `report_text = f"🌅 {push_group} 晨报"` 作为前导，保留后续进化摘要追加逻辑。
2. 不接入 `MorningReportBuilder`（避免 scope 蔓延；如需 per-user 晨报另立需求）。

**关键文件**：`emily-core/emily_core/scheduler/jobs/morning_report.py:25-26`

**验证**：若临时把 job 设 `enabled: true` 触发一次（再改回 false），回复文本应无"待接入完整逻辑"字样。

---

### 修复 7（P2）— 删除孤儿文件整链

**问题**：报告 §2.1 列 9 个孤儿。复核后 `tools/base/`、`tools/business/` 非"零导入者"而是被 `tools/__init__.py` 导入，但 `tools/__init__.py` 本身无外部导入者（全库 `from emily_core.tools import` / `from .tools import` 零匹配；实际调用均直接 `from emily_core.tools.X_tool import`）。三者 + 报告 §6.1 的 `tools/__init__.py __all__` bug 构成一条自洽的孤儿重导出链。

**删除清单**（整链删除）：
1. `emily-core/emily_core/tools/node_voice_entry_tool.py`（并入修复 4）
2. `emily-core/emily_core/workitem/pipeline/interfaces/auth.py`（AuthDecision/AuthResult，唯一消费者 workitem_agent.py 已删；复核确认 `interfaces/__init__.py` 不导出它，`permission/auth_engine.py` 是同名不同模块）
3. `emily-core/emily_core/scripts/build_world_book.py`（根 `scripts/build_world_book.py` 已注册且更完善）
4. `emily-core/emily_core/infrastructure/database/scripts/fill_users_required_fields.py`（有 SQL 孪生 `007_*.sql`）
5. `emily-core/emily_core/tools/base/`（整个目录，含 `__init__.py`）
6. `emily-core/emily_core/tools/business/`（整个目录，含 `__init__.py`）
7. `emily-core/emily_core/tools/__init__.py`（整链删除——它只重导出 base/business/project，无人导入 `emily_core.tools` 包本身；删除后 §6.1 `__all__` bug 自动消失）

   ⚠ 删除前必须确认：`registry.py` 是否通过 `from .base import` 或 `from .business import` 导入？复核 Explore 结论：`registry.py` 直接 import 扁平模块（`from .query_tool import`、`from .knowledge_search_tool import`、`from .event_tool import` 等），**不经过** `base/`/`business/`/`__init__.py`。因此删除整链不影响 registry。**但执行删除前必须再次 grep 确认 `from .tools import`、`from emily_core.tools import`、`from .base import`、`from .business import` 全库零匹配**（排除 docs/.md 与历史需求文档）。

8-10. 插件 3 个不使用副本（并入修复 9 处理）

**关键文件**：见上清单。

**验证**：删除后 `docker compose restart emily-core` 无 ImportError；`uv run python scripts/check_tools_consistency.py` 通过；清除 `__pycache__` 后重启。

---

### 修复 8（P2）— 删除 `_workitem_agent` 死属性 + 同步文档

**问题**：`emily_core/__init__.py:57` `self._workitem_agent = None` 死属性（全库无其他读写，复核确认 grep 仅命中定义本身 + `workitem/__init__.py:13` 注释 + docs）。CLAUDE.md:143 仍列 `workitem_agent.py` 为当前模块（已删除）。

**方案**：
1. 删除 `emily-core/emily_core/__init__.py:57` 的 `self._workitem_agent = None` 行。
2. CLAUDE.md:143 删除 workitem_agent.py 行（更新 §8 关键文件索引，指向 `workitem/langgraph_engine/`）。
3. 清理残留注释引用（修复 14 处理）。

**关键文件**：
- `emily-core/emily_core/__init__.py:57`
- `CLAUDE.md:143`（及 §8 索引表）

**验证**：`docker compose restart emily-core` 正常启动；`grep -r "_workitem_agent" emily-core/emily_core/` 仅剩 `workitem/__init__.py` 注释（或一并清理）。

---

### 修复 9（P2）— 插件适配器副本分歧

**问题**：`data/plugins/emily_agent/adapters/standard/` 是 `emily_core/adapters/standard/` 的手动副本。`message.py` 插件版多了 `event_id: str = ""`（企微去重用），核心版缺失。`command.py`/`result.py`/`route_decision.py` 插件版不被插件使用（插件只 import `message`/`reply`）。

**方案**：
1. 将 `event_id: str = ""` 字段（含注释 "插件层消息去重 ID（UUID4）"）同步到核心版 `emily-core/emily_core/adapters/standard/message.py`（加在 attachments 字段后）。**注意**：核心版 `create_from_standard()` 等逻辑需确认是否需处理新字段（字段有默认值，向后兼容，一般无需改）。
2. 删除插件不使用的 3 个副本：`data/plugins/emily_agent/adapters/standard/command.py`、`result.py`、`route_decision.py`。
3. 清理 `data/plugins/emily_agent/adapters/standard/__init__.py` 中对这 3 个模块的 re-export（复核：该 `__init__.py` re-export 它们但无 `from .standard import X` 触发，故也是死代码）。

**关键文件**：
- `emily-core/emily_core/adapters/standard/message.py`（加 event_id）
- `data/plugins/emily_agent/adapters/standard/{command,result,route_decision}.py`（删除）
- `data/plugins/emily_agent/adapters/standard/__init__.py`（清理 re-export）

**验证**：插件容器（astrbot）重启无 ImportError；核心容器重启后发一条企微格式消息（带 event_id）解析正常。

---

### 修复 10（P2）— 去重 remote/wecom compose

**问题**：`docker-compose-remote.yml` 与 `docker-compose-wecom.yml` 字节完全相同（`diff` 无输出），其一冗余。两者第 2 行都是 wecom 注释，说明 remote.yml 是 stale 副本。

**方案**：删除 `docker-compose-remote.yml`，保留 `docker-compose-wecom.yml`（命名更准确）。核查是否有文档/脚本引用 `docker-compose-remote.yml`，若有则更新引用。

**关键文件**：`docker-compose-remote.yml`（删除）

**验证**：`grep -r "docker-compose-remote" .` 无遗漏引用。

---

### 修复 11（P3）— 注册遗漏脚本

**问题**：8 个 scripts/ 脚本未注册到 `emily-data/config/scripts_registry.yaml`（该文件由 `scriptmgr export` 自动生成）。

**待评估脚本**（需逐个判断是否应注册，非全部注册）：
- `run_daily_file_parse.py` — 日常文件解析手动入口，**可能应注册**
- `ingest_knowledge.py` — 知识库向量化导入，**可能应注册**
- `snapshot.py` — 快照工具包装器，**可能应注册**
- `verify_langgraph_engine.py` — 引擎验证工具，**可能应注册**
- `rag_batch_test.py` — RAG 批量测试，测试类，可选
- `pdfread.py` — PDF 读取工具，工具类，可选
- `gen_paper_tables.py` — 论文表格生成，一次性脚本，**不注册**
- `migrate_file_purpose.py` — 文件用途迁移，一次性，**不注册**

**方案**：阅读每个脚本的 `__main__`/argparse 与 docstring，按约束 #10（ToolManager vs ScriptManager 边界）评估是否属"开发者脚本"范畴。对应注册的，按 `scripts_registry.yaml` 现有条目格式补条目（或用 `scriptmgr` 机制——若 scriptmgr 支持扫描注册则用之）。**此项需人工逐个判断，执行时先逐脚本读 docstring 再定**。一次性/迁移类脚本不注册。

**关键文件**：`emily-data/config/scripts_registry.yaml`、各 scripts/*.py

**验证**：`uv run python scripts/scriptmgr.py list` 列出新注册项；`uv run python scripts/scriptmgr.py check` 通过。

---

### 修复 12（P3）— 补全 scheduler/jobs/__init__.py __all__

**问题**：`scheduler/jobs/__init__.py` `__all__` 只导出 8 个，实际 13 个 handler 模块。缺失：`daily_insight`、`rule_induction`、`patch_validator`、`world_book_update`、`system_description_update` 对应的 Handler 类。

**方案**：读 5 个模块取真实类名（报告猜的 `PatchValidationHandler` 等可能与实际 `PatchValidator`→`PatchValidationHandler` 不符，须以源码为准），补 import 行与 `__all__` 条目。注意：`emily_core/__init__.py` 直接按模块路径 import 各 handler（不经 `jobs/__init__.py`），故功能不受影响，但包导出应完整。

**关键文件**：`emily-core/emily_core/scheduler/jobs/__init__.py`

**验证**：`docker exec emily-core python -c "from emily_core.scheduler.jobs import __all__; print(len(__all__))"` 应为 13。

---

### 修复 13（P3）— tools/__init__.py

并入修复 7（整链删除，bug 自动消失）。若修复 7 决定保留 `tools/__init__.py`（不删整链），则单独修复：从 `__all__` 移除 `handle_query_data`（或补 `from .base import handle_query_data` 导入）。**推荐随修复 7 整链删除。**

---

### 修复 14（P3）— 清理 workitem_agent.py 残留注释

**问题**：多处注释引用已删的 `workitem_agent.py`（无功能影响）。

**方案**：清理以下注释（改为指向 `langgraph_engine/` 或删除引用）：
- `workitem/pipeline/bus.py:12`
- `workitem/langgraph_engine/agent/loop.py:31,296,336`
- `workitem/pipeline/context.py:10-11,40,96`
- `infrastructure/llm/prompt_loader.py:32,92`
- `workitem/__init__.py:13`（"_workitem_agent 和 injector 移至 langgraph_engine"）

**关键文件**：见上。

**验证**：`grep -rn "workitem_agent" emily-core/emily_core/` 无功能引用（注释已清理或更新）。

---

## 执行顺序与依赖

1. **先做无依赖的清理**：修复 7（删孤儿）、8（死属性）、10（去重 compose）、14（注释）——纯删除，低风险。
2. **再做配置修复**：修复 1（PG 路径，**需停 PG 容器迁移数据**）、2、3（scheduler）。
3. **再做代码修复**：修复 4（voice_entry 摘除）、5（rag_logger 接入）、6（morning_report 清理）、9（插件副本同步）。
4. **最后做完善**：修复 11（脚本注册）、12（__all__ 补全）。

每个修复后**清除 `__pycache__`**（`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`）并 `docker compose restart emily-core` 验证启动。

---

## 端到端验证清单

| 修复项 | 验证命令/方法 | 预期 |
|--------|--------------|------|
| 1 PG 路径 | `docker compose up -d emily-postgres` → `psql ... "SELECT count(*) FROM users;"` | 非零（数据已迁移） |
| 2 session_cleanup | 重启后查日志 | 无"已关闭 0 个"假成功；sweeper 仍工作 |
| 3 data_sync/webhook | 手动调 handler | 返回 `success=False` |
| 4 voice_entry | 启动日志 + `check_tools_consistency.py` | 无 voice_entry 注册，无 ImportError |
| 5 rag_logger | emy-test 触发知识检索 → 查 `rag_retrieval_logs` 表 | 有新行 |
| 6 morning_report | 临时启用触发一次 | 无"待接入完整逻辑"文本 |
| 7 孤儿删除 | 重启 + `check_tools_consistency.py` | 无 ImportError，一致性通过 |
| 8 死属性 | `grep -r "_workitem_agent" emily-core/emily_core/` | 仅剩注释（或已清理） |
| 9 插件副本 | astrbot + emily-core 重启 | 无 ImportError，企微消息解析正常 |
| 10 去重 compose | `grep -r "docker-compose-remote"` | 无遗漏引用 |
| 11 脚本注册 | `scriptmgr list` / `scriptmgr check` | 新项列出，自检通过 |
| 12 __all__ | `python -c "from emily_core.scheduler.jobs import __all__; print(len(__all__))"` | 13 |
| 14 注释 | `grep -rn "workitem_agent" emily-core/emily_core/` | 无功能引用 |

**整体冒烟**：`uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "<真实用户名>"` 应正常回复（知识检索/工具调用路径未因清理而中断）。

---

## 风险与回滚

- **修复 1 数据迁移**风险最高：务必先 `docker compose stop emily-postgres` 再移动目录，移动后核对 `PG_VERSION`/`base/` 完整。回滚：把目录改回 `db_seeds`。
- **修复 4/7 删除代码**：删除前 git 已提交当前状态可 `git checkout` 回滚单文件。
- **修复 5 rag_logger**：日志写入用 try/except 包裹，失败不影响主流程（仅 warning）。
- **修复 9 插件 event_id**：核心版加字段有默认值，向后兼容；若有 `**kwargs` 序列化路径需测试。

> 所有修复遵循 CLAUDE.md 约束 #0（根治）、#11（工具注册三步）、#12（注册接入元原则）。改动代码后同步更新对应 docs/（代码文件目录、接口协议、数据库设计按需）。
