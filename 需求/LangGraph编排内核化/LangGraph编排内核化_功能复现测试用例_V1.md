# LangGraph 编排内核化 — 功能复现测试用例（V1）

> **用途**：新框架（LangGraph 编排内核）取代原实现后，逐项验证原系统功能是否复现。本文是"复现测试材料"——每一行都是可直接执行的测试，且写明预期答案。
> **依据**：原系统功能清单（`SessionLoop` 方法清单 + 19 个业务工具 + 11 个 SOP + API 路由 + 规则资产 + 既有黄金语料）
> **参照**：[复盘 V1](LangGraph编排内核化_复盘_V1.md)（原功能恢复与接入情况）、[测试报告 V3](LangGraph编排内核化_测试报告_V3.md)
> **命名**：用例编号 `FR-{组}{序号}`（FR = Functional Reproduction）

## 一、使用说明

### 1.1 状态口径（判定"是否复现"的标准）

| 状态 | 含义 | 判定 |
|------|------|------|
| ✅ 已恢复 | 新路径已有实现且有证据 | 预期答案全部满足 = 复现成功 |
| ⚠️ 语义差异 | 已由新机制替代，但保证对象或分支不同 | 按"预期答案"判定，差异记入备注 |
| ❌ 未接入 | 原功能在新路径缺失（**复现目标**） | 当前预期为"不符合"，修好后转为 ✅ |
| ⛔ 休眠 | 经决策 D10 暂停（专家评审） | 仅验证"休眠生效"，不验证功能本体 |

### 1.2 断言字段（沿用既有黄金语料词汇，可直接转成语料条目）

`reply_contains`（回复包含指定文本）、`capability_hit`（命中指定能力/SOP）、`permission_block`（被拒绝）、`suspend_resume`（挂起并续接）、`archive_completeness`（归档完整）、`llm_calls`（模型调用次数）、`db_effect`（库内可观测变化）、`log_contains`（日志包含指定行）。

### 1.3 执行方式

- **回放**（可自动化）：`uv run python scripts/golden_session_loop.py --corpus {语料} --path new --report {报告}`
- **接口**：`POST /message/send`（真实用户 UUID）
- **内核专项**：`docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/session_graph_replay.py`
- **库核对**：`docker exec emily-postgres psql -U emily -d emily -c "..."`

### 1.4 执行前置

1. 容器健康：`emily-core` running 且 `{"status":"ok","initialized":true,"langgraph_engine":true}`；`emily-postgres` accepting。
2. 真实用户（宪法 Q3，禁止伪造）：`0551d8cc-b584-4b19-843e-25dbd0287240`（林建辉，level 3，管理单位）；另需一名访客级与一名管理级用户做权限对照。
3. 开关：`EMILY_SESSION_LOOP_ENABLED=true`（主循环）、`EMILY_SESSION_GRAPH_ENABLED=true`（图路径）。
4. 基线快照：记录 `messages`/`events`/`tasks`/`scheduler_jobs` 行数，测试后比对。

## 二、用例总览

| 组 | 领域 | 用例数 | 其中复现目标（未接入/差异） |
|----|------|-------|--------------------------|
| A | 会话主干 | 8 | 0 |
| B | 提示与模型 | 5 | 0 |
| C | 能力与 SOP | 14 | 1（专家评审休眠） |
| D | 计划与粗排 | 5 | 0 |
| E | 挂起与确认 | 5 | 3（写操作确认、待确认注入、结算分支） |
| F | 归档与进度 | 5 | 0 |
| G | 权限与可见范围 | 8 | 0 |
| H | 上下文注入 | 5 | 2（群聊注入、待确认事件注入） |
| I | 附件与文件 | 6 | 1（图路径附件端到端） |
| J | 检索与知识 | 4 | 0 |
| K | 定时与作业 | 5 | 0 |
| L | 全景节点 | 8 | 0 |
| M | 外壳与稳定性 | 6 | 0 |
| N | 治理与合规 | 6 | 0 |
| O | 编排内核专项 | 8 | 0 |
| **合计** | | **98** | **7** |

## 三、A 组 会话主干

| 编号 | 原功能（来源） | 状态 | 操作 | 预期答案 | 断言字段 |
|------|--------------|------|------|---------|---------|
| FR-A1 | 闲聊零成本直答（`_handle_impl` 快速短路） | ✅ | 发「你好」 | HTTP 200；回复含「你好」；**模型调用 0 次**；耗时应 < 1s | reply_contains, llm_calls |
| FR-A2 | 致谢直答 | ✅ | 发「谢谢」 | 回复含「不客气」 | reply_contains |
| FR-A3 | 单轮业务问答 | ✅ | 发「翠湖庭院最近有什么事件？」 | 回复列出该项目的真实事件，含状态（已确认/待确认）或数量；未编造不存在的事件 | reply_contains, capability_hit |
| FR-A4 | 多轮上下文继承 | ✅ | 同一 `sender_id`+`conversation_id`；先发「翠湖庭院最近有什么事件？」，再发「那第二条是什么情况？」 | 第二轮能引用第一轮结果（第 2 条事件），不反问"哪条" | reply_contains |
| FR-A5 | 复合请求触发计划 | ✅ | 发「帮我记录事件：科技城5号楼铺装完成25平米，验收通过；另外把这件事也记到任务里」 | 走计划分支（日志出现计划构造与执行）；两步能力均被调用；回复含两步成果 | capability_hit, log_contains |
| FR-A6 | 迭代上限收敛 | ✅ | 内核回放用例 3（桩模型持续索要工具调用） | 达上限后返回可读收尾文案，不抛异常、不死循环 | （回放断言） |
| FR-A7 | 模型/能力异常降级 | ✅ | 桩能力抛异常 | 回复为可理解的失败说明；主循环不中断；无 Traceback 抛出到用户 | reply_contains |
| FR-A8 | 会话终止 | ✅ | `POST /session/terminate` | 返回成功；该会话从 `GET /sessions` 消失；后续消息新建会话 | db_effect |

## 四、B 组 提示与模型

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-B1 | 系统提示词渲染 | ✅ | 任一轮对话后查 Session 归档头部 | 出现「Session Prompt 模板名 + 字数」；关键变量（user_name/project_name）非「（无）」 | archive_completeness |
| FR-B2 | 提示模板加载（`session_loop.md`/`session.md`） | ✅ | 同上 | 归档显示所用模板名与 `emily-data/prompts/` 中的文件一致 | log_contains |
| FR-B3 | 模型分层（flash 意图 / pro 能力循环） | ✅ | 查 `emily-data/logs/llm_trace.jsonl` | 意图识别用 `deepseek-v4-flash`；能力循环用 `deepseek-v4-pro`；无异常 model | log_contains |
| FR-B4 | 历史消息拼装 | ✅ | 见 FR-A4，查归档 LLM 调用段的 messages 摘要 | 第 2 轮请求携带第 1 轮问答 | archive_completeness |
| FR-B5 | 上下文压缩（token 预算） | ✅ | 长对话（> N 轮）后查归档 | 出现压缩动作记录；后续请求 prompt 长度不随轮次线性增长 | db_effect |

## 五、C 组 能力与 SOP

> 19 个业务工具 + 11 个 SOP。SOP 能力与工具均需验证"能被模型选中并执行"。

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-C1 | SOP-001 会议纪要 | ✅ | 发「把今天的会议纪要整理一下：讨论科技城5号楼进度，要求本周完成铺装」 | 命中 SOP-001；产生会议纪要记录；回复为纪要确认 | capability_hit, db_effect |
| FR-C2 | SOP-002 事件记录 | ✅ | 发「帮我记录事件：科技城5号楼铺装完成了25平米，验收通过。」 | 命中 SOP-002；`events` 新增 1 行且含项目与描述；回复确认已记录 | capability_hit, db_effect |
| FR-C3 | SOP-003 任务管理 | ✅ | 发「给张工创建任务：本周五前提交苗木验收报告」 | 命中 SOP-003；`tasks` 新增 1 行含执行人与时限；回复确认 | capability_hit, db_effect |
| FR-C4 | SOP-004 文件归档 | ✅ | 发「把这份验收单归档到科技城5号楼」（附文件） | 命中 SOP-004；文件与节点建立关联；回复确认归档位置 | capability_hit |
| FR-C5 | SOP-005 数据查询 | ✅ | 发「翠湖庭院目前有哪些待确认的事件？」 | 命中 SOP-005；`query_type=event`；回复仅含该项目数据 | capability_hit |
| FR-C6 | SOP-007 用户记忆 | ✅ | 发「记住我负责翠湖庭院项目」→ 新会话发「我负责哪个项目？」 | 首轮写入 `user_memory`；新会话能答出翠湖庭院 | db_effect, reply_contains |
| FR-C7 | SOP-008 遗留问题 | ✅ | 发「记录一个遗留问题：3号楼外墙渗水，需设计院复核」 | 命中 SOP-008；`pending_issue` 类记录新增；回复确认 | capability_hit, db_effect |
| FR-C8 | SOP-011 节点管理 | ✅ | 发「在科技城5号楼下面加一个子节点：地下车库」 | 命中 SOP-011；节点表新增子节点且父节点正确 | capability_hit, db_effect |
| FR-C9 | SOP-012 专家评审 | ⛔ | 发「请专家评审这份方案」 | 休眠生效：**不触发评审**（无 expert_review 节点执行、无评审记录）；回复走通用流程 | log_contains（无 expert_review） |
| FR-C10 | SOP-999 兜底 | ✅ | 发无明确意图的内容「嗯……」 | 命中兜底/澄清；不误调写类能力 | capability_hit, reply_contains |
| FR-C11 | 文件工具（file_tool） | ✅ | 发「列出科技城5号楼的文件」 | 返回该节点文件清单；仅含可见范围文件 | capability_hit |
| FR-C12 | 知识检索工具（knowledge_search_tool） | ✅ | 发「查一下公司关于苗木验收的规定」 | 返回知识库/公司制度片段；引用来源可追溯 | capability_hit, reply_contains |
| FR-C13 | 待办工具（task_tool / node_task_tool） | ✅ | 发「我有哪些待办？」 | 返回该用户可见待办清单（与 `GET /my-tasks` 一致） | capability_hit, db_effect |
| FR-C14 | 能力参数注入（`_inject_runtime_params`） | ✅ | FR-C2 后查库 | 写入行含发起人、会话、项目等运行期字段，无空必填项 | db_effect |

## 六、D 组 计划与粗排

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-D1 | 粗排覆盖全部能力 | ✅ | 复合请求含查询 + 写入（见 FR-A5） | 计划中同时包含查询类与写入类步骤；不再出现"仅 SOP 能力可粗排"的排除 | log_contains |
| FR-D2 | 层内并行 | ✅ | 内核回放用例 6 | 同层两步执行时间区间重叠；第二层起点晚于第一层结束 | （回放断言） |
| FR-D3 | 失败级联跳过 | ✅ | 内核回放用例 6 | 写能力失败 → 依赖它的步骤 `skipped`；不依赖的同级步骤照常完成 | （回放断言） |
| FR-D4 | 深度守卫 | ✅ | 同上（`depth=5` 追加） | 超深步骤被丢弃，不进入任何终态 | （回放断言） |
| FR-D5 | 契约校验拦截丢参 | ✅ | 内核回放用例 10 | 问题清单同时报「缺少必填参数」与「含未声明参数」；**入计划前拦截**（历史"未命名事件"降级不再出现） | （回放断言） |

## 七、E 组 挂起与确认

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-E1 | 能力需补参时挂起 | ✅ | 发「记一条事件」（缺项目） | 回复为追问（如「请补充项目名称」）；挂起态写入检查点 | suspend_resume |
| FR-E2 | 跨进程续接 | ✅ | 阶段 A 触发挂起 → 重启 `emily-core` → 补发项目名 | 重启后仍能续接并完成写入；无数据丢失 | suspend_resume, db_effect |
| FR-E3 | 挂起归属判定 | ✅ | 非发起者在同会话回复挂起项 | 拒绝（回复提示由发起者确认）；挂起态不被消费 | permission_block |
| FR-E4 | **写操作二次确认** | ❌ **未接入** | 发「删除科技城5号楼的文件」等写类操作 | **原功能**：回复确认请求（是/否），用户确认后才执行；**当前预期**：直接执行或直接拒绝，无确认环节 → 修好后须复测 | reply_contains（确认问句） |
| FR-E5 | **待确认事件注入 + 结果结算分支** | ❌ **未接入** | 在有待确认项时回复「确认」 | **原功能**：注入待确认上下文并消费确认；**当前预期**：无待确认上下文注入 → 修好后须复测 | reply_contains |

## 八、F 组 归档与进度

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-F1 | 轮次归档 | ✅ | 一轮对话后查 `emily-data/session_archives/` | 出现该会话 md，含「第 N 轮 · 时间」「用户消息」「回复」 | archive_completeness |
| FR-F2 | 归档内嵌能力调用清单 | ✅ | 业务提问后查归档 | 出现「🔧 能力调用」段：能力名、参数、成果摘要、触发者 | archive_completeness |
| FR-F3 | 权限快照未降级 | ✅ | 查归档「会话快照」段 | `level` 与用户一致；授权节点非空；`sop_allow` 非空；`scopes` 非空 | archive_completeness |
| FR-F4 | 进度事件同源 | ✅ | 端到端一次业务提问，观察 SSE | 出站事件含 `progress`（≥1）与 `reply`（=1）；进度文案来自节点映射（如「正在调用能力处理…」） | log_contains |
| FR-F5 | TTL 归档与段落完整性 | ⚠️ | 会话过期后查归档 | 归档文件生成；**已知缺陷**：追加轮次段与头部之间缺换行（`总轮数: 1## 第 1 轮`） | archive_completeness |

## 九、G 组 权限与可见范围

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-G1 | 等级权限（level 1-6 体系） | ✅ | 访客级用户发业务提问 | 被拒或返回空可见集；不泄露他方数据 | permission_block |
| FR-G2 | 项目归属推导可见范围 | ✅ | level 3 用户查非参与项目「星河湾」 | 不返回该项目数据；回复提示无权限或无数据 | permission_block |
| FR-G3 | 行级过滤（events/tasks/files/messages） | ✅ | 低级别用户查事件 | 返回行数 < 全表行数；无他企业数据 | db_effect |
| FR-G4 | 能力可见集 fail-closed | ✅ | 越权能力调用（内核回放用例 4） | 门禁拒绝且**能力未被调用**；回复为无权限说明 | permission_block |
| FR-G5 | 门禁为单一判定点 | ✅ | 图运行日志 | 出现 `fast→understand→gate⇄execute→summarize, gate=True`；门禁收敛说明日志可见 | log_contains |
| FR-G6 | 行级过滤 fail-open 面（存量） | ⚠️ | 构造无法安全注入的复杂查询 | **现状**：放行并打 WARNING（已知收窄点）；修复后应为拒绝 | log_contains |
| FR-G7 | 权限矩阵接口 | ✅ | `POST /grant`、`POST /revoke`、`POST /check` | 授权/回收/校验返回符合预期；越权校验返回拒绝 | db_effect |
| FR-G8 | 权限等级编号一致性 | ⚠️ | 对照 `docs/Manual/权限矩阵.md` 与 `permission/level.py` | 文档 0 起、代码 1 起，名称相同编号差一位（已知口径差），判定时以代码为准 | — |

## 十、H 组 上下文注入

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-H1 | 三书/规则书注入 | ✅ | 业务提问后查归档 prompt 段 | 含规则书相关条目；注入字数非 0 | archive_completeness |
| FR-H2 | 节点模板注入 | ✅ | 查归档会话快照「能力」段 | 技能数/工具数/三书摘要字数均非 0 | archive_completeness |
| FR-H3 | 项目上下文注入 | ✅ | 在项目群内提问 | 归档 prompt 含当前项目名；回复不反问"哪个项目" | archive_completeness |
| FR-H4 | **群聊注入** | ❌ **未接入** | 群聊中 @机器人 提问 | **原功能**：额外注入群成员/群上下文（`_group_injections`）；**当前预期**：无群维注入 → 修好后须复测 | archive_completeness |
| FR-H5 | **待确认事件注入** | ❌ **未接入** | 见 FR-E5 | 同 FR-E5 | archive_completeness |

## 十一、I 组 附件与文件

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-I1 | 文件上传 | ✅ | `POST /upload` | 返回成功；落盘到附件目录；返回文件标识 | db_effect |
| FR-I2 | 节点文件关联 | ✅ | `POST /node-file` | 文件与节点建立关联；`GET /{node_id}` 可见 | db_effect |
| FR-I3 | 文档解析 | ✅ | 上传 PDF/Word 后提问其内容 | 回复基于解析文本；无"无法读取" | reply_contains |
| FR-I4 | OCR / 表格抽取 | ✅ | 上传扫描件图片、表格截图 | 提取文本/表格结构；回复含关键字段 | reply_contains |
| FR-I5 | 向量化入库 | ✅ | `POST /rag-index` 后 `POST /rag-search` | 索引成功；检索能召回刚入库内容 | db_effect |
| FR-I6 | 图路径附件端到端 | ⚠️ | 图开关开启时上传文件并提问内容 | **未验证**（旧路径无附件专用方法，附件由工具承载）；需补端到端证据 | reply_contains |

## 十二、J 组 检索与知识

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-J1 | RAG 检索 | ✅ | `POST /rag-search`（关键词） | 返回命中片段 + 相似度；结果限于可见范围 | db_effect |
| FR-J2 | 索引/删除 | ✅ | `POST /rag-index`、`POST /rag-delete` | 索引后命中、删除后不命中 | db_effect |
| FR-J3 | 后端可列举 | ✅ | `GET /rag/backends` | 返回已配置后端；与 `emily-embed` 状态一致（当前该容器异常 → 依赖嵌入的检索可能降级） | db_effect |
| FR-J4 | 知识库问答引用 | ✅ | 发「公司关于苗木验收的规定是什么？」 | 回复含制度内容；引用来源可追溯（非幻觉） | reply_contains |

## 十三、K 组 定时与作业

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-K1 | 手册触发 | ✅ | `POST /run/{name}` | 指定作业手动执行成功；执行日志新增 | db_effect |
| FR-K2 | 作业清单与状态 | ✅ | `SELECT status FROM scheduler_jobs` | 3 行：JOB-001 周报（ACTIVE）、JOB-002 晨报（ACTIVE）、JOB-003 文件过期提醒（INACTIVE） | db_effect |
| FR-K3 | 系统自检 | ✅ | `POST /self-check` | 返回各项自检结果；其中调度能力判据为「存在 ACTIVE 作业」（当前应为通过） | db_effect |
| FR-K4 | 自检与内核解耦 | ✅ | 关闭全部作业后触发自检 | 自检**仍可执行**，不因某作业未运行而失败（原判据查作业日志且写法错误，已解耦） | db_effect |
| FR-K5 | 内核不依赖定时器 | ✅ | 停用调度执行 | 会话问答、能力调用、挂起续接均正常，无降级 | reply_contains |

## 十四、L 组 全景节点（领域）

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-L1 | 节点查询 | ✅ | `GET /nodes`、`GET /nodes/{node_id}` | 返回节点树/详情；仅含可见范围 | db_effect |
| FR-L2 | 节点创建与属性 | ✅ | `POST /nodes`（或 SOP-011 对话创建） | 新节点入库；属性完整；父节点正确 | db_effect |
| FR-L3 | 依赖维护 | ✅ | `POST /{node_id}/dependencies` | 依赖关系建立；`GET /node-table` 反映 | db_effect |
| FR-L4 | 参与企业 | ✅ | `POST /{node_id}/participant-companies`、`DELETE`、`PUT` | 增/删/改生效；影响该节点可见范围 | db_effect |
| FR-L5 | 成果与确认 | ✅ | `POST /{node_id}/deliverables`、`POST /{node_id}/acknowledge` | 成果登记成功；确认改变状态；不可重复确认（非法流转拒绝） | db_effect |
| FR-L6 | 状态机合法/非法流转 | ✅ | 对已终态节点再发流转请求 | 合法流转通过；非法流转被拒绝并给出原因 | db_effect |
| FR-L7 | 进展洞察 | ✅ | `POST /insights/generate`、`GET /insights/{date}` | 生成洞察并按日查询可取回 | db_effect |
| FR-L8 | 节点变更审计 | ✅ | `PATCH /{node_id}`、`PATCH /{node_id}/assign` | 变更入库；审计记录可查（谁、何时、改了什么） | db_effect |

## 十五、M 组 外壳与稳定性

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-M1 | 会话池 TTL | ✅ | 空闲超过 TTL | 会话被清扫；归档触发（`archive reason: expired`） | log_contains |
| FR-M2 | 并发上限 | ✅ | 并发超过上限时新会话请求 | 触发清扫或排队；无 500；无会话串号 | log_contains |
| FR-M3 | 会话列表与消息 | ✅ | `GET /sessions`、`GET /sessions/{cid}/messages` | 返回进行中会话与其消息；与库内一致 | db_effect |
| FR-M4 | 重启恢复 | ✅ | 重启 `emily-core` 后查会话 | 检查点表数据保留；新会话正常；无重复回复 | db_effect |
| FR-M5 | 灰度回退 | ✅ | 置 `EMILY_SESSION_GRAPH_ENABLED=false` 后重发消息 | 日志仅 `session loop path`、无 `graph path`；消息正常答复 | log_contains |
| FR-M6 | 开关可达性 | ✅ | 静态对照 `session_graph_enabled` | 定义 + bootstrap 映射 + 池读取 + compose 声明（≥2 处引用），非死开关 | log_contains |

## 十六、N 组 治理与合规

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-N1 | 规则演进闭环 | ✅ | `POST /patches/generate` → `POST /patches/{no}/approve` / `reject` / `rollback` | 生成补丁、审批生效、回滚可还原；`GET /patches` 可查 | db_effect |
| FR-N2 | 规则归纳与确认 | ✅ | `POST /rules/induct` → `POST /rules/{no}/confirm` / `discard` | 归纳出候选规则；确认后进入规则书；丢弃后不生效 | db_effect |
| FR-N3 | 规则查询 | ✅ | `GET /rules` | 返回当前生效规则条目 | db_effect |
| FR-N4 | 自我认知 | ✅ | `POST /meta_cognition`（或对应端点） | 生成自我评估/洞察记录；不越权 | db_effect |
| FR-N5 | 系统描述构建与校验 | ✅ | `POST /system-description/build`、`POST /system-description/check` | 构建成功；校验返回一致/差异结论 | db_effect |
| FR-N6 | 技能热加载 | ✅ | `POST /skills/reload` | 重载成功；日志可见技能数；不影响进行中会话 | log_contains |

## 十七、O 组 编排内核专项（新框架特有，用于证明"约束被用满"）

| 编号 | 原功能 | 状态 | 操作 | 预期答案 | 断言字段 |
|------|-------|------|------|---------|---------|
| FR-O1 | 唯一编排形态 | ✅ | 图路径运行时查日志 | `session graph built: fast→understand→gate⇄execute→summarize`；检查点 `AsyncPostgresSaver ready` | log_contains |
| FR-O2 | 状态可序列化 | ✅ | 内核回放用例 9 | 收口状态违规清单为空 | （回放断言） |
| FR-O3 | 领域对象不进状态 | ✅ | 代码检索 `kernel_state.py` | 无领域类型引用；状态仅含基础类型与标识 | — |
| FR-O4 | 挂起跨进程 | ✅ | 见 FR-E2 | 新进程可读取并续接 | suspend_resume |
| FR-O5 | 事件同源 | ✅ | 见 FR-F4 | 进度由节点迁移派生，图内无第二事件通道 | log_contains |
| FR-O6 | 端口可降级 | ✅ | 内核回放用例 8 | 宿主未提供端口时按能力缺失降级，不报错 | （回放断言） |
| FR-O7 | 回归语料常驻 | ✅ | `session_graph_replay.py` | 11 项全部 PASS（快速短路/工具回环/上限/门禁/挂起/并行/事件/端口/序列化/契约校验） | （回放断言） |
| FR-O8 | 版本上界锁定 | ✅ | 查 `requirements.txt` 与容器 `pip show langgraph` | 声明 `>=1.2,<2.0`；实装 1.2.11 落在区间内 | — |

## 十八、复现目标清单（当前预期为"不符合"的 7 项）

| 编号 | 缺失功能 | 原实现位置 | 修复建议 | 修复后转 |
|------|---------|-----------|---------|---------|
| FR-E4 | 写操作二次确认 | `confirm_dialog`（`loop.py:813`）、更老链路 `ConfirmQueue` | 在图内 M6 门禁后增设确认判定节点 | ✅ |
| FR-E5 | 待确认事件注入与确认消费 | `_pending_event_injection`（`loop.py:207`） | 与 FR-E4 同批实现（同一链路） | ✅ |
| FR-H4 | 群聊注入 | `_group_injections`（`loop.py:203`） | 统一为一个"上下文注入"节点 | ✅ |
| FR-H5 | （同 FR-E5） | 同上 | 同上 | ✅ |
| FR-E5/FR-H5 关联项 | 能力结果结算分支差异 | `_settle_capability_result`（`loop.py:197`） | 输出新旧分支对照表后决定补齐或废弃 | ✅ |
| FR-F5 | 归档段缺换行 | 归档追加逻辑 | 追加前补换行 | ✅ |
| FR-I6 | 图路径附件端到端 | — | 补一条端到端用例证据 | ✅ |

> 另有 2 项为语义差异（FR-G6 行级过滤 fail-open、FR-G8 等级编号口径差）与 1 项经决策休眠（FR-C9 专家评审），不列为缺陷，但需在其修复或启封后复测。

## 十九、附录

### 19.1 与既有语料的关系

本文用例与 `emily-data/golden/session_loop_cases.yaml` 使用同一套断言词汇。可自动化的用例（A1-A5、C2、C5、G4、E1、F2）建议追加为语料条目，用同一回放器执行：

```
uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --report emily-data/golden/report_new.json
```

当前该语料仅 5 例（chitchat 2 / business 2 / permission 1），距"齐全"差距较大，建议以本文分组为骨架扩充。

### 19.2 执行命令模板

```bash
# 内核专项回放（11 项）
docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/session_graph_replay.py

# 语料回放
uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --report emily-data/golden/report_new.json

# 入站消息（真实用户 UUID）
curl -s -X POST http://localhost:18080/api/v1/message/send -H "X-Emily-Token: $EMILY_API_TOKEN" -H "Content-Type: application/json" -d '{"conversation_id":"fr-a1","sender":"林建辉","sender_id":"0551d8cc-b584-4b19-843e-25dbd0287240","content":"你好"}'

# 库核对
docker exec emily-postgres psql -U emily -d emily -c "SELECT count(*) FROM events;"

# 日志与归档
docker logs --since 5m emily-core | Select-String 'graph path|gate=True|capability'
Get-ChildItem 'emily-data\session_archives' | Sort-Object LastWriteTime -Descending | Select-Object -First 3
```

### 19.3 判定与记录建议

每题记录：实际输出（回复全文/接口响应/库内行）、与预期差异、结论（复现/部分/未复现）。测试完成后按 req-verify 流程出报告，并把 7 项复现目标作为独立的缺陷编号登记。

---

*本文用例均对应原系统的真实实现（方法名、工具名、SOP 编号、接口路径、作业行均可回溯），未凭空构造功能项。*
