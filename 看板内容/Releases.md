# EmyBot 开发记录

> 本文档记录 EmyBot 项目的里程碑进度、操作速查和开发状态。项目介绍见 [README.md](../README.md)。

---

## 1. 里程碑进度

### M 主线（核心业务）

```
M1 ── 插件壳 + 分域接管        ✅ 验收通过 (2026-06-09)
M2 ── 消息入库 + 用户绑定      ✅ 验收通过 (2026-06-09)
M3 ── Router + 事件录入       ✅ 验收通过 (2026-06-10)
M4 ── 任务 + 会议 + 文件      ✅ 验收通过 (2026-06-10)，verify_m4 9/9 通过，Router Prompt 已扩展
M5 ── 结构化查询              ✅ 验收通过 (2026-06-11)，9 种 query_type 覆盖全部业务表 + 通讯记录查询
M7 ── MasterAgent (ReAct)    ✅ 验收通过 (2026-06-11)，ReAct 主 Agent + 动态技能 + ToolRegistry
M6 ── 守护Agent（Guardian）     ✅ 验收通过 (2026-06-12)，GuardianAgent ReAct 调查 + invoke_guardian 唤醒 + write_notebook 笔记 + QQ 3 用例实测通过
M8 ── 守护增强与体验优化      ✅ 验收通过 (2026-06-13)，3 子阶段 17 验收用例，QQ 实测通过
M9 ── 业务流架构（发现式路由）  ✅ 实施完成 (2026-06-14)，4 阶段 19 文件，SOPIntentRegistry + BusinessFlowAgent + sop_routing_logs
       M9-refactor ── 工具库架构重构   ✅ 实施完成 (2026-06-19)，15→10 工具，5 伪工具重新安置（内置方法/Hook/prompt），关注点分离
M10 ── 地产领域知识注入          ✅ 实施完成 (2026-06-18)，L1 核心认知注入 + L2 按需检索，4 新增 + 9 修改文件，108 用例全部通过
M11 ── 全量聊天存档 + Agent 追踪  ✅ 实施完成 (2026-06-18)，4 张新表 + 3 新服务 + 4 新 Repository + chat_archive 工具 + PG 引擎，IM 实战 7/8 通过（M15 收尾阶段移除 SQLite 双引擎）
M12a ── Hook 总线架构           ✅ 实施完成 (2026-06-19)，MessagePipeline 9 阶段总线 + Hook 三态决策 + 5 种 Hook 子类 + 声明式 JSON 配置 + hook_execution_logs，23/23 验收通过（M13 新增 download 阶段）
                                    ⚠ 已废弃 (2026-06-22)，由 M15 8 阶段状态机全面接管。M12a 9 阶段代码完全移除。`pipeline_config_m15.json` 替代 `pipeline_config.json`。
M12b ── Checkpoint 持久化        ✅ 实施完成 (2026-06-19)，SOPCheckpoint 表 + CheckpointService + _stage_confirm 集成 + 启动恢复 + 新旧并存，6 新增/修改文件
M13 ── 文件传输（收发+Agent按需读取） ✅ 实施完成 (2026-06-20)，download 管道阶段 + send_file/read_local_file 工具 + chain_result 文件发送 + FileStorageService.store_attachment_async
M14 ── 核心业务工具迁移SOP业务流  ✅ 实施完成 (2026-06-21)，5 核心工具从 LLM ToolRegistry 迁移至 BusinessFlowToolRegistry，BusinessFlowAgent 结构化输出模式（LLM→JSON参数→框架直接调用handler）
M15 ── 总线管道重构              ✅ 实施完成 (2026-06-22)，8 阶段状态机驱动管道（intake→route→auth→plan→execute→compose→verify→archive），WorkOrder 全息流转单 + PipelineScheduler 主管调度器 + 6 Mock 占位模块 + 17 Hook 全覆盖。M12a 9 阶段铁序管道已完全移除，M15 为唯一管道路径。
```

### Ex 支线（扩展/集成功能 — 协作开发，不阻塞主线）

```
Ex1 ── Web 测试控制台          ✅ 验收通过 (2026-06-16)，Gradio 聊天界面 + 参数侧边栏 + LLM 自动配置
Ex2 ── Web 运维控制台          🔲 待开发（Docker 状态、日志流、账号状态、容器启停）
Ex3 ── 知识库 RAG 集成        已废弃，功能由Ex4实现
Ex4 ── MaxKB 部署 + RAG 集成  ✅ 实施完成 (2026-06-21)，hit_test 纯向量检索 API + MaxKBRagProvider 重写 + main.py/tester.py 接线修复 + knowledge_search 工具端到端验证通过
```

> **编号说明**：M 系列保留给核心业务逻辑，Ex 系列用于扩展/集成功能，可并行开发，互不阻塞。

### 已具备能力

**M1（已验收）：**
- ✅ AstrBot 插件正常加载运行
- ✅ 群聊 @Emy / 私聊 → 接管并回复
- ✅ 群聊未 @机器人 → 放行 AstrBot 原流程
- ✅ 消息去重（pipeline 多次分发只处理一次）

**M2（已验收）：**
- ✅ PostgreSQL + SQLAlchemy 2.0 集成，自动建表（users / user_im_bindings / conversations / messages）
- ✅ 消息持久化：接管消息写入 messages 表，自动查找/创建 Conversation
- ✅ 用户自动绑定：首次发消息给 Emy 自动创建系统用户并绑定 IM 账号
- ✅ event_id 幂等去重：重复消息不会重复入库
- ✅ sender_user_id 回填：用户绑定后自动关联到消息记录

**M4（验收通过）：**
- ✅ Task CRUD + 编号生成（TSK-YYYYMMDD-NNNN）
- ✅ Meeting CRUD + 编号生成（MTG-YYYYMMDD-NNNN）
- ✅ File CRUD + 编号生成（FIL-YYYYMMDD-NNNN）
- ✅ TaskService / MeetingService / FileService 业务逻辑
- ✅ TaskApplication / MeetingApplication / FileApplication 编排层
- ✅ MessageApplication._dispatch() 分发到 M4 Handler
- ✅ Handler 注册表扩展：`_ensure_components_initialized()` 注册全部 5 个 intent
- ✅ Router Prompt 扩展：task_record / meeting_record / file_record 参数提取规则
- ✅ LLM 路由已启用，端到端正常运行

**M4 验收测试**：`verify_m4.py` 9/9 通过。

**M5（验收通过）：**
- ✅ QueryService：跨 9 种 query_type 的结构化查询（event/task/meeting/file/message/conversation/user/project/summary）
- ✅ 时间范围解析：today/this_week/this_month/all → 自动转换为日期区间
- ✅ 通讯记录查询：messages 表多维筛选 + conversations 活跃度排行 + 关键词搜索
- ✅ QueryApplication：RouteResult → QueryCommand → execute → format_reply
- ✅ 注册表集成：query handler 已在 `_ensure_components_initialized()` 注册
- ✅ Router Prompt 扩展：query intent 含 9 种 query_type 参数提取规则

**M5 验收测试**：`verify_m5.py` 12/12 通过。

**M7（验收通过）：**
- ✅ MasterAgent（ReAct 模式）：接收用户消息→多步推理→工具调用→生成回复
- ✅ ToolRegistry：8 个工具（event/task/meeting/file/query/router/skill/general），OpenAI function calling
- ✅ SkillRegistry：动态技能系统（创建/持久化/重载/删除），trigger_keywords 匹配
- ✅ ConversationContext：按 conversation_id 索引滑动窗口上下文（最近 12 轮）
- ✅ RouterAgent 降级为 Tool：可由 MasterAgent 按需调用
- ✅ 降级路径：`enable_master_agent=False` 时完全回退到原 Router+Handler 模式
- ✅ MessageApplication 集成：`set_master_agent_factory()` 注入

**M7 验收测试**：`verify_m7.py` 8/8 通过（5-7 项需要 LLM Key）。

**M6（验收通过）：**
- ✅ GuardianAgent：一次性深度调查 ReAct Agent，与 MasterAgent 共享 LLMClient
- ✅ 守护 Prompt：`prompts/守护Agent.md`，管理员可直接编辑，重启生效
- ✅ invoke_guardian → 已重构为 MasterAgent._invoke_guardian() + DeepAuditHook（2026-06-19）
- ✅ write_notebook：GuardianAgent 专用工具，将发现写入 `notebooks/守护Agent-笔记.md`
- ✅ 行为规则：不够再查/够了就停/不必穷举，Agent 自行判断需查哪些维度（软约束）
- ✅ QQ 实测：3 用例通过（巡检发现缺失 + 审核发现矛盾并写笔记 + 闲聊不误触发）

**M6 验收测试**：`verify_m6.py` 6/10 通过（4 项需 LLM Key，当前无 key 时 SKIP），QQ 对话 3 用例实测通过。

**M8（验收通过）：**

M8a — 守护Agent 增强：
- ✅ 系统启动自动体检：`_startup_health_check()` 异步执行，不阻塞启动
- ✅ 回复前核验：GuardianReview.review_reply()，有隐患追加 `⚠ 守护提醒`
- ✅ 录入前核验 + 三选一：修改重审 / 坚持录入（标记+待解决清单）/ 取消录入
- ✅ 待解决问题清单：`tem_log/待解决问题.md`，PND 编号，读/写/状态流转
- ✅ events 表扩展：`related_event_ids` 字段（TEXT / JSON 数组）
- ✅ 待解决问题处理闭环：决策事件录入 → 关联原事件 → 移出清单

M8b — 前导信息机制：
- ✅ `send_progress()` 立即发送前导消息（使用 `event.send()` 直发，不经过 result pipeline）
- ✅ 触发条件：对话轮数 ≥ threshold 或 消息含深度操作关键词
- ✅ 模板可配置：`progress_message_template` + `action` 自动推断
- ✅ 闲聊快速通道：问候/感谢/告别/自我介绍 直接回复，不走 LLM（秒级响应）

M8c — 项目日记与长期记忆：
- ✅ EventJournal：事件/任务/会议/文件/守护发现/启动体检/用户绑定/待解决处理 → 写入项目日志
- ✅ UserMemoryService：`memory/{用户名}-长期记忆.md` 读写
- ✅ write_user_memory 工具：Agent 识别长期意图自动写入记忆
- ✅ 新对话加载记忆：ConversationContext 初始化时加载，注入 system prompt
- ✅ query_type="journal"：可查询项目事件流水

**Session 修复（2026-06-13）**：
- 🐛 `memory_tool.py`：`create_memory_tool` 返回 `ToolDefinition` 替代原始 `dict`（AttributeError 导致 MasterAgent 崩溃）
- 🐛 `message_forwarder`：移除 `event.stop_event()`，不再阻断 team_brain_agent
- 🐛 GuardianAgent 重复查询：新增代码级重复检测 + `guardian_max_iterations=5` + Prompt 强化
- 🐛 前导消息：`send_progress` 改用 `event.send()` 直发（`set_result()` 被 `send()` 覆盖）
- 🐛 NapCat 端口：`onebot11_2680188817.json` 从 6200 改为 `ws://astrbot:6199/ws`

**M9（实施完成）：**
- ✅ SOPIntentRegistry：启动扫描 SOPrepository/ → 解析 §1+§2 → 构建内存索引（9 份 SOP，全部 ok）
- ✅ 发现式路由：main.md 不包含具体 SOP 名称，路由分支由运行时动态决定
- ✅ dump_as_text()：纯文本格式化 SOP 目录（4651 chars），注入 LLM system prompt
- ✅ LLM 语义匹配：LLM 对用户消息 vs SOP 目录做语义分类，输出 SOPMatchDecision
- ✅ 原子替换 reload：RLock 线程安全，先建新索引 → 验证非空 → swap；失败保留旧索引
- ✅ 容错降级：单文件异常不影响全局；权限缺失降级 ["all"]；字段缺失标记 partial
- ✅ sop_routing_logs：12 字段路由决策日志表，每次匹配写入一条记录
- ✅ BusinessFlowAgent：Specialist Agent，加载 SOP §1-§7 全文，限定工具集执行
- ✅ SOPLoader：按需加载 SOP 全文 + unmatched.md 兜底指引
- ✅ invoke_business_flow → 已重构为 MasterAgent._dispatch_specialist()（2026-06-19）
- ✅ list_sop_catalog → 已删除（SOPIntentRegistry.dump_as_text() 注入 prompt，2026-06-19）
- ✅ 复合请求拆解：_decompose_and_dispatch() + 权限前置检查
- ✅ SOP-007-REC（长期记忆）与其他 SOP 平等路由，不再硬编码 V→W 节点
- ✅ master_agent.txt 瘦身：180 → 85 行，删除硬编码 SOP 规则
- ✅ 7 份新 SOP 文件：SOP-002 至 SOP-008（事件/任务/文件/查询/守护审计/长期记忆/待解决问题）

**M10（实施完成）：**
- ✅ L1 核心认知注入：`domain_knowledge.md`（51 行 / ~320 tokens），覆盖 7 阶段 + 9 部门 + 18 术语
- ✅ MasterAgent + GuardianAgent system prompt 注入 `{DOMAIN_KNOWLEDGE}` 占位符
- ✅ L2 按需检索：`knowledge_search_tool` 扩展 stage/role 过滤参数
- ✅ LocalFileRagProvider 扩展：metadata 分块提取 + 过滤检索，239 chunks / ~27K tokens
- ✅ knowledge_builder.py：三阶段知识提取流水线（解析 → 规则提取 → 统计）
- ✅ RagProvider ABC `search()` 签名扩展 + MaxKB provider 对齐
- ✅ 懒加载修复：MaxKBRagProvider 改为懒加载，避免本地缺 aiohttp 时导入失败
- ✅ 验收：L1 71/71 通过 + L2 37/37 通过

**M11（实施完成）：**
- ✅ 全量聊天记录存档：入站消息增强（msg_type/attachments）+ 出站回复 + 前导消息存档
- ✅ Agent 推理追踪：`agent_reasoning_logs`（14 列）+ `llm_interaction_logs`（16 列）+ `tool_call_logs`（12 列）
- ✅ 消息附件关联：`message_attachments` 中间表，支持一条消息多个附件
- ✅ 聊天记录检索：`chat_archive` 工具（search/history/user 三种 action）
- ✅ 附件物理存储：`data/files/{platform}/{YYYY-MM}/FIL-YYYYMMDD-NNNN.ext` 目录结构
- ✅ PostgreSQL 单引擎：`migrate_sqlite_to_pg.py` 一次性迁移脚本（M15 收尾阶段完成迁移后已移除）
- ✅ Boolean 标准化：~20 个 Integer 字段 → Boolean（SQLAlchemy 自动适配两种方言）
- ✅ LLMClient trace callback：`set_trace_callback()` 非侵入式追踪所有 LLM 调用
- ✅ 向后兼容：全部新功能默认启用，关闭开关即退化为原行为
- ✅ 新增 3 服务 + 4 Repository + 1 工具，8 新增文件 + 7 修改文件
- ✅ IM 实战测试：7/8 通过（TC01 观察模式接管已知问题）

**M11 已知问题**：
- ⚠️ TC01（群聊不 @bot 观察模式）：Agent 仍接管了未 @ 的消息（期望不接管）。`DomainTakeoverService` 的群聊接管规则需要调整。

**M12a（已废弃 — 2026-06-22，M15 全面接管）：**
- ⚠ M12a 9 阶段铁序管道（record→download→bind→confirm→classify→decompose→execute→verify→archive）已被 M15 8 阶段状态机全面接管并完全移除
- ⚠ `pipeline_config.json` 已删除，由 `pipeline_config_m15.json` 替代
- ✅ Hook 三态决策 + 6 种 Hook 子类 + HookRegistry 注册表 → 已由 M15 继承
- ✅ HookExecutionLog 表 + PipelineContext 共享状态 → 已由 M15 复用
- ✅ SQLite → PG 彻底迁移 → 已完成
- ✅ 验收记录：verify_m12a 15/15 + verify_m12a_communication 8/8 → 历史通过

**M12b（实施完成）：**
- ✅ SOPCheckpoint 表：16 列持久化快照表（sop_checkpoints），含 status 状态机（pending→confirmed/cancelled/expired/resumed）
- ✅ CheckpointService：核心服务（create / get_active / confirm / cancel / expire / mark_resumed / sweep_expired / find_resumable / restore_state）
- ✅ 内存缓存 + DB 双级查找：短会话内优先缓存加速，重启后从 DB 恢复
- ✅ `_stage_confirm()` 集成：优先 CheckpointService，回退 pending_confirmations dict
- ✅ `create_pending_checkpoint()` 辅助方法：供 EventApplication 等调用方创建检查点
- ✅ 启动恢复：`_checkpoint_startup_sweep()` 异步扫描过期检查点并标记 expired
- ✅ 配置项：checkpoint_enabled / checkpoint_ttl_seconds / checkpoint_resume_window_seconds / checkpoint_max_per_user
- ✅ 新旧并存：pending_confirmations dict 保留，CheckpointService 优先处理；checkpoint 未命中时回退 dict
- ✅ 新增 1 文件（checkpoint_service.py），修改 4 文件（config / models / __init__ / message_app）
- ✅ 向后兼容：checkpoint_enabled=False 或未注入时完全退化为原内存 dict 行为

**M13（实施完成）：**
- ✅ 附件自动下载：新增 `download` 管道阶段（record 之后），遍历 `message.attachments` 异步下载到本地
- ✅ 异步下载：`FileStorageService.store_attachment_async()` 使用 aiohttp（urllib 兜底），30 秒超时
- ✅ 下载失败不阻断：异常只记 warning 日志，不中断管道
- ✅ 文件物理存储：`{storage_root}/{platform}/{YYYY-MM}/FIL-YYYYMMDD-NNNN.ext`
- ✅ 主动发送文件：`AstrBotOutboundSender.send_files()` 使用 `event.chain_result([Plain, File])` 立即发送
- ✅ Agent `send_file` 工具：支持 `file_path` 或 `file_no` 定位文件，通过 `send_file_callback` 闭包发送
- ✅ Agent `read_local_file` 工具：按需读取本地文件（文本全文 / 二进制元信息），通过 `file_no` 或 `file_path` 定位
- ✅ `ReplyMessage.file_paths`：最终回复可携带附件文件
- ✅ `PipelineContext.downloaded_files`：阶段间共享已下载文件信息
- ✅ `FileStorageService` 始终初始化（不再由 `file_download_enabled` 门控），Agent 可随时按需读取
- ✅ `send_file_callback` 注入链：`main.py → handle_message → MessageApp → context.baggage → _stage_execute → 工具工厂 → send_file 工具`
- ✅ 修改 10 文件：file_storage_service / pipeline_context / message_app / __init__ / reply / outbound_sender / file_tool / tools/__init__ / main / config
- ✅ 向后兼容：`file_download_enabled=False` 时仅跳过下载，`send_file_callback=None` 时 send_file 工具不注册

**M14（实施完成 — 核心业务工具迁移 SOP 业务流）：**
- ✅ BusinessFlowToolRegistry：新注册表，管理 5 个核心业务流工具（record_event / record_task / record_meeting / record_file / query_data），框架直接执行
- ✅ 结构化输出模式：BusinessFlowAgent 新增 `_execute_structured()`，使用 `chat_json` 强制 JSON 输出 → 解析 `{tool, params}` → 框架直接调用 handler
- ✅ 执行模式自适应：`_can_use_structured_mode()` 检测 SOP §3.2 工具是否全部在 BusinessFlowToolRegistry 中注册，是则走结构化输出，否则回退 ReAct
- ✅ 5 个 handler 提取：每个 tool 文件新增独立 async handler 函数 + schema/description 常量（`_EVENT_TOOL_SCHEMA` 等）
- ✅ ToolRegistry 精简：仅保留条件工具（pending_issues / user_memory / knowledge_search / chat_archive / send_file / read_local_file / create_flow_diagram）+ query_data 兜底查询
- ✅ GuardianReview 保留：核验逻辑仍在 handler 内部（数据写屏障位置不变）
- ✅ `create_business_flow_tools()` 工厂：按请求创建，闭包注入 user_id / message_id / guardian_review / pending_issues
- ✅ MasterAgent 接线：构造函数 + `_dispatch_specialist()` 传递 `business_flow_tools`
- ✅ unmatched 兜底：query_data 保留在 ToolRegistry 供自由推理路径只读查询
- ✅ 新增 1 文件（business_flow_tools.py）+ 重构 5 tool 文件 + 修改 5 文件（tools/__init__ / business_flow_agent / master_agent / __init__ / README / CLAUDE）
- ✅ 端到端验证通过：SOP-002 事件录入（结构化输出+确认单）、SOP-005 数据查询、unmatched 闲聊兜底均正常

**Ex4（实施完成 — MaxKB RAG 集成）：**
- ✅ MaxKB 纯向量检索：通过 `hit_test` admin API 获取文档段落 + 相似度，不经过 LLM 二次加工
- ✅ 向量模型：Qwen3-Embedding-0.6B（1024 维），pgvector HNSW 索引做余弦相似度检索
- ✅ MaxKBRagProvider：`_login()` 获取 admin token → `_do_hit_test()` 纯向量检索 → 401 自动重试
- ✅ knowledge_search 工具已注册到 Agent tool registry，LLM 可通过 function calling 调用
- ✅ 工具支持参数：`query`（查询文本）、`top_k`（返回条数）、`stage`（按项目阶段过滤）、`role`（按岗位过滤）
- ✅ 配置门控：`kb_enabled=true` + `maxkb_admin_password` + `maxkb_knowledge_id` 三者齐全才启用 MaxKB
- ✅ 本地回退：MaxKB 不可用时自动降级到 `LocalFileRagProvider`（项目资料目录关键词搜索）
- ✅ 修复 3 个接线 bug：(1) main.py provider 在 bootstrap 之后创建；(2) tester.py 直接 new TeamBrainCore 未传 rag_provider；(3) AstrBot 插件配置不读 JSON 文件需兜底
- ✅ 修复 FK 违规 bug：`create_outbound()` + `create_reasoning_log()` 未将业务 conversation_id 解析为 UUID
- ✅ EmysTester 支持 RAG（自动替换 Docker 服务名 `maxkb` → `localhost`）
- ✅ 端到端验证通过：LLM 调用 knowledge_search → hit_test API 返回知识库段落 → LLM 基于检索结果回复
- ✅ 配置文件：`team_brain_agent_config.json` 含 `maxkb_admin_password` / `maxkb_knowledge_id` / `maxkb_search_mode` / `maxkb_similarity_threshold` / `kb_top_k`
- ✅ 修改 9 文件：config / maxkb_provider / main / bootstrap / tester / message_repo / agent_reasoning_repo / _conf_schema / tools/__init__

**Ex1（验收通过）：**
- ✅ EmysTester Web 控制台：Gradio 聊天界面，侧边栏参数配置
- ✅ 配置自动发现：从 `data/cmd_config.json` 自动读取 LLM Key，零环境变量
- ✅ 平台参数透传：napcat / wechat / dingtalk / feishu / simulator
- ✅ 会话管理：独立状态 + 重置功能，多 Gradio 用户互不干扰
- ✅ 群聊接管规则：@bot=True 接管，@bot=False 不接管
- ✅ 文档：`tem_log/开发需求/阶段总结/Ex1-Web测试控制台-实施总结.md`

启动命令：`python .claude/skills/emy-test/emy_web/gradio_app.py` → http://localhost:8000

**M3（已验收）：**
- ✅ LLM 意图路由：RouterAgent 调用 LLM 对消息进行 6 类意图分类（event_record / task_record / meeting_record / file_record / query / chat）
- ✅ 事件录入：intent=event_record 时自动创建 pending 事件，生成确认简报
- ✅ 确认流程：用户回复"确认"正式录入，回复"取消"放弃；5 分钟超时保持 pending
- ✅ 降级策略：LLM 不可用时 fallback 为 chat，不崩溃不沉默
- ✅ projects / events 表自动建表，事件编号自动生成（EVT-YYYYMMDD-NNNN）
- ✅ MessageApplication 核心编排：接管 → 持久化 → 用户绑定 → 路由 → Handler 分发 → 回复
- ✅ Handler 注册表模式：`register_handler(intent, handler_fn)`，新增意图无需改 `_dispatch()` 代码
- ✅ Phase A 基础层验收 9/9 通过

**M2 数据库路径**：PostgreSQL `postgresql://root@maxkb:5432/team_brain`（M12a 收尾阶段由 SQLite 迁移至 PG）

**M3 LLM 配置**：AstrBot 插件配置页填写 DeepSeek API Key 后，Router 自动启用。

**数据库扩展（2026-06-12）**：
- ✅ 4 张现有表新增列对齐需求文档：messages +4列、projects +6列、meetings +11列、files +13列
- ✅ 7 张新表创建：employees / company_info / project_indicator_details / business_flow_orders / instruction_orders / project_plans / plan_items
- ✅ 迁移脚本 `migration.py`：幂等 ALTER TABLE（M15 收尾阶段切换 PG 后移除，由 `Base.metadata.create_all()` 管理 schema）
- 📊 总计 16 张表，原有 4 users / 4 bindings / 4 conversations / 35 messages / 1 events / 2 tasks 数据未受影响

---

## 2. 开发操作速查

### 重启 AstrBot（应用代码变更）
```powershell
docker compose -f docker-compose-napcat.yml restart astrbot
```

### 查看日志
```powershell
docker logs --tail 100 astrbot 2>&1
# 只看 Emy 相关：
docker logs --tail 100 astrbot 2>&1 | Select-String -Pattern "teambrain|Emy"
```

### 查看 NapCat 状态
```powershell
docker logs --tail 50 napcat 2>&1
```

### 查看全体容器状态
```powershell
docker compose -f docker-compose-napcat.yml ps
```

### 启动 Web 测试控制台（Ex1 — 本地测试，无需 Docker）
```powershell
python .claude/skills/emy-test/emy_web/gradio_app.py
# 浏览器自动打开 http://localhost:8000
# LLM 配置自动从 data/cmd_config.json 读取
```

### 命令行快速测试（无需 Web 界面）
```powershell
# 无 LLM 烟雾测试
python .claude/skills/emy-test/emys_tester.py --message "你好" --sender "Alice"

# LLM 模式（自动读取 cmd_config.json 中的 Key）
python .claude/skills/emy-test/emys_tester.py --managed --message "帮我创建事件：样板段放线完成" --sender "张工"

# 交互式 REPL
python .claude/skills/emy-test/emys_tester.py -i --sender "测试者"
```

### 运行 M2 验收测试
```powershell
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m2.py
```

### 运行 M3 验收测试（Phase A — 无需 LLM）
```powershell
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m3.py
```

### 运行 M4 验收测试
```powershell
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m4.py
```

### 运行 M5 验收测试
```powershell
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m5.py
```

### 运行 M7 验收测试
```powershell
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m7.py
```

### 运行 M6 验收测试
```powershell
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m6.py
```
```powershell
docker exec astrbot python -c "
import sys; sys.path.insert(0, '/AstrBot/data/plugins/team_brain_agent')
from teambrain_core.infrastructure.database.session import init_db, get_session
from teambrain_core.infrastructure.database.models import Project
init_db()
with get_session() as s:
    if not s.query(Project).filter(Project.name == '未来城').first():
        s.add(Project(code='wlc', name='未来城', description='未来城项目'))
        print('已添加项目：未来城')
    else:
        print('项目已存在')
"
```

### 查询数据库
```powershell
# PostgreSQL 查询（通过 MaxKB 容器）
docker exec -it maxkb psql -U root -d team_brain -c \
  "SELECT id, sender_name, content, takeover, status, created_at FROM messages ORDER BY created_at DESC LIMIT 5;"

# 方式 2：用 Python（更通用）
docker exec -it astrbot python -c "
import sys; sys.path.insert(0, '/AstrBot/data/plugins/team_brain_agent')
from teambrain_core.infrastructure.database.session import init_db, get_session
init_db()
with get_session() as s:
    from sqlalchemy import text
    rows = s.execute(text('SELECT id, sender_name, content, takeover, status FROM messages ORDER BY created_at DESC LIMIT 5'))
    for r in rows: print(r)
"
```

### NapCat 重新登录
NapCat 被 QQ 踢下线时，访问 `http://localhost:6099` → WebUI → 重新扫码。

### 清除 pycache（重要！）
```powershell
# 容器内代码变更后必须执行，否则旧 .pyc 缓存导致 ImportError 或运行旧逻辑
docker exec astrbot find /AstrBot/data/plugins/team_brain_agent -name '__pycache__' -type d -exec rm -rf {} +
```

### 运行全部验收测试（推荐）
```powershell
# M4 完整测试（覆盖 M1-M4 全部能力）
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m4.py

# M5 结构化查询测试
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m5.py

# M6 GuardianAgent 测试（需 LLM Key）
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m6.py

# M7 MasterAgent 测试（需 LLM Key）
docker exec astrbot python /AstrBot/data/plugins/team_brain_agent/verify_m7.py
```
> 注意：`verify_m4.py` 的 Phase B（LLM 路由端到端）需要 DeepSeek API Key 已配置。`verify_m6.py` 第 7-9 项需要 LLM Key。`verify_m7.py` 第 5-7 项需要 LLM Key。

| 验证脚本 | 覆盖阶段 | 需要 LLM Key |
|----------|----------|--------------|
| `verify_m2.py` | M1-M2（接管+入库+绑定） | 不需要 |
| `verify_m3.py` | M3（Router+事件录入）Phase A | 不需要 |
| `verify_m4.py` | M1-M4 全部 | Phase B 需要 |
| `verify_m5.py` | M5 结构化查询 | 不需要 |
| `verify_m6.py` | M6 GuardianAgent | 第 7-9 项需要 |
| `verify_m7.py` | M7 MasterAgent | 第 5-7 项需要 |
| `verify_m8a.py` | M8a 守护增强（启动体检+核验+待解决清单+events扩展） | 第 2-6 项需要 |
| `verify_m8b.py` | M8b 前导信息机制 | 需要 |
| `verify_m8c.py` | M8c 项目日记+长期记忆 | 第 4-5 项需要 |
| `verify_m12a.py` | M15 管道总线架构（15 用例） | 不需要 |
| `verify_m12a_communication.py` | M15 通信实战（8 用例） | 需要 |
| `verify_m12b.py` | M12b Checkpoint 持久化 | 不需要 |

## 3. 架构决策记录（ADR）

> 记录关键架构决策及其原因，供后续开发参考。防止 AI 或新开发者推翻已验证的设计。

### ADR-001：Repository 层用 @staticmethod

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-09 (M2) |
| **决策** | 所有 Repository 方法为 `@staticmethod`，不持有实例状态 |
| **原因** | `get_session()` 是模块级上下文管理器，不依赖实例状态；团队使用偏好静态方法风格 |
| **后果** | 代码简洁，但无法 mock Repository 实例（MVP 阶段不需要，可后续重构） |

### ADR-002：Handler 注册表模式

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-10 (M3) |
| **决策** | `MessageApplication._dispatch()` 用注册表模式（`register_handler()`），而非 `if-elif` 链 |
| **原因** | 新增意图不改 `_dispatch()` 代码，符合开闭原则；注册表在 `__init__.py` 集中管理 |
| **备选** | `if intent == "xxx": handler()` — 简单但每次新增意图要改核心方法 |
| **参考** | [M3 实施计划](开发需求/M3-Router加事件录入-实施计划.md) |

### ADR-003：Prompt 模板独立文件

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-10 (M3) |
| **决策** | LLM Prompt 文本存 `prompts/` 目录 `.txt` 文件，代码中 `read_text()` 加载 + 缓存 |
| **原因** | Prompt 调优不需要改 Python 代码；非开发人员（项目经理）可审阅 Prompt 内容 |
| **后果** | 文件读写有性能开销，但 `_prompt_loaded` 缓存 + 低频调用（只在 RouterAgent 初始化时加载一次）足够 |

### ADR-004：EmyCore 不依赖 AstrBot

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-09 (M1) |
| **决策** | `teambrain_core/` 不 import 任何 `astrbot.*` 包，所有 AstrBot 交互通过 Adapter 层隔离 |
| **原因** | 未来可能将 EmyCore 独立为微服务；不绑定 AstrBot 容器环境 |
| **后果** | Adapter 层开销，但隔离收益远大于成本 |

### ADR-005：LLM 降级策略 — 放行而非硬编码回复

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-10 (M3 修正) |
| **决策** | 无 LLM API Key 时，Emy 不接管回复（`return None`），放行给 AstrBot 原生 Agent 兜底 |
| **原因** | 硬编码回复（如"M3 功能不可用"）用户体验差，AstrBot 有自身 Agent 可兜底；消息仍完整入库，数据不丢 |
| **原始错误** | M3 初版 chat handler 返回"收到，有什么需要帮助的吗？"，掩盖了兜底意图 |
| **参考** | [踩坑记录#25](踩坑记录.md) |

### ADR-006：QueryCommand.query_type 覆盖全部 9 表

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-11 (M5 计划修订) |
| **决策** | M5 结构化查询的 `query_type` 覆盖 10 种类型（event/task/meeting/file/message/conversation/user/project/summary/knowledge），包括通讯原始记录 messages 和 conversations |
| **原因** | 原计划只覆盖业务对象表（events/tasks/meetings/files），遗漏了通讯记录，导致无法回答"张三昨天说了什么"、"哪些群最活跃"等问题 |
| **参考** | [M5 实施计划](开发需求/M5-知识库检索与查询-实施计划.md) |

### ADR-007：独立 DB 不合并到 AstrBot data_v4.db

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-11 |
| **决策** | Emy 的 PostgreSQL 数据库（`team_brain`）独立于 AstrBot 的 `data_v4.db`，不合并 |
| **原因** | (1) 共享 DB ≠ AstrBot 能查询 Emy 数据；(2) AstrBot 升级可能毁表；(3) 违背 ADR-004（EmyCore 独立性）；(4) 独立 PG 库提供更好的并发写入性能 |
| **兜底方案** | 当前已实现：无 LLM 时 Emy 静默降级（消息入库 + 放行 AstrBot），数据完整 |

### ADR-008：MasterAgent 作为统一对话入口（M7）

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-11 |
| **决策** | 引入 MasterAgent（ReAct 模式）作为统一对话入口，RouterAgent 降级为一个 Tool；同时保留 `enable_master_agent=False` 时完全回退到原 Router+Handler 模式 |
| **原因** | (1) 原 Router→Handler 模式是单步分类→单步执行，无法处理多步骤复杂任务；(2) OpenAI function calling 比纯文本分类更适合工具调度；(3) 动态技能（SkillRegistry）需要带状态的 Agent 循环 |
| **后果** | M7 与 M3-M5 并行运行，双路径并存；增加了一次 LLM 调用（但换来了更好的灵活性和可扩展性） |

### ADR-009：Ex 支线编号体系

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-12 |
| **决策** | 引入 Ex 编号体系（Ex1、Ex2...）用于扩展/集成功能，与 M 主线分离 |
| **原因** | (1) Ex 任务可由其他人协作开发，不阻塞 M 主线；(2) Ex 任务不计入项目核心技术路线（如 Web 控制台、RAG 集成）；(3) 避免 M 编号膨胀（如原 M8 是一个庞大的需求包） |
| **编号分配** | Ex1 = Web 运维控制台；Ex2 = 知识库 RAG 集成（原 M6 移入）；预留 Ex3-Ex5 |

### ADR-010：M8→M6 重新编号

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-12 |
| **决策** | 原 M8（项目生命周期管理）需求文档移入新 M6 位置，原 M6（RAG）移至 Ex2 |
| **原因** | (1) M5 和 M7 已完成后，主线直接下一个编号是 M6；(2) 项目生命周期管理是核心业务逻辑，应留在 M 主线；(3) RAG 集成属于扩展功能，更适合 Ex 支线 |

### ADR-011：M6 守护 Agent（GuardianAgent）

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-12 |
| **决策** | M6 采用极简守护 Agent 方案：管理员通过 `prompts/守护Agent.md` 书写职责，冷启动加载为 System Prompt；用户按需唤醒，Agent 自行推理判断；不做固定状态机、不做兼容矩阵、不做日报系统 |
| **原因** | (1) MVP 阶段应尽量简单，能通过 Agent 自行推理判断的内容暂不写固定任务流；(2) 生命周期阶段模型、异常检测等需求依赖足够数据积累，过早建设无意义；(3) 职责由管理员维护的 Markdown 文件，非开发人员也能调整 Agent 行为 |
| **产出** | GuardianAgent 类（ReAct 循环 + query_data + write_notebook）、invoke_guardian 工具（注册到 ToolRegistry；2026-06-19 重构为 MasterAgent._invoke_guardian() + DeepAuditHook）、`notebooks/` 笔记目录 |
| **验收** | verify_m6 10 用例 + QQ 3 对话用例实测通过

### ADR-012：M8 Session 修复（2026-06-13）

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-13 |
| **决策** | 本 session 完成 M8 QQ 实测验收，修复 5 个关键 bug |
| **修复清单** | (1) `memory_tool.py` 返回 `ToolDefinition` 替代原始 `dict`（`AttributeError` 导致 MasterAgent 每次创建崩溃回退 Router）；(2) `message_forwarder` 移除 `event.stop_event()` 不再阻断 team_brain_agent；(3) GuardianAgent 新增代码级重复调用检测 + `guardian_max_iterations=5` 独立配置 + Prompt 强化；(4) `send_progress()` 改用 `event.send()` 直发（`set_result()` 被最终 `send()` 覆盖导致前导消息丢失）；(5) NapCat 端口 6200→6199，改用 `ws://astrbot:6199/ws` |
| **教训** | (1) AstrBot 的 `event.set_result()` 同事件多次调用只保留最后一次，RespondStage 在 handler 全部执行完毕后统一发送；中间消息必须用 `event.send()` 直发。(2) `ToolRegistry.register()` 要求 `ToolDefinition` 对象，不能传原始 `dict`。(3) 同一 Docker 网络内用 service name（如 `astrbot`）优于 `host.docker.internal` |

### ADR-013：M9 发现式路由 — 目录即路由表

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-14 |
| **决策** | SOP 路由分支不硬编码在任何文件中，由 `SOPIntentRegistry` 在运行时扫描 `SOPrepository/` 动态决定 |
| **原因** | 新增 SOP 应该是"放文件→重启→生效"，不应该改任何 Python 代码或 prompt 文件；SOP 数量随业务增长，硬编码不可维护 |
| **后果** | `main.md` 不包含具体 SOP 名称；Orchestrator 的 system prompt 中包含动态生成的 SOP 目录（`{SOP_CATALOG}` 占位符） |

### ADR-014：M9 路由日志不可跳过

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-14 |
| **决策** | 每次路由决策必须写入 `sop_routing_logs` 表（12 字段），无论命中与否 |
| **原因** | 没有数据就无法优化——SOP 命中率、误判率、覆盖盲区都需要数据支撑 |
| **后果** | 增加一次异步数据库写入（不阻塞回复）；是必要成本 |

### ADR-015：M9 Specialist 与 Orchestrator 分层

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-14 |
| **决策** | Orchestrator (MasterAgent) 负责路由与编排，Specialist (BusinessFlowAgent) 负责按 SOP 隔离执行；两路径并存 |
| **原因** | MasterAgent 的 ReAct 在灵活场景不可替代；Specialist 解决 SOP 隔离执行（限定工具集、按 SOP 手册逐步执行） |
| **后果** | 路由逻辑：命中 SOP → Specialist；未命中 → unmatched 兜底；共用 ToolRegistry 和 GuardianReview |

### ADR-016：M9 长期记忆是普通 SOP 意图

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-14 |
| **决策** | SOP-007-REC（长期记忆管理）与其他 SOP 在路由层完全平等，不硬编码在流程末尾 |
| **原因** | 长期记忆有自己的 §2 触发条件 + §3 执行流程 + §4 字段分级，完全符合独立 SOP 定义 |
| **后果** | 复合请求中记忆意图与其他意图并行派发；记忆 SOP 可独立修改；V→W 硬编码节点已删除 |

### ADR-017：M10 领域知识分层注入

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-18 |
| **决策** | 领域知识分两层注入：L1（核心认知，~320 tokens）注入 Agent system prompt，L2（详细文档，~27K tokens / 239 chunks）通过 RAG 按需检索 |
| **原因** | (1) 全部注入会导致 context window 爆炸；(2) L1 覆盖高频术语和阶段框架，L2 覆盖细节；(3) 知识内容稳定，可作为独立 prompt 变量缓存 |
| **后果** | `domain_knowledge.md` 作为 L1 独立文件；`knowledge_search_tool` 扩展 stage/role 过滤；prompt 中 `{DOMAIN_KNOWLEDGE}` 占位符独立于高频变更的 `{TOOL_LIST}` 和 `{SOP_CATALOG}` |

### ADR-018：M11 Agent 全链路追踪与异步写入

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-18 |
| **决策** | Agent 推理/LLM 调用/工具调用全部写入数据库，追踪写入在 try/except 中异步执行，失败不阻塞主流程 |
| **原因** | (1) 没有追踪数据就无法审计 Agent 决策、统计 LLM 用量、优化 prompt；(2) 追踪写入绝对不能影响用户体验延迟；(3) PostgreSQL 提供更高写入吞吐 |
| **后果** | `agent_reasoning_logs` / `llm_interaction_logs` / `tool_call_logs` 三张表 + `AgentTraceService`；`LLMClient.set_trace_callback()` 非侵入式注入；`agent_trace_enabled=False` 可完全关闭；默认 `detail_level=summary` 不存完整 prompt |

### ADR-019：M11 聊天归档增强与附件关联

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-18 |
| **决策** | 出站回复和前导消息存档到 `messages` 表（direction="agent_to_user"）；入站多模态消息遍历 `event.get_messages()` 组件填充 msg_type/attachments；附件通过 `message_attachments` 中间表关联 |
| **原因** | (1) 原系统只存用户消息，无法查询"Agent 说了什么"；(2) IM 消息含图片/文件/语音等，只取 `message_str` 丢失多模态信息；(3) 一条消息可能含多个附件，需中间表 |
| **后果** | `ChatArchiveService` + `chat_archive` 工具；`FileStorageService` 负责附件下载与物理存储；`StandardMessage` 新增 msg_type/attachments 字段（有默认值，向后兼容） |

### ADR-020：M12a MessagePipeline 总线 + Hook 声明式架构

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-19 |
| **决策** | 将 `MessageApplication.process()` 的硬编码线性流程重构为 MessagePipeline 总线架构：8 个独立管道阶段 + HookRegistry 声明式挂载横切关注点 |
| **原因** | (1) process() 180 行内联方法，M8b/M8a/M11 三次侵入式修改；(2) 鉴权、审计、核验、追踪等横切关注点应声明式注册而非硬编码；(3) 管道没有可挂载点，每次新增横切功能都要改核心编排代码 |
| **后果** | `process()` 重写为 MessagePipeline 9 阶段总线模式（legacy 双架构已移除，M13 新增 download 阶段）；Hook 通过 JSON 配置声明式注册，三态决策 ALLOW/WARN/BLOCK；新增 pipeline/ 包（6 文件）+ HookExecutionLog 表 |
| **借鉴** | Claude Code Harness 的 exit code 语义（0/1/2 → ALLOW/WARN/BLOCK）+ Dify 的 Definition-Execution 分离理念 |
| **关键约束** | before hook 异常视为 BLOCK（安全第一原则）；after hook 异常不阻断（fire-and-forget）；deny always wins |
| **参考** | [M12a-Hook总线架构实施计划.md](../M12a-Hook总线架构实施计划.md) |

### ADR-021：M12b Checkpoint 持久化 — 确认状态 DB 化

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-19 |
| **决策** | 将确认流程状态从 `MessageApplication.pending_confirmations` 内存 dict 迁移到 `SOPCheckpoint` DB 表 + `CheckpointService`，借鉴 LangGraph checkpoint-per-superstep 模式 |
| **原因** | (1) 容器重启后所有待确认项丢失，用户说"刚才的还有吗"无法恢复；(2) 5 分钟超时后直接丢弃，无法审计确认流程成功率；(3) 只支持事件确认一种场景，任务/文件确认无法复用；(4) 内存 dict 无法跨进程共享 |
| **后果** | `pending_confirmations` dict 保留（向后兼容），CheckpointService 优先处理；超时后标记 expired 而非丢弃；新增 `sop_checkpoints` 表（16 列）+ `CheckpointService`（~400 行）；启动时异步扫描过期检查点 |
| **新旧并存** | checkpoint 优先查找 → 内存缓存加速 → DB 回退；checkpoint 未命中时回退 pending_confirmations dict；`checkpoint_enabled=False` 完全退化为原行为 |
| **关键约束** | 状态变更同时写 DB 和更新缓存；只序列化可 JSON 序列化的字段；state_json 不存 LLMClient 等复杂对象；DB 行锁 + status 状态检查防并发确认 |
| **参考** | [M12b-Checkpoint持久化实施计划.md](../M12b-Checkpoint持久化实施计划.md) |

### ADR-022：M9 工具库架构重构 — 关注点分离（Tools / Hooks / Built-in）

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-19 |
| **决策** | 将 ToolRegistry 从 15 个工具精简为 10 个纯原子工具。5 个"伪工具"重新安置：`invoke_business_flow` → `MasterAgent._dispatch_specialist()`（内置方法）、`invoke_guardian` → `MasterAgent._invoke_guardian()` + `DeepAuditHook`（内置方法 + Pipeline Hook）、`list_sop_catalog` → 删除（已通过 `dump_as_text()` 注入 prompt）、`read_flow_diagram` / `list_flow_diagrams` → 删除（子图全部注入 `{SUB_FLOWS}` 占位符） |
| **原因** | (1) Agent→Agent 编排（invoke_business_flow/invoke_guardian）不应由 LLM 通过 tool calling 决定——这是框架级 handoff；(2) 内省工具（list_sop_catalog/list_flow_diagrams/read_flow_diagram）数据已注入 system prompt，再暴露为工具是冗余 + 浪费 context；(3) 用户指出这与 Harness 模式不符——工具应为原子 I/O 操作，编排/路由/内省应由框架处理 |
| **后果** | ToolRegistry 15→10；MasterAgent 新增 `_dispatch_specialist()`、`_invoke_guardian()` 两个内置方法；新增 `DeepAuditHook`（deep_audit 类型，默认关闭）；单 SOP 匹配时框架自动派发（不再等 LLM 调工具）；`FlowMapManager` 新增 `get_all_sub_flows_text()`；SOP-006/SOP-005/intent_registry/master_agent.txt/main.md/unmatched.md 更新引用；删除 `.sop_index.json` 缓存 |
| **关键约束** | `_dispatch_specialist()` 逻辑从 `business_flow_tool.py` 原 `execute()` 迁移，`BusinessFlowAgent` 不变；SOP-006 匹配时走 `_invoke_guardian()` 而非 `_dispatch_specialist()`；`DeepAuditHook` 永不为 BLOCK（审计建议性）；`guardian_tool.py` / `business_flow_tool.py` 标记废弃但保留 |
| **参考** | [Harness 模式借鉴分析报告](../Harness模式借鉴分析报告.md) |

### ADR-023：M13 文件传输 — download 管道阶段 + Agent 按需读取

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-20 |
| **决策** | (1) 附件下载作为独立管道阶段 `download`（record 之后），不混入 record 阶段；(2) Agent 不默认加载文件内容，通过 `read_local_file` 工具按需读取；(3) 文件发送通过 `send_file_callback` 闭包注入（与 `progress_sender` 同模式），使用 `event.send()` 直发不冲突最终 reply |
| **原因** | (1) 下载是 I/O 密集型操作，与 DB 写入（record）关注点不同；(2) 文件可能很大（图纸、PDF），自动注入 context window 会爆炸；(3) `send_file` 需要在每次请求时捕获不同的 `event` 引用，闭包注入是已验证模式（M8b progress_sender）；(4) `event.chain_result()` 支持 `[Plain, File, Image]` 混合消息链 |
| **后果** | Pipeline 阶段 8→9；新增 `download` 阶段在 `record` 和 `bind` 之间；`FileStorageService` 始终初始化（不再由 `file_download_enabled` 门控）；2 个新 Agent 工具（send_file / read_local_file） |
| **备选** | (a) 作为 Hook：不够——下载是核心数据流，不是横切关注点；(b) 在 record 阶段内下载：混淆了持久化和 I/O 的职责；(c) Agent 默认加载全部文件：context window 风险 |
| **风险** | QQ/NapCat 的附件 URL 可能需要 cookie 认证，当前 aiohttp 裸请求可能失败（`file_download_enabled` 默认 True，但下载失败不阻断管道） |

### ADR-024：Ex4 — MaxKB hit_test 纯向量检索 API 集成

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-21 |
| **决策** | (1) 使用 MaxKB 内部 admin API `hit_test` 做纯向量检索（不经过 LLM 聊天接口）；(2) RAG provider 在 `bootstrap.init()` 之前创建并通过参数链传递；(3) EmysTester 自动替换 Docker 服务名为 localhost |
| **原因** | (1) Chat API 返回 LLM 生成答案而非原始文档段落，不可控且混入通用知识；(2) main.py 原接线 bug：provider 在 `bootstrap.init()` 之后创建且从未传入 TeamBrainCore，导致 knowledge_search 工具永不注册；(3) 测试器在宿主机运行，Docker 服务名 `maxkb` 不可达 |
| **后果** | 6 文件改动：config.py（4 新字段）、maxkb_provider.py（重写为 hit_test）、main.py（接线修复 + JSON config 兜底）、bootstrap.py（透传 rag_provider）、tester.py（RAG provider 初始化 + localhost 替换）、_conf_schema.json（Web UI 字段） |
| **备选** | (a) 直接查 pgvector 表：需处理 MaxKB 内部表结构和 embedding 模型同步，耦合过深；(b) 用 MaxKB application chat API：无法获取原始检索结果，返回被 LLM 二次加工 |
| **风险** | hit_test 是 MaxKB 未公开内部 API，未来版本可能变更或移除 |

### ADR-025：M14 — 核心业务工具迁移至 SOP 业务流（LLM 结构化输出 + 框架执行）

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-21 |
| **决策** | (1) 5 个核心业务工具（record_event / record_task / record_meeting / record_file / query_data）从 LLM ToolRegistry 移除，迁移至新 BusinessFlowToolRegistry；(2) BusinessFlowAgent 新增结构化输出模式：LLM chat_json → 提取参数 → 框架直接调用 handler，不再走 ReAct + function calling；(3) GuardianReview 核验逻辑保留在 handler 内部（数据写屏障）；(4) query_data 保留在 LLM ToolRegistry 供 unmatched 兜底只读查询 |
| **原因** | (1) SOP §3.2 已声明需要什么工具，LLM 无须再通过 function calling "决定"调用哪个工具——LLM 的核心价值是自然语言→结构化参数提取；(2) 减少 LLM tool calling 往返延迟和 token 消耗；(3) 框架直接执行消除了 LLM 调用错误工具的风险；(4) 关注点分离——SOP 匹配后的工具选择是确定的，不应依赖 LLM 动态决策 |
| **后果** | 新增 1 文件（business_flow_tools.py）；重构 5 个 tool 文件（提取独立 handler + schema 常量）；修改 5 文件（tools/__init__ / business_flow_agent / master_agent / __init__ / README / CLAUDE）；ToolRegistry 精简为条件工具 + query_data；BusinessFlowAgent 自动选择执行模式（结构化输出 vs ReAct 回退） |
| **备选** | (a) 全部工具保留为 LLM function calling：LLM 延迟和 token 开销大，SOP 路径下调用错误工具的风险无法消除；(b) 框架完全硬编码：丧失 LLM 自然语言参数提取能力，unmatched 路径不可用 |
| **风险** | (1) chat_json 依赖模型支持 response_format={"type":"json_object"}，不支持的模型需回退到 chat + 正则提取；(2) unmatched 兜底路径无写能力（仅 query_data），纯写操作未命中 SOP 时无法执行——需关注 unmatched 使用率 |

### ADR-026：M15 — 总线管道重构 Phase 0（状态机调度 + WorkOrder 流转单 + Mock 驱动）

| 项 | 内容 |
|----|------|
| **日期** | 2026-06-22 |
| **决策** | (1) 引入 WorkOrder 全息流转单作为阶段间唯一合法数据通道；(2) PipelineScheduler 为唯一驱动引擎；(3) Phase 0 中间 6 阶段使用 Mock 实现；(4) intake 和 archive 使用真实逻辑；(5) 2026-06-22 M12a 9 阶段铁序管道完全移除 |
| **原因** | (1) 缺少统一状态机导致 `should_abort`/`is_fast_reply` 语义过载；(2) MasterAgent 职责过重（路由+执行+合成三重角色）；(3) 鉴权位置不合理（execute 阶段才拦截）；(4) decompose 阶段架空；(5) 守护模式滞后（事后审查） |
| **后果** | 新增 14 文件（interfaces/×7 + mocks/×7 + work_order.py + pipeline_scheduler.py + pipeline_config_m15.json）；修改 7 文件（pipeline/__init__ + pipeline_context + message_pipeline + message_app + __init__ + config）；2026-06-22 收尾：M12a 9 阶段铁序管道 + pipeline_config.json 完全移除，M15 为唯一路径 |
| **备选** | (a) 在旧管道上打补丁：治标不治本；(b) 全真实实现：Phase 0 风险高，Mock 驱动先跑通总线再并行替换 |
| **风险** | (1) Mock 模式不能验证业务正确性（需 Phase 1-4 替换）；(2) 状态机复杂度增加（11 状态 vs 原 3 标志位）；(3) `_force_status()` 绕过转换校验是应急方案，需细化 |

## 4. 文档索引

| 文档 | 路径 |
|------|------|
| **项目 README（架构+设计原则）** | [../README.md](../README.md) |
| **CLAUDE.md（AI 操作指令）** | [../CLAUDE.md](../CLAUDE.md) |
| **踩坑记录** | [踩坑记录.md](踩坑记录.md) |
| **CodeGraph 使用指南** | [CodeGraph使用指南.md](CodeGraph使用指南.md) |
| **M1 实施计划** | [开发需求/M1-插件壳加分域接管-实施计划.md](开发需求/M1-插件壳加分域接管-实施计划.md) |
| **M2 实施计划** | [开发需求/M2-消息入库加用户绑定-实施计划.md](开发需求/M2-消息入库加用户绑定-实施计划.md) |
| **M3 实施计划** | [开发需求/M3-Router加事件录入-实施计划.md](开发需求/M3-Router加事件录入-实施计划.md) |
| M5 实施计划 | [开发需求/M5-知识库检索与查询-实施计划.md](开发需求/M5-知识库检索与查询-实施计划.md) |
| M6 需求整理 | [开发需求/M6-项目生命周期管理-需求整理.md](开发需求/M6-项目生命周期管理-需求整理.md)（原 M8 需求移入 M6） |
| M6 实施计划 | [开发需求/M6-项目生命周期管理-实施计划.md](开发需求/M6-项目生命周期管理-实施计划.md)（2 子阶段：M6a 守护Agent核心 + M6b 唤醒机制）
M6 代码实现设计 | [开发需求/M6-守护Agent-代码实现设计.md](开发需求/M6-守护Agent-代码实现设计.md)（新建 5 文件 + 修改 4 文件，含验收用例） |
M6 QQ 测试用例 | [开发需求/M6-QQ测试用例.md](开发需求/M6-QQ测试用例.md)（3 用例：巡检 + 审核 + 负向验证） |
| M7 实施计划 | [开发需求/M7-MasterAgent-实施计划.md](开发需求/M7-MasterAgent-实施计划.md)（ReAct 主 Agent + 动态技能 + ToolRegistry，✅已验收） |
| M8 实施计划 | [开发需求/M8-守护增强与体验优化-实施计划.md](开发需求/M8-守护增强与体验优化-实施计划.md)（3 子阶段：守护增强 + 前导机制 + 日记与记忆，17 用例，✅已验收） |
| M9 业务流架构规划 | [../业务流规划.md](../业务流规划.md)（14 章完整设计，✅ 2026-06-14 实施完成，4 阶段 19 文件） |
| M9 实施总结 | [../M9_实施总结.md](../M9_实施总结.md)（Phase A/B/C/D 全部完成，变更概览 + 验收状态） |
| M10 实施报告 | [开发需求/M10_实施报告.md](开发需求/M10_实施报告.md)（✅ L1+L2 108 用例全部通过） |
| M10 方案设计 | [开发需求/M10-地产领域知识注入方案.md](开发需求/M10-地产领域知识注入方案.md) |
| M11 实施计划 | [开发需求/m11-全量聊天记录存储.md](开发需求/m11-全量聊天记录存储.md)（✅ 4 阶段实施完成，7/8 IM 实战通过） |
| M11 IM 通信测试 | [验收测试/M11阶段IM通信模式实战测试报告.md](验收测试/M11阶段IM通信模式实战测试报告.md) |
| **M12a Hook 总线实施计划** | [../M12a-Hook总线架构实施计划.md](../M12a-Hook总线架构实施计划.md)（✅ 2026-06-19 实施完成，2026-06-22 M12a 已废弃移除） |
| **M12a 通信测试报告** | [../M12a-通信测试报告.md](../M12a-通信测试报告.md)（8 用例全部通过，历史记录） |
| **M12b Checkpoint 持久化计划** | [../M12b-Checkpoint持久化实施计划.md](../M12b-Checkpoint持久化实施计划.md)（✅ 2026-06-19 实施完成，1 新增 + 4 修改文件） |
| Ex1 Web 控制台 | [开发需求/Ex1-Web运维控制台-实施计划.md](开发需求/Ex1-Web运维控制台-实施计划.md)（待创建） |
| Ex2 RAG 集成 | [开发需求/Ex2-知识库RAG集成-实施计划.md](开发需求/Ex2-知识库RAG集成-实施计划.md)（原 M6 移入） |
| 代码审查 M5-M7 | [代码审查-M5-M7.md](代码审查-M5-M7.md) |
| **原始需求文档** | [开发需求/需求文档/](开发需求/需求文档/) |
| **MVP 设计文档包** | [开发需求/TeamBrain-MVP开发整合包/](开发需求/TeamBrain-MVP开发整合包/) |
| **🔗 Harness 模式借鉴分析** | [../Harness模式借鉴分析报告.md](../Harness模式借鉴分析报告.md)（📄 5 项目架构对比 + 5 推荐模式 + 实施路线） |
| **M12-M14/Ex4 实战测试报告** | [../emybot_M12-M14_Ex4_实战测试报告_20260621.md](../emybot_M12-M14_Ex4_实战测试报告_20260621.md)（5 组对话式测试，2026-06-21） |
| **M15 Phase 0 实施报告** | [../M15-Phase0-总线管道重构-实施报告.md](../M15-Phase0-总线管道重构-实施报告.md)（14 新文件 + 7 修改文件，7/7 验收通过，2026-06-22） |
| **总线管道排布计划** | [../总线管道排布计划.md](../总线管道排布计划.md)（v2.0 架构设计 + Mock 驱动分步实施方案） |

---

## 5. 实战测试记录

### 2026-06-21 M12-M14/Ex4 实战测试（emy-test）

**测试范围**：M15 Hook总线 / M12b Checkpoint持久化 / M13 文件传输 / M14 SOP结构化输出 / Ex4 MaxKB RAG

**测试环境**：Docker 生产环境，daemon 模式 (`http://127.0.0.1:9020`)

**测试结果**：

| # | 测试项 | 结果 | 备注 |
|---|--------|------|------|
| T1 | M15 auth.admin_check 鉴权Hook拦截 | ⚠️ 未拦截 | Hook已在pipeline_config_m15.json注册，但普通用户仍可执行守护审计，需补充角色校验逻辑 |
| T2 | M12b/M14 事件录入多轮确认 | ⚠️ 确认环节异常 | 拟录入单生成正常（第1轮），但确认时提示"record_event功能未上线"，BusinessFlowToolRegistry handler注册断裂 |
| T3 | M13 read_local_file 按需读取文件 | ✅ 正常 | 成功读取《消防验收指南》并摘要，主动询问是否发送原文件 |
| T4 | M14 query_data 结构化查询 | ❌ 报错 | "查一下今天有哪些事件"返回"处理请求遇到问题"，需查看容器日志定位异常 |
| T5 | Ex4 MaxKB knowledge_search RAG检索 | ✅ 正常 | 竣工验收阶段材料检索准确，按「前置专项→竣工→备案」三段式组织答案 |

**整体通过率**：2/5（40%），基础设施层（RAG/文件读取）正常，M14迁移后业务流工具链路存在断裂

**修复优先级**：
- P0：M14 query_data/record_event handler注册检查 + 容器日志定位
- P1：AuthHook角色校验实现（绑定users.role字段）
- P2：RAG来源标注、群聊非@阻断等体验优化

**乱码问题说明**：Windows PowerShell 默认GBK编码导致中文输出显示为乱码，设置 `$env:PYTHONIOENCODING="utf-8"` 后正常。后续emy-test命令应预先设置编码环境变量。

**详细报告**：[emybot_M12-M14_Ex4_实战测试报告_20260621.md](../emybot_M12-M14_Ex4_实战测试报告_20260621.md)
