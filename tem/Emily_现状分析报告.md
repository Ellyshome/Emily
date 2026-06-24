# Emily 项目现状分析报告

> **生成日期**: 2026-06-24  
> **项目版本**: v0.6.0  
> **当前阶段**: Phase 0 + Phase A 已完成，Phase B 待启动

---

## 一、项目概况

### 1.1 项目定位

**Emily** 是一个面向建筑工程行业的**企业项目管理 AI 助手**，通过即时通讯（QQ/NapCat + AstrBot）为用户提供：

- 项目事件记录与追溯
- 任务跟踪与管理
- 会议纪要归档
- 文件元数据管理与存储
- 结构化数据查询
- 企业知识库 RAG 检索
- 业务 SOP 数字化引导与执行
- 深度数据分析（Guardian 守护审计）

### 1.2 技术栈

| 层次 | 技术 | 说明 |
|------|------|------|
| IM 桥接 | NapCat | QQ 协议 WebSocket 桥接 |
| 插件宿主 | AstrBot | 插件运行时框架 |
| 业务核心 | Python 3.12 + FastAPI | 独立 Docker 容器 |
| 数据库 | PostgreSQL 16 + SQLAlchemy 2.0 | 22 张业务表 |
| AI / LLM | DeepSeek API（兼容 OpenAI） | 思考模式、函数调用 |
| 向量检索 | MaxKB + Qwen3-Embedding-0.6B | RAG 知识库 |
| 容器编排 | Docker Compose | 5 服务：napcat, astrbot, emily-core, maxkb, emily-postgres |

### 1.3 架构概览

```
QQ → NapCat → AstrBot → emily_agent (薄插件, ~100行) 
                              │ HTTP POST /api/v1/message/send
                              ▼
                         emily-core (Docker, FastAPI)
 ┌──────────────────────────────────────────────────────┐
 │ SessionPool → SessionAgent → WorkItem → PipelineBUS  │
 │                                              │        │
 │   wi_node1 → wi_node2 → wi_node3 → wi_node4  │        │
 │   (路由+拆分) (规划+标准) (执行+验证) (汇总) │        │
 │                                              │        │
 │   当前：4个节点全部使用 Mock 大脑              │        │
 │   待接入：MasterAgent / BusinessFlowAgent /   │        │
 │          GuardianAgent / GuardianReview       │        │
 └──────────────────────────────────────────────────────┘
                              │ SSE event=reply
                              ▼
                         emily_agent → AstrBot → NapCat → QQ
```

### 1.4 项目规模

| 指标 | 数值 |
|------|------|
| Python 有效代码行 | ~15,300 行 |
| Python 源文件 | ~105 个 |
| SOP 文档 | 10 篇 ~2,000 行 |
| 基础设施层占比 | ~50%（已迁移，生产级） |
| 真实 Agent 大脑占比 | ~25%（已迁移，未接入） |
| 新编排层占比 | ~21%（Phase A 骨架） |
| 薄插件层占比 | ~5% |

---

## 二、已完成模块清单

### 2.1 API 层（emily-core/api/）

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| FastAPI 应用入口 | `api/server.py` | ✅ 完成 | 路由注册、SSE 挂载 |
| 消息接收路由 | `api/routes/message.py` | ✅ 完成 | POST /api/v1/message/send |
| 会话终止路由 | `api/routes/session.py` | ✅ 完成 | POST /api/v1/session/terminate |
| 健康检查路由 | `api/routes/health.py` | ✅ 完成 | GET /api/v1/health |
| SSE 出站流 | `api/sse/outbound.py` | ✅ 完成 | GET /api/v1/events/outbound |
| 鉴权中间件 | `api/middleware/auth.py` | ⚠️ 占位 | Token 校验可选，默认放行 |

### 2.2 数据库层（emily-core/emily_core/infrastructure/database/）

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| ORM 模型（22 表） | `models.py` | ✅ 完成 | 用户、消息、事件、任务、会议、文件、项目、SOP 追踪等 |
| 数据库会话 | `session.py` | ✅ 完成 | 连接池、自动建表、上下文管理器 |

### 2.3 仓储层（emily-core/emily_core/repositories/） — 10 个

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 消息仓储 | `message_repo.py` | ✅ 完成 | 创建、查询、历史、统计 |
| 用户仓储 | `user_repo.py` | ✅ 完成 | CRUD、IM 绑定 |
| 事件仓储 | `event_repo.py` | ✅ 完成 | CRUD、编号生成、筛选 |
| 任务仓储 | `task_repo.py` | ✅ 完成 | CRUD、编号生成 |
| 会议仓储 | `meeting_repo.py` | ✅ 完成 | CRUD、编号生成 |
| 文件仓储 | `file_repo.py` | ✅ 完成 | CRUD、编号生成 |
| 聊天归档仓储 | `chat_archive_repo.py` | ✅ 完成 | 附件、会话文件 |
| Agent 推理日志仓储 | `agent_reasoning_repo.py` | ✅ 完成 | 写入、完结、追踪 |
| LLM 交互日志仓储 | `llm_interaction_repo.py` | ✅ 完成 | 创建、更新、用量统计 |
| 工具调用日志仓储 | `tool_call_repo.py` | ✅ 完成 | 创建、更新 |

### 2.4 服务层（emily-core/emily_core/services/）— 15 个

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 事件服务 | `event_service.py` | ✅ 完成 | 创建待确认/已确认/已取消事件、编号、回复格式化 |
| 任务服务 | `task_service.py` | ✅ 完成 | 创建任务、编号生成 |
| 会议服务 | `meeting_service.py` | ✅ 完成 | 归档会议纪要 |
| 文件服务 | `file_service.py` | ✅ 完成 | 文件元数据记录 |
| 查询服务 | `query_service.py` | ✅ 完成 | 9 种查询类型、跨仓储查询 |
| 消息服务 | `message_service.py` | ✅ 完成 | 消息追踪、幂等、已处理标记 |
| 用户绑定服务 | `user_binding_service.py` | ✅ 完成 | 自动创建用户、IM 身份绑定 |
| 领域接管服务 | `domain_takeover_service.py` | ✅ 完成 | @机器人、群聊/私聊模式判断 |
| 用户记忆服务 | `user_memory_service.py` | ✅ 完成 | 长期记忆 Markdown 文件读写 |
| 事件日志 | `event_journal.py` | ✅ 完成 | 追加式项目事件日志 |
| 待办问题服务 | `pending_issues.py` | ✅ 完成 | 待办检查清单增删查 |
| 文件存储服务 | `file_storage_service.py` | ✅ 完成 | IM 附件下载、本地存储 |
| 聊天归档服务 | `chat_archive_service.py` | ✅ 完成 | 出入站消息归档、日统计 |
| Agent 追踪服务 | `agent_trace_service.py` | ✅ 完成 | 推理+交互+工具调用日志 |
| 检查点服务 | `checkpoint_service.py` | ✅ 完成 | SOP 执行检查点确认/取消/过期/恢复 |

### 2.5 工具层（emily-core/emily_core/tools/）— 10 个

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 数据查询工具 | `query_tool.py` | ✅ 完成 | 9 种查询类型 |
| 知识搜索工具 | `knowledge_search_tool.py` | ✅ 完成 | RAG 知识库搜索 |
| 用户记忆工具 | `memory_tool.py` | ✅ 完成 | 写长期记忆 |
| 待办问题工具 | `pending_issue_tool.py` | ✅ 完成 | 查询/解决待办 |
| 聊天归档工具 | `chat_archive_tool.py` | ✅ 完成 | 聊天历史查询 |
| 文件工具 | `file_tool.py` | ✅ 完成 | 发送/读取文件 |
| 事件记录工具 | `event_tool.py` | ✅ 完成 | M14 结构化输出 |
| 任务记录工具 | `task_tool.py` | ✅ 完成 | M14 结构化输出 |
| 会议记录工具 | `meeting_tool.py` | ✅ 完成 | M14 结构化输出 |
| 文件记录工具 | `file_tool.py` | ✅ 完成 | M14 结构化输出 |
| 流程图工具 | 注册于 `tools/__init__.py` | ✅ 完成 | 管理员专属 Mermaid 流程图 |

### 2.6 基础设施

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| LLM 客户端 | `infrastructure/llm/client.py` | ✅ 完成 | DeepSeek/OpenAI 兼容，函数调用、思考模式、追踪回调 |
| MaxKB RAG | `providers/rag/maxkb_provider.py` | ✅ 完成 | 向量搜索、自动登录、Token 刷新 |
| 本地 RAG 回退 | `providers/rag/local_fallback.py` | ✅ 完成 | TF-IDF 关键词搜索、无外部依赖 |
| 配置系统 | `config.py` | ✅ 完成 | ~80 字段 dataclass |
| 启动引导 | `bootstrap.py` | ✅ 完成 | DB 初始化 + EmilyCore 工厂 |

### 2.7 真实 Agent 大脑（已迁移、未接入主路径）— Phase B/C 冷储备

| 模块 | 文件 | 代码量 | 状态 | 说明 |
|------|------|--------|------|------|
| MasterAgent | `agent/master_agent.py` | ~800 行 | ✅ 完成 | ReAct 主引擎，SOP 发现路由，Specialist 调度 |
| BusinessFlowAgent | `agent/business_flow_agent.py` | ~400 行 | ✅ 完成 | 双模式（结构化+M14 / ReAct+工具）SOP 执行 |
| GuardianAgent | `agent/guardian_agent.py` | ~500 行 | ✅ 完成 | 无状态深度审计 ReAct Agent |
| GuardianReview | `agent/guardian_review.py` | ~200 行 | ✅ 完成 | 轻量单次 LLM 验证（回复+记录） |
| SOPIntentRegistry | `agent/intent_registry.py` | ~400 行 | ✅ 完成 | 动态 SOP 发现、目录序列化、热重载 |
| ToolRegistry | `agent/tool_registry.py` | ~300 行 | ✅ 完成 | LLM 工具注册、条件注册 |
| BusinessFlowToolRegistry | `tools/business_flow_tools.py` | — | ✅ 完成 | M14 框架直调工具注册 |
| MermaidFlowManager | `agent/flow_renderer.py` + `mermaid_flow.py` | ~600 行 | ⚠️ 部分 | 流程图管理完成，NL2Flow 无LLM时使用占位模板 |

### 2.8 Pipeline BUS 基础设施（完成 — 仅大脑为 Mock）

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| PipelineBUS | `workitem/pipeline/bus.py` | ✅ 完成 | 4 节点公共总线，Hook 系统 |
| PipelineNode | `workitem/pipeline/node.py` | ✅ 完成 | 节点定义、处理器绑定、必选标记 |
| BusContext | `workitem/pipeline/context.py` | ✅ 完成 | 共享状态容器 |
| Hook 基类 + 6 子类 | `workitem/pipeline/hook.py` | ✅ 完成 | Auth/Audit/Verify/Trace/Progress/DeepAudit |
| HookRegistry | `workitem/pipeline/hook_registry.py` | ✅ 完成 | 挂载点索引管理 |
| 7 个抽象接口 | `workitem/pipeline/interfaces/` | ✅ 完成 | Router/Planner/WorkAgent/Guardian/AuthEngine/RiskGrader 接口定义 |
| 6 个 Mock 实现 | `workitem/pipeline/mocks/` | ⚠️ Mock | MockRouter/MockPlanner/MockWorkAgent/MockGuardian/MockAuthEngine/MockRiskGrader |
| WorkItem 状态机 | `workitem/workitem_state.py` | ✅ 完成 | CREATED→PLANNING→EXECUTING→DONE/FAILED |
| SessionScheduler | `workitem/scheduler.py` | ✅ 完成 | 会话级 WorkItem 队列调度 |
| KnowledgeInjector | `workitem/injector.py` | ⚠️ 占位 | 集合差算法完成，真增量注入/回收待实现 |
| WorkItemAgent | `workitem/workitem_agent.py` | ⚠️ Mock | 持有 4 个 Mock 大脑，回复加 `[Mock mode]` 前缀 |
| 薄插件层 | `data/plugins/emily_agent/` | ✅ 完成 | API 客户端、SSE 监听器、AstrBot 适配器 |

---

## 三、Mock / 占位 / 待实现模块清单

### 3.1 Pipeline Mock 大脑（核心阻塞项）

> **位置**: `emily-core/emily_core/workitem/pipeline/mocks/`  
> **影响**: PipelineBUS 的 4 个节点全部使用确定性固定输出，无真实 AI 能力  
> **标记**: 所有输出含 `_source="mock"`；所有回复前缀 `[Mock mode]`

| # | Mock 组件 | 文件 | 替换接口 | 当前行为 | 真实替代 |
|---|-----------|------|----------|----------|----------|
| 1 | **MockRouter** | `mocks/mock_routing.py` | Routing interface | 固定返回 SOP-002-REC，高置信度 | MasterAgent + SOPIntentRegistry |
| 2 | **MockPlanner** | `mocks/mock_planning.py` | Planning interface | 固定返回 3 步 ExecutionPlan | LLM 动态规划 |
| 3 | **MockWorkAgent** | `mocks/mock_execution.py` | WorkAgent interface | 固定返回硬编码业务数据 + RAG 片段 | BusinessFlowAgent + M14 工具 |
| 4 | **MockGuardian** | `mocks/mock_guardian.py` | Guardian interface | 永远返回 PASS | GuardianAgent + GuardianReview |
| 5 | **MockAuthEngine** | `mocks/mock_auth.py` | AuthEngine interface | 永远返回 ALLOW | Hook 权限列表鉴权 |
| 6 | **MockRiskGrader** | `mocks/mock_risk.py` | RiskGrader interface | 永远返回 "L2" | 真实意图风险评估 |

**关键代码位置**：
- Mock 注入点: `workitem/workitem_agent.py:26-30`（4 个 Mock 导入并初始化）
- 总线构建: `workitem/pipeline/bus.py:44-49`（`build_default()` 方法）
- 启动日志: `__init__.py:82`（无 LLM key 时打印 Mock 警告）

### 3.2 Session 层占位（5 项）

> **位置**: `emily-core/emily_core/session/`

| # | 模块 | 文件 | 行号 | 当前状态 | Phase B/C 目标 |
|---|------|------|------|----------|----------------|
| 1 | **SessionAgent 拆分逻辑** | `session_agent.py` | L108-121 | 所有非快捷消息固定创建 1 个 WorkItem | MasterAgent 接入，真实复合请求拆分 |
| 2 | **SessionAgent 归档** | `session_agent.py` | L125-136 | `archive()` 方法为空（仅状态机推进） | 用户记忆更新 + 通信记录归档 + SOP-010 |
| 3 | **SessionContext 懒加载** | `session_context.py` | L9 | 骨架字段（摘要字段全部为空字符串/空列表） | Phase B/C 触发式懒加载 |
| 4 | **FocusLock 主题匹配** | `focus_lock.py` | L13 | 最小占位：仅维护当前焦点 WorkItem | 主题感知优先级调度 |
| 5 | **ConfirmQueue 交付匹配** | `confirm_queue.py` | L11 | 最小占位：优先级排序 + FIFO 取出 | WorkItem 级别确认交付 |

### 3.3 WorkItem 层占位（2 项）

> **位置**: `emily-core/emily_core/workitem/`

| # | 模块 | 文件 | 行号 | 当前状态 | Phase B/C 目标 |
|---|------|------|------|----------|----------------|
| 1 | **WorkItemAgent 真大脑** | `workitem_agent.py` | L11, L41 | 持有 4 个 Mock 大脑，无 LLM 推理 | 替换为真实 WorkItem-Agent 推理 |
| 2 | **KnowledgeInjector 全量** | `injector.py` | L9-10, L54, L85 | 骨架：集合差注入；回收为 no-op | 增量 SOP/工具/Schema 加载 + 引用计数/LRU 回收 |

### 3.4 适配器层占位（1 项）

> **位置**: `emily-core/emily_core/adapters/session/`

| # | 模块 | 文件 | 行号 | 当前状态 | Phase B/C 目标 |
|---|------|------|------|----------|----------------|
| 1 | **SessionFactory 上下文构建** | `session_factory.py` | L61-79 | 仅填基本字段；`recent_turns`、`user_preferences`、`history_summary`、`sop_catalog_summary` 等为空 | 真实服务填充摘要字段 |

### 3.5 API 层占位（1 项）

> **位置**: `emily-core/api/middleware/`

| # | 模块 | 文件 | 当前状态 | Phase B/C 目标 |
|---|------|------|----------|----------------|
| 1 | **AuthMiddleware** | `auth.py` | 可选 Token 校验，默认全放行 | 共享密钥鉴权完善 |

### 3.6 Agent 层占位（1 项）

> **位置**: `emily-core/emily_core/agent/`

| # | 模块 | 文件 | 行号 | 当前状态 | Phase B/C 目标 |
|---|------|------|------|----------|----------------|
| 1 | **NL2Flow 无 LLM 回退** | `mermaid_flow.py` | L530-548 | `_placeholder_template()` 返回占位模板 | LLM 驱动的真实流程图生成 |

### 3.7 其他预留/待实现字段

| 位置 | 内容 | 状态 |
|------|------|------|
| `config.py:224` | 预留扩展字段 | 预留 |
| `adapters/standard/route_decision.py:21,27` | intent / handler 预留字段 | 预留 |
| `services/domain_takeover_service.py:42` | M1 管理模式预留 | 预留 |
| `application/query_app.py:38-39` | 未用预留 | 预留 |
| `workitem/pipeline/hook.py:104` | AuthHook 鉴权模型预留 | 预留 |

---

## 四、Phase B/C 待接入模块清单（冷储备 — 已完成但未接入主路径）

这些模块代码完整且经过独立验证，但在当前主消息处理路径中**未被调用**：

| # | 模块 | 文件 | 代码量 | 功能 | 接入目标 |
|---|------|------|--------|------|----------|
| 1 | **MasterAgent** | `agent/master_agent.py` | ~800 行 | ReAct 主引擎，SOP 发现路由 | 替换 MockRouter → SessionAgent 意图识别 |
| 2 | **BusinessFlowAgent** | `agent/business_flow_agent.py` | ~400 行 | 双模式 SOP 执行 | 替换 MockWorkAgent → Pipeline 节点 3 |
| 3 | **GuardianAgent** | `agent/guardian_agent.py` | ~500 行 | 深度审计 ReAct | 替换 MockGuardian → Pipeline 节点 3 验证 |
| 4 | **GuardianReview** | `agent/guardian_review.py` | ~200 行 | 轻量回复/记录验证 | 替换 MockGuardian → Pipeline 节点 4 |
| 5 | **SOPIntentRegistry** | `agent/intent_registry.py` | ~400 行 | 动态 SOP 发现 | 接入 MasterAgent 和 SessionAgent |
| 6 | **ToolRegistry** | `agent/tool_registry.py` | ~300 行 | 工具注册 | 接入 MasterAgent ReAct 循环 |
| 7 | **BusinessFlowToolRegistry** | `tools/business_flow_tools.py` | — | M14 框架直调 | 接入 BusinessFlowAgent 结构化模式 |

---

## 五、进度总览表

### 5.1 按层次统计

| 层次 | 完成 | Mock/占位 | 待实现 | 完成率 |
|------|------|-----------|--------|--------|
| API 层 | 5 | 1 | 0 | 83% |
| 数据库层 | 2 | 0 | 0 | 100% |
| 仓储层 | 10 | 0 | 0 | 100% |
| 服务层 | 15 | 0 | 0 | 100% |
| 工具层 | 11 | 0 | 0 | 100% |
| 基础设施 | 5 | 0 | 0 | 100% |
| Agent 大脑（冷储备） | 7 | 1 | 0 | 88% |
| Pipeline 基础设施 | 8 | 6 | 0 | 57% |
| Session 层 | 3 | 5 | 0 | 38% |
| WorkItem 层 | 4 | 2 | 0 | 67% |
| 适配器层 | 3 | 1 | 0 | 75% |
| **合计** | **73** | **16** | **0** | **82%** |

### 5.2 Mock/占位模块优先级矩阵

| 模块 | 影响范围 | 阻塞后续 | 复杂度 | 业务价值 | 建议优先级 |
|------|----------|----------|--------|----------|-----------|
| MockRouter → MasterAgent | 全消息路由 | 是（意图识别一切入口） | 高 | 核心 | 🔴 P0 |
| MockPlanner → LLM Planning | WorkItem 规划 | 否（规划可渐进增强） | 中 | 高 | 🟡 P1 |
| MockWorkAgent → BusinessFlowAgent | 任务执行 | 否（可先接再优化） | 高 | 核心 | 🔴 P0 |
| MockGuardian → GuardianAgent | 输出验证 | 否（可先接轻量版） | 中 | 高 | 🟡 P1 |
| SessionAgent 拆分 | 多任务并行 | 是（影响复合请求） | 中 | 高 | 🟡 P1 |
| KnowledgeInjector | 知识注入 | 否（骨架可用） | 中 | 中 | 🟢 P2 |
| Session 上下文完善 | 上下文质量 | 否（基本可用） | 低 | 中 | 🟢 P2 |
| FocusLock / ConfirmQueue | 调度精度 | 否（最小可用） | 高 | 中 | 🟢 P2 |
| Session 归档 | 会话生命周期 | 否 | 中 | 低 | 🔵 P3 |
| AuthMiddleware | API 安全 | 否（内网部署） | 低 | 低 | 🔵 P3 |
| MockAuthEngine | 权限控制 | 否（初期默认放行） | 中 | 中 | 🟢 P2 |
| MockRiskGrader | 风险评估 | 否（固定 L2 可用） | 中 | 低 | 🔵 P3 |
| NL2Flow 真实化 | 流程图生成 | 否（文本注入已可用） | 中 | 低 | 🔵 P3 |
| SessionFactory 完善 | 会话摘要 | 否 | 低 | 中 | 🟢 P2 |

---

## 六、关键架构决策记录

### ADR-026: Phase 0 Mock 占位策略

- **决策**: Phase 0 阶段使用 6 个 Mock 组件跑通 Pipeline BUS，验证总线/Hook/节点编排的正确性
- **状态**: 已达成，Mock 组件产出的 `[Mock mode]` 回复通过冒烟测试
- **下一步**: Phase B 逐个替换 Mock → 真实大脑

### 核心架构原则（魔改蓝图 §12）

1. **渐进替换**: 6 个 Mock 组件按优先级逐个替换，每一步都保持系统可运行
2. **接口隔离**: 所有 Mock 和真实实现共享同一接口定义（`pipeline/interfaces/`），替换只需改 import
3. **冷热分离**: Agent 大脑代码完整保留在 `agent/` 目录，接入后即从"冷储备"变为"热路径"
4. **双模兼容**: M14 结构化输出模式与 ReAct 工具调用模式共存，按场景切换

---

*报告结束*
