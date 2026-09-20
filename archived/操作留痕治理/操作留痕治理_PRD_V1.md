# 操作留痕治理 — PRD（规格）V1

> **模块标识**：操作留痕治理
> **版本**：V1
> **日期**：2026-09-16
> **基于需求基线**：[操作留痕治理_需求基线_V1.md](操作留痕治理_需求基线_V1.md)（FR-1~FR-15、NFR-1~NFR-7、约束 1~7、决策 Q1~Q18 已全部确认，无遗留待决策项）
> **状态**：待评审
> **与本项目其他需求的关系**：独立需求线，不与「检索通道治理」「LangGraph 编排内核化」合并；属**治理层**，不改内核形态——不触碰 `session/loop.py` 与 LangGraph 执行引擎（约束 13 内核冻结）

## 一、概述

**问题**：Emily 的留痕挂在**入口层**而非统一执行层，导致三个后果——① 同一动作从不同入口走，有无留痕取决于该入口有没有人写过（文件删除从 IM 走无痕，从 console 走有痕；脚本建节点全程无痕）；② console 因挂载点缺失而在路由层**自行造一份**，绕过 service 直接触达 repo / tool / provider（upload / rag-index / rag-search 三处），违反约束 14；③ "这条记录是真实发生还是后台测试产生"这一信息**只在请求进入那一刻存在**，现有靠 `console_chat_` 字符串前缀的手段覆盖不了无消息的操作（上传 / 入库 / 密级 / 脚本），且留痕表无关联键，事后不可推。

**目标**：把留痕下沉到统一执行层建立**单一挂载点**（动作本身留痕，而非按入口补录），并在其上补齐**归因四元组**与**流量性质**维度，使留痕完整、归因可答、真实与测试可结构性分离。

**范围**：`emily_core/services/` 动作层挂载点与动作边界定义；service 返回约定收敛（三态判定前置）；上下文注入机制（contextvars）与四类入口注入；console 三个绕过端点的 `service` 能力补齐与回接；`business_event_logs` 等留痕表的维度对齐与 `source` 索引；`ConsoleLogRepo` 的分辨与过滤；存量回填与文档同步。

**不范围**：`EvolutionLogWriter` 三原则（非阻断 / 截断 / 异步）与 `EventJournal` md 双写的存废；Session 归档正文机制；日志物理清理与生命周期治理；日志面板 UI 重设计（仅加过滤开关）；`scripts/` 下 44 个 CLI 脚本的逐个改造；**入口分流与测试流量拦截**（统一的是日志模型与执行层，不是调用入口）。

## 二、用户故事与验收标准

### US-01 动作即留痕（单点挂载）
**AC-US-01.1** 留痕记录点**唯一**位于 `emily_core/services/` 的动作方法；`api/routes/`、`application/`、`scripts/` 下对留痕写入器的调用数**为 0**。
**AC-US-01.2** 同一个动作从 IM 与 console 分别触发，**两条均产生留痕**（现状：IM 侧文件删除无痕）。
**AC-US-01.3** 一次用户动作在 `business_event_logs` 中**只产生一条**记录（service 内部互调不重复留痕）。
**AC-US-01.4** 新增一个入口（渠道）时，留痕侧**不需要写任何代码**——只要该入口经过统一执行层并注入一次上下文。

### US-02 三态可判定
**AC-US-02.1** 动作类 service 返回**可机械判定**的结果（`success` + `reason_code`），挂载点据此区分「成功 / 失败（异常）/ 被拒（规则或权限不通过）」。
**AC-US-02.2** 非上传人、非 L5/L6 用户尝试改密级，产生一条**"被拒"留痕并含拒绝原因**（现状：仅 `logger.info`，无留痕）。
**AC-US-02.3** **请求尚未进入动作**的参数校验拒绝（`file_id` 为空、密级值域非法）**不产生留痕**——校验位置 ≠ 留痕位置。
**AC-US-02.4** 动作抛异常时记「失败」，且**非阻断**：留痕失败不影响业务返回（NFR-1）。

### US-03 流量性质结构化
**AC-US-03.1** `business_event_logs` 具备 `source`（`user`/`ops`/`auto`/`test`）、`channel`、`channel_account`、`result`、`error_reason`，`source` 建有索引。
**AC-US-03.2** `source` 记的是**行为性质**，不是入口类型：console 模拟对话记 `test`（尽管它走的是真实 IM 入口），console 直接操作与脚本 CLI 记 `ops`，调度与系统内部记 `auto`。
**AC-US-03.3** `channel` 仅在 `source=user` 时必填；`ops` / `auto` / `test` 允许为空，不被强制要求携带通讯工具。
**AC-US-03.4** `actor` 缺失时记 `actor=unknown` 并**保留记录**，不得静默不留痕（现状：`_delete_file` 为 `if operator_id:` 才写）。

### US-04 入口只注入、不记录
**AC-US-04.1** IM 入口（`core.handle_message`）从 `StandardMessage` 提取渠道身份并置 `source=user`，覆盖 QQ / 企微 / 小程序 / 邮箱四渠道，**不改任何 SOP 或工具**。
**AC-US-04.2** console 入口经路由层统一依赖注入（模拟对话 → `test`，直接操作 → `ops`），**各端点不手工传参**。
**AC-US-04.3** 调度入口注入 `auto`、脚本 CLI 入口注入 `ops` 且 `channel_account` 记操作载体（`cli` / `console`）。
**AC-US-04.4** 上下文载体为 `contextvars`：两个会话（或一个会话 + 一个调度作业）并发执行状态变更动作，各自留痕的 `source` / `channel` / `actor` **互不串扰**（现状：类级可变字典会互相覆盖）。

### US-05 console 缺口回接统一执行层
**AC-US-05.1** `/console/upload`、`/console/rag-index`、`/console/rag-search` **先补齐 service 能力**，再由 console 与 IM/Tool 同源调用；路由层不再直调 repo / tool / provider。
**AC-US-05.2** 三者**不做端点级补录**——留痕由挂载点自动产生。
**AC-US-05.3** `files` / `knowledge_chunks` **不纳入**日志聚合白名单。

### US-06 消费端分辨与过滤
**AC-US-06.1** 提供**统一分辨方法**供日志面板、行为分析、进化闭环共用，**读字段而非 grep 字符串前缀**。
**AC-US-06.2** 日志面板提供"排除测试流量"开关；行为分析与进化闭环**默认仅取 `user`**（排除 `test` / `ops` / `auto`）。
**AC-US-06.3** `emy-test` 直发 HTTP 产生的留痕**可被识别为非 `user`**，不依赖调用方自觉加记号。

### US-07 系统与调度同归因
**AC-US-07.1** `scheduler_job_logs` 补 `source`（`auto`）与 `actor`（"系统"或作业标识）。
**AC-US-07.2** `auto` 类操作统一登记，与业务事件处于同一归因模型。

### US-08 留痕失败可观测
**AC-US-08.1** "留痕写失败"有计数或记录，纳入 `scripts/self_check.py` 体检项。
**AC-US-08.2** "未记"与"记失败"外部**可区分**。

### US-09 防自激
**AC-US-09.1** 连续刷新日志面板 / 轮询 `/llm-trace` 五分钟，"日志聚合"条数**不因观测行为增长**；SSE keep-alive 帧不留痕。
**AC-US-09.2** service 内部互调不产生额外留痕（与 US-01.3 同源）。

### US-10 存量回填与文档同步
**AC-US-10.1** 存量 `business_event_logs` 按 `event_action` 前缀回填 `source`（`console_*` → `ops`；其余按是否经 IM 渠道判定为 `user`，无法判定 → `auto`）。
**AC-US-10.2** `messages.event_id` 前缀迁移至 `source`（`console_chat_` → `test`，其余 `user`）；前缀保留作兼容，不再作为判定依据。
**AC-US-10.3** 抽样核对回填后的 `source` 取值正确；同步更新 `docs/数据库设计.md`、`docs/接口协议与调用约定.md`。

### US-11 链路不变
**AC-US-11.1** 不新增任何拦截、分流或请求路径变更——console 操作**不得**包装为 IM 消息投递，测试流量**不得**在入口被拦截。
**AC-US-11.2** 主链路（消息处理、SOP 执行）无新增告警。

## 三、术语

引用需求基线 §四（统一执行层、动作边界、挂载点、三态、流量性质四分类、渠道与来源标识、暗记、分辨方法、归因四元组），不在此复述。本规格中额外使用的简写：

| 简写 | 含义 |
|------|------|
| 动作类 service | 产生状态变更的 service 公开方法（留痕挂载对象）；查询类 service 不在其列 |
| 挂载点 | 留痕写入的唯一位置（动作类 service 方法，经统一装饰器 / 写入器落地） |
| 分辨方法 | 消费端读取"这条留痕属于哪类行为"的统一入口（读 `source` 字段） |

## 四、约束（约束型技术决策）

纳入本需求范围的约束型技术决策共 13 条：

1. **挂载点唯一**：留痕记录点唯一在动作类 service；入口层禁止直接调用留痕写入器（发现即回归）。
2. **console 不得绕过统一执行层**：console 动作类能力必须复用 service 层能力，不得在路由层复刻或直调 repo / tool / provider。
3. **不新建重复层**：挂载点复用 `emily_core/services/`，不新建"留痕服务层"或"操作层"（分层保持 C2：API → EmilyCore → Session → WorkItem → Application → Service → Repository）。
4. **入口不合并、不隔离**：console 操作不得包装为 IM 消息投递；也不得在入口拦截测试流量。入口仅**注入标记**。
5. **标识必须结构化**：流量性质须落库为字段；**禁止**以字符串前缀 / 消息体记号作为判定依据。
6. **上下文载体必须为 `contextvars`**：不得沿用类级可变字典；须覆盖 `asyncio.to_thread` / executor 的上下文传递。
7. **三态口径固定**：动作已开始后的规则/权限拒绝须记；请求尚未进入动作的参数校验拒绝不记。
8. **先补 service 能力再接线**：console 三个绕过端点不得以"端点补录留痕"方式收口。
9. **`actor` 缺失不得静默**：记 `unknown` 并保留记录。
10. **权限模型不改**：IM 走 L1–L6 与项目可见范围，console 走后台视角，互不让渡。
11. **不新建重复表**：优先 `_PENDING_COLUMNS` 补列机制。
12. **不触碰内核**：不得为落实本需求修改 `session/loop.py` 或 LangGraph 执行引擎（约束 13）。
13. **挂载方式统一注册**：留痕以统一装饰器 / 写入器形态挂载，**不得**逐个方法手写写入代码（约束 12 的功能注册接入原则）。

**触碰的架构铁律**：约束 2（分层不可跳）、约束 12（功能注册接入）、约束 13（内核冻结）、约束 14（console 复用既有能力）。
**触碰的质量红线**：无残留（迁移后入口层不得留旧调用点）、接线闭合（新增字段必须有写入方与读取方）。

## 五、非功能需求

1. **非阻断不变**：沿用 `EvolutionLogWriter`「失败只告警、截断（Text 5000 / String 500）、`asyncio.to_thread` 异步」。
2. **主链路零额外开销**：留痕不引入额外 LLM 调用；运维动作不走意图路由与 ReAct 循环。
3. **可过滤**：`source` 建索引，过滤查询不因新增字段劣化。
4. **可回退**：新增字段与注入逻辑可整体关闭，回落到当前行为。
5. **不改业务语义**：`project_events` 不得混入运维动作。
6. **入口零留痕代码**：新增入口时留痕侧无需写代码。
7. **不改链路**：不改变请求路径、不新增拦截或分流。

## 六、非目标

1. 不重构 `EvolutionLogWriter` 与非阻断原则。
2. 不替换 `EventJournal` md 双写（存废另案）。
3. 不做日志聚合面板 UI 重设计（仅加过滤开关）。
4. 不做日志归档压缩与生命周期治理。
5. 不改造 `session_archives` 归档正文机制。
6. 不逐个改造 `scripts/` 下 CLI 脚本。
7. 不做入口分流、测试流量拦截或链路隔离。
8. 不引入 console 认证（Q17：立为独立后续项）。

## 七、验收与度量

**验收场景**（对应基线 §九）：

| # | 场景 | 判定 |
|---|------|------|
| 1 | 挂载点唯一 | `api/routes/`、`application/`、`scripts/` 下留痕写入器调用数为 0 |
| 2 | 同源同痕 | 文件删除从 IM 与 console 各触发一次，两条均有痕且 `source` 可区分 |
| 3 | 脚本留痕 | 运行 `scripts/manage_nodes.py` 建/改节点，产出 `source=ops` 的留痕（现状：零记录） |
| 4 | 无消息操作可分辨 | 上传 / RAG 入库 / 密级调整 / 脚本变更无 message，仍可按 `source` 分辨 |
| 5 | 真实/测试可分离 | `emy-test` 直发 HTTP 的留痕可识别为非 `user`；`WHERE source='user'` 即真实样本 |
| 6 | 渠道溯源 | QQ 与企微同用户各一条指令，`channel` / `channel_account` 可区分且与 `user_im_bindings` 一致 |
| 7 | 三态覆盖 | 越权改密级记"被拒"（含原因）；参数校验类拒绝不记 |
| 8 | 防自激 | 刷新面板 / 轮询 llm-trace 五分钟，条数不增长；一次动作只留一条 |
| 9 | 并发不串扰 | 两会话并发动作，`source` / `channel` / `actor` 不串 |
| 10 | 不回归 | 主链路无新增告警；请求路径与拦截行为无变化 |
| 11 | 回填正确 | 抽样核对存量 `source` 取值正确 |

**度量**：
1. **留痕覆盖率** = 已挂载的动作类 service 方法数 ÷ 动作类 service 方法总数（目标：100%，清单见附录 A）。
2. **`source` 分布**：按 `user` / `ops` / `auto` / `test` 的日/周分布，用于确认测试流量未混入 `user`。
3. **三态分布**：成功 / 失败 / 被拒 的占比，用于观察越权尝试是否被捕获。
4. **留痕写失败计数**（FR-12 体检项）。

## 八、风险与开放问题

| 风险 | 说明 | 缓解 |
|------|------|------|
| 返回约定收敛面大 | `services/` 下有 50 余个模块，返回形态混杂（dict / ORM / `raise` / `None`） | **只收敛动作类 service**（写操作），查询类一律不动；分批推进，不为收敛而重构查询路径 |
| 装饰器与同步 service + 异步写入的调度冲突 | service 全 sync，可能运行在线程内（`asyncio.to_thread` / executor），装饰器内不宜直接 `await` 留痕 | 写入以"非阻断投递"实现；须明确调度策略（见开放问题 3） |
| 上下文在 executor 中丢失 | `asyncio.to_thread` 会复制上下文，但 `loop.run_in_executor` 不传播 contextvars | 约束 6：显式覆盖线程切换路径，并做并发验收（场景 9） |
| 装饰器导致重复留痕 | service 内部互调（如 `node_batch` → `node_state_machine`） | 动作边界判定规则（FR-15）+ 验收场景 8 |
| 防自激遗漏 | 查询类默认不记，但"高风险读"清单未定 | 见开放问题 2；默认不记（fail-safe） |
| 回填准确率无基线 | 存量数据只能按前缀与渠道推断 | 无法判定者一律归 `auto`（保守）；抽样核对（验收场景 11） |
| console 无认证 | `ops` / `test` 的 `operator_id` 由请求体自述 | Q17 已显式声明：**不具对外审计效力**，仅作内部参考；认证立为独立后续项 |
| 直调 service 的新代码 | 后续新增路由若继续直调 repo，会再次绕开挂载点 | 约束 1 / 2 写入本规格；在 code review 与残留扫描中检查 |

**开放问题**：
1. **动作类 service 的完整清单**：附录 A 为已核实锚点，需在计划阶段补全（判定标准：产生数据写入或状态迁移的 service 公开方法）。
2. **"高风险读"清单**：全量导出、文件下载、跨项目查询之外是否还有（FR-2 的例外项）。
3. **非阻断投递的调度方式**：装饰器在 sync 方法内如何投递异步写入（`ensure_future` 需事件循环；线程内无循环）。属方案问题，交计划阶段。
4. **`_PENDING_COLUMNS` 新增 5 列的落地**：是否需迁移脚本、`error_reason` / `channel_account` 的长度与截断口径。
5. **回填的抽样比例与判定口径**：抽样多少条、由谁核对。
6. **`source` 是否需要回填 `node_events` / `scheduler_job_logs` 的存量**（基线 FR-13 只写了 `business_event_logs` 与 `messages`）。

## 附录 A 动作分类与挂载点清单（初版，已核实锚点）

| 动作 | 当前实现位置 | 入口覆盖现状 | 目标挂载点 |
|------|-------------|-------------|-----------|
| 文件密级调整 | `services/file_service.py` `update_confidentiality` | console（service 内无痕） | 该方法 |
| 文件软删除 | `services/file_manager.py` `soft_delete` | IM 与 console 同调，**仅 console 有痕** | 该方法 |
| 文件上传归档 | **无 service**（console 直调 `FileRepository.create`） | 仅 console，无痕 | 新增 service 能力 |
| 知识入库 | `tools/embed_tool.py` `handle_embed_and_index`（console 直调 tool） | console 无痕 | 新增 service 能力，tool 与 console 同源 |
| 知识检索 | `providers/rag/pgvector_provider.py`（console 直调 provider） | console 无痕 | 新增 service 能力 |
| 节点批量创建 | `services/node_batch.py` `create_node_tree` | 脚本 / 系统工具，均无痕 | 该方法 |
| 节点批量更新 / 激活 / 废弃 / 进度 / 关联 | `services/node_batch_update.py` `batch_*` | 脚本 / 系统工具，均无痕 | 各方法 |
| 事件录入 / 确认 / 取消 | `services/event_service.py`（留痕现挂 `application/event_app.py`） | IM | `create_pending_event` / `confirm_event` / `cancel_event` |
| 任务、会议类动作 | `services/task_service.py` / `meeting_service.py`（留痕现挂 `application/task_app.py` / `meeting_app.py`） | IM | 对应动作方法（**方法名待计划阶段核实**） |

> 说明：本表只列**已核实**的位置。"待补全"部分由计划阶段按开放问题 1 的判定标准补齐，补齐前不得实施挂载。

## 附录 B 交接给计划阶段的方案型问题

以下属实现方案，不在本规格内求解：

1. 留痕挂载的统一形态：装饰器 vs 写入器中间层；如何识别"动作类"方法（显式声明 / 命名约定 / 白名单注册）。
2. 动作类 service 返回约定的目标形态与错误码 → 三态的映射表；`raise` 型动作如何归类。
3. 非阻断异步写入在 sync service 内的调度方式（开放问题 3）。
4. contextvars 的载体设计（变量定义、`bind` / `reset` 时机、与既有多层调用链的关系）。
5. 四类入口注入点的具体落点（console 的"统一依赖 / 中间件"形态；脚本 CLI 的统一注入位置，是否需要 ScriptManager 侧统一处理而非改 44 个脚本）。
6. console 三个端点所需 service 能力的接口设计（文件上传归档 / 知识入库 / 知识检索），以及与 `tools/embed_tool`、`PgVectorRagProvider` 的关系（tool 是否改为调用 service）。
7. `business_event_logs` 新增 5 列的定义与 `_PENDING_COLUMNS` 落地方式。
8. 分辨方法的接口形态与消费端接入点（日志面板、行为分析、进化闭环各自的调用位置）。
9. 存量回填的执行方式（脚本 or 一次性迁移）与抽样核对口径。
10. 分批实施顺序（基线 §十二 的四批）在每个批次内的模块级任务拆解。

---

*本规格与需求基线逐条对应：US-01~US-11 覆盖 FR-1~FR-15 与 NFR-1~NFR-7，Q1~Q18 已写入第四节约束；实施批次沿用基线 §十二。*
