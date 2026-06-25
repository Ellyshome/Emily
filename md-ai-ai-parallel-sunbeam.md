# Emily 项目共享基础文档重建计划

## Context（背景）

Emily 项目（原 EmyBot / `teambrain_core`）经历了主逻辑重构，现为 **Emily v0.6.0**：容器拆分（薄插件 `emily_agent` + 独立 `emily-core` FastAPI 内核），主逻辑由旧的 8 阶段 WorkOrder 管道（`intake→route→auth→plan→execute→compose→verify→archive`）重构为 **Session 主线 + WorkItem + 4 节点 Pipeline BUS**（意图→计划→执行→总结）。`看板内容/` 现有文档（CLAUDE.md / Wiki.md / Projects.md / Releases.md）描述的仍是旧 EmyBot 架构（`teambrain_core`、M15 八阶段、`pipeline_config_m15.json`），已不准确，需要全部重新梳理。

本次目标：按用户要求在项目根目录下生成一组多人协作共享基础 MD 文档，便于 AI 工具与新成员快速理解当前真实架构。所有内容以**当前实际代码**为准（已通过 3 路 Explore 全量核实：代码文件目录、DB schema 与接口协议、业务流与开发记录）。

## 决策（已与用户确认）

1. **旧文档**：`看板内容/` 下 4 份过时文档（CLAUDE.md / Wiki.md / Projects.md / Releases.md）**保留原位**，新文档取而代之成为权威；`issues.md` 是用户个人笔记，不动。
2. **存放位置**：AI 入口 `CLAUDE.md` 放项目根目录（Claude Code 自动加载）；其余 6 份放 `docs/` 子目录。根目录无现存 CLAUDE.md/README.md，不涉及覆盖。

## 文件清单（共 7 份，1 根 + 6 子目录）

| # | 路径 | 对应用户要求 |
|---|------|------------|
| 1 | `d:\app\Emily\CLAUDE.md` | 统一 AI 入口文件（概述+导引） |
| 2 | `d:\app\Emily\docs\代码文件目录.md` | 代码文件层级目录 + 各文件一句话说明 |
| 3 | `d:\app\Emily\docs\业务模块与运转全景.md` | 业务逻辑模块清单 + 运转全景图 |
| 4 | `d:\app\Emily\docs\接口协议与调用约定.md` | 接口协议 + 调用约定清单 |
| 5 | `d:\app\Emily\docs\数据库设计.md` | 表清单一句话功能 + 详细架构 |
| 6 | `d:\app\Emily\docs\开发记录.md` | 项目开发记录 |
| 7 | `d:\app\Emily\docs\技术踩坑备忘录.md` | 技术踩坑备忘录 |

命名沿用项目既有中文短名惯例（如旧 `开发记录.md` / `踩坑记录.md`），不加编号前缀；阅读顺序由 CLAUDE.md 导引表给出。文件名使用中文（Windows/UTF-8 已验证项目内大量中文文件名正常）。

## 各文档内容大纲

### 1. `CLAUDE.md`（根，AI 入口）
- **项目定位**：Emily v0.6.0，企业公共大脑 Agent，IM(QQ)交互，工作流留痕+SOP数字化+RAG知识库；一句话点明当前架构（容器拆分 + Session主线 + WorkItem + 4节点BUS）。
- **技术栈表**：FastAPI / SQLAlchemy 2.0 / PostgreSQL / DeepSeek(OpenAI兼容) / MaxKB+pgvector / Docker Compose。
- **容器拓扑**：5 容器（napcat / astrbot+emily_agent 薄插件 / emily-core 内核 / maxkb / emily-postgres）。
- **分层一句话**：薄插件 → HTTP/SSE → EmilyCore → Session → WorkItem → 4节点BUS → Application → Service → Repository → DB。
- **AI 会话启动流程**：① 读 CLAUDE.md → ② 按任务按需读 `docs/` 下 6 份 → ③ Memory 由 Claude 自动加载。
- **6 份文档导引表**：文件名 | 一句话 | 何时读。
- **开发约束**：业务内核独立（不 import astrbot）、分层不可跳、SOP 即路由（放文件→重启生效）、Hook 声明式三态、M14 结构化输出（框架直调 handler）、Sync repo + `asyncio.to_thread`。
- **日常命令**：docker compose 启停/日志、`python .claude/skills/emy-test/...` 实战测试、清 pycache。
- **维护约定**：非数据类代码改动后，自主同步更新对应 `docs/` 文档。
- **关键路径索引**：入口（`emily_core/__init__.py` EmilyCore）、配置（`emily-data/config/core_config.json` + `hook_config.json`）、SOP 目录（`emily-data/sops/`）、模型（`infrastructure/database/models.py`）。

### 2. `docs/代码文件目录.md`
- 顶层结构树（root：docker-compose-napcat.yml / emily-core/ / data/plugins/emily_agent/ / emily-data/ / tem/ / 需求文件/ / 看板内容/ / notebook.md）。
- 按 package 分组，每个 `.py` 一行：`路径 — 一句话`。覆盖：
  - `emily-core/api/`（HTTP/SSE 层：server / routes/{message,session,health} / sse/outbound / middleware/auth）
  - `emily_core/` 根（`__init__` EmilyCore / config / bootstrap / outbound_bus）
  - `adapters/`（standard/{message,reply,command,result,route_decision}、session/{session_pool,session_factory,session_config}）
  - `session/`（session_agent / session_context / session_state / focus_lock / confirm_queue）
  - `workitem/`（workitem / workitem_state / workitem_agent / scheduler / injector / pipeline/{bus,node,context,hook,hook_registry} / pipeline/interfaces/{auth,routing,planning,execution,guardian,risk} / pipeline/mocks/*；**明确标注** `_work_order_ref.py` 与 `_pipeline_context_ref.py` 为旧 M15 遗留参考、已被 WorkItem/BusContext 取代）
  - `agent/`（**明确标注冷备**：master_agent / business_flow_agent / guardian_agent / guardian_review / intent_registry / sop_parser / tool_registry / conversation_context / flow_renderer / mermaid_flow — 逻辑已提取到 SessionAgent/WorkItemAgent，热路径不 import）
  - `application/`（event/task/meeting/file/query/plan_task_app）
  - `services/`（domain_takeover / user_binding / message / event / task / meeting / file / file_storage / query / event_journal / pending_issues / user_memory / checkpoint / chat_archive / agent_trace / plan_task* / workflow_integrator）
  - `repositories/`（user/message/event/task/meeting/file/chat_archive/agent_reasoning/llm_interaction/tool_call/plan_task_repo）
  - `infrastructure/`（database/{session,models}、llm/client）
  - `providers/rag/`（base / maxkb_provider / local_fallback）
  - `tools/`（__init__ / business_flow_tools / business_flow_tool / event/task/meeting/file/query/plan_task/knowledge_search/memory/notebook/pending_issue/chat_archive_tool）
  - `data/plugins/emily_agent/`（薄插件：main / api_client / sse_listener / astrbot/{inbound_adapter,outbound_sender} / standard/* 副本）
  - `emily-data/`（sops/ 12份 + .sop_index.json / prompts/{master_agent.txt,守护Agent.md,domain_knowledge.md,flows/} / config/{core_config,hook_config}.json / baseknowledge/ / 运行时目录 notebooks,logs,attachments,user_memory,journal）
- 末尾"弃用/冷备/Mock 说明"小节汇总。

### 3. `docs/业务模块与运转全景.md`
- **Mermaid 端到端全景图**：IM → 薄插件去重+标准化 → POST /api/v1/message/send → `EmilyCore.handle_message` → DomainTakeover → UserBinding → `SessionPool.route` → `SessionAgent.handle`(fast-reply短路 / LLM意图识别 / WorkItem拆分 / FocusLock / ConfirmQueue) → `SessionScheduler` → `PipelineBUS` 4节点(+hooks) → WorkItemAgent → BusinessFlowTool handlers → Application → Service → Repository → DB → `OutboundEventBus` → SSE → 薄插件 → IM。
- **Mermaid WorkItem 状态机**：CREATED→PLANNING→EXECUTING→DONE/FAILED，EXECUTING↔WAITING_CONFIRM（注明 WAITING_CONFIRM 当前未被节点驱动）。
- **Mermaid 4 节点 BUS**：node1 意图+拆分 / node2 计划+标准 / node3 执行+验收 / node4 成果总结，每节点 before/after/on_error 挂载点。
- **Mermaid 分层架构图**。
- **业务模块清单表**（≈30 模块）：模块 | 职责 | 实现文件 | 依赖。
- **Mock vs Real 切换表**：Planner/Executor/Guardian/Auth/Risk 对应 `EMILY_*_MODE`，无 LLM 回退 mock。
- **SOP 系统说明**：发现（SOPIntentRegistry 扫描 sops/）/ 匹配（LLM 语义，registry 不匹配）/ 派发（SessionAgent→WorkItem→KnowledgeInjector 灌注→WorkItemAgent 计划绑定工具→RealExecutor 直调 handler）；12 份 SOP 清单。

### 4. `docs/接口协议与调用约定.md`
- **标准协议对象**（`adapters/standard/`）：StandardMessage / RouteDecision(adapter层) / ReplyMessage / RouteResult / HandlerResult / AgentStep+AgentResult / Command DTOs（Event/Task/Meeting/File/Query）—— 字段表。
- **管道阶段接口**（`workitem/pipeline/interfaces/`）：AuthEngine / RouteDecision(pipeline层) / PlanStep+ExecutionPlan / WorkAgent+StepResult(+ToolCallRecord/RagChunk/RagResult/DbResult/GuardianStepVerdict) / Guardian / RiskGrader —— 方法签名 + 契约职责。
- **工具定义协议**：ToolDefinition + ToolRegistry（LLM function-calling）、BusinessFlowTool + BusinessFlowToolRegistry（M14 框架直调）—— 字段 + 注册策略 + `get_openai_tools` / `get_tools_schema`。
- **RAG provider 协议**：RagProvider ABC（`search(query, top_k, stage, role)`）、SearchResult / RagSearchResponse。
- **LLM client 接口**：`chat` / `chat_json` / `chat_with_tools` / `set_trace_callback` —— 签名 + 返回 + trace 回调字段。
- **状态机契约**：WorkOrder(11态,旧) / WorkItem(6态,现行) / BusContext 字段与转换表。
- **HTTP/SSE API**：`POST /api/v1/message/send`（MessageIn→ReplyOut 或 204 异步）、`POST /api/v1/session/terminate`、`GET /api/v1/health`、`GET /api/v1/events/outbound`（SSE 事件 reply/progress/file_send/session_closed + 15s 心跳）。
- **调用约定清单**：分层不可跳；sync repo + `asyncio.to_thread`；M14 命中 SOP 走框架直调 handler、unmatched 走 ToolRegistry 自由推理；Hook 三态 deny-wins（before 异常=BLOCK、after 异常不阻断）；FK 列语义（`messages.conversation_id` 是 UUID FK 非业务串，需解析）；每请求新建 Registry 闭包注入 user_id；Session 级串行锁、跨 Session 并行。

### 5. `docs/数据库设计.md`
- **引擎/会话**：PostgreSQL，连接串 `postgresql://{user}:{pw}@emily-postgres:5432/emily`，pool(5/10/pre_ping/recycle=3600)，`expire_on_commit=False`；时间戳为 ISO8601 字符串；PK 为 UUID/带前缀 ID（`_new_uuid`/`_new_id(prefix)` → `{PREFIX}-YYYYMMDD-{uuid8}`）；无 Alembic，`Base.metadata.create_all()` 幂等建表（**已有库不自动加索引/列，需手动 DDL**）。
- **表清单速查表**（29 表）：表名 | 列数 | 一句话功能。
- **详细架构**（29 表逐表）：字段名 | 类型 | 可空 | 默认 | 主/外键 | 说明。覆盖：users / user_im_bindings / conversations / messages / message_attachments / projects / events / tasks / meetings / files / company_info / project_indicator_details / business_flow_orders / instruction_orders / project_plans / plan_items / sop_routing_logs / agent_reasoning_logs / llm_interaction_logs / tool_call_logs / hook_execution_logs / sop_checkpoints / permission_groups / sop_business_flows / sop_permission_bindings / plan_task_templates / plan_task_instances / plan_task_logs / plan_task_deliverables。
- **Mermaid 核心表关系图**（users↔user_im_bindings↔messages↔conversations；projects↔events/tasks/meetings/files；plan_task_templates↔instances↔logs/deliverables；sop_business_flows↔permission_groups）。
- **关键索引/约束**（unique、partial unique `WHERE status!='CANCELLED'`、advisory lock 等）。

### 6. `docs/开发记录.md`
- **演进时间线**：EmyBot 旧架构（M1–M15，teambrain_core，AstrBot 内嵌插件）→ Emily v0.6.0 Phase 0（容器拆分）→ Phase A（Session 骨架 + 4节点BUS，全 Mock）→ Phase B（SessionAgent LLM 意图 + KnowledgeInjector + WorkItemAgent 自规划 + AuthHook，旧 agent/ 转冷备）→ Phase C（RealExecutor M14 直调 + GuardianReview/Agent + RealAuth/Risk）→ 计划任务模块（SOP-009/010，4 表，7态机，后台调度，P0–P2 修复完成）。
- **当前状态**：Phase 0+A+B+C 实施完成（13/13 实战测试通过、7 bug 已修）；计划任务模块代码审核 P0–P2 已修、待真实验证；遗留 P1–P3（knowledge_search 接线 RealExecutor、GuardianAgent 缺 query_service、ProgressHook progress_sender 为 None、AuthHook registry 初始化）。
- **各阶段能力清单**（精简，从 tem/ 报告提取）。
- **关键架构决策（ADR 式）**：WorkOrder→WorkItem（消息级处理上移 Session，一消息→0..N WorkItem）；旧 agent/ 冷备不 import；M14 结构化输出优先于 ReAct；Hook 声明式 JSON 配置；容器拆分内核独立；PermissionSnapshot 一次性灌注只读；sync repo + to_thread 妥协。
- **权威文档索引**：`tem/`（0623主系统架构/README/规模报告/PhaseB/PhaseC/test_report/现状分析/实现计划）、`需求文件/`（计划任务/鉴权/全局状态机/记忆系统）。

### 7. `docs/技术踩坑备忘录.md`
按类别组织，每条：**现象 | 原因 | 解决 | 相关文件**。
- **容器/部署**：`__pycache__` 不刷新（bind-mount 不触发重编译，需手动清）；Git Bash 路径污染致 SOP 目录解析到 `C:/Program Files/Git/app/sops`（容器内 `/app/sops` 优先）；Docker 服务名 `maxkb` 在宿主机不可达（tester 自动替换 localhost）；PowerShell 默认 GBK 中文乱码（`$env:PYTHONIOENCODING="utf-8"`）。
- **数据库**：FK 列语义陷阱（`messages.conversation_id` 是 UUID FK 非业务串，`create_outbound`/AgentReasoningLog 需解析，`create_from_standard` 安全）；`create_all` 不给已有表加索引/列；PostgreSQL advisory lock 是 session 级，session 关闭即释放（`_tick` 失去保护）；`expire_on_commit=False` 避 DetachedInstanceError；时间戳为字符串非 DB datetime；`instance_no` vs `instance_id` 混淆（工具收 no 传 id 致断裂，需 `get_by_instance_no` 解析）；缺 `UniqueConstraint` 致无幂等；User 模型缺 `permission_level` 字段致反委派检测失效。
- **AstrBot/IM**：`event.plain_result()` 非 awaitable；`set_result` 被后续 `send()` 覆盖（前导/中间消息须 `event.send()` 直发）；插件 `__init__(self, context, config=None)` 必收 config；消息去重指纹不含 sender_id；NapCat 端口 `ws://astrbot:6199/ws`。
- **异步/并发**：sync repo + async service 阻塞事件循环（`asyncio.to_thread` 包裹）；事务边界——instance 创建与 log 创建跨 session，log 失败仍留已提交 instance。
- **Hook/管道**：三态 deny-wins（before 异常=BLOCK、after 异常不阻断）；TraceHook 名称不匹配（代码 `trace.reasoning_start` vs 配置 `trace.execution_start`）；`context.baggage` 是 dict 无 `.set()`；`create_all_tools` mock 模式缺参（mock 模式跳过，M14 直用 BusinessFlowToolRegistry）。
- **RAG**：tester 替换 `maxkb`→`localhost`；MaxKB `hit_test` 为未公开内部 API；`knowledge_search` 未接线 RealExecutor 的 BusinessFlowToolRegistry（RAG 走 node3 内联，遗留）。
- **模式切换**：`bootstrap._config_from_env` 曾缺 10 个 `EMILY_*_MODE`/`EMILY_MAXKB_*` 映射（Docker 设 real 但 Config 留 mock，MaxKB 未初始化）；real 模式无 LLM 时 `_resolve_mode` 回退 mock。

## 交叉链接与维护约定
- `CLAUDE.md` 导引表链接到 `docs/` 下 6 份；每份 `docs/` 文档顶部"← 返回 [CLAUDE.md](../CLAUDE.md)"。
- 相关文档间互相引用（如 `业务模块与运转全景.md` ↔ `接口协议与调用约定.md` ↔ `数据库设计.md`）。
- `CLAUDE.md` 维护约定：非数据类代码改动后同步更新对应 `docs/` 文档与 `开发记录.md`。
- 全文使用相对路径 markdown 链接（VSCode 可点击），中文文件名直接 URL 编码即可。

## 验证方式
1. **文件就位**：根目录生成 `CLAUDE.md`；`docs/` 下生成 6 份；`看板内容/` 与 `issues.md` 原样未动。
2. **链接有效**：CLAUDE.md 中 6 条导引链接能跳转到对应文件；每份 docs 顶部返回链接有效。
3. **内容准确抽查**（对照实际代码）：数据库表数 = 29；`emily_core/workitem/_work_order_ref.py` 标注为遗留；`agent/` 标注为冷备；4 节点 BUS = node1..4；WorkItem 状态 = CREATED/PLANNING/EXECUTING/DONE/FAILED/WAITING_CONFIRM。
4. **Mermaid 语法**：3 张 Mermaid 图能在 VSCode 预览渲染（端到端流、WorkItem 状态机、4 节点 BUS；DB 关系图）。
5. **无覆盖**：确认未覆盖根目录任何现存文件（根目录无 CLAUDE.md/README.md，仅新增）；`docs/` 为新建目录。
6. **可读性**：每份文档有目录/小标题，可在 1–2 分钟内扫读定位。

## 实施步骤（退出 plan 后执行）
1. 创建 `docs/` 目录。
2. 依次写入 7 份文档（按 1→7 顺序，CLAUDE.md 最后写以便引用其余 6 份最终文件名）。
3. 自查链接与 Mermaid 语法。
4. 向用户汇报生成结果与各文档要点。
