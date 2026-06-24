# CLAUDE.md — EmyBot 项目 AI 辅助开发指令

> Claude Code 启动时自动加载本文档。进入会话后无需等待用户指示，直接按下方流程加载上下文。

## 会话启动流程（每次新会话自动执行）

1. **读完本文档（CLAUDE.md）** ← 你在这里
2. **读** **[README.md](README.md)** → 全面理解项目定位、架构、数据流、目录结构、数据库表结构、设计原则
3. **读** **[tem\_log/开发记录.md](tem_log/开发记录.md)** → 了解当前里程碑进度、架构决策记录（ADR）、开发操作速查
4. Memory 文件（`~/.claude/projects/d--app-EmyBot/memory/`）由 Claude 自动加载，无需手动读取，其中包含用户角色、关键设计约束、踩坑提醒

> **重要**：上述文档已经包含了项目全貌。不要向用户重复介绍项目定位、架构、目录结构、设计原则、开发阶段——除非用户明确要求。进入后直接问"接下来做什么"或直接开始当前任务。对项目非数据类的改动在改完代码后，应自主更新readme.md、CLAUDE.md、开发记录.md文档。

***

## 开发约束

1. **业务内核独立**：`teambrain_core/` 不 import 任何 `astrbot.*` 包，未来可独立迁移为微服务
2. **注册表模式**：新增意图只在 `__init__.py` 的 `_ensure_components_initialized()` 中调用 `register_handler()`，**不要改** `message_app.py` 的 `_dispatch()`
3. **Prompt 独立文件**：LLM Prompt 放 `prompts/` 目录，不硬编码在 Python 代码中
4. **分层不可跳**：`main.py → Adapter → EmyCore → Service → Repository → DB`，不跨层调用
5. **Hook 声明式注册**：新增横切关注点（鉴权/审计/核验/追踪）通过 `pipeline_config_m15.json` 声明式挂载，不改 `message_app.py` 核心编排。Hook 三态决策：ALLOW/WARN/BLOCK，deny always wins
6. **M14 业务流工具**：`record_event / record_task / record_meeting / record_file / query_data` 已从 LLM ToolRegistry 迁移至 `BusinessFlowToolRegistry`。BusinessFlowAgent SOP 匹配后使用结构化输出（LLM → JSON 参数 → 框架直接调用 handler），不再走 ReAct + tool calling。新增核心写操作工具必须注册为 `BusinessFlowTool` 而非 `ToolDefinition`。`ToolRegistry` 仅保留条件工具（如 knowledge_search、chat_archive 等）+ query_data 兜底查询。

## 日常命令

```powershell
# 重启 AstrBot（每次代码变更后）
docker compose -f docker-compose-napcat.yml restart astrbot

# 启动全部容器（含 MaxKB）
docker compose -f docker-compose-napcat.yml up -d

# 查看 EmyBot 日志
docker logs --tail 100 astrbot 2>&1 | Select-String -Pattern "teambrain|Emy"

# 启动 Web 测试控制台（生产环境实战测试）
python .claude/skills/emy-test/emy_web/app.py

# 命令行快速测试
python .claude/skills/emy-test/emys_tester.py --message "你好" --sender "Alice"
python .claude/skills/emy-test/emys_tester.py --managed --message "帮我创建事件：样板段放线完成" --sender "张工"

# 运行验收测试（脚本位于 tem_log/验收测试/，从项目根目录执行）
python tem_log/验收测试/verify_m4.py
python tem_log/验收测试/verify_m12a.py              # M15 管道总线（15 用例，无需 LLM）
python tem_log/验收测试/verify_m12a_communication.py # M15 通信实战（8 用例，需 LLM）

# Ex4 RAG 知识库测试
python testsearch.py "消防验收"                      # 独立脚本测试 MaxKB hit_test 纯向量检索
python .claude/skills/emy-test/cli.py --managed --no-daemon --llm --message "请用knowledge_search工具查询：消防验收需要哪些材料" --sender "张工" --sender-id "zhang_gong"
# 注：--managed 模式下 tester 会自动创建管理员用户，无需手动设置 is_admin

# M10 领域知识验收
python tem_log/验收测试/verify_domain_l1.py     # L1 核心认知注入（71 用例）
python tem_log/验收测试/verify_domain_l2.py     # L2 按需检索（37 用例）

# 知识提取工具
python tem_log/knowledge_builder.py --stats

# 查看容器状态
docker compose -f docker-compose-napcat.yml ps

# 清除 pycache（容器内代码变更后必须执行，否则旧 .pyc 可能导致 ImportError）
docker exec astrbot find /AstrBot/data/plugins/team_brain_agent -name '__pycache__' -type d -exec rm -rf {} +
```

## 项目路径

| 路径                                                            | 说明                        |
| ------------------------------------------------------------- | ------------------------- |
| `d:\app\EmyBot\`                                              | 项目根目录                     |
| `d:\app\EmyBot\data\plugins\team_brain_agent\`                | Emy 主插件                   |
| `d:\app\EmyBot\data\plugins\team_brain_agent\teambrain_core\` | 业务内核                      |
| `d:\app\astrbot\`                                             | AstrBot 源码（参考用，不可修改）      |
| `d:\app\Qwen\Qwen3-Embedding-0___6B\`                         | 本地 embedding 模型（MaxKB 挂载） |
| `d:\app\pgdata\`                                              | PostgreSQL 数据（MaxKB 容器挂载） |

## 关键文件索引

| 文件                                                  | 角色                                                                                   |
| --------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `main.py`                                           | AstrBot 插件入口，消息去重 + 适配转换                                                             |
| `teambrain_core/__init__.py`                        | EmyCore 类，组件组装 + handler 注册                                                          |
| `teambrain_core/application/message_app.py`         | 核心编排：PipelineScheduler 8 阶段状态机总线（唯一消息处理路径）— M13 download 已合并到 intake |
| `teambrain_core/pipeline/message_pipeline.py`       | M15 MessagePipeline 总线核心：阶段管理 + Hook 注册表 + 状态机协调执行                                     |
| `teambrain_core/pipeline/hook.py`                   | Hook 基类 + 三态决策 + 6 个具体 Hook 子类（含新增 DeepAuditHook）                             |
| `teambrain_core/pipeline/hook_registry.py`          | HookRegistry 注册表（借鉴 ToolRegistry 模式）                                            |
| `teambrain_core/pipeline/pipeline_context.py`       | PipelineContext 共享状态 + SubTask                                                  |
| `teambrain_core/pipeline/pipeline_scheduler.py`     | M15 PipelineScheduler 状态机驱动执行循环                                                       |
| `teambrain_core/pipeline/work_order.py`             | M15 WorkOrder 流转单（11 状态 + 3 短路路径）                                                     |
| `data/config/pipeline_config_m15.json`              | M15 Hook 声明式配置（业务人员可编辑，重启生效）                                                        |
| `teambrain_core/pipeline/mocks/`                    | M15 Mock 层（Phase 0 总线验证）: MockRouter/AuthEngine/RiskGrader/Planner/WorkAgent/Guardian |
| `teambrain_core/agent/master_agent.py`              | ReAct 主 Agent + M9 发现式路由（统一对话入口）                                                     |
| `teambrain_core/agent/intent_registry.py`           | SOPIntentRegistry：启动扫描 SOPrepository/ 动态构建目录                                         |
| `teambrain_core/prompts/master_agent.txt`           | MasterAgent system prompt 模板（含 M10 领域知识占位符）                                          |
| `teambrain_core/prompts/domain_knowledge.md`        | L1 核心领域认知（M10）— \~320 tokens，7 阶段 + 9 部门 + 18 术语                                     |
| `teambrain_core/services/agent_trace_service.py`    | Agent 推理全链路追踪服务（M11）                                                                 |
| `teambrain_core/services/chat_archive_service.py`   | 全量聊天记录存档服务（M11）                                                                      |
| `teambrain_core/services/file_storage_service.py`   | 文件存储服务（M13）— IM附件下载到本地、file_no 生成、files/message_attachments 联动               |
| `teambrain_core/tools/business_flow_tools.py`       | M14 业务流工具注册表 — 框架直接执行的 5 个核心工具（不暴露为 LLM function calling）                 |
| `teambrain_core/tools/file_tool.py`                 | 文件工具集（M13）— record_file + send_file + read_local_file                                   |
| `teambrain_core/tools/knowledge_search_tool.py`     | RAG 知识库搜索工具（Ex4）— 封装 RagProvider.search()，支持 stage/role 过滤                        |
| `teambrain_core/providers/rag/maxkb_provider.py`    | MaxKB hit_test 纯向量检索（Ex4）— admin 登录 + hit_test API + 401 自动重试                        |
| `teambrain_core/providers/rag/local_fallback.py`    | 本地关键词回退检索（Ex4）— 无需外部服务，支持项目资料目录文件搜索                                        |
| `teambrain_core/config.py`                          | 全局配置（含 Ex4 RAG 字段：maxkb_* / kb_enabled / kb_top_k）                                    |
| `data/config/team_brain_agent_config.json`          | 运行时配置文件（含 RAG 凭证：maxkb_admin_password / maxkb_knowledge_id）                           |
| `teambrain_core/repositories/message_repo.py`       | 消息 CRUD（含 FK 解析 `_resolve_conversation_id()` — business ID → UUID）                     |
| `teambrain_core/repositories/agent_reasoning_repo.py`| Agent 推理日志 CRUD（含 FK 解析）                                                            |
| `teambrain_core/infrastructure/database/models.py`  | ORM 模型（22 表，含 HookExecutionLog）                                                 |
| `teambrain_core/infrastructure/database/session.py` | DB 连接管理（PostgreSQL 单引擎）                                                       |

## 踩坑速查

- **`__pycache__`** **不会自动刷新**：容器 bind mount 不触发 Python 重编译，每次代码变更后必须清除
- **`event.plain_result()`** **不是 awaitable**：用 `result = event.plain_result(text); event.set_result(result); event.stop_event()`
- **插件** **`__init__`** **必须接收** **`config`** **参数**：`__init__(self, context, config=None)`，AstrBot 通过 `Star(config=plugin_config)` 传入
- **expire\_on\_commit=False**：避免 ORM 对象在 session 外访问属性报 `DetachedInstanceError`
- **消息去重指纹不包含 sender\_id**：同一消息不同 pipeline 阶段 sender\_id 可能不同
- **无 LLM key 时放行给 AstrBot 兜底**：不要用硬编码文本回复，`return None` 即可
- **M15 pipeline 始终启用**：PipelineScheduler 8 阶段状态机总线（intake→route→auth→plan→execute→compose→verify→archive）是唯一消息处理路径，auth.admin\_check hook 会阻断非管理员请求
- **M13 附件自动下载**：intake 阶段自动下载消息附件到本地，下载失败不阻断管道。Agent 可按需调用 `read_local_file`/`send_file` 工具读取或发送文件
- **Hook 三态语义**：ALLOW=放行、WARN=非致命警告继续执行、BLOCK=立即终止管道。before hook 异常视为 BLOCK（安全第一原则），after hook 异常不阻断
- **EmysTester RAG URL**：tester 在宿主机运行，会自动将 `maxkb` 替换为 `localhost`（Docker 服务名在宿主机不可达）
- **FK 列语义陷阱**：`messages.conversation_id` 列是 FK→`conversations.id`（UUID），不是业务 `conversation_id` 字符串。`create_outbound()` / `AgentReasoningLog` 写入前需通过 `_resolve_conversation_id()` 转换，否则 FK 违规（此坑 `create_from_standard()` 不存在——它正确用了 `conv.id`）

## 提交规范

- commit message 格式：`M<n>: <变更摘要>`
- 示例：`M3: Router + 事件录入`、`M4: 任务/会议/文件 CRUD`
- 每个 M 阶段尽量分步提交：infra → logic → verify

