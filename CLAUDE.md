# Emily — 企业公共大脑 Agent

> **本文档作用**：AI 辅助开发工具的统一入口。Claude Code 启动时自动加载本文档。概述项目全局 + 后续关键文档导引，便于 AI 工具快速理解当前真实架构。
>
> **⚠ 注意**：本项目经历过主逻辑重构（EmyBot/teambrain_core → Emily/emily_core）。`看板内容/CLAUDE.md` 描述的是**旧架构**（EmyBot M15 八阶段 WorkOrder + AstrBot 内嵌插件），**已不准确**。本文档为 **当前真实架构**（Emily v0.6.0：双容器 + Session 主线 + WorkItem 4 节点 BUS）。旧架构已完全移除，当前唯一路径是 `WorkItem` + `BusContext` + 4 节点 `PipelineBUS`。

---

## 1. 项目定位

Emily v0.6.0 是面向企业的 AI Agent 工具，通过 IM（QQ）与员工交互，实现：

- 团队工作流记录与留痕（事件/任务/会议/文件）
- 业务 SOP 数字化与 LLM 引导
- 企业知识库 RAG（MaxKB hit_test 纯向量检索，Qwen3-Embedding-0.6B + pgvector）
- 全景节点图（V2 重构中：文件级依赖 + 父子权重聚合 + 三态流转模型）

**当前架构一句话**：双容器系统（薄插件 `emily_agent` + 独立 `emily-core` FastAPI 内核），采用 **Session 主线 + WorkItem + 4 节点 PipelineBUS** 的消息处理架构。旧 M15 八阶段 WorkOrder 管道已完全移除，旧 `sm_*` 全局状态机模块已清理，全景节点 V2 重构进行中。

**Python 环境管理**：本项目基于 uv。请使用 `uv run python ...` 而非裸 `python`。

---

## 2. 技术栈

| 层 | 技术选型 | 说明 |
|----|----------|------|
| **IM 接入** | NapCat + AstrBot (Docker) | QQ 消息桥接 |
| **插件** | AstrBot Plugin Shell（薄插件 ~100 行） | 仅负责去重 + 标准化 + HTTP 转发 + SSE 监听，**无业务逻辑** |
| **业务内核** | FastAPI + Python async | `emily-core` 独立容器，不 import AstrBot |
| **数据库** | PostgreSQL + SQLAlchemy 2.0 sync | 53 表，esmily-postgres 容器 |
| **AI/LLM** | DeepSeek API（OpenAI 兼容） | chat / chat_json / chat_with_tools |
| **RAG** | MaxKB hit_test API | Qwen3-Embedding-0.6B 向量检索，可选本地关键词回退 |
| **部署** | Docker Compose | 5 容器：napcat / astrbot / emily-core / maxkb / emily-postgres |

---

## 3. 容器拓扑

```
QQ → NapCat → AstrBot → emily_agent 薄插件
                            ↓ HTTP POST /api/v1/message/send
                         emily-core (FastAPI :18080)
                            ↓ emily-postgres:5432
                         PostgreSQL (emily)
                            ↓ SSE /api/v1/events/outbound
                         emily_agent → AstrBot → QQ 回复
```

**5 容器**：

| 容器 | 端口 | 说明 |
|------|------|------|
| napcat | 6099 (WebUI) | QQ 桥接 |
| astrbot | — | 消息平台 + 薄插件宿主 |
| emily-core | 18080 | **业务内核** — FastAPI + emily_core 包 |
| maxkb | 8080 | 知识库 RAG（Qwen3-Embedding-0.6B + pgvector） |
| emily-postgres | 5432 | 独立 PostgreSQL（emily 库） |

---

## 4. AI 会话启动流程

1. **读完本文档（CLAUDE.md）** ← 你在这里
2. **按需读 `docs/` 下的 6 份导航文档**（见下方导引表）
3. **代码探索优先用 CodeGraph MCP**：项目已初始化 [CodeGraph](docs/代码文件目录.md) 索引（169 文件 / 2,110 符号 / 4,051 关系边）。查符号、调用链、架构流程直接用 `codegraph_explore` / `codegraph_search` / `codegraph_callers` / `codegraph_callees`，避免全量 Grep/Read
4. Memory 文件由 Claude 自动加载，无需手动读取

> **重要**：本文档 + docs/ 已覆盖项目全貌。不要向用户重复介绍架构——除非明确要求。进入后直接开始当前任务。改动代码后应同步更新对应的 docs/ 文档。

---

## 5. 文档导引

| 文档 | 一句话 | 何时读 |
|------|--------|--------|
| [docs/代码文件目录.md](docs/代码文件目录.md) | 全量 100+ 文件树 + 每文件一句话 | 找代码位置、理解文件职责、了解哪些是弃用/冷备/Mock |
| [docs/业务模块与运转全景.md](docs/业务模块与运转全景.md) | Mermaid 端到端流程图 + ~30 模块清单 + WorkItem 状态机 + Mock/Real 切换 | 理解系统如何运转、模块之间如何交互、消息处理全路径 |
| [docs/接口协议与调用约定.md](docs/接口协议与调用约定.md) | 标准协议对象 + 管道接口 ABC + 工具定义 + HTTP/SSE API + 12 条调用约定 | 写新模块/工具前确认契约、排查接口问题 |
| [docs/数据库设计.md](docs/数据库设计.md) | 53 表速查 + 每表完整字段架构 + ER 关系图 + 维护注意事项 | 改模型、加表/字段、排查数据问题 |
| [docs/开发记录.md](docs/开发记录.md) | EmyBot M1-M15→Emily Phase 0/A/B/C 演进 + 7 项架构决策 + 权威文档索引 | 了解历史决策原因、查阅原始设计文档 |
| [docs/技术踩坑备忘录.md](docs/技术踩坑备忘录.md) | 按类别的 20+ 踩坑（容器/DB/AstrBot/异步/Hook/RAG/模式切换），每条现象+原因+解决 | 遇到问题先查、写新代码避坑 |

---

## 6. 开发约束

| # | 约束 | 说明 |
|----|------|------|
| 1 | **业务内核独立** | `emily_core` 不 import 任何 `astrbot.*` 包 |
| 2 | **分层不可跳** | `API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB` |
| 3 | **SOP 即路由** | 新增 SOP = 放 `.md` 到 `emily-data/sops/` → 重启生效。SkillRegistry 管理目录索引，LLM 做语义匹配 |
| 4 | **Hook 声明式 JSON** | 新增 Hook 编辑 `hook_config.json` 注册 → 改 `pipeline/hook.py` 实现类 → 重启生效。当前 4 种 Hook：Auth/Audit/Trace/Progress |
| 5 | **M14 结构化输出优先** | 命中 SOP → LLM chat_json → `{tool, params}` → 框架直调 `BusinessFlowTool.handler(params)`。不暴露为 LLM function-calling。Unmatched → SkillExecutor 兜底 |
| 6 | **Sync repo + `asyncio.to_thread`** | Repository 全 sync，async Service 用 `asyncio.to_thread()` 包裹 |
| 7 | **Hook 三态 deny-wins** | ALLOW/WARN/BLOCK。before 异常=BLOCK；after 异常不阻断 |
| 8 | **`agent/` 仅保留 sop_parser** | 原 MasterAgent/BusinessFlowAgent 等已提取到 SessionAgent/WorkItemAgent。SOPIntentRegistry 和 ToolRegistry 已废弃删除，`agent/` 仅保留 sop_parser 工具模块 |
| 9 | **M15 WorkOrder 已弃用** | 旧 M15 8 阶段 WorkOrder 管道已完全移除，当前唯一路径是 `WorkItem` + `BusContext` + 4 节点 `PipelineBUS` |

---

## 7. AI 开发工具

| 工具 | 说明 | 用法 |
|------|------|------|
| **CodeGraph MCP** | 代码知识图谱索引（169 文件 / 2,110 符号 / 4,051 边），SQLite 后端，文件变更秒级热更新 | `codegraph_explore` 理解代码/架构/流程；`codegraph_search` 搜符号；`codegraph_callers/callees` 查调用链；`codegraph_impact` 评估改动影响 |
| **emy-test** | Docker 内 emily-core 生产实战测试（HTTP + SSE） | `uv run python .claude/skills/emy-test/cli.py --managed --llm --message "..." --sender "真实用户名" --sender-id "真实UUID"` |

**emy-test 强制规则**：`--sender-id` 必须使用 `users` 表中真实存在的用户 UUID。随便编一个不存在的 ID 会导致：系统自动创建用户（污染 users 表）+ 权限降级到 level 1（测试的是访客降级路径，结果完全不可信）。每次测试前先查：`docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 10;"`

**CodeGraph 优先原则**：代码探索类问题（"X 怎么工作"、"谁调用了 Y"、"改动 Z 会影响什么"）优先用 CodeGraph，一句 `codegraph_explore("SessionAgent handle_message")` 替代多轮 Grep+Read。日常写代码前先用 `codegraph_explore` 了解相关模块，而非全量通读文件。

> CodeGraph 索引已随项目初始化（`codegraph init`），`.codegraph/` 目录已加入 `.gitignore`，无需手动维护。

---

## 8. 关键文件索引

| 文件 | 角色 |
|------|------|
| `emily-core/emily_core/__init__.py` | `EmilyCore`：惰性初始化所有子系统，暴露 `handle_message` |
| `emily-core/emily_core/bootstrap.py` | `init()`：env→config 映射、PostgreSQL init_db、返回 EmilyCore 实例 |
| `emily-core/emily_core/config.py` | `Config` dataclass：全部运行时配置（LLM/DB/RAG/Session/管道节点模式） |
| `emily-core/api/server.py` | FastAPI app，lifespan 初始化 Core，注册全部路由 |
| `emily-core/api/routes/message.py` | `POST /api/v1/message/send`：消息入口 |
| `emily-core/emily_core/adapters/session/session_pool.py` | `SessionPoolManager`：conversation_id→SessionAgent 路由 |
| `emily-core/emily_core/session/session_agent.py` | `SessionAgent`：每会话大脑——快回/意图/WorkItem 拆分/回复聚合 |
| `emily-core/emily_core/workitem/workitem.py` | `WorkItem`：单任务全息记录 + 6 态状态机 |
| `emily-core/emily_core/workitem/workitem_agent.py` | `WorkItemAgent`：4 节点 handler + Mock/Real 模式切换 + auth/risk |
| `emily-core/emily_core/workitem/pipeline/bus.py` | `PipelineBUS`：4 节点执行总线 + Hook 触发 |
| `emily-core/emily_core/workitem/pipeline/hook.py` | Hook 基类 + 4 种具体 Hook 子类（Auth/Audit/Trace/Progress） |
| `emily-core/emily_core/services/node_batch.py` | `create_node_tree`：全景节点批量创建核心（CLI 和系统工具共享） |
| `emily-core/emily_core/services/node_batch_update.py` | 批量更新/激活/废弃/进度更新核心（CLI 和系统工具共享） |
| `emily-core/emily_core/scheduler/engine.py` | `SchedulerEngine`：系统调度引擎（tick 循环 + Advisory Lock + Hook + JobHandlerRegistry） |
| `emily-core/emily_core/scheduler/service.py` | `SchedulerService`：调度作业 CRUD + 执行记录 |
| `emily-core/emily_core/scheduler/jobs/periodic_node.py` | `PeriodicNodeHandler`：定期创建 TASK 节点（替代旧 PlanTask 循环模板） |
| `scripts/manage_nodes.py` | 全景节点管理 CLI 脚本（create/update/activate/discard/progress/query） |
| `emily-core/emily_core/infrastructure/database/models.py` | ORM 模型——**53 张表**（PlanTask 4 表 + SOPCheckpoint 已废弃删除） |
| `data/plugins/emily_agent/main.py` | AstrBot 薄插件入口（~100 行，无业务逻辑） |
| `emily-data/config/core_config.json` | 非机密运行时配置 |
| `emily-data/config/hook_config.json` | Hook 声明式挂载配置 |
| `emily-data/sops/` | SOP 业务流手册仓库（10 份 .md） |
| `emily-data/prompts/` | Agent system prompt 模板（session/workitem/guardian_step/guardian_reply/project .md） |

---

## 8. 日常命令

```powershell
# 启动全部容器
docker compose -f docker-compose-napcat.yml up -d

# 重启 emily-core（代码变更后）
docker compose -f docker-compose-napcat.yml restart emily-core

# 查看 Emily 日志
docker logs --tail 100 emily-core 2>&1

# 查看全体容器状态
docker compose -f docker-compose-napcat.yml ps

# 生产环境实战测试（推荐用 --sender 传入用户名，自动从 users 表解析 QQ 号）
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' LIMIT 5;"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "真实用户名"

# 命令行快速测试（不指定用户则交互式选择）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好"

# 全景节点批量创建（预览模式）
uv run python scripts/manage_nodes.py create --file nodes.yaml --dry-run

# 全景节点批量创建（实际写入）
uv run python scripts/manage_nodes.py create --file nodes.yaml

# 批量更新节点字段
uv run python scripts/manage_nodes.py update --file updates.yaml

# 批量激活/废弃节点
uv run python scripts/manage_nodes.py activate --node-ids SG-001,SG-002 --operator-id <UUID>
uv run python scripts/manage_nodes.py discard --node-ids SG-001,SG-002 --operator-id <UUID>

# 批量更新成果进度
uv run python scripts/manage_nodes.py progress --file progress.yaml

# 全景节点查询
uv run python scripts/manage_nodes.py query --project-id ECOCITY-26

# 查看 PostgreSQL 表
docker exec -it emily-postgres psql -U emily -d emily -c "\dt"

# 清除 pycache（bind-mount 不自动刷新！）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
```

---

## 9. 踩坑速查

- **`__pycache__` 不会自动刷新**：Docker bind-mount 不触发 Python 重编译，每次代码变更后必须清除
- **FK 列语义陷阱**：`messages.conversation_id` 是 FK→`conversations.id`（UUID），不是业务 conversation_id 字符串。`create_outbound()` 写入前需 `_resolve_conversation_id()` 转换。`create_from_standard()` 已正确处理
- **SOP 目录解析**：容器内 `/app/sops` 优先 → env `EMILY_SOP_REPOSITORY_DIR` → dev 回退 `emily-data/sops`
- **`instance_no` vs `instance_id`**：工具 handler 收到业务编号（PTI-...），需先 `get_by_instance_no()` 解析 UUID
- **`event.plain_result()` 非 awaitable**：AstrBot 中 `result = event.plain_result(text); event.set_result(result); event.stop_event()`
- **`set_result()` 被覆盖**：使用 `event.send()` 直发前导/中间消息
- **插件 DTO 副本是设计意图**：`data/plugins/emily_agent/adapters/standard/` 下副本使插件不依赖 Core 包
- **无 LLM 时 return None**：放行给 AstrBot 兜底，不硬编码回复
- **Mock 是默认模式**：`EMILY_PLANNER_MODE=mock` 等。`real` 需有 LLM client 才生效
- **expire_on_commit=False**：避免 ORM 对象在 session 外访问报 `DetachedInstanceError`
- **Windows PowerShell GBK 乱码**：预先 `$env:PYTHONIOENCODING="utf-8"`
- **emy-test 禁用假 sender-id**：随便造一个 `--sender-id`（如 `zhang_gong`、`alice`）不在 users 表中，会导致系统自动创建用户（permission_level=1）污染 DB，且权限降级使测试结果完全不可信。必须先查 users 表取真实 UUID 再测试

> 完整踩坑清单见 [docs/技术踩坑备忘录.md](docs/技术踩坑备忘录.md)

---

## 10. 维护约定

1. **改动代码 → 同步更新 docs/**：非数据类代码改动后，自主检查并更新对应文档（代码文件目录、接口协议、数据库设计、业务模块全景图）。记录新架构决策到开发记录。
2. **新增 SOP → 更新 docs/业务模块与运转全景.md**：加 `.md` 到 `sops/` 后，同步更新全景图中的 SOP 清单。
3. **新增表/字段 → 更新 docs/数据库设计.md**：同步更新表清单一句话 + 详细架构。
4. **踩坑 → 追加 docs/技术踩坑备忘录.md**：发现新坑及时记录，按类别追加。
5. **`看板内容/` 保留不变**：旧 EmyBot 开发看板保留作历史参考。
