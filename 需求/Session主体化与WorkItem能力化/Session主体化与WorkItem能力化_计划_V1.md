# Session 主体化与 WorkItem 能力化 — 概要设计（SD）

> **基于规格（PRD）**：[Session主体化与WorkItem能力化_PRD_V1.md](file:///d:/app/Emily/需求/Session主体化与WorkItem能力化/Session主体化与WorkItem能力化_PRD_V1.md)（`Session主体化与WorkItem能力化_PRD_V1.md`）
> **宪法版本**：v1.1
> **设计版本**：v1.0
> **级别**：System Design（概要设计）
> **目标**：在不改写旧会话链路的前提下，以并行模块实现"会话唯一主循环 + SOP 能力化 + 两级编排 + 挂起/确认对话化 + 灰度双轨"。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **系统设计师** + **异步运行时工程师**（自适应补充角色：因涉及 asyncio 并发/超时治理与 LangGraph 复用边界），严格按以下模块顺序设计，逐模块验收，验证不通过不进入下一个模块。具体代码实现在编码阶段落地，本设计给出接口契约和实现约束。

---

## 硬约束（违反即失败）

> 项目铁律直接引用 [宪法 §2](../../.trae/skills/_shared/constitution.md)，**不自行发明**。

1. **禁止修改已有接口签名**：本设计对现有文件只做「新增方法 / 新增分支 / 新增模块」，不改任何现有方法签名（例外：无）。
2. **遵循宪法铁律**，逐条自检并列出**本设计涉及**的铁律及遵守方式：

| 铁律 | 本设计如何遵守 |
|------|--------------|
| C0 根治而非迁就 | 不新增挂起/确认的特例通道，而是把二者收敛为"能力调用 + 对话状态"（D3/D7）；不为兼容旧形态保留第二套语义 |
| C2 分层不可跳 | 新模块全部落在 Session 层（`session/`），未跨越 WorkItem/Application/Service/Repository；链路仍为 `API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`。SOP 能力内部经 `SessionScheduler`（WorkItem 层）→ 现有 LangGraph 图 |
| C3 SOP 即路由 | **显式变更**（PRD §4.1）：M8 改写为"SOP 即能力"，随主循环转默认生效 |
| C5 结构化输出优先 | **显式变更**（PRD §4.1）：M8 改写其派发范式前提的措辞 |
| C6 Sync repo + to_thread | 新模块不直接访问 Repository；如需读数据一律经 Service/现有 async 封装 |
| C8 唯一执行引擎 | SOP 能力的内部实现复用现有 `_workitem_graph`（`core._workitem_graph`），不另起执行引擎 |
| C10 工具必须带参数 schema | M2 组装的能力工具与 M6 的控制工具全部携带 JSON Schema；缺 schema 视为不合格 |
| C11 功能注册接入 | SOP 能力经 M3 的 `CapabilityRegistry` + `register_capabilities()` 注册通道接入；新脚本经 `emily-data/config/scripts_registry.yaml` 注册 |
| C12 Session 主循环冻结 | 本需求属"对话机制本身的一次性升级"（PRD §4.1）；M8 落地该约束文本，完成后主循环冻结 |

3. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告。
4. **遵循接口契约**：跨模块调用只经约定接口，不允许绕过接口直接操作对方内部数据。
5. **不私自扩需**：发现规格缺陷**回退 req-review**，不在本设计里新增/改写需求。
6. **接线闭合（宪法 §3 Q6）**：任何产出物必须有消费者（见"接线矩阵"）；无消费者不得进入编码。

---

## PRD 约束落实

> 逐条回应 PRD §4.4 的**约束型技术决策**。

| # | PRD 约束（原文摘录） | 本设计如何遵守 | 落在哪个模块 | 若无法遵守 |
|---|-------------------|--------------|------------|-----------|
| 1 | 必须复用现有工单执行引擎作为重能力的内部实现，不得另起执行引擎 | SOP 能力执行器构造 WorkItem 后调用现有 `SessionScheduler._run_one()` → `core._workitem_graph`，不新建图/不新建引擎 | M3 | — |
| 2 | 必须复用现有工具装配的权限过滤通道与分级兜底门禁，不得新造权限判定 | 复用 `tool_adapter.build_tool_specs` / `_session_api_ids`（fail-closed）与 `FallbackPolicy.resolve/assert_write_allowed`；SOP 能力可见性复用既有 `SessionContext.sop_allow` 字段 | M2 | — |
| 3 | 新增能力必须经注册通道接入，禁止裸调用 | SOP 能力经 `CapabilityRegistry` + `register_capabilities(core)`（对称于 `tools/registry.py::register_all`）；脚本经 `scripts_registry.yaml` | M3, M9 | — |
| 4 | 新主循环必须以**并行模块**形态与旧路径并存，不得原地改写旧会话处理链路 | 新建 `session/loop.py` + `session/loop_pool.py`；`SessionAgent` / `SessionPoolManager` / `workitem/**` **零改动**；`EmilyCore.handle_message` 仅**新增分派分支**（旧分支代码原样） | M1, M8 | — |
| 5 | 归档以会话轮次为唯一锚点，轮次记录内嵌能力调用清单；不得新增独立的第二套能力执行记录存储 | 扩展现有 `SessionArchiveWriter`：新增纯函数 `render_capability_section()`，由 M1 在轮次归档段内追加；**不新增表、不新增 repo** | M7 | — |
| 6 | 挂起状态仅存会话内存，不得为本需求引入新的挂起持久化机制 | 挂起注册表为**会话内内存对象**（随 Session 生命周期），无落库、无外部存储 | M5 | — |
| 7 | 能力调用计划必须是与工单计划相互独立的计划形态，不得与旧工单拆分计划混用同一数据形态 | 新建 `CapabilityPlan` / `CapabilityStep`，**不复用** `WorkItemPlan`；`orchestrator.py` 不被改造 | M4 | — |
| 8 | 能力失败以结构化失败结果回灌对话，不得声明能力执行的原子性；部分完成状态如实回传 | `CapabilityResult.status ∈ {success, partial, failed}`，失败/超时/部分完成统一转结构化结果回灌，不抛异常到循环外 | M3, M1 | — |

**未落实约束清单**：无。

---

## 追溯矩阵（US → 模块）

> 基准：PRD §2.2 US 清单（US-01 ~ US-10）。**必须覆盖全部 US。**

| US-ID | 需求一句话 | 实现模块 | 覆盖状态 |
|-------|-----------|---------|---------|
| US-01 | 会话唯一主循环，回复直接产出，循环有界可收敛 | M1 | ✅ |
| US-02 | 一 SOP 一能力，内部质量机制保留，实体不泄露 | M2, M3 | ✅ |
| US-03 | 查询能力直达对话，仍受可见范围约束 | M1, M2 | ✅ |
| US-04 | 写操作护栏：权限过滤 + 分级兜底 + 高危不出自由集 | M2 | ✅ |
| US-05 | 任务级粗排 + 循环照单执行 + 进度可见 | M4（+M1 执行） | ✅ |
| US-06 | 挂起对话化，多轮续接，重启失效为已接受缺口 | M5（+M1 执行） | ✅ |
| US-07 | 确认/取消归位对话行为，复用现有确认存储 | M6（+M1 执行） | ✅ |
| US-08 | 归档锚定会话轮次，内嵌能力调用清单 | M7 | ✅ |
| US-09 | 全局开关 + 按 SOP 放开的灰度，旧路径最终下线 | M8 | ✅ |
| US-10 | 成本不回归 + golden 语料四断言验收 | M9 | ✅ |

**缺失清单**：无。

---

## 系统架构概览

### 架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                          EmilyCore (handle_message)                   │
│                                                                       │
│   [M8 SessionPathRouter]  ── 读 config.session_loop_enabled/allowlist │
│         │                                                             │
│         ├── 关（默认）──→ 旧链路（零改动，原样保留）                    │
│         │                   SessionPoolManager → SessionAgent          │
│         │                     └→ orchestrator → SessionScheduler       │
│         │                          → _workitem_graph（LangGraph）      │
│         │                                                             │
│         └── 开 ──────→ [M1 SessionLoopPool / SessionLoop]  ★新主体      │
│                          │                                            │
│                          ├─[M2 CapabilityCatalog] 工具集装配（权限裁剪）│
│                          │     ├ 查询/写能力 ← tools/registry.py 现有   │
│                          │     ├ SOP 能力    ← [M3 CapabilityRegistry] │
│                          │     └ 控制工具    ← [M6 ConfirmDialog]      │
│                          ├─[M4 SessionPlanner / CapabilityPlan] 粗排    │
│                          ├─[M5 SuspendRegistry] 挂起（内存）           │
│                          └─[M7 render_capability_section] 轮次归档      │
│                                       │                               │
│                                       ▼                               │
│                     [M3 SopCapabilityRunner] ── 复用现有执行引擎        │
│                        WorkItem → SessionScheduler._run_one            │
│                        → core._workitem_graph（SOP/质量门/专家/兜底/审计）│
└──────────────────────────────────────────────────────────────────────┘
                    [M9 scripts/golden_session_loop.py] ← 回验全部 US
```

### 分层关系

| 新模块 | 所在分层 | 上层依赖 | 下层被依赖 |
|--------|---------|----------|-----------|
| M3-SOP 能力执行 | Session（能力执行侧） | 现有 WorkItem 层（`SessionScheduler`/`_workitem_graph`） | M2 |
| M2-能力目录与工具集装配 | Session | 现有 `tools/registry.py`、`tool_adapter`、`skill.registry`、M3 | M1 |
| M4-能力调用计划与粗排 | Session | 无（纯数据结构 + LLM 规划） | M1 |
| M5-挂起与续接 | Session | LLM 客户端 | M1, M2 |
| M6-确认对话化 | Session | 现有 `EventApplication.handle_confirmation` | M1, M2 |
| M1-会话主循环 | Session（主体） | M2, M4, M5, M6, M7 | EmilyCore（入口分派） |
| M7-轮次归档内嵌 | Service（扩展现有） | 无 | M1 |
| M8-灰度开关与治理同步 | Session + 配置 + 文档 | M1 | EmilyCore（入口分派） |
| M9-验收语料与度量 | 脚本层（ScriptManager） | M1, M8 | req-verify |

---

## 数据流设计

### 主流程（单条消息，新路径）

```
用户消息 → EmilyCore.handle_message → M8 SessionPathRouter
   ├ 关 → 旧链路（SessionAgent，原样）
   └ 开 → M1 SessionLoop.handle
            ├ ① 快速短路（问候/感谢/告别/自我介绍）→ 直接回复
            ├ ② 装配工具集（M2：查询/写能力 + SOP 能力 + 控制工具，按 actor 权限裁剪）
            ├ ③ 主循环：
            │     LLM(chat_with_tools) ─┬─ text ────────────────→ 收敛 → 回复
            │                           ├─ tool_call(查询/写) ──→ 执行 → tool_result 回灌
            │                           ├─ tool_call(SOP 能力) ─→ M3 → WorkItem→图→CapabilityResult → 回灌
            │                           └─ tool_call(控制) ──────→ M5 挂起 / M6 确认
            ├ ④ 多能力协作：M4 CapabilityPlan 粗排 → 循环逐项调用（层内并行/层间顺序）
            └ ⑤ 轮次收口：M7 追加能力调用清单段 + 回复
```

### 核心流程

| 流程 | 触发条件 | 参与者 | 数据流向 | 异常路径 |
|------|---------|--------|---------|---------|
| 闲聊直答 | 消息命中快速短路词表 | M1 | 无 LLM 调用 → 回复 | 无 |
| 单能力调用 | 循环内 LLM 发起 tool_call | M1→M2→M3→WorkItem→图 | 能力名+入参 → CapabilityResult → tool_result 回灌 | 超时/失败 → `CapabilityResult(status=failed)` |
| 多能力粗排执行 | LLM 判定需多能力协作 | M1→M4→M1→M3 | 需求 → `CapabilityPlan` → 逐层调用 → 成果回灌 → 按结果重排 | 前置失败 → 下游跳过；计划失效 → 重排 |
| 缺参挂起 | 能力返回 `need_input` | M1→M5 | 问题文本 → 对话提问 → 挂起态（内存） | 新话题 → 挂起作废 |
| 确认交互 | 存在待确认项且用户表达确认/取消 | M1→M6→EventApplication | pending 项注入 → 确认/取消 → 回复 | 事件已处理 → 可读说明 |
| 轮次归档 | 轮次结束（有回复） | M1→M7 | 能力调用记录 → 归档 md 段 | 归档失败不阻断（fail-open） |

---

## 模块依赖图

```
M3(SOP能力执行) ──→ M2(能力目录/工具集) ──→ M1(会话主循环) ←── M4(计划/粗排)
                                            ↑
M5(挂起与续接) ─────────────────────────────┤
M6(确认对话化) ─────────────────────────────┤
M7(轮次归档内嵌) ───────────────────────────┘
M8(灰度开关/治理) ─────────────────────────────→ 门控 M1

M9(验收语料) 独立（回验 US-01 ~ US-10，不参与构建顺序）
```

> 无循环依赖。构建顺序：M3 → M2 → {M4, M5, M6, M7} → M1 → M8 → M9。

---

## 交付物总览

| 模块 | 实现的 US | 交付物类型 | 新增/修改 | 核心接口/类/表 |
|------|----------|-----------|----------|---------------|
| M1 | US-01, US-03 | 主循环 + 池 | 新增 | `SessionLoop`, `SessionLoopPool`, `session/loop.py`, `session_loop.md` |
| M2 | US-02, US-03, US-04 | 目录 + 装配器 + 脚本 | 新增 | `CapabilityCatalog`, `CapabilityEntry`, `scripts/capability_catalog.py` |
| M3 | US-02 | 能力执行器 + 注册表 | 新增 | `SopCapabilityRunner`, `CapabilityRegistry`, `CapabilityResult`, `short_sop_id`, `SYSTEM_INTERNAL_SOPS` |
| M4 | US-05 | 计划结构 + 规划器 | 新增 | `CapabilityPlan`, `CapabilityStep`, `PlanCursor`, `SessionPlanner` |
| M5 | US-06 | 挂起注册表 | 新增 | `SuspendRegistry`, `PendingCall` |
| M6 | US-07 | 确认对话组件 | 新增 | `ConfirmDialog` |
| M7 | US-08 | 归档渲染扩展 | 修改（新增方法） | `SessionArchiveWriter.render_capability_section()`, `CapabilityCallRecord` |
| M8 | US-09 | 路径分派 + 配置 + 文档 | 新增 + 修改 | `SessionPathRouter`, 3 个 config 项, `CLAUDE.md` 改写 |
| M9 | US-10 | golden 语料脚本 | 新增 | `scripts/golden_session_loop.py` |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/__init__.py` | 修改 | ① `__init__` 新增属性 `_capability_registry` / `_session_loop_pool` 初始化（默认 None）；② `_ensure_initialized` 末尾新增 `register_capabilities(self)` 调用（新增调用，不动现有调用）；③ `handle_message` 内新增"M8 路径分派"分支（旧分支代码与顺序不变） |
| `emily-core/emily_core/config.py` | 扩展 | 新增 3 个配置项：`session_loop_enabled` / `session_loop_sop_allowlist` / `capability_call_timeout_seconds` |
| `emily-core/emily_core/bootstrap.py` | 修改 | env → config 映射新增 `EMILY_SESSION_LOOP_ENABLED` / `EMILY_SESSION_LOOP_SOP_ALLOWLIST` / `EMILY_CAPABILITY_CALL_TIMEOUT_SECONDS` |
| `emily-core/emily_core/services/session_archive_writer.py` | 扩展 | 新增 `@staticmethod render_capability_section(calls, prompt_info=None) -> str`（纯函数）；**不改**现有方法签名 |
| `emily-data/config/scripts_registry.yaml` | 扩展 | 注册 `capability_catalog`、`golden_session_loop` 两条脚本元信息（含 params schema） |
| `CLAUDE.md` | 修改 | §6 约束 #3 改写为"SOP 即能力"、#5 措辞改写、新增"会话主循环冻结"约束（M8，随 Phase 2 生效） |
| `emily-core/emily_core/workitem/langgraph_engine/**` | 不变 | SOP 能力内部复用现有图 |
| `emily-core/emily_core/workitem/scheduler.py` | 不变 | `SessionScheduler` 构造签名与 `_run_one` 已满足单 WI 独立调用 |
| `emily-core/emily_core/session/session_agent.py` | 不变 | 旧路径原样保留（US-09.3 二期下线时才删除） |
| `emily-core/emily_core/session/orchestrator.py` | 不变 | 旧编排器不动；M4 新建独立计划形态（PRD §4.4-7） |
| `emily-core/emily_core/adapters/session/session_pool.py` | 不变 | 旧池不动；M1 提供并行池 |
| `emily-core/emily_core/tools/registry.py` | 不变 | SOP 能力走 M3 独立注册入口，不改动旧工具注册面 |

---

## 接线矩阵（Producer → Consumer）

| 产出物 | 类型 | 生产模块 | 消费方（模块 / 调用点） | 接线点 | 状态 |
|--------|------|---------|----------------------|--------|------|
| `SessionLoop.handle()` | 公开方法 | M1 | EmilyCore 入口分派 | `EmilyCore.handle_message` 新分支 | ✅ |
| `SessionLoopPool.route()` | 公开方法 | M1 | M8 `SessionPathRouter` | 分派后调用 | ✅ |
| `CapabilityCatalog.build_tool_specs()` | 公开方法 | M2 | M1 | 主循环装配工具集 | ✅ |
| `CapabilityCatalog.list_capabilities()` | 公开方法 | M2 | M1 进度展示 / `scripts/capability_catalog.py` | 日志与 CLI | ✅ |
| `CAPABILITY_TWO_STAGE_THRESHOLD` | 常量 | M2 | M2 装配时告警 + `capability_catalog.py --check` | `logger.warning` / CLI 退出码 | ✅ |
| `CapabilityRegistry` + `register_capabilities()` | 注册表 + 注册入口 | M3 | EmilyCore `_ensure_initialized`；M2 读取 | 注册 + 枚举 | ✅ |
| `CapabilityPlan` / `PlanCursor` | 数据结构 | M4 | M1 主循环逐项执行 | 循环内 `next_runnable/mark_*` | ✅ |
| `SuspendRegistry` / `PendingCall` | 会话内状态 | M5 | M1 挂起与续接判定 | 循环内 register/match/claim | ✅ |
| `ConfirmDialog` + 控制工具 spec | 组件 + 工具 spec | M6 | M1（注入待确认项 + 执行）、M2（spec 装配） | 循环内调用 | ✅ |
| `render_capability_section()` / `CapabilityCallRecord` | 归档渲染 | M7 | M1 轮次收口 | `append_section(path, ...)` | ✅ |
| `SessionPathRouter` | 分派器 | M8 | EmilyCore `handle_message` | 入口分支判定 | ✅ |
| `session_loop_enabled` / `session_loop_sop_allowlist` | 配置项 | M8 | M8 `SessionPathRouter` | 开关判定 + 准入清单 | ✅ |
| `capability_call_timeout_seconds` | 配置项 | M8 | M1 | `asyncio.wait_for` 超时值 | ✅ |
| `scripts/capability_catalog.py` | 脚本 | M2 | 运营/开发者（`scriptmgr run`） | ScriptManager | ✅ |
| `scripts/golden_session_loop.py` | 脚本 | M9 | req-verify（US-10 回验） | ScriptManager + 报告文件 | ✅ |
| CLAUDE.md 约束改写 + 运维口径 | 文档 | M8 | 开发者 / 运维 / 缺陷治理清单 | 文档正文 | ✅ |

**未接线清单**：无。

---

## 断线模式自查（宪法 Q6 / C11）

| 断线模式 | 本次是否新增 | 消费方已接线？ | 接线点 / 证据 |
|---------|------------|--------------|--------------|
| 新增配置项 | 是（3 个） | 是 | `session_loop_enabled`/`session_loop_sop_allowlist` → M8 `SessionPathRouter`；`capability_call_timeout_seconds` → M1 `wait_for` |
| 新增工具 / 公开方法 | 是（`SessionLoop.handle`、`CapabilityCatalog.build_tool_specs`、`SopCapabilityRunner.run` 等） | 是 | 见接线矩阵第 1–12 行 |
| 新增 DB 列 | 否 | — | 本设计无数据模型变更 |
| 新增文件产出 | 是（归档 md 能力段、golden 报告） | 是 | 归档段 → 运维/审计抽样核对；golden 报告 → req-verify |
| 新增注册项（能力 / 脚本） | 是 | 是 | 能力 → `CapabilityRegistry`（M2 枚举 + 循环装配）；脚本 → `scripts_registry.yaml`（ScriptManager 可达） |

---

## 独立脚本架构设计

### 独立脚本清单

| # | 脚本（建议命名） | 职责 | 关键参数 | `--dry-run` 行为 |
|---|----------------|------|---------|------------------|
| 1 | `scripts/capability_catalog.py` | 枚举/核对能力目录（SOP 能力 + 查询/写能力 + 控制工具），校验与注册面一致 | `--list` `--json` `--check` `--actor-id` | `--check` 即只读校验（无写操作，天然 dry） |
| 2 | `scripts/golden_session_loop.py` | golden 语料回放 + 四类断言 + token/延迟度量 | `--corpus` `--path old\|new` `--report` `--dry-run` | 只打印计划执行的语料与断言清单，不发请求 |

### 聚合薄壳

| # | 脚本（建议命名） | 串联逻辑 |
|---|----------------|---------|
| 1 | — | 本需求无跨脚本编排需求，不设聚合壳（避免无消费者的薄壳） |

### 脚本交互关系

```
scripts/capability_catalog.py（CLI 单跑）
  └── import emily_core.session.capability_catalog.run_catalog()
        → dict（系统调用通道 / 报告）

scripts/golden_session_loop.py（CLI 单跑）
  ├── --path old → 走旧链路会话入口（基线度量）
  ├── --path new → 走新主循环（对照度量）
  └── --report → 输出 json（四类断言 + token/延迟），供 req-verify 消费
```

> 两个脚本均须在 `emily-data/config/scripts_registry.yaml` 注册（含 `params` schema），否则 Web 控制台不可用（CLAUDE.md #10b）。

---

## M3: SOP 能力执行模块（CapabilityRunner）

> 先建 M3：它是 M2 的数据来源，且复用现有引擎是最高风险点，应尽早验证。

**依赖**：无（复用现有 WorkItem 层）

**实现的需求**：US-02（AC-US-02.1, AC-US-02.3, AC-US-02.4）

**层级**：Session（能力执行侧）；内部下沉至 WorkItem 层

**职责**：把"一个启用中的 SOP"包装为一个可被会话循环调用的能力：内部构造 WorkItem 并复用现有 LangGraph 引擎执行，对外只回传成果（结构化 + 可读文本），不暴露 WorkItem 实体与状态。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `CapabilityResult` | dataclass | `status: str`(success\|partial\|failed), `summary: list[str]`, `data: dict`, `business_object_no: str`, `issues: list[str]`, `readable_text: str`, `needs_input: bool=False`, `question: str=""`, `elapsed_ms: int=0` | 能力对外唯一成果形态 |
| `SopCapabilityRunner` | class | `__init__(self, core, skill_registry, config)` | 构造 |
| `run()` | 方法 | `async def run(self, sop_id: str, user_input: str, *, message, db_message_id: str, actor_snapshot: dict, session_context) -> CapabilityResult` | 执行单个 SOP 能力；**不抛异常**（异常转 `status=failed`） |
| `CapabilityRegistry` | class | `register(name: str, tool: BusinessFlowTool) -> None` / `get(name)` / `list_names()` / `list_by_kind(kind: str)` | 独立注册表（不污染旧 `_business_flow_tools`） |
| `register_capabilities()` | 函数 | `def register_capabilities(core) -> None` | 唯一注册入口，枚举 `SkillRegistry.list_sops()` 生成 SOP 能力工具 |

#### 依赖接口（本模块需要调用哪些已有接口）

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `SkillRegistry.list_skills() -> list[SopDoc]` | `skill/registry.py` | 枚举启用中 SOP（加/删 `.md` 即注册/停用，沿用 C3 语义） |
| `SessionScheduler.__init__(session_id, bus=None, session_context=None, core=None)` | `workitem/scheduler.py` | 构造调度器（签名已满足，不改） |
| `SessionScheduler._run_one(wi, message=None, db_message_id="")` | `workitem/scheduler.py` | 单 WI 独立执行（走 `core._workitem_graph`，含质量门/专家评审/兜底/审计） |
| `WorkItem(...)` + `transition_to` | `workitem/workitem.py` | 构造能力执行载体 |
| `StructuredResult` | `workitem/pipeline/interfaces/execution.py` | 读取执行成果，映射为 `CapabilityResult` |
| `FallbackPolicy.gate(actor)` | `langgraph_engine/agent/fallback_policy.py` | 兜底档位（写入 WI.fallback_tier） |

#### 关键约束（能力边界的实现口径）

| # | 约束 | 实现口径 |
|---|------|---------|
| B1 | 不暴露 WorkItem 实体 | `CapabilityResult` 不含 `wi.id`/`state`/`sop_id` 之外的状态词；`readable_text` 由 `structured_result.summary_facts` 拼装，禁止出现工单编号/状态机词汇（AC-US-02.1） |
| B2 | 否决即不出成果 | 质量门/专家评审否决时，图内不进 `summarizing` 成果，`CapabilityResult.status=failed` 且 `issues` 带否决理由（AC-US-02.3；R6 否决效力保留） |
| B3 | 审计口径不变 | 复用现有图的审计链路（Hook/事件流），不新增审计写入点（AC-US-02.4） |
| B4 | 超时由调用方包裹 | M3 自身不设超时；超时由 M1 `asyncio.wait_for` 施加，M3 的 `try/finally` 保证 `wi` 落到终态（避免残留非终态 WI） |

#### 能力准入与命名契约（v1.1 新增）

**实测约束（2026-09-11，生产库 + 代码核对）**：sop_id 在既有系统中存在**三种形态**，不可假定统一：

| 来源 | 形态 | 例 | 权威性 |
|------|------|----|--------|
| `sop_business_flows.sop_id`（唯一约束） | 短形 | `SOP-002-REC` | **权限权威**：`sop_allow` / `auth_engine._get_sop_flow` 均以该表为准 |
| `experts.sop_id` | 全 stem | `SOP-012-SYS-expert_review` | 专家绑定 |
| `SkillRegistry._scan`（`p.stem`） | 全 stem | `SOP-002-REC-event_record` | SOP 目录（LLM 现状所见）+ `_load_sop_text` glob |
| `sop_business_flows.sop_file_name` / 历史日志 | 混合 | `SOP-001-REC.skill.yaml` / `SOP-001` | 历史遗留，不作为契约 |

**命名契约**：

| 字段 | 取值 | 理由 |
|------|------|------|
| `CapabilityEntry.name` / 能力工具名 | **短形**（`short_sop_id(stem)`，取前 3 段） | 与 `SessionContext.sop_allow` 同形（否则权限过滤恒为空）；与 PRD/基线 Q1 结论一致 |
| 传给 `WorkItem.sop_id` 的执行 id | **文件 stem 全文** | 保持 `_match_expert`（experts 表为全 stem）与 `_load_sop_text` glob 现有行为不变 |

**准入契约（能力 ≠ 所有 SOP）**：

| 规则 | 口径 |
|------|------|
| 准入来源 | `sops/*.md` 扫描出的 stem 集合（沿用 C3「放 `.md` 即注册」语义） |
| 排除集 `SYSTEM_INTERNAL_SOPS` | `{"SOP-000-SYS", "SOP-012-SYS", "SOP-999-SYS"}` —— 系统/元规范/图形内触发/工具直调兜底，**非用户可调用能力**（SOP-999 的职责由新主循环直接调工具自然吸收；旧路径仍需该 SOP，**不得删除其 .md**） |
| 一致性闸门 | `scripts/capability_catalog.py --check` 断言：`sops/*.md 总数 == 准入能力数 + 排除集命中数`；出现孤儿（既不在能力表也不在排除集）即退出码非 0 |

### 数据模型

**无数据模型变更**：不新增表、不改已有表（PRD §4.4-5 禁止第二套执行记录存储；能力执行记录落归档 md，见 M7）。

### 模块验收检测

```bash
# 验收 1：模块可导入、注册入口存在
uv run python -c "from emily_core.session.capability_runner import SopCapabilityRunner, CapabilityResult, CapabilityRegistry, register_capabilities; print('ok')"
→ 预期输出：ok

# 验收 2：注册入口已接线（宪法 Q6 静态可达性）
grep -rn "register_capabilities" --include=*.py emily-core/emily_core
→ 预期输出：≥2 处命中（定义处 + EmilyCore._ensure_initialized 调用处）；仅 1 处 = 未接线，FAIL

# 验收 3：能力与 SOP 一一对应、可枚举
uv run python scripts/scriptmgr.py run capability_catalog --args "--list"
→ 预期输出：能力清单中 SOP 能力条目数 == `emily-data/sops/SOP-*.md` 文件数（差值须为 0）

# 验收 4：实体不泄露（AC-US-02.1 静态断言）
grep -rn "WorkItem\|wi\." emily-core/emily_core/session/capability_runner.py
→ 预期输出：仅出现在内部构造/读取处；`CapabilityResult` 字段定义区无 WorkItem 字段

# 验收 5：能力失败不抛异常（结构化回灌）
uv run python -c "
import asyncio
from emily_core.session.capability_runner import SopCapabilityRunner
# 用不存在的 sop_id 调用，断言返回 status=failed 而非抛异常
print('ok')"
→ 预期输出：ok（无异常上抛）

# 验收 6：能力准入一致性（v1.1 新增，宪法 Q6）
uv run python scripts/capability_catalog.py --check
→ 预期输出：退出码 0；输出 `sops=N, capabilities=M, excluded=K` 且 N == M + K；出现 orphans 行即 FAIL

# 验收 7：命名契约（短形用于权限、stem 用于执行）
uv run python -c "
from emily_core.session.capability_runner import short_sop_id
assert short_sop_id('SOP-002-REC-event_record')=='SOP-002-REC'
assert short_sop_id('SOP-999-SYS-fallback')=='SOP-999-SYS'
print('ok')"
→ 预期输出：ok

# 验收 8：排除集已接线（定义 + 消费，宪法 Q6）
grep -rn "SYSTEM_INTERNAL_SOPS" --include=*.py emily-core scripts
→ 预期输出：≥2 处命中（定义处 + 注册/CLI 消费处）；仅 1 处 = 死常量，FAIL
```

**失败处理**：验收 2 不通过 → 检查 `EmilyCore._ensure_initialized` 的注册调用是否落在 `register_all(self)` 之后；验收 3 计数不一致 → 检查 `SkillRegistry._scan()` 的 `SOP-*.md` glob 口径与目录解析（`/app/sops` 优先）；验收 5 抛异常 → 检查 `run()` 是否遗漏 `except Exception` 包装与 `wi` 终态兜底。

---

## M2: 能力目录与工具集装配模块（CapabilityCatalog）

**依赖**：M3

**实现的需求**：US-02（AC-US-02.2）, US-03（AC-US-03.1/03.3）, US-04（AC-US-04.1/04.2）

**层级**：Session

**职责**：把"会话可见的能力"装配为 LLM 工具集，并施加权限与写护栏；提供能力目录枚举（供循环、进度展示与运营核对）。**不执行**任何能力。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `CapabilityEntry` | dataclass | `name: str`, `kind: str`(sop\|query\|write\|control), `sop_id: str`, `display_name: str`, `description: str`, `write_mode: str`, `parameters: dict` | 目录条目 |
| `CapabilityCatalog` | class | `__init__(self, core, skill_registry, business_tools, capability_registry)` | 构造（依赖注入，便于单测） |
| `list_capabilities()` | 方法 | `def list_capabilities(self, actor_snapshot: dict, session_context) -> list[CapabilityEntry]` | 按当前操作者权限裁剪后的目录 |
| `build_tool_specs()` | 方法 | `def build_tool_specs(self, actor_snapshot: dict, session_context, *, fallback_tier: str="basic") -> list[dict]` | 产出 LLM 可见工具集（OpenAI function 格式） |
| `list_capability_names()` | 方法 | `def list_capability_names(self, actor_snapshot, session_context) -> set[str]` | 供执行期白名单校验（fail-closed） |

#### 目录合成与裁剪规则

| 能力类别 | 来源 | 可见性裁剪（fail-closed） |
|---------|------|------------------------|
| `query`（只读） | 现有 `tools/registry.py` 注册的只读工具（`query_data`/`knowledge_search`/`chat_archive` 等） | `session_api_ids`（`SessionContext.available_tools` 的 `api_id` 集合） |
| `write`（执行手脚） | 现有注册的写工具（`record_*`/`node_*`/`file_*`…） | `session_api_ids` + `FallbackPolicy.resolve(tier, with_write=True)` + `FallbackPolicy.assert_write_allowed` |
| `sop` | M3 `CapabilityRegistry`（name = SOP 编号） | `session_context.sop_allow`（非空时取交集）+ `session_loop_sop_allowlist`（M8 灰度准入） |
| `control` | M6 `ConfirmDialog` 的确认控制工具 | 始终可见（非业务能力，不经权限过滤） |

| 护栏规则 | 口径 | 对应 AC |
|---------|------|--------|
| 高危不出自由集 | `write_mode ∈ {overwrite, delete}` 的能力**不进入** `build_tool_specs` 结果，只能经对应 SOP 能力完成 | AC-US-04.1 |
| 查询不受写门禁 | `write_mode == read` 的能力不经过 `assert_write_allowed` | AC-US-03.2 |
| 查询受可见范围 | 只读能力内部沿用现有可见集解析（`VisibleFileSetResolver` / RAG 可见集），M2 不新增过滤逻辑 | AC-US-03.3 |
| 分级兜底不变 | 普通档只读、高级档可追加写；越权写拒绝并回可读说明 | AC-US-04.2 |

#### 模块级常量（两段式加载阈值）

| 常量 | 值 | 消费方 |
|------|----|--------|
| `CAPABILITY_TWO_STAGE_THRESHOLD` | `20` | ① M2 `build_tool_specs` 在能力数 > 阈值时 `logger.warning`（记录"需评估两段式"）；② `scripts/capability_catalog.py --check` 超阈值时输出提示 |

> 说明：PRD 风险 2 / 附录 B-4 要求"当前全量暴露，阈值由计划阶段定，现在不实现两段式"。本设计**仅定义阈值 + 两个真实读取点**（满足 Q6 接线闭合），**不实现**两段式加载逻辑。

#### 依赖接口（本模块需要调用哪些已有接口）

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `build_tool_specs(business_tools, resolvers, session_api_ids, *, fallback_mode, fallback_tier)` | `langgraph_engine/agent/tool_adapter.py` | **复用**权限过滤与 spec 构造（不重写） |
| `_session_api_ids(ctx)` | 同上 | 取当前操作者可见工具集合 |
| `FallbackPolicy.resolve / assert_write_allowed / gate` | `langgraph_engine/agent/fallback_policy.py` | 分级兜底门禁 |
| `WriteMode` | `tools/definitions.py` | 判定 `read/append/overwrite/delete` |
| `SkillRegistry.list_skills()` | `skill/registry.py` | SOP 目录展示名与说明 |
| `CapabilityRegistry.list_names()` | M3 | SOP 能力枚举 |

> **实现注记**：`build_tool_specs` 现有实现会追加 WI 生命周期控制工具（`complete_work`/`ask_user`）。新循环的控制工具语义不同（M5/M6），因此 M2 调用时**不使用**其控制工具追加结果，仅取其业务工具 + resolver 部分，控制工具由 M2 自行追加。为不改既有签名，M2 侧对返回列表按名过滤 `CONTROL_TOOL_NAMES` 后追加自有控制工具。

### 数据模型

**无数据模型变更。**

### 模块验收检测

```bash
# 验收 1：模块可导入
uv run python -c "from emily_core.session.capability_catalog import CapabilityCatalog, CapabilityEntry, CAPABILITY_TWO_STAGE_THRESHOLD; print('ok')"
→ 预期输出：ok

# 验收 2：阈值常量已接线（定义 + 读取，宪法 Q6）
grep -rn "CAPABILITY_TWO_STAGE_THRESHOLD" --include=*.py emily-core scripts
→ 预期输出：≥2 处命中（定义处 + M2 装配读取处 / CLI --check）；仅 1 处 = 死常量，FAIL

# 验收 3：高危能力不出自由集（AC-US-04.1）
uv run python scripts/capability_catalog.py --list --json | uv run python -c "
import json,sys
caps=json.load(sys.stdin)['capabilities']
bad=[c['name'] for c in caps if c.get('write_mode') in ('overwrite','delete')]
print('leaked:', bad)"
→ 预期输出：leaked: []（空列表）

# 验收 4：权限裁剪生效（越权不出现）
uv run python scripts/capability_catalog.py --list --actor-id <L1用户UUID> | wc -l
uv run python scripts/capability_catalog.py --list --actor-id <L4用户UUID> | wc -l
→ 预期输出：L1 行数 < L4 行数（低权限可见能力更少）

# 验收 5：复用而非新造权限判定（AC 约束 §4.4-2）
grep -rn "build_tool_specs\|FallbackPolicy" emily-core/emily_core/session/capability_catalog.py
→ 预期输出：≥2 处命中（确用了现有通道）；若 0 处 = 自造权限判定，FAIL
```

**失败处理**：验收 3 出现泄露 → 检查 `write_mode` 是否在注册时正确申报（`tools/registry.py::_tool` 的 `write_mode` 参数）；验收 4 行数相同 → 检查 `_session_api_ids` 的 `actor_snapshot` 传递链（群聊多用户越界为历史缺陷 BUG #3）；验收 5 为 0 → 停止，禁止自造权限判定。

---

## M4: 能力调用计划与粗排模块（CapabilityPlan）

**依赖**：无（数据结构 + LLM 规划）；被 M1 消费

**实现的需求**：US-05（AC-US-05.1/05.2/05.3/05.4）

**层级**：Session

**职责**：把"一条需要多能力协作的消息"粗排为带依赖的能力调用计划，并提供**步进式**游标 API，使执行权保留在会话循环手中（D7：任务包是执行依据，不是交付物）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `CapabilityStep` | dataclass | `step_id: str`, `capability: str`, `params: dict`, `depends_on: list[str]`, `objective: str`, `status: str="pending"`, `result: CapabilityResult\|None=None` | 单个调用步 |
| `CapabilityPlan` | dataclass | `plan_id: str`, `steps: list[CapabilityStep]`, `source: str`(llm\|rule), `max_depth: int` | 能力调用计划（**独立于 `WorkItemPlan`**） |
| `PlanCursor` | class | `__init__(self, plan: CapabilityPlan, max_depth: int=3)` | 步进游标 |
| `next_runnable()` | 方法 | `def next_runnable(self) -> list[CapabilityStep]` | 返回当前可执行层（所有前置已 DONE）；无可执行且仍有 pending → 视为依赖不可满足 |
| `mark_done()` | 方法 | `def mark_done(self, step_id: str, result: CapabilityResult) -> None` | 标记成功 |
| `mark_failed()` | 方法 | `def mark_failed(self, step_id: str, result: CapabilityResult) -> None` | 标记失败并级联 SKIPPED 下游 |
| `append()` | 方法 | `def append(self, step: CapabilityStep, *, depth: int) -> bool` | 动态追加（超 `max_depth` 返回 False） |
| `progress_text()` | 方法 | `def progress_text(self) -> str` | 简版进度文本（AC-US-05.3） |
| `SessionPlanner` | class | `__init__(self, llm_client, config)` | 规划器 |
| `plan()` | 方法 | `async def plan(self, user_input: str, capability_names: set[str], context_hint: str="") -> CapabilityPlan \| None` | 任务级粗排；返回 None 表示单能力/无需规划 |

#### 语义契约（沿用现有 DAG 语义，AC-US-05.1）

| 语义 | 口径 | 参照源 |
|------|------|--------|
| 层内并行 | `next_runnable()` 返回的同一层可并行调用（M1 用 `asyncio.gather`） | `SessionScheduler.run_dag`（`workitem/scheduler.py:129-155`） |
| 层间顺序 | 下一层必须等本层结束 | 同上 |
| 前置失败下游跳过 | `mark_failed` 级联 `depends_on` 命中者为 `skipped` | 同上（`:174-183`） |
| 依赖不可满足 | `next_runnable()` 空且仍有 pending → 全部置 `skipped`（防死循环） | 同上（`:135-142`） |
| 动态追加 | 步完成后由 M1 按结果决定 `append()`，受 `max_depth` 约束 | 同上（`:191-208`） |

#### 边界契约（R5 / D5：两级编排互不越界）

| 约束 | 口径 |
|------|------|
| 粗排只在能力边界间 | `CapabilityStep.capability` 只能取 `capability_names`（M2 产出的可见能力集）中的名字；出现未知能力即丢弃该步 |
| 粗排不进入能力内部 | `CapabilityPlan` 不含工具级步骤；能力内部（WorkItem 图的细排）对会话不可见 |
| 细排失败上浮 | 能力失败以 `CapabilityResult(status=failed)` 冒泡；由 M1 决定重试/换能力/放弃（AC-US-05.4） |

### 核心算法/策略

| 算法/策略 | 用途 | 选型理由 | 备选方案 |
|----------|------|---------|---------|
| LLM 单次 `chat_json` 产出 steps（含 `needs_previous`） | 任务级粗排 | 与现有意图识别同构（结构化 JSON），零新依赖；计划形态与旧 `WorkItemPlan` 解耦（PRD §4.4-7） | 规则关键词触发（现有 `_ORDER_CONNECTORS`）——保留为 `plan()` 返回 None 时的兜底判据，但不作为主路径 |
| 步进式 `PlanCursor` 而非整体移交执行 | 保证执行权/纠错权留在循环 | 直接落实 D7（禁止"打包扔给第三方自主组合执行"） | 整体 plan 交给执行器 —— **禁止**（即派发范式复辟） |

### 数据模型

**无数据模型变更**（计划为会话内内存对象，不落库）。

### 模块验收检测

```bash
# 验收 1：模块可导入
uv run python -c "from emily_core.session.capability_plan import CapabilityPlan, CapabilityStep, PlanCursor, SessionPlanner; print('ok')"
→ 预期输出：ok

# 验收 2：未复用旧工单计划形态（PRD §4.4-7）
grep -rn "WorkItemPlan" emily-core/emily_core/session/capability_plan.py emily-core/emily_core/session/loop.py
→ 预期输出：0 处命中（新路径不得引用 WorkItemPlan）

# 验收 3：DAG 语义正确（层内并行 / 层间顺序 / 失败跳过）
uv run python -c "
from emily_core.session.capability_plan import CapabilityPlan, CapabilityStep, PlanCursor
from emily_core.session.capability_runner import CapabilityResult
p=CapabilityPlan('p1',[CapabilityStep('s1','A',{},[]),CapabilityStep('s2','B',{},['s1'])],'rule',3)
c=PlanCursor(p); assert [s.step_id for s in c.next_runnable()]==['s1']
c.mark_failed('s1',CapabilityResult(status='failed',summary=[],data={},business_object_no='',issues=['x'],readable_text=''))
assert p.steps[1].status=='skipped'; print('ok')"
→ 预期输出：ok

# 验收 4：执行权未移交（D7 静态断言）
grep -rn "class .*Executor" emily-core/emily_core/session/
→ 预期输出：0 处（不出现"整体接收计划并自行执行"的执行器类）；执行只发生在 M1 循环内
```

**失败处理**：验收 2 命中 → 立即改为独立形态（混用即换位半途而废）；验收 3 断言失败 → 对照 `run_dag` 的层判定与失败级联重写；验收 4 出现 Executor 类 → 设计返工（违反 D7）。

---

## M5: 挂起与多轮续接模块（SuspendRegistry）

**依赖**：无；被 M1/M2 消费

**实现的需求**：US-06（AC-US-06.1/06.2/06.3/06.4）

**层级**：Session（会话内内存状态）

**职责**：把"能力缺参/需用户决策"表达为对话中的待回答调用，并处理续接判定与多挂起归属。**纯内存、不落库**（D3）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `PendingCall` | dataclass | `call_id: str`, `capability: str`, `params: dict`, `question: str`, `initiator_user_id: str`, `step_id: str="", `created_at: str=""` | 待回答的能力调用 |
| `SuspendRegistry` | class | `__init__(self, llm_client=None, config=None)` | 每 Session 一个实例 |
| `register()` | 方法 | `def register(self, pending: PendingCall) -> None` | 挂起登记 |
| `is_continuation()` | 方法 | `async def is_continuation(self, user_input: str, actor_user_id: str) -> bool` | 续接判定（相关则续、无关则新话题；犹豫时倾向 False） |
| `match()` | 方法 | `def match(self, actor_user_id: str) -> PendingCall \| None` | 取该操作者最近一条挂起（谁发起谁确认） |
| `claim()` | 方法 | `def claim(self, call_id: str, actor_user_id: str) -> PendingCall \| None` | 认领并从挂起表移除（转入续接执行） |
| `discard_all()` | 方法 | `def discard_all(self) -> int` | 话题切换时作废全部挂起 |
| `pending_count` | 属性 | `-> int` | 挂起数 |

#### 续接判定契约

| 分支 | 口径 | 对应 AC |
|------|------|--------|
| 相关则续 | 用户消息与挂起提问相关 → `True`；M1 取 `claim()` 后以补充信息继续同一能力 | AC-US-06.1 |
| 无关则新话题 | 不相关 → `False`；M1 调 `discard_all()` 后按新话题处理 | AC-US-06.2 |
| 多挂起归属 | `match/claim` 按 `initiator_user_id` 过滤（谁发起谁确认） | AC-US-06.3 |
| 重启失效 | 内存态天然失效；M8 运维口径书面记载（非回归缺陷） | AC-US-06.4 |

### 数据模型

**无数据模型变更**（PRD §4.4-6：不得引入新的挂起持久化机制）。

### 模块验收检测

```bash
# 验收 1：模块可导入且无持久化依赖
uv run python -c "from emily_core.session.suspend_registry import SuspendRegistry, PendingCall; print('ok')"
→ 预期输出：ok

# 验收 2：无落库（PRD §4.4-6）
grep -rn "Repository\|to_thread\|\.commit(\|session.add" emily-core/emily_core/session/suspend_registry.py
→ 预期输出：0 处命中（纯内存）

# 验收 3：多挂起归属正确（AC-US-06.3）
uv run python -c "
from emily_core.session.suspend_registry import SuspendRegistry, PendingCall
r=SuspendRegistry()
r.register(PendingCall('c1','SOP-A',{},'问题A','user-1'))
r.register(PendingCall('c2','SOP-B',{},'问题B','user-2'))
assert r.match('user-2').call_id=='c2' and r.match('user-1').call_id=='c1'
print('ok')"
→ 预期输出：ok

# 验收 4：话题切换作废（AC-US-06.2）
uv run python -c "
from emily_core.session.suspend_registry import SuspendRegistry, PendingCall
r=SuspendRegistry(); r.register(PendingCall('c1','SOP-A',{},'问题A','u1'))
n=r.discard_all(); assert n==1 and r.pending_count==0; print('ok')"
→ 预期输出：ok
```

**失败处理**：验收 2 命中落库 → 立即移除（违反 D3 书面决策）；验收 3 归属错乱 → 检查 `match/claim` 是否按 `initiator_user_id` 过滤（勿用全局最近一条）。

---

## M6: 确认/取消对话化模块（ConfirmDialog）

**依赖**：无；被 M1/M2 消费

**实现的需求**：US-07（AC-US-07.1/07.2）

**层级**：Session；存储侧复用 Application 层现有链路

**职责**：把"待确认项"从工单特例改为会话循环内的对话行为；确认/取消动作走现有确认存储链路。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `ConfirmDialog` | class | `__init__(self, journal=None)` | 构造 |
| `fetch_pending()` | 方法 | `def fetch_pending(self, conversation_id: str) -> object \| None` | 取待确认项（复用现有 repo 查询） |
| `prompt_injection()` | 方法 | `def prompt_injection(self, pending) -> dict \| None` | 产出注入主循环 system/user 的提示块（含编号/内容/引导） |
| `handle()` | 方法 | `async def handle(self, action: str, event_id: str, confirmed_by: str) -> str` | 执行确认/取消，返回可读回复 |
| `confirm_spec` / `cancel_spec` | 工具 spec 常量 | `dict` | 暴露给循环的控制工具（含 JSON Schema） |

#### 依赖接口（本模块需要调用哪些已有接口）

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `EventRepository.find_pending_by_conversation_id(conversation_id)` | `repositories/event_repo.py` | 取待确认事件 |
| `EventApplication.handle_confirmation(event_id, action, confirmed_by)` | `application/event_app.py` | **复用确认存储与状态迁移**（AC-US-07.2：无第二套确认存储） |
| `EventJournal` | `services/event_journal.py` | 复用注入（不可用时保持 fail-open） |

> 实现注记：`handle_confirmation` 的 `action` 取值沿用现有语义（`confirm` / `cancel`），不新增取值。

### 数据模型

**无数据模型变更**（确认记录落现有事件/确认链路，不新增表）。

### 模块验收检测

```bash
# 验收 1：模块可导入 + 控制工具带 schema
uv run python -c "from emily_core.session.confirm_dialog import ConfirmDialog, CONFIRM_SPEC, CANCEL_SPEC; assert CONFIRM_SPEC['function']['parameters']; print('ok')"
→ 预期输出：ok

# 验收 2：复用现有确认链路（AC-US-07.2）
grep -rn "handle_confirmation" emily-core/emily_core/session/confirm_dialog.py
→ 预期输出：≥1 处命中（复用）；0 处 = 自造确认存储，FAIL

# 验收 3：无第二套确认存储
grep -rn "CREATE TABLE\|__tablename__" emily-core/emily_core/session/confirm_dialog.py emily-core/emily_core/infrastructure/database/models.py | grep -i confirm
→ 预期输出：0 处新增确认表

# 验收 4：控制工具已接线到工具集装配（宪法 Q6）
grep -rn "CONFIRM_SPEC\|CANCEL_SPEC" --include=*.py emily-core/emily_core
→ 预期输出：≥2 处命中（定义处 + M2 装配处）；仅 1 处 = 未接线，FAIL
```

**失败处理**：验收 2 为 0 → 改为复用 `EventApplication.handle_confirmation`，不得自造；验收 4 仅 1 处 → 检查 M2 是否把控制工具追加进 `build_tool_specs`。

---

## M7: 轮次归档内嵌能力调用清单（TurnArchive）

**依赖**：无；被 M1 消费

**实现的需求**：US-08（AC-US-08.1/08.2）

**层级**：Service（扩展现有 `SessionArchiveWriter`）

**职责**：在既有轮次归档段内追加"能力调用清单"，使一条轮次记录同时满足审计与溯源；**不新增第二套执行记录存储**。

### 接口契约

#### 对外接口（全部为新增，不动现有签名）

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `CapabilityCallRecord` | dataclass | `capability: str`, `params_digest: str`, `result_status: str`, `result_digest: str`, `triggered_by: str`, `elapsed_ms: int`, `needs_input: bool=False` | 单次能力调用记录（会话内内存） |
| `render_capability_section()` | 静态方法（新增） | `@staticmethod def render_capability_section(calls: list, prompt_info=None) -> str` | 渲染"### 🔧 能力调用"段 |
| 复用 | — | `SessionArchiveWriter.append_section(path, content)` | 追加写入（现有方法，签名不变） |

#### 渲染契约（AC-US-08.1 的五要素）

| 要素 | 来源 | 渲染形式 |
|------|------|---------|
| 调了哪些能力 | `CapabilityCallRecord.capability` | `- 能力: SOP-002-REC` |
| 入参摘要 | `params_digest` | `参数: {截断 120 字}` |
| 产出什么 | `result_digest`（来自 `CapabilityResult.summary`） | `成果: 事实1；事实2` |
| 由谁触发 | `triggered_by` | `触发者: {user_name}({user_id})` |
| 成败 | `result_status` | `✓ 成功 / ✗ 失败(原因)` |

> 审计意见（专家评审/质量门）作为**会话参考意见**呈现：沿用现有 `render_turn_end(reply_body, guardian_warnings)` 的"系统审核标记"段，不由 M7 另造。

### 数据模型

**无数据模型变更**（PRD §4.4-5）。

### 模块验收检测

```bash
# 验收 1：新增方法可导入、现有签名未变
uv run python -c "
import inspect
from emily_core.services.session_archive_writer import SessionArchiveWriter as W
print(inspect.signature(W.render_turn_end)); assert hasattr(W,'render_capability_section'); print('ok')"
→ 预期输出：`(reply_body: str, guardian_warnings: str = '')` 后接 `ok`（签名与改造前一致）

# 验收 2：渲染五要素齐全（AC-US-08.1）
uv run python -c "
from emily_core.services.session_archive_writer import SessionArchiveWriter as W, CapabilityCallRecord as R
s=W.render_capability_section([R('SOP-002-REC','{}','success','已记录 EVT-1','张三(u1)',120)])
assert all(k in s for k in ('能力','参数','成果','触发者')); print('ok')"
→ 预期输出：ok

# 验收 3：无第二套执行记录存储（AC-US-08.2）
grep -rn "CapabilityExecution\|capability_executions" --include=*.py emily-core
→ 预期输出：0 处命中

# 验收 4：渲染方法已接线（宪法 Q6）
grep -rn "render_capability_section" --include=*.py emily-core/emily_core
→ 预期输出：≥2 处命中（定义处 + M1 轮次收口调用处）
```

**失败处理**：验收 1 签名变化 → 回退为纯新增（不得改现有方法）；验收 3 命中 → 删除新增存储，改为归档内嵌（PRD §4.4-5）。

---

## M1: 会话主循环模块（SessionLoop）★核心

**依赖**：M2, M4, M5, M6, M7

**实现的需求**：US-01（AC-US-01.1~01.4）, US-03（AC-US-03.1 执行侧）

**层级**：Session（主体）

**职责**：作为唯一连续对话主循环，直接产出回复；装配并调用能力；处理挂起/确认；轮次收口归档。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `SessionLoop` | class | `__init__(self, conversation_id, context: SessionContext, llm_client, catalog: CapabilityCatalog, planner: SessionPlanner, suspends: SuspendRegistry, dialog: ConfirmDialog, archive_writer=None, config=None, capability_runner=None)` | 组合 `SessionContext`（复用其历史/压缩/用量能力） |
| `handle()` | 方法 | `async def handle(self, message: StandardMessage, db_message_id: str="", current_user_id: str="") -> ReplyMessage \| None` | 与旧 `SessionAgent.handle` **同签名**，便于入口分派 |
| `SessionLoopPool` | class | `__init__(self, config: SessionConfig \| None=None, core=None)` | 每 conversation 一实例、TTL 复用、会话内串行（对齐旧池语义） |
| `route()` | 方法 | `async def route(self, message, user_id="", db_message_id="") -> ReplyMessage \| None` | 与旧 `SessionPoolManager.route` **同签名** |

#### 主循环契约

| 环节 | 口径 | 对应 AC |
|------|------|--------|
| 快速短路 | 复用 `session_agent` 模块级词表常量与 `_try_fast_reply` 静态方法（只 import，不改旧文件）；命中即回复，**零 LLM 调用** | AC-US-01.2 |
| 工具集装配 | 每轮经 M2 `build_tool_specs(actor_snapshot, context)` | US-03/04 |
| LLM 调用 | `chat_messages(messages, tools=tool_specs, model=agent_loop_model)`，`max_tokens` 走 `context.cap_max_tokens` | US-01 |
| 工具执行 | 业务工具/resolver 执行语义对齐现有 `tool_node`（权限 fail-closed → 兜底门禁 → handler）；能力工具走 M3 | US-02/03/04 |
| 能力超时 | `asyncio.wait_for(runner.run(...), timeout=config.capability_call_timeout_seconds)`；超时 → `CapabilityResult(status=failed, issues=["能力调用超时"])` 作为 tool_result 回灌 | AC-US-01.4 |
| 循环上限 | `config.agent_loop_max_iterations`（默认 12）；达上限产出**可读收尾回复**而非报错原文 | AC-US-01.3 |
| 回复产出 | 循环内 text 分支直接作为回复；**不调用** `_synthesize_final_reply` 二次合成层 | AC-US-01.1 |
| 上下文管理 | 复用 `SessionContext.get_llm_history()` / `compress_overflow()` / `record_usage()` / `should_compact()` | 依赖 1 |
| 轮次收口 | `archive_writer.append_section(path, render_capability_section(calls))` + `render_turn_end(reply)` | US-08 |

#### 错误/边界契约

| 场景 | 行为 |
|------|------|
| LLM 连续异常 | 计数达 3 次 → 可读失败回复 + 轮次收口（防止无响应） |
| LLM 返回纯文本 | **正常收敛**（新路径下 text 是合法产出，非错误路径）——与旧路径"纯文本必须纠正"语义不同，仅新循环如此 |
| 能力返回 `needs_input` | 经 M5 `register()` 挂起 → 直接回复提问（不调 LLM 改写） |
| 挂起续接 | 回合入口先判 `is_continuation()`：续则 `claim()` 后以补充信息继续；否则 `discard_all()` 按新话题 |
| 能力失败 | `CapabilityResult(status=failed)` 作为 tool_result 回灌，由循环决定重试/换能力/放弃并向用户交代（AC-US-05.4） |
| 归档失败 | fail-open（不阻断回复） |

#### 依赖接口（本模块需要调用哪些已有接口）

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `SessionContext.create(user_id, conversation_id, sender_name, core)` | `session/session_context.py` | 构造会话上下文（复用现有灌注） |
| `SessionContext.get_llm_history()` / `compress_overflow()` / `record_usage()` / `cap_max_tokens()` | 同上 | 上下文与预算复用 |
| `LLMClient.chat_messages(messages, tools=..., model=..., max_tokens=...)` | `infrastructure/llm/client.py` | 主循环 LLM 调用 |
| `load_prompt("session_loop")` | `infrastructure/llm/prompt_loader.py` | 主循环 system prompt 模板 |
| `SessionAgent._try_fast_reply` / `_SIMPLE_*` 常量 | `session/session_agent.py` | 短路复用（只读 import，不改旧文件） |
| `OutboundEventBus.publish("progress", {...})` | `outbound_bus.py` | 进度可见（AC-US-05.3） |
| `SessionArchiveWriter.ensure_header/append_section` | `services/session_archive_writer.py` | 轮次归档 |
| `SessionConfig` | `adapters/session/session_config.py` | 池 TTL/上限复用 |

### 新增 Prompt 模板

| 文件 | 消费者 | 用途 |
|------|-------|------|
| `emily-data/prompts/session_loop.md` | `SessionLoop`（`load_prompt("session_loop")`） | 对话主体 system prompt：人格 + 能力目录占位 + 会话上下文占位 + 行为规则（能直答则直答；需执行则调能力；不暴露能力内部实现） |

> 模板变量至少含：`{sop_catalog}` / `{user_name}` / `{user_company}` / `{user_level}` / `{project_name}` / `{current_datetime}` / `{capability_progress}`。变量替换沿用 `SessionContext.get_prompt_variables()`。

### 数据模型

**无数据模型变更。**

### 模块验收检测

```bash
# 验收 1：模块可导入
uv run python -c "from emily_core.session.loop import SessionLoop, SessionLoopPool; print('ok')"
→ 预期输出：ok

# 验收 2：与旧链路接口同签名（便于入口分派）
uv run python -c "
import inspect
from emily_core.session.loop import SessionLoop
from emily_core.session.session_agent import SessionAgent
print(inspect.signature(SessionLoop.handle)); print(inspect.signature(SessionAgent.handle))"
→ 预期输出：两行签名均为 `(self, message, db_message_id='', current_user_id='')`

# 验收 3：未原地改写旧链路（PRD §4.4-4 静态断言）
git diff --name-only | grep -E "session/session_agent.py|adapters/session/session_pool.py|workitem/langgraph_engine/"
→ 预期输出：空（旧链路零改动）

# 验收 4：二次合成层未被新路径调用（AC-US-01.1）
grep -n "_synthesize_final_reply\|session_reply" emily-core/emily_core/session/loop.py
→ 预期输出：0 处命中

# 验收 5：超时与循环上限已接线
grep -n "capability_call_timeout_seconds\|agent_loop_max_iterations" emily-core/emily_core/session/loop.py
→ 预期输出：≥2 处命中（两个配置项各有读取）

# 验收 6：闲聊零 LLM（AC-US-01.2）
uv run python -c "
from emily_core.session.loop import SessionLoop
from emily_core.session.session_agent import SessionAgent
assert SessionLoop._try_fast_reply('你好')==SessionAgent._try_fast_reply('你好'); print('ok')"
→ 预期输出：ok

# 验收 7：端到端（Docker 内真实链路，需真实用户 UUID）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "<真实用户名>"
→ 预期输出：回复为问候语，且 `emily-data/logs/llm_trace.jsonl` 中该轮**无工具调用、无 LLM 请求**
```

**失败处理**：验收 3 有 diff → 立即回退（违反 PRD §4.4-4）；验收 4 命中 → 删除二次合成调用；验收 6 不等 → 检查是否误改了短路词表；验收 7 出现 LLM 请求 → 检查短路判定是否在 LLM 调用之前。

---

## M8: 灰度开关与治理同步模块（PathSwitch & Governance）

**依赖**：M1

**实现的需求**：US-09（AC-US-09.1/09.2/09.3）

**层级**：Session + 配置 + 文档

**职责**：以全局开关 + 按 SOP 准入清单实现双轨灰度；同步治理文档（约束改写与主循环冻结）与运维口径。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `SessionPathRouter` | class | `__init__(self, config)` | 构造 |
| `use_loop()` | 方法 | `def use_loop(self) -> bool` | 全局开关判定 |
| `allowed_sops()` | 方法 | `def allowed_sops(self) -> set[str] \| None` | 校准后的 SOP 准入清单（None = 全部放开） |
| `is_sop_allowed()` | 方法 | `def is_sop_allowed(self, sop_id: str) -> bool` | 单个 SOP 准入判定（供 M2 过滤能力目录） |

#### 新增配置项（全部有读取方，见接线矩阵）

| 配置项 | 类型 | 默认 | 读取方 |
|--------|------|------|--------|
| `session_loop_enabled` | bool | `False` | M8 `use_loop()` |
| `session_loop_sop_allowlist` | str | `""`（空 = 全部放开） | M8 `allowed_sops()` → M2 目录准入 |
| `capability_call_timeout_seconds` | int | `120` | M1 `asyncio.wait_for` |

#### 灰度语义（AC-US-09 实现口径）

| 场景 | 行为 | 对应 AC |
|------|------|---------|
| 全局开关关（默认） | `EmilyCore.handle_message` 走**旧链路原样**；新模块不被实例化（零影响） | AC-US-09.1（旧侧） |
| 全局开关开 + 清单为空 | 新循环处理全部消息，全部 SOP 能力进入能力目录 | AC-US-09.1（新侧） |
| 全局开关开 + 清单非空 | 仅清单内 SOP 进入能力目录（**准入清单语义**）；清单外 SOP 的能力不被装配，新循环不会对其发起写操作 | AC-US-09.2 |
| 二期验收通过 | 删除旧派发路径（`SessionAgent` 等）与开关，保留单一新路径 | AC-US-09.3 |

> **规格口径登记**：PRD AC-US-09.2 原文为"未被放开的 SOP 走旧路径行为不变"。由于 R2/D6 明确禁止回合开始的显式分类步骤，消息入口**无法**按 SOP 分流；故本设计将清单实现为**能力目录准入清单**（放开 = 该 SOP 能力对新循环可见）。此口径与 AC 的差异点：清单外 SOP 在新循环下**不被调用**（回落为对话直答/询问），而非"走旧路径"。**若此口径不被接受，须回退 req-review 澄清 AC-US-09.2**（属规格问题，不在计划内自行改写）。

#### 治理同步清单（文档交付物）

| # | 文档 | 改动 | 生效时点 |
|---|------|------|---------|
| 1 | `CLAUDE.md §6 约束 #3` | "SOP 即路由" → "SOP 即能力"（措辞改写，含新增 SOP = 放 `.md` 到 `sops/`，其能力随之可被会话调用） | 随 Phase 2 主循环转默认 |
| 2 | `CLAUDE.md §6 约束 #5` | 改写其"命中 SOP → 框架直调"派发前提的措辞 | 同上 |
| 3 | `CLAUDE.md §6` | 新增"会话主循环冻结"约束（PRD 记为 C12）：主循环只因对话机制本身而改，业务功能增长一律落能力层；业务 PR 触达主循环即违规 | 同上 |
| 4 | 运维口径文档（`docs/Manual/` 下新增或追加） | 书面记载"挂起仅存会话内存，进程重启后未完成的能力调用失效，用户需重新提出"，并登记至缺陷治理清单 | Phase 1 结束 |

### 数据模型

**无数据模型变更。**

### 模块验收检测

```bash
# 验收 1：开关已接线（宪法 Q6：定义 + 读取）
grep -rn "session_loop_enabled" --include=*.py emily-core/emily_core
→ 预期输出：≥2 处命中（config.py 定义 + M8 读取）；仅 1 处 = 死开关，FAIL

grep -rn "session_loop_sop_allowlist" --include=*.py emily-core/emily_core
→ 预期输出：≥2 处命中（定义 + 读取）

grep -rn "capability_call_timeout_seconds" --include=*.py emily-core/emily_core
→ 预期输出：≥2 处命中（定义 + M1 读取）

# 验收 2：默认关（灰度安全）—— 不显式开启时旧路径不变
grep -n "session_loop_enabled: bool = False" emily-core/emily_core/config.py
→ 预期输出：1 处命中

# 验收 3：治理文档已改写（文档可达性）
grep -n "SOP 即能力" CLAUDE.md
→ 预期输出：≥1 处命中（约束 #3 新措辞）

grep -n "主循环冻结\|Session 主循环" CLAUDE.md
→ 预期输出：≥1 处命中（新增约束）

# 验收 4：运维口径已记载（AC-US-06.4）
grep -rn "挂起.*重启.*失效\|重启后.*挂起" docs/Manual/*.md
→ 预期输出：≥1 处命中

# 验收 5：双轨可回退（AC-US-09.1）
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core printenv EMILY_SESSION_LOOP_ENABLED   # 未开启
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我记录事件：样板段放线完成" --sender "<真实用户名>"
→ 预期输出：旧路径行为与改造前一致（WorkItem 派发链路，日志含 Scheduler[..] WI .. DONE）
```

**失败处理**：验收 1 仅 1 处命中 → 该配置未接线，必须补读取方或删除（不得留死开关）；验收 3/4 未命中 → 文档未同步，属未完成；验收 5 行为变化 → 回退开关实现（默认必须为关）。

---

## M9: 验收语料与度量模块（GoldenSet）

**依赖**：无（独立）；回验 US-01 ~ US-10

**实现的需求**：US-10（AC-US-10.1/10.2）

**层级**：脚本层（ScriptManager 注册）

**职责**：建立 golden 语料与四类断言回放能力，输出 token/延迟度量与断言报告，供 req-verify 消费。

### 接口契约

#### 对外接口

| 接口/函数 | 类型 | 签名 | 说明 |
|-----------|------|------|------|
| `run()` | 函数 | `def run(corpus_path: str, path: str, *, report_path: str="", dry_run: bool=False) -> dict` | CLI 与 import 双通道 |
| `load_corpus()` | 函数 | `def load_corpus(corpus_path: str) -> list[GoldenCase]` | 载入 YAML/JSON 语料 |
| `assert_case()` | 函数 | `def assert_case(case: GoldenCase, observed: dict) -> list[str]` | 返回失败断言列表（空 = 全过） |

#### 语料与断言语义（AC-US-10.2 四类断言）

| 断言类 | 判定依据 | 采集方式 |
|--------|---------|---------|
| 能力命中正确性 | 期望能力名出现在该轮能力调用清单中 | 归档 md 轮次段 + `llm_trace.jsonl` |
| 权限拦截 | 越权诉求的回复含拒绝说明，且无对应写操作记录 | 回复文本 + 事件/审计记录 |
| 挂起续接 | 缺参场景出现提问 → 回答后续接同一能力（能力名一致） | 归档 md 两轮段对照 |
| 归档完整性 | 轮次归档段含五要素（能力/参数/成果/触发者/成败） | 归档 md 断言 |

#### 度量口径（AC-US-10.1）

| 指标 | 口径 |
|------|------|
| token | 该轮 `llm_trace.jsonl` 中 `usage.prompt_tokens + completion_tokens` 之和 |
| 延迟 | 该轮首请求发出 → 回复产出（归档时间戳差） |
| 对照 | `--path old` 与 `--path new` 跑同一语料，分组（闲聊类 / 业务类）比对；**闲聊类不得劣于现状基线** |

#### 语料文件（新增）

| 文件 | 内容 |
|------|------|
| `emily-data/golden/session_loop_cases.yaml` | 语料题目 + 分组 + 期望断言（由 M9 建立；具体题目清单在本模块编码阶段定，见"计划阶段定值"） |

#### 依赖接口

| 现有接口 | 来源 | 调用目的 |
|----------|------|---------|
| `.claude/skills/emy-test/cli.py`（HTTP + SSE 测试通道） | 仓库现有 | 真实链路回放（**必须用 users 表真实 UUID**，宪法 Q3） |
| `emily-data/logs/llm_trace.jsonl` | mitmproxy | token/延迟与工具调用采集 |
| 归档 md 文件 | M7 | 断言证据 |

### 数据模型

**无数据模型变更**（语料为仓库内文件；报告为脚本输出文件）。

### 模块验收检测

```bash
# 验收 1：脚本已注册（C11 / CLAUDE.md #10b）
uv run python scripts/scriptmgr.py describe golden_session_loop
→ 预期输出：输出脚本描述与参数 schema（非"脚本不存在"）

# 验收 2：CLI + import 双通道
uv run python -c "from scripts.golden_session_loop import run, load_corpus, assert_case; print('ok')"
→ 预期输出：ok

# 验收 3：dry-run 不发起请求
uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --dry-run
→ 预期输出：仅打印待执行语料与断言清单；无 HTTP 请求（llm_trace.jsonl 行数不变）

# 验收 4：语料含四类断言（AC-US-10.2）
uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --report emily-data/golden/report_new.json
→ 预期输出：报告含 4 个断言分组（capability_hit / permission_block / suspend_resume / archive_completeness），每组均有用例

# 验收 5：成本对照（AC-US-10.1）
uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path old --report emily-data/golden/report_old.json
→ 预期输出：两组报告中闲聊类分组 token/延迟可比对；新路径闲聊类不劣于旧路径
```

**失败处理**：验收 1 失败 → 未注册进 `scripts_registry.yaml`，补注册；验收 3 发起请求 → 检查 `dry_run` 是否在 HTTP 调用前返回；验收 5 新路径劣于基线 → 检查短路是否生效（闲聊类应零 LLM）。

---

## 组装验证

| 验证项 | 验证方式 | 预期结果 |
|--------|---------|---------|
| 规格覆盖完整 | 对照"追溯矩阵（US → 模块）" | US-01 ~ US-10 全部有实现模块，无遗漏 |
| 数据层正确 | `docker exec emily-postgres psql -U emily -d emily -c "\dt"` 前后比对 | 表数量不变（本设计无新增表） |
| 接口契约正确 | 验收 1（各模块 import + 签名断言） | 全部通过 |
| 核心流程正确 | 端到端：闲聊 / 单能力 / 多能力 / 缺参续接 / 确认取消 五条语料 | 回复符合 AC 描述 |
| 异常路径正确 | 超时 / 能力失败 / 话题切换 / 重启失效 | 均为可读收口，无未捕获异常 |
| 接线闭合 | 各模块"验收"中的 grep 可达性条目 | 全部 ≥2 处命中（定义 + 消费） |

```bash
# 端到端组装验证命令（新路径全链路）
docker compose -f docker-compose-napcat.yml up -d
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status='active' ORDER BY permission_level LIMIT 5;"

# 开启新路径（全局开关 + 放开清单）
docker exec emily-core printenv EMILY_SESSION_LOOP_ENABLED     # 期望 True
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我记录事件：样板段放线完成" --sender "<真实用户名>"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "翠湖庭院最近有什么事件" --sender "<真实用户名>"

uv run python scripts/golden_session_loop.py --corpus emily-data/golden/session_loop_cases.yaml --path new --report emily-data/golden/report_new.json
→ 预期输出：报告四类断言分组齐全；能力命中/权限拦截/挂起续接/归档完整性均有用例且通过；闲聊类 token 与延迟新路径 ≤ 旧路径
```

---

## 计划阶段需定值（对应 PRD 附录 B）

> PRD 附录 B 将以下方案型问题留给计划/DD；本节给出 SD 级定值，DD 阶段可细化实现。

| # | PRD 附录 B 问题 | 本计划定值 |
|---|----------------|-----------|
| 1 | 新主循环文件位置与组合方式 | `session/loop.py`（`SessionLoop`）+ `session/loop_pool.py`（`SessionLoopPool`），组合 `SessionContext`；旧文件零改动（约束 §4.4-4） |
| 2 | `CapabilityPlan` 数据结构 | 见 M4：`CapabilityPlan{plan_id, steps[], source, max_depth}` + `CapabilityStep{step_id, capability, params, depends_on, objective, status, result}` |
| 3 | 单次能力调用超时值与清理方式 | 默认 `120s`（`capability_call_timeout_seconds`）；超时 → `wait_for` 取消 → M3 `try/finally` 保证 `wi` 终态 → 回灌结构化失败；残留图检查点由现有 `startup_recovery` 清扫 |
| 4 | 两段式加载阈值 | `CAPABILITY_TWO_STAGE_THRESHOLD = 20`；仅定义 + 两处读取（告警/CLI），**不实现**两段式 |
| 5 | golden 语料清单、判定人、指标采集 | 语料 `emily-data/golden/session_loop_cases.yaml`（四类断言分组）；指标采集自 `llm_trace.jsonl` + 归档 md；判定人 = req-verify 执行者 + 技术负责人复核（编码阶段填入具体题目） |
| 6 | 进度回灌的事件形式与节奏 | 每轮开始时经 `OutboundEventBus.publish("progress", ...)` 推送一次 `CapabilityPlan.progress_text()`（简版）；能力完成后再推一次更新 |
| 7 | 编排器对现有 DAG 语义的复用与改造切分 | **不复用**旧 `SessionOrchestrator`（PRD §4.4-7）；M4 `PlanCursor` 重实现 `run_dag` 的层内并行/层间顺序/失败跳过语义（对照源 `workitem/scheduler.py:129-208`），执行权留在 M1 |
| 8 | CLAUDE.md 约束 #3/#5 改写文案与生效时点 | 见 M8 治理同步清单；生效时点 = 随 Phase 2 主循环转默认 |

---

## 阶段反思指令

每完成一个模块的设计，在进入下一个模块之前，执行以下反思：

1. **检查设计完整性**：本模块的接口契约、数据模型（或无变更声明）、验收检测是否完整
2. **检查追溯闭合**：本模块声明的 US 是否都有接口/验收覆盖；对照追溯矩阵有无遗漏
3. **检查设计偏差**：是否有与规格（PRD）不符的设计？如发现规格缺陷，**回退 req-review**，不在计划内扩需
   - 本设计已登记 1 处规格口径问题（M8 `灰度语义` 中 AC-US-09.2 与 R2/D6 的张力），待评审确认
4. **判断是否继续**：
   - 偏差 ≤ 1 个接口调整 → 直接修改设计文档对应模块，继续
   - 偏差 2-4 个接口或模块职责调整 → 在文末追加 "v1.1 修订记录"，继续
   - 偏差 > 4 个接口或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成设计

---

## v1.1 修订记录

| # | 日期 | 修订项 | 原因 | 影响的模块 |
|---|------|-------|------|-----------|
| 1 | 2026-09-11 | M3 新增「能力准入与命名契约」：能力工具名取**短形 sop_id**（`short_sop_id`），执行期 `wi.sop_id` 取**文件 stem 全文**；新增排除集 `SYSTEM_INTERNAL_SOPS = {SOP-000-SYS, SOP-012-SYS, SOP-999-SYS}`；`capability_catalog --check` 增加准入一致性闸门 | 实现前探查发现 sop_id 在既有系统中存在**三种形态**（`sop_business_flows` 短形 / `experts` 全 stem / `SkillRegistry` 全 stem），且 `SessionContext.sop_allow` 派生自短形表——若能力名不用短形，权限过滤恒为空。同时 `sops/` 下有 3 份非用户可调用 SOP（元规范 / 图内触发 / 工具直调兜底），无差别枚举会产出错误能力 | M3（验收 +2 条）、M2（准入清单来源） |
| 2 | 2026-09-11 | 偏差等级判定 | 属接口调整 1 项（命名契约）→ 按「阶段反思」规则直接修改设计并追加本记录 | — |
| 3 | 2026-09-11 | M8 灰度语义落地为"能力目录准入清单"（`session_loop_sop_allowlist` 由 M2 在装配时按 `sop_allow ∩ 清单` 过滤） | 落实 M8 已登记的规格口径（AC-US-09.2 与 R2/D6 的张力）；实测 `path_router.describe()` 与 allowlist 过滤均生效 | M2, M8 |
| 4 | 2026-09-11 | M9 语料位置与运行位置明确：`emily-data/golden/session_loop_cases.yaml` **未被挂载进容器**（compose 仅挂 config/sops/prompts 等），故 golden 脚本按**宿主机运行**设计（`uv run python scripts/...`，HTTP 打 127.0.0.1:18080），归档/trace 路径自动探测宿主机目录、回退容器路径 | 实现期发现 compose 卷清单不含 `emily-data/golden`；若改为容器内运行，需另行加卷（未在本需求范围内） | M9 |
| 5 | 2026-09-11 | M8「治理同步」中的 `CLAUDE.md #3/#5 改写 + 主循环冻结约束」**Phase 1 不执行**（计划已定"随 Phase 2 主循环转默认"生效）；运维口径已落 `docs/Manual/技术踩坑备忘录.md §9.3` | 保持默认开关关闭期间，旧约束仍准确描述现行主路径；避免文档先于行为变更 | M8 |
| 6 | 2026-09-11 | 组装验证范围声明：本次交付完成**静态可达性 + 导入 + 准入闸门 + 能力目录装配 + dry-run** 级验证；**端到端（重启 + golden 真实回放）未执行**——golden 业务用例会真实写库（如记录事件），须显式授权后再跑 | 宪法 Q2（证据驱动，禁止编造 PASS）：未跑即不判 PASS | M1–M9 |
| 7 | 2026-09-11 | **端到端验证已执行**（req-verify）：产出 `Session主体化与WorkItem能力化_测试报告_V1.md`；结论 ⚠️ 有条件通过（规格符合度 8/10 完整满足）。过程中暴露并修复 2 个 🟡中缺陷 —— ① `_run_plan` 未按能力类型分派（新增 `_run_plan_step`）；② 粗排把非 SOP 能力纳入计划导致入参不匹配产出"未命名事件"（改为**粗排只编排 SOP 能力**）。另有 3 个 🟢低项与 5 项未覆盖 AC 登记在报告 §3.2/§五 | 按 req-verify 流程实测发现，非设计推导 | M1, M4 |

---

*本设计为概要设计（SD），由 req-plan 技能生成，遵循项目宪法，产出 US→模块追溯。*
