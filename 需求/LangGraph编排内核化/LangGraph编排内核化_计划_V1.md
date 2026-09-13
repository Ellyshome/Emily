# LangGraph 编排内核化 — 概要设计（SD）

> **基于规格（PRD）**：`LangGraph编排内核化_PRD_V1.md`（`需求/LangGraph编排内核化/LangGraph编排内核化_PRD_V1.md`）
> **宪法版本**：v1.1
> **设计版本**：v1.0
> **级别**：System Design（概要设计）
> **目标**：把会话编排并入既有图式执行引擎（C8），以显式能力契约与可序列化状态为边界，让挂起可恢复、计划全覆盖、门禁不减弱、外壳不侵入。

---

## 你的角色

你作为 **Emily开发者资深架构师** + **系统设计师** + **异步运行时工程师**（自适应补充角色：因涉及图状态序列化边界、中断恢复与并行编排），严格按以下模块顺序设计，逐模块验收，验证不通过不进入下一个模块。具体代码实现在编码阶段落地，本设计给出接口契约和实现约束。

---

## 硬约束（违反即失败）

> 项目铁律直接引用 [宪法 §2](../../.trae/skills/_shared/constitution.md)，**不自行发明**。

1. **禁止修改已有接口签名**：除非本设计明确标注"修改接口签名"，否则只在已有类中新增方法。已知必须保留签名的入口：会话处理入口 `handle(message, db_message_id="", current_user_id="")`、图构建入口 `build_workitem_graph(...)`、调度执行入口 `SessionScheduler._run_one(wi, message=None, db_message_id="")`。
2. **遵循宪法铁律 C0~C11**，逐条自检并列出**本设计涉及**的铁律及遵守方式：

| 铁律 | 本设计如何遵守 |
|------|--------------|
| C0 根治而非迁就 | 取消并列的第二套编排，不做"两条链路各修一遍"的折中；挂起改为可恢复，不以内存态收尾 |
| C2 分层不可跳 | 图只调用能力端口（M2）与门禁端口（M6），不触达 Repository；链路仍为 `API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB` |
| C6 Sync repo + to_thread | 领域数据访问保持同步实现，由既有异步层包裹；本设计不新增同步直连 |
| C8 唯一执行引擎 | 会话侧纳入既有 StateGraph；`pipeline/` 废弃路径不复活，不新增第二引擎 |
| C10 工具必须带参数 schema | M2 的能力目录只收录带 JSON Schema 的能力，缺 schema 不进入可见集 |
| C11 功能注册接入 | 新能力经 `CapabilityContractRegistry` 接入；手动作业入口经 `scripts_registry.yaml` 与作业注册表接入 |
| Q4 无残留 | M10 负责清理废弃路径与死代码，清理清单在"现有模块改动清单"中逐条标注 |
| Q6 接线闭合 | 每个新增开关、端口、注册项在"接线矩阵"与"断线模式自查"中给出读取方 |

3. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告。
4. **遵循接口契约**：跨模块调用只经约定接口，不允许绕过接口直接操作对方内部数据。
5. **不私自扩需**：发现规格缺陷**回退 req-review**，不在本设计里新增或改写需求。
6. **接线闭合（宪法 §3 Q6）**：任何产出物必须有消费者（见"接线矩阵"）；无消费者不得进入编码。

---

## PRD 约束落实

> 逐条回应 PRD §4.4 的 11 条约束型技术决策。不得沉默跳过。

| # | PRD 约束（摘要） | 本设计如何遵守 | 落在哪个模块 | 若无法遵守 |
|---|-----------------|--------------|------------|-----------|
| 1 | 复用现有图式执行引擎作为唯一编排形态，不得并行保留第二套 | 复用 `build_workitem_graph` 的引擎与检查点能力，新增会话编排图构建入口；旧会话链路在 M10 退役删除 | M3, M10 | — |
| 2 | 复用现有业务执行引擎作为业务流能力内部实现，不重写能力本体 | SOP 能力执行仍走 `SessionScheduler._run_one` → 既有图；本设计不新增执行引擎 | M3 | — |
| 3 | 状态只含可序列化内容，领域对象按标识重建 | 新状态结构仅基础类型 + ID/摘要；领域对象经 `BusContext` 型进程内上下文与重建端口获取 | M1, M3 | — |
| 4 | 复用现有权限判定与分级兜底通道，不新造判定，不引策略引擎 | 门禁节点只做调用编排，判定仍由 `AuthHook`、`FallbackPolicy`、`build_tool_specs` 完成 | M6 | — |
| 5 | 不引入 LangChain 主包；新增子包须登记版本上界 | 依赖清单无新增项；`langgraph` 版本上界在 M10 登记 | M10 | — |
| 6 | 新增能力与作业入口经既有注册通道接入 | 能力经契约注册表接入；手动自检入口经 `scripts_registry.yaml` 接入 | M2, M9 | — |
| 7 | 保留灰度双轨与可回退 | 沿用 `session_loop_enabled` 语义并扩展为全局 + 按能力两级 | M10 | — |
| 8 | 挂起必须支持跨进程恢复 | 挂起以图中断 + 检查点承载，恢复走 `Command(resume=...)`；内存登记表退出 | M5 | — |
| 9 | 无人值守作业不得构成内核必要条件；自检不得依赖其他作业日志 | 自检判定改读业务数据本身；作业降级为手动入口；引擎停用不影响主流程 | M9 | — |
| 10 | 新增或升级组件锁版本上界并登记，配套升级回归语料 | `requirements.txt` 钉上界；语料落在 M11 | M10, M11 | — |
| 11 | 领域与外壳不进执行状态，外部资源经端口访问 | 新增四个端口协议与现有实现适配器；状态只留标识 | M8 | — |

**未落实约束清单**：无。

---

## 追溯矩阵（US → 模块）

> 基准：`LangGraph编排内核化_PRD_V1.md` 的 US 清单（US-01 至 US-11）。

| US-ID | 需求一句话 | 实现模块 | 覆盖状态 |
|-------|-----------|---------|---------|
| US-01 | 会话编排收敛为唯一执行形态 | M3, M10 | ✅ |
| US-02 | 任务级计划覆盖全部能力且不丢参 | M4, M2 | ✅ |
| US-03 | 能力契约显式化与可见范围裁剪 | M2, M6 | ✅ |
| US-04 | 挂起可跨进程恢复且归属发起者 | M5 | ✅ |
| US-05 | 进度与结果统一可见且与实际一致 | M7, M3 | ✅ |
| US-06 | 门禁与评审在编排内判定，否决效力保留 | M6 | ✅ |
| US-07 | 中间状态可序列化、可恢复、可幂等重放 | M1, M5 | ✅ |
| US-08 | 领域与外壳不进执行状态，只经端口访问 | M8, M1 | ✅ |
| US-09 | 灰度双轨与可回退 | M10 | ✅ |
| US-10 | 治理与业务资产不减 | M6, M7, M10 | ✅ |
| US-11 | 无人值守作业与内核解耦 | M9 | ✅ |

**缺失清单**：无。11 条 US 全部有实现模块。

---

## 系统架构概览

### 架构图

```
┌───────────────────────────────────────────────────────────────────────┐
│                              EmilyCore                                │
│                                                                       │
│  入站 ──► 入口分派（M10 灰度开关）                                      │
│              │                                                        │
│              ├── 旧链路（保留至 M10 退役）：SessionAgent → Scheduler     │
│              │                                                        │
│              └── 新链路 ──► 【M3 会话编排图】★核心                      │
│                                │                                      │
│              ┌─────────────────┼──────────────────┐                   │
│              ▼                 ▼                  ▼                   │
│      【M4 计划子图】   【M5 中断挂起】    【M6 门禁判定节点】            │
│      层内并行/依赖/深度   归属/恢复/作废    权限/兜底/质量门/评审        │
│              │                 │                  │                   │
│              └─────────┬───────┴──────────────────┘                   │
│                        ▼                                              │
│              【M2 能力契约】──► 既有工具注册表 / SOP 索引               │
│                        │                                              │
│                        ▼                                              │
│              【M1 状态与序列化边界】──► 既有检查点（Postgres）          │
│                        │                                              │
│                        ▼                                              │
│              【M7 统一事件】──► 出站通道 + 轮次归档                     │
│                                                                       │
│  图外经端口（M8）：会话运行时 / 事件 / 文件 / 检索 ── 现有实现适配器    │
│  图外独立（M9）：无人值守作业手动化 ── ScriptManager / 作业注册表       │
│  横切（M11）：验收语料与度量                                            │
└───────────────────────────────────────────────────────────────────────┘
```

### 分层关系

| 新模块 | 所在分层 | 上层依赖 | 下层被依赖 |
|--------|---------|----------|-----------|
| M1 状态与序列化边界 | 编排内核（`session/`） | 无 | M3, M5, M8 |
| M2 能力契约 | 编排内核 ↔ 能力层接口 | 无 | M3, M4, M6 |
| M3 会话编排图 | 编排内核（`session/`） | M1, M2 | M4, M5, M6, M7, M10 |
| M4 计划子图 | 编排内核（`session/`） | M2, M3 | M11 |
| M5 中断挂起 | 编排内核（`session/`） | M1, M3 | M11 |
| M6 门禁判定 | 编排内核（`session/`） | M2, M3 | M11 |
| M7 统一事件 | 编排内核 ↔ 宿主接口 | M3 | M10, M11 |
| M8 外壳端口 | 宿主外壳接口层 | M1 | M3 |
| M9 作业解耦 | 宿主外壳（运维面） | 无 | M11 |
| M10 灰度与治理 | 装配层（`bootstrap`/`config`） | M3, M7 | M11 |
| M11 语料与度量 | 验收面（脚本） | M3~M7, M9 | 无 |

---

## 数据流设计

### 主流程（单条消息，新链路）

```
用户消息 → API → EmilyCore.handle_message
                     │
                     ├─ M10 开关判定 ──关──► 旧链路
                     │
                     └─开─► M3 会话编排图（构造仅含 ID/摘要的初始状态）
                                   │
                     ┌─────────────┴──────────────┐
                     ▼                            ▼
              M2 能力目录装配（按操作者裁剪）   M5 续接判定（有挂起？）
                     │                            │
                     └────────► M4 计划子图 ◄──────┘
                                   │（层内并行、层间顺序、深度上限）
                                   ▼
                         能力调用（SOP 能力→既有执行引擎；查询/写→现有工具）
                                   │
                          M6 门禁判定（权限 / 兜底 / 质量门 / 评审）
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
              M5 挂起（中断）            M7 事件与归档
                     │                           │
                     └──────► 成果回灌 ──► 回复产出 ──► 出站通道
```

### 核心流程

| 流程 | 触发条件 | 参与者 | 数据流向 | 异常路径 |
|------|---------|--------|---------|---------|
| 单轮直答 | 是否需要执行为否 | M3→M7 | 状态 → 回复文本 | 模型无返回 → 可读兜底回复 |
| 计划执行 | 判定需执行且存在计划 | M4→M2→M6→M7 | 计划（含请求摘要）→ 成果 → 对话 | 前置失败 → 下游跳过并如实说明 |
| 挂起续接 | 能力返回需补充信息 | M5→M3→M4 | 提问 → 用户补充 → 恢复执行 | 非发起者或新话题 → 作废，不误接 |
| 门禁拒绝 | 权限或兜底不通过 | M6→M7 | 拒绝结论 → 可读说明 + 留痕 | 判定异常 → 保守拒绝（fail-closed） |
| 重启恢复 | 进程重启后同会话再来消息 | M1→M5 | 检查点状态 → 续接执行 | 检查点缺失 → 提示重新提出 |
| 作业手动执行 | 运维显式触发 | M9 | 手动入口 → 自检结果 | 调度引擎停用 → 不影响主流程 |

---

## 模块依赖图

```
M1(状态与序列化) ──┐
                   ├──► M3(会话编排图)★ ──► M4(计划子图) ──┐
M2(能力契约) ──────┘        │                              │
                            ├──► M5(中断挂起) ─────────────┤
                            ├──► M6(门禁判定) ─────────────┤
                            └──► M7(统一事件) ──► M10(灰度治理) ──┤
                                                                │
M8(外壳端口) ──► M3                                             ▼
M9(作业解耦) ────────────────────────────────────────────► M11(语料度量)
```

构建顺序：M1、M2 并行 → M3 → M4 / M5 / M6 / M7 并行 → M8、M9 → M10 → M11。无循环依赖。

---

## 交付物总览

| 模块 | 实现的 US | 交付物类型 | 新增/修改 | 核心接口/类/表 |
|------|----------|-----------|----------|---------------|
| M1 | US-07, US-08 | 状态结构与守卫 | 新增 | `KernelState`、`validate_state_serializable()`、`DomainRef` |
| M2 | US-03 | 契约注册表 + 装配 | 新增 | `CapabilitySpec`、`CapabilityContractRegistry`、`build_specs()` |
| M3 | US-01, US-02, US-05, US-07 | 图构建 + 节点 | 新增 | `build_session_graph()`、节点工厂、路由函数 |
| M4 | US-02 | 计划子图 | 新增 | `build_plan_subgraph()`、`PlanState`、深度守卫 |
| M5 | US-04, US-07 | 中断与恢复 | 新增 | `build_suspend_node()`、`resume_config()`、归属校验 |
| M6 | US-03, US-06, US-10 | 判定节点 | 新增 | `GateDecision`、`build_gate_node()`、三处适配器 |
| M7 | US-05, US-10 | 事件适配 + 归档接线 | 新增/修改 | `EventAdapter`、进度渲染、归档渲染接线 |
| M8 | US-08 | 端口协议 + 适配器 | 新增 | `SessionRuntimePort`、`EventSinkPort`、`FilePort`、`RetrievalPort` |
| M9 | US-11 | 手动入口 + 解耦 | 新增/修改 | 手动自检入口、自检判定改造、作业注册调整 |
| M10 | US-09, US-10 | 开关 + 清理 + 治理 | 新增/修改 | 灰度开关（全局 + 按能力）、生效范围查询、清理清单 |
| M11 | 全部 US（回验） | 语料 + 回放 | 新增 | `golden_session_kernel.yaml`、回放脚本、度量输出 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/__init__.py` | 修改 | 新增分派分支与图装配（不改 `handle_message` 签名）；注册 M2 契约与 M10 开关 |
| `emily-core/emily_core/config.py` | 修改 | 新增开关项与端口开关；保留既有 `session_loop_enabled` 语义兼容 |
| `emily-core/emily_core/bootstrap.py` | 修改 | 环境变量映射补新增开关项 |
| `emily-core/emily_core/session/loop.py` | 修改 | 主循环与计划步进迁出为图（M3/M4），保留 `handle(...)` 签名与快速短路分支 |
| `emily-core/emily_core/session/capability_plan.py` | 修改 | 由 M4 取代；游标语义迁移，文件降级为兼容壳或删除 |
| `emily-core/emily_core/session/capability_catalog.py` | 修改 | 改为消费 M2 契约数据源，`build_tool_specs` 保持签名 |
| `emily-core/emily_core/session/capability_runner.py` | 修改 | 改为能力子图入口，保留 `_run_one` 调用路径（约束 2） |
| `emily-core/emily_core/session/suspend_registry.py` | 修改 | 由 M5 取代；内存登记表退出最终形态，续接判定语义迁移 |
| `emily-core/emily_core/session/confirm_dialog.py` | 修改 | 确认语义迁入 M5/M6 判定，复用 `EventApplication` 不变 |
| `emily-core/emily_core/session/path_router.py` | 修改 | 扩展为全局 + 按能力两级开关，新增生效范围查询 |
| `emily-core/emily_core/session/confirm_queue.py` | 删除 | 无调用者后清理（Q4） |
| `emily-core/emily_core/session/focus_lock.py` | 删除 | 焦点语义由图状态表达后清理（Q4） |
| `emily-core/emily_core/session/session_state.py` | 删除 | 自述为骨架，语义并入 M1（Q4） |
| `emily-core/emily_core/session/orchestrator.py` | 删除 | 旧链路退役后清理（M10，Q4） |
| `emily-core/emily_core/session/session_agent.py` | 删除 | 旧链路退役后清理（M10，Q4） |
| `emily-core/emily_core/services/session_archive_writer.py` | 修改 | 现有渲染函数接入 M7，不新增存储 |
| `emily-core/emily_core/workitem/scheduler.py` | 不变 | `_run_one` / `_run_graph` 作为适配层保留 |
| `emily-core/emily_core/workitem/langgraph_engine/graph.py` | 修改 | 新增会话编排图构建入口，不改既有 `build_workitem_graph` 签名 |
| `emily-core/emily_core/workitem/langgraph_engine/state.py` | 修改 | 保持纯可序列化约定，供 M1 参照与复用 |
| `emily-core/emily_core/workitem/pipeline/bus.py`、`node.py`、`real_guardian.py` | 删除 | 无调用者，M10 清理（Q4） |
| `emily-core/emily_core/tools/expert_manage_tool.py` | 修改 | 去掉对图内部状态的直接 import，改经端口取参数（PRD US-08.4） |
| `emily-core/emily_core/tools/registry.py` | 修改 | 补齐读写模式与权限归属声明，经 M2 一并登记 |
| `emily-core/emily_core/infrastructure/tools_consistency.py` | 修改 | 补入 5 个清单漂移工具，消除静态清单与运行时差异 |
| `emily-core/emily_core/services/initialization_checker.py` | 修改 | 自检判定去掉对作业执行日志的依赖（US-11.2） |
| `emily-core/emily_core/scheduler/handler_registry.py` | 修改 | 自检类作业降级；空实现与无 handler 作业行清理 |
| `emily-core/emily_core/scheduler/jobs/health_check.py` | 修改 | 改为手动入口可复用的纯函数形态 |
| `emily-core/emily_core/scheduler/jobs/data_sync.py`、`webhook.py` | 删除 | 恒返回失败的空实现（Q4） |
| `emily-data/config/scripts_registry.yaml` | 修改 | 新增手动自检入口条目；版本上界登记落点 |
| `emily-core/requirements.txt` | 修改 | `langgraph` 收紧为上界锁定（约束 10） |
| `emily-core/emily_core/permission/*`、`application/*`、`services/*`（其余） | 不变 | — |

---

## 接线矩阵（Producer → Consumer）

| 产出物 | 类型 | 生产模块 | 消费方（模块 / 调用点） | 接线点 | 状态 |
|--------|------|---------|----------------------|--------|------|
| `KernelState` 状态结构 | 数据结构 | M1 | M3 | 图 State 类型声明 | ✅ |
| 序列化守卫函数 | 方法 | M1 | M3、M11 | 图编译前校验 + 语料静态断言 | ✅ |
| `CapabilitySpec` 契约 | 数据结构 | M2 | M3、M4、M6 | 目录装配、计划校验、门禁判定 | ✅ |
| 契约注册调用 | 注册项 | M2 | `EmilyCore` 启动装配 | 启动注册 + 目录枚举 | ✅ |
| `build_session_graph` | 方法 | M3 | `EmilyCore` 分派分支 | 图装配点 | ✅ |
| 计划子图 | 方法 | M4 | M3 | 计划节点调用 | ✅ |
| 挂起节点与恢复入口 | 方法 | M5 | M3、入站续接路径 | 中断触发 + 恢复配置 | ✅ |
| 门禁判定节点 | 方法 | M6 | M3 | 条件边判定 | ✅ |
| 事件适配器 | 方法 | M7 | 现有出站通道与归档 | 事件订阅 + 归档渲染 | ✅ |
| 四个端口协议 | 接口 | M8 | M3 | 端口注入 | ✅ |
| 手动自检入口 | 注册项 | M9 | 运维人工 + 控制台 | `scripts_registry.yaml` + 控制台列表 | ✅ |
| 灰度开关（两级） | 配置 | M10 | 入口分派、目录装配 | 分派读取 + 裁剪读取 | ✅ |
| 生效范围查询 | 方法 | M10 | 运维核对 | 控制台或接口调用 | ✅ |
| 回归语料 | 数据文件 | M11 | 母版与发布验证 | 回放脚本读取 | ✅ |

**未接线清单**：无。

---

## 断线模式自查（宪法 Q6 / C11）

| 断线模式 | 本次是否新增 | 消费方已接线？ | 接线点 / 证据 |
|---------|------------|--------------|--------------|
| 新增配置项 | 是（两级灰度开关、端口开关） | 是 | 入口分派读取 + 目录裁剪读取，静态扫描命中 ≥2 处 |
| 新增工具 / 公开方法 | 是（图构建、计划子图、挂起、门禁、事件适配） | 是 | 均在 M3 的节点与条件边中被调用 |
| 新增 DB 列 | 否 | — | 复用既有检查点存储，不新增列 |
| 新增文件产出 | 是（回归语料） | 是 | 回放脚本读取 |
| 新增注册项（能力 / 作业 / 脚本） | 是（能力契约、手动自检入口） | 是 | 能力经 `EmilyCore` 启动注册并被目录消费；入口经脚本注册表被控制台消费 |

---

## 独立脚本架构设计

### 独立脚本清单

| # | 脚本（建议命名） | 职责 | 关键参数 | `--dry-run` 行为 |
|---|----------------|------|---------|------------------|
| 1 | `scripts/self_check.py`（改造） | 系统级自检，去掉作业日志依赖 | `--project-id` `--dry-run` | 仅打印检查项与结论，不写库 |
| 2 | `scripts/kernel_state_lint.py`（新增） | 扫描状态结构与端口使用，断言无领域对象、无外部实例 | `--path` | 仅输出违规清单 |
| 3 | `scripts/kernel_replay.py`（新增） | 按语料回放会话编排并输出度量 | `--cases` `--tag` `--dry-run` | 仅校验语料格式与用例可达性 |
| 4 | `scripts/kernel_resume_probe.py`（新增） | 构造挂起后重启并续接，验证跨进程恢复 | `--case` `--restart` | 仅打印将要执行的步骤 |

### 聚合薄壳

| # | 脚本（建议命名） | 串联逻辑 |
|---|----------------|---------|
| 1 | `scripts/cold_start.py`（既有，扩展） | 初始化检查 → 自检 → 语料抽样回放 → 汇总 |
| 2 | `scripts/cognition_cycle.py`（既有，保持） | 认知偏差检测 → 三书更新 → 汇总 |

### 脚本交互关系

```
cold_start.py（聚合薄壳）
  ├── check_initialization.py   → 初始化层级结论
  ├── self_check.py             → 系统自检结论（M9 改造后不再依赖作业日志）
  ├── kernel_replay.py          → 语料回放度量（M11）
  └── 汇总输出

kernel_resume_probe.py（M5 专用探针）
  └── 构造挂起 → 触发重启 → 断言续接成功

EmilyCore 集成通道
  └── import self_check.run() → dict
```

---

## M1: 状态与序列化边界模块（KernelState）

**依赖**：无（首建模块）

**实现的需求**：US-07（AC-US-07.1、AC-US-07.3）、US-08（AC-US-08.1）

**层级**：编排内核（`session/`）

**职责**：定义编排图的执行状态结构，只允许基础类型与标识、摘要；提供序列化守卫与领域对象引用（`DomainRef`）约定，作为 M3 与 M8 的共同边界。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `KernelState` | TypedDict | 字段见下 | 编排图状态，纯可序列化 |
| `DomainRef` | dataclass | `kind: str`, `ref_id: str`, `digest: str` | 领域对象引用，只存标识与摘要 |
| `validate_state_serializable(state: dict) -> list[str]` | 方法 | 入参状态字典，返回违规字段路径列表 | 空列表表示通过 |
| `make_initial_state(*, conversation_id: str, actor_ref: DomainRef, max_iterations: int) -> dict` | 方法 | 构造初始状态 | 不接收领域对象 |

#### `KernelState` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | `str` | 会话标识 |
| `actor_ref` | `dict` | 操作者引用（标识 + 摘要） |
| `messages` | `list[dict]` | 对话历史（基础类型） |
| `plan` | `dict` | 计划（步骤为标识与摘要） |
| `iteration_count` / `_max_iterations` | `int` | 迭代控制 |
| `pending_call` | `dict` | 挂起中的能力调用（标识 + 参数摘要 + 发起者标识） |
| `capability_calls` | `list[dict]` | 本轮能力调用清单（供归档） |
| `gate_result` | `dict` | 最近一次判定结论 |
| `reply_text` | `str` | 产出回复 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `make_initial_state()`（既有图状态） | `workitem/langgraph_engine/state.py` | 复用其"仅基础类型"约定作为范式参照 |
| `set_bus_context` / `get_bus_context` | 同上 | 领域上下文经进程内上下文传递，不进状态 |

### 数据模型

无新增表。状态持久化复用既有检查点存储（`LazyPostgresCheckpointer`）；本模块只定义结构，不新增存储。

### 模块验收检测

```bash
# 验收 1：模块可导入且状态可构造
uv run python -c "from emily_core.session.kernel_state import make_initial_state, validate_state_serializable; s=make_initial_state(conversation_id='c1', actor_ref={'kind':'user','ref_id':'u1','digest':'L2'}, max_iterations=12); print(validate_state_serializable(s))"
→ 预期输出：[]

# 验收 2：领域对象进状态即被拦截（AC-US-08.1）
uv run python -c "from emily_core.session.kernel_state import validate_state_serializable; print(validate_state_serializable({'bad': object()}))"
→ 预期输出：非空列表（指出违规字段路径）

# 验收 3：静态断言状态定义无领域类型
grep -nE "SessionContext|WorkItem|BusContext|StandardMessage" emily-core/emily_core/session/kernel_state.py
→ 预期输出：0 命中

# 验收 4：跨进程写入检查点后读回（AC-US-07.1）
uv run python scripts/repro_checkpoint_persistence.py
→ 预期输出：写入成功、读回一致、无降级日志
```

**失败处理**：验收 2 出现空列表说明守卫未生效，检查类型判定是否覆盖容器嵌套；验收 4 出现降级日志说明状态混入不可序列化对象，按违规字段路径回溯调用方。

---

## M2: 能力契约模块（CapabilityContract）

**依赖**：无

**实现的需求**：US-03（AC-US-03.1、AC-US-03.4）、US-08（AC-US-08.4）

**层级**：编排内核 ↔ 能力层接口

**职责**：为查询、写入、SOP 三类能力提供统一的契约描述（参数 schema、读写模式、权限归属、可见性来源），作为目录装配、计划校验与门禁判定的唯一数据源；切断能力层对图内部状态的反向依赖。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `CapabilitySpec` | dataclass | `name: str`, `kind: str`, `params_schema: dict`, `write_mode: str`, `perm_scope: str`, `source: str` | 单条能力契约；`kind ∈ {query, write, sop}` |
| `CapabilityContractRegistry` | 类 | `register(spec) -> None`、`get(name) -> CapabilitySpec \| None`、`list_all() -> list[CapabilitySpec]` | 契约注册表 |
| `build_specs(core, actor_snapshot, session_context, *, allowed_sops=None) -> list[CapabilitySpec]` | 方法 | 按操作者装配可见契约 | 缺 `params_schema` 的能力被剔除 |
| `register_capabilities(core) -> None` | 方法 | 启动注册（扩展既有同名入口） | 兼容既有调用点 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `build_tool_specs(...)` | `langgraph_engine/agent/tool_adapter.py` | 复用权限过滤（fail-closed），不重造 |
| `FallbackPolicy.resolve` | `langgraph_engine/agent/fallback_policy.py` | 取写模式与兜底档位 |
| `CapabilityCatalog.list_capabilities(...)` | `session/capability_catalog.py` | 现有目录枚举，改为消费本模块契约 |
| `SkillRegistry.list_skills()` | `skill/registry.py` | SOP 能力来源 |

#### DTO 定义

| DTO | 字段 | 用途 | 流向 |
|-----|------|------|------|
| `CapabilitySpec` | 见上 | 契约描述 | M2 → M3 / M4 / M6 |
| `VisibilityResult` | `visible: list[str]`, `rejected: list[tuple[str, str]]` | 裁剪结果与拒绝原因 | M2 → M3 |

### 数据模型

无新增表。契约来源为现有工具注册表与 SOP 索引；权限归属复用既有权限绑定数据。

### 模块验收检测

```bash
# 验收 1：契约可枚举且三类齐备（AC-US-03.1）
uv run python scripts/capability_catalog.py
→ 预期输出：能力清单含 query / write / sop 三类，且每项带 params_schema

# 验收 2：缺 schema 的能力不进入可见集
uv run python scripts/check_tools_consistency.py
→ 预期输出：无 SchemaGuard WARNING；漂移工具数为 0

# 验收 3：能力层无反向依赖（AC-US-08.4）
grep -rn "get_bus_context" emily-core/emily_core/tools/
→ 预期输出：0 命中

# 验收 4：裁剪按操作者生效（AC-US-03.4）
uv run python .claude/skills/emy-test/cli.py --managed --llm --sender "张正宏" --sender-id "ce996655-d346-4c43-a4ac-5da60dc20e2b" --message "查询我没有权限的项目数据"
→ 预期输出：回复为权限拒绝语，无越权数据
```

**失败处理**：验收 2 有 WARNING 说明契约缺 schema，须回工具源文件补 schema 并在一致性清单登记（C10 三步）；验收 3 有命中说明有人绕过端口取值，改经 `CapabilitySpec` 注入。

---

## M3: 会话编排图模块（SessionGraph）★核心

**依赖**：M1、M2

**实现的需求**：US-01（AC-US-01.1~01.4）、US-02（AC-US-02.5）、US-05、US-07

**层级**：编排内核（`session/`）

**职责**：构建会话编排图，承载理解、计划、能力执行、门禁、成果回灌、回复与归档；以条件边实现重排与收敛；作为唯一编排主体。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `build_session_graph(*, capability_registry, gate, suspend, plan_builder, ports, config) -> "StateGraph"` | 方法 | 构建并编译编排图 | 检查点由既有 `build_checkpointer` 提供 |
| `SessionGraphRunner` | 类 | `async run(message, db_message_id: str = "", current_user_id: str = "") -> str \| None` | 与既有 `handle` 同签名语义，供分派调用 |
| `route_after_understand(state) -> str` | 方法 | 条件路由 | 直答 / 计划 / 续接 |
| `route_after_step(state) -> str` | 方法 | 条件路由 | 继续 / 重排 / 门禁 / 收敛 |

#### 节点与条件边

| 节点 | 职责 | 后继 |
|------|------|------|
| `fast_reply` | 零 LLM 快速短路（问候类） | END |
| `understand` | 组装上下文与可见工具集，调用模型 | 条件边：`reply` / `plan` / `resume` |
| `plan` | 触发 M4 计划构建 | M4 |
| `execute` | 执行单个能力调用 | M6 |
| `gate` | M6 判定 | 条件边：`execute`（重做）/ `suspend` / `reply` |
| `suspend` | M5 中断 | END（挂起态） |
| `summarize` | 成果回灌与回复产出 | M7 归档 |
| `error_analysis` | 迭代上限与异常兜底 | 条件边：`execute` / END |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `build_checkpointer(config)` | `langgraph_engine/checkpointer.py` | 复用检查点，不新建 |
| `build_hook_adapter_from_config(...)` | `langgraph_engine/hook_adapter.py` | 复用 Hook 桥接（审计与归档） |
| `SessionScheduler._run_one(wi, ...)` | `workitem/scheduler.py` | SOP 能力执行路径（约束 2） |
| `SessionAgent._try_fast_reply(...)` | `session/session_agent.py` | 复用快速短路判定（退役前） |

### 数据模型

无新增表。执行中间状态落既有检查点表；能力调用清单落既有归档。

### 模块验收检测

```bash
# 验收 1：图可构建并编译
uv run python -c "from emily_core.session.session_graph import build_session_graph; print('ok')"
→ 预期输出：ok；启动日志含 checkpointer=postgres

# 验收 2：唯一编排入口（AC-US-01.4）
grep -rn "for iteration in range" emily-core/emily_core/session/loop.py
→ 预期输出：0 命中（手写循环已迁入图）

# 验收 3：闲聊零能力（AC-US-01.2）
uv run python .claude/skills/emy-test/cli.py --managed --llm --sender "李景利" --sender-id "<真实UUID>" --message "你好"
→ 预期输出：直接回复；调用记录无新增能力调用

# 验收 4：迭代上限可收敛（AC-US-01.3）
uv run python scripts/verify_langgraph_engine.py --mock-failure
→ 预期输出：达到上限后产出可读收尾，无死循环

# 验收 5：端到端单轮业务消息
uv run python .claude/skills/emy-test/cli.py --managed --llm --sender "李景利" --sender-id "<真实UUID>" --message "翠湖庭院最近有什么事件？"
→ 预期输出：回复由编排直接产出，归档轮次段落存在
```

**失败处理**：验收 2 有命中说明仍留有并列实现，须回退检查迁出范围；验收 3 出现能力调用说明快速短路未前置；验收 5 无归档说明 M7 未接线。

---

## M4: 计划子图模块（PlanSubgraph）

**依赖**：M2、M3

**实现的需求**：US-02（AC-US-02.1~02.5）

**层级**：编排内核（`session/`）

**职责**：以子图表达任务级粗排，覆盖查询、写入、SOP 三类能力；实现层内并行、层间顺序、失败级联跳过、动态追加与深度上限；执行权留在编排。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `PlanState` | TypedDict | `steps: list[dict]`、`done: list[str]`、`failed: list[str]`、`depth: int` | 计划状态（仅标识与摘要） |
| `build_plan_subgraph(*, specs: list[CapabilitySpec], max_depth: int) -> "StateGraph"` | 方法 | 构建计划子图 | 步骤入参统一经契约校验 |
| `next_layer(state) -> list[dict]` | 方法 | 取当前可执行层 | 返回同层可并行步骤 |
| `cascade_skip(state) -> list[str]` | 方法 | 标记下游跳过 | 上游失败即跳过 |
| `can_append(state, *, depth: int) -> bool` | 方法 | 深度守卫 | 超限返回 False |

#### 核心算法/策略

| 算法/策略 | 用途 | 选型理由 | 备选方案 |
|----------|------|---------|---------|
| 分层推进 + 层内并行分支 | 计划执行 | 与既有 `run_dag` 语义一致，迁移风险最低；图内并行由框架并行边承担，无需自写协程池 | 自写 `asyncio.gather`（否决：回到手写编排，违反 C8） |
| 契约校验后入计划 | 防止丢参 | 查询与写入能力 schema 各异，入计划前按 `CapabilitySpec` 校验，消除历史"参数丢失"降级 | 统一万能入参（否决：即历史缺陷根因） |
| 深度守卫 + 动态追加 | 防无限扩张 | 与既有 `max_depth` 语义一致 | 取消追加（否决：损失跨域编排能力） |

### 数据模型

无新增表。计划与步骤结果存图状态与既有归档记录。

### 模块验收检测

```bash
# 验收 1：三类能力可入计划且参数完整（AC-US-02.1，复现历史降级场景）
uv run python .claude/skills/emy-test/cli.py --managed --llm --sender "李景利" --sender-id "<真实UUID>" --message "记一条事件，然后查一下翠湖庭院最近的记录"
→ 预期输出：归档含查询与写入两类能力调用；业务记录字段完整，无空标题降级记录

# 验收 2：层内并行可观测（AC-US-02.2）
uv run python scripts/golden_session_loop.py --cases emily-data/golden/session_kernel_cases.yaml
→ 预期输出：同层步骤时间戳重叠，跨层递增

# 验收 3：前置失败下游跳过（AC-US-02.3）
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml --tag precheck-fail
→ 预期输出：下游步骤标记跳过，回复如实说明

# 验收 4：执行权未移交（静态断言）
grep -rn "def plan(" emily-core/emily_core/session/capability_plan.py
→ 预期输出：若仍存在，须确认其仅为兼容壳且无调用者（0 处调用）
```

**失败处理**：验收 1 出现降级记录说明入计划未做契约校验，检查 M2 装配是否被跳过；验收 4 有调用者说明执行权外移，属违反 PRD 约束 1，回退设计。

---

## M5: 中断挂起模块（InterruptSuspend）

**依赖**：M1、M3

**实现的需求**：US-04（AC-US-04.1~04.4）、US-07（AC-US-07.2）

**层级**：编排内核（`session/`）

**职责**：以框架中断承载挂起，挂起态落检查点；支持跨进程恢复、归属发起者、话题切换作废；替代内存登记表。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `build_suspend_node(*, ask_builder) -> Callable` | 方法 | 构造中断节点 | 触发框架中断，返回待回答问题 |
| `PendingCall` | dataclass | `call_id: str`, `capability: str`, `params_digest: str`, `question: str`, `initiator_id: str` | 挂起调用（标识与摘要） |
| `resume_config(conversation_id: str) -> dict` | 方法 | 生成恢复配置 | 供恢复路径构造恢复输入 |
| `is_continuation(user_input: str, pending: PendingCall, *, actor_id: str) -> bool` | 方法 | 续接判定 | 非发起者直接 False |
| `discard_on_topic_switch(state) -> bool` | 方法 | 话题切换作废 | 作废返回 True |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `SuspendRegistry.is_continuation(...)` | `session/suspend_registry.py` | 复用续接判定语义（迁移，不重写判定标准） |
| `pending.question` 直出语义 | `session/loop.py` | 保留"提问不再经模型改写"的既有行为 |
| `Command(resume=...)` | 既有图调用路径（`scheduler._run_graph`） | 复用恢复机制 |

### 数据模型

无新增表。挂起态随检查点持久化；恢复窗口与清扫复用既有 `startup_recovery` 的过期策略。

### 模块验收检测

```bash
# 验收 1：挂起不产出成果（AC-US-04.1）
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml --tag suspend
→ 预期输出：对话出现提问；归档无成果段

# 验收 2：跨进程恢复（AC-US-04.2）
uv run python scripts/kernel_resume_probe.py --case suspend --restart
→ 预期输出：重启后补充信息完成调用并产出成果

# 验收 3：归属发起者（AC-US-04.3）
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml --tag multi-user
→ 预期输出：非发起者回答不认领挂起

# 验收 4：内存登记表不再是最终形态（静态）
grep -rn "_pending.append" emily-core/emily_core/session/
→ 预期输出：0 命中或仅存在于标注为兼容壳且无调用者的文件
```

**失败处理**：验收 2 失败先查检查点是否落盘（M1 验收 4）；验收 4 有命中说明内存态仍为路径，须回退。

---

## M6: 门禁判定模块（GateConvergence）

**依赖**：M2、M3

**实现的需求**：US-06（AC-US-06.1~06.4）、US-03（AC-US-03.3）、US-10（AC-US-10.1）

**层级**：编排内核（`session/`）

**职责**：把装配期裁剪、执行期判定、分级兜底三处判定收敛为图内明确判定点；保留质量门与专家评审的否决效力；不新造权限判定。（v1.1 起专家评审转休眠，不进入执行路径，见文末修订记录）

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `GateDecision` | dataclass | `decision: str`, `reason: str`, `retry_budget: int`, `evidence: dict` | `decision ∈ {allow, deny, redo, reject}` |
| `build_gate_node(*, spec, session_context, fallback, reviewer) -> Callable` | 方法 | 构造判定节点 | 内部只调用既有判定通道 |
| `assert_consistency(spec, actor_snapshot, session_context) -> list[str]` | 方法 | 三处结论一致性抽样 | 返回不一致项 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `AuthHook.execute(context)` | `workitem/pipeline/hook.py` | 权限判定（不重造） |
| `FallbackPolicy.assert_write_allowed(...)` | `langgraph_engine/agent/fallback_policy.py` | 分级兜底门禁 |
| `build_tool_specs(...)` | `langgraph_engine/agent/tool_adapter.py` | 装配期裁剪 |
| `make_quality_gate()` / `make_expert_review(...)` | `langgraph_engine/nodes.py` | 质量门与评审（复用其否决语义） |

### 数据模型

无新增表。拒绝与评审结论写既有审计与归档。

### 模块验收检测

```bash
# 验收 1：越权写入被拒并留痕（AC-US-06.1）
uv run python .claude/skills/emy-test/cli.py --managed --llm --sender "周文斌" --sender-id "<真实UUID>" --message "删除事件 EVT-20260710-0001"
→ 预期输出：回复为明确拒绝；审计表新增 ACCESS_DENIED 记录

# 验收 2：评审否决不产出成果（AC-US-06.2）
uv run python scripts/repro_expert_review_switch.py
→ 预期输出：否决态回复明确说明，归档无成果

# 验收 3：质量门重做有上限（AC-US-06.3）
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml --tag gate-redo
→ 预期输出：达到上限后以失败收尾，耗时有限

# 验收 4：三处判定一致（AC-US-06.4）
uv run python -c "from emily_core.session.gate import assert_consistency; print('ok')"
→ 预期输出：抽样无不一致项
```

**失败处理**：验收 4 出现不一致说明判定仍分散，检查是否有人绕开 M2 契约直接查权限。

---

## M7: 统一事件与归档模块（UnifiedEvents）

**依赖**：M3

**实现的需求**：US-05（AC-US-05.1~05.3）、US-10（AC-US-10.2、AC-US-10.3）

**层级**：编排内核 ↔ 宿主接口

**职责**：把图产生的事件适配为统一来源，同时服务对话内进度与对外流式；归档内嵌能力调用清单（五要素）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `EventAdapter` | 类 | `attach(graph) -> None`、`subscribe(sink) -> None` | 订阅图事件并转发 |
| `render_progress(state) -> str` | 方法 | 渲染简版进度 | 与实际执行一致 |
| `render_capability_section(calls: list[dict]) -> str` | 方法 | 渲染归档能力段 | 由既有归档写入器消费 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `OutboundBus.publish(event)` | `outbound_bus.py` | 对外通道 |
| `SessionArchiveWriter.ensure_header(...)` / 轮次段写入 | `services/session_archive_writer.py` | 归档落盘（不新增存储） |
| `astream_events`（框架能力） | 既有图调用路径 | 事件来源 |

### 数据模型

无新增表。归档落既有归档记录与文件。

### 模块验收检测

```bash
# 验收 1：两路输出同源可比对（AC-US-05.2）
uv run python scripts/golden_session_loop.py --cases emily-data/golden/session_kernel_cases.yaml --compare-channels
→ 预期输出：两路事件序列一致

# 验收 2：归档五要素齐全（AC-US-10.2）
uv run python -c "from emily_core.services.session_archive_writer import render_capability_section; print(render_capability_section([]))"
→ 预期输出：可渲染（空清单输出空段），字段含能力名/入参摘要/成果摘要/触发者/成败

# 验收 3：部分完成如实回传（AC-US-05.3）
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml --tag partial
→ 预期输出：回复同时说明已完成与未完成部分
```

**失败处理**：验收 1 不一致说明仍有第二事件源，检查出站通道是否被直接调用。

---

## M8: 外壳端口模块（HostShellPorts）

**依赖**：M1

**实现的需求**：US-08（AC-US-08.2、AC-US-08.3）

**层级**：宿主外壳接口层

**职责**：把会话运行时状态、事件、文件、检索抽象为端口，编排只持引用不持实例；会话运行时状态先以现有数据库承载落地，外部存储组件延后引入（技术栈定版批一）。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `SessionRuntimePort` | Protocol | `get(key: str) -> dict \| None`、`set(key: str, value: dict, ttl_seconds: int) -> None`、`sweep_expired() -> int` | 会话运行时状态 |
| `EventSinkPort` | Protocol | `publish(event: dict) -> None` | 事件出口 |
| `FilePort` | Protocol | `resolve(visible_ids: list[str]) -> list[dict]` | 文件可见集 |
| `RetrievalPort` | Protocol | `search(query: str, actor_ref: dict, top_k: int) -> list[dict]` | 检索 |
| `DbBackedRuntimeStore` | 类 | 实现 `SessionRuntimePort` | 方案 A：复现有 Postgres |
| `RedisBackedRuntimeStore` | 类 | 实现 `SessionRuntimePort` | 方案 B：外部存储（本期不落地，仅留接口） |

#### 核心策略

| 策略 | 用途 | 选型理由 | 备选方案 |
|------|------|---------|---------|
| 端口 + 适配器 | 解耦外壳 | 编排不持实例，可测试、可多副本；符合 PRD 约束 11 | 直接注入现有对象（否决：状态含实例，违反 US-08.3） |
| 运行时状态落现有数据库 | 会话过期与并发 | 零新组件、单机多容器足够；符合"不引入新依赖"与生效范围控制 | 外部存储（延迟到批一，接口已留） |

### 数据模型

#### 新增表

| 表名 | 用途 | 关键字段 | 约束 | 索引 |
|------|------|---------|------|------|
| `session_runtime_state` | 会话运行时状态（TTL 与并发控制） | `key VARCHAR(255) PK`, `value JSONB NOT NULL`, `expires_at TIMESTAMP NOT NULL`, `updated_at TIMESTAMP DEFAULT NOW()` | 主键唯一 | `idx_expires_at` |

#### 字段说明

| 字段 | 类型 | 必填 | 默认值 | 说明 | 业务规则 |
|------|------|------|--------|------|---------|
| `key` | VARCHAR(255) | ✓ | — | 状态键（会话标识 + 用途） | 键命名由适配器统一生成 |
| `value` | JSONB | ✓ | — | 状态内容 | 仅基础类型与摘要 |
| `expires_at` | TIMESTAMP | ✓ | — | 过期时间 | 到期即视为失效并由清扫移除 |
| `updated_at` | TIMESTAMP | ✓ | NOW() | 更新时间 | — |

> 若后续改由外部存储承载，本表退化为可选实现，接口不变。

### 模块验收检测

```bash
# 验收 1：端口可导入且无外部实例进状态（AC-US-08.3）
uv run python scripts/kernel_state_lint.py --path emily-core/emily_core/session
→ 预期输出：违规项 0

# 验收 2：TTL 与清扫可用
uv run python -c "from emily_core.session.ports import DbBackedRuntimeStore as S; s=S(None); print(hasattr(s,'sweep_expired'))"
→ 预期输出：True

# 验收 3：重建后行为与现状一致（AC-US-08.2）
uv run python scripts/collect_session_data.py --user "<真实UUID>"
→ 预期输出：上下文注入与裁剪结果与迁移前一致（对照记录）

# 验收 4：依赖清单无新增第三方组件
git diff emily-core/requirements.txt
→ 预期输出：仅 `langgraph` 行收紧上界，无新增包
```

**失败处理**：验收 1 有违规说明端口未收敛，逐项改为端口注入；验收 4 出现新增包即违反 PRD 约束 5/10，回退。

---

## M9: 无人值守作业解耦模块（ManualJob）

**依赖**：无（可并行）

**实现的需求**：US-11（AC-US-11.1~11.5）

**层级**：宿主外壳（运维面）

**职责**：自检类作业降级为显式手动入口；解开自检对作业执行日志的依赖；保证调度引擎停用不影响主流程；登记后续以插件形式回归的清单。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `run_health_check() -> dict` | 方法 | 纯函数自检入口 | 供脚本与控制台复用 |
| `run_self_check(*, project_id: str = "", dry_run: bool = False) -> dict` | 方法 | 系统自检 | 不读作业日志 |
| `list_manual_jobs() -> list[dict]` | 方法 | 枚举手动入口 | 供控制台渲染 |
| `disable_auto_trigger(action_type: str) -> bool` | 方法 | 关闭自动触发 | 通过作业状态而非删除 handler |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|----------|---------|---------|
| `SchedulerRepository.list_active_jobs()` / `update_job_status()` | `repositories/scheduler_repo.py` | 停用自动行 |
| `InitializationChecker` | `services/initialization_checker.py` | 改造后复用检查项 |
| `ScriptManager` 与脚本注册表 | `scripts/manager.py`、`scripts_registry.yaml` | 手动入口承载 |
| `JobHandlerRegistry` | `scheduler/handler_registry.py` | 保留手动可调用形态 |

### 数据模型

无新增表。作业状态复用既有调度作业表；手动执行记录复用既有执行日志表。

### 模块验收检测

```bash
# 验收 1：停用后不再自动执行（AC-US-11.1）
uv run python -c "from emily_core.services.jobs_manual import disable_auto_trigger; print(disable_auto_trigger('system_health_check'))"
→ 预期输出：True；随后观察窗口内无新的自动执行记录

# 验收 2：自检不依赖作业日志（AC-US-11.2）
grep -rn "morning_report" emily-core/emily_core/services/initialization_checker.py
→ 预期输出：0 命中；`uv run python scripts/self_check.py` 结论仍成立

# 验收 3：停用调度引擎不影响主流程（AC-US-11.3）
uv run python .claude/skills/emy-test/cli.py --managed --llm --sender "李景利" --sender-id "<真实UUID>" --message "查询我的节点"
→ 预期输出：回复正常，能力调用正常

# 验收 4：手动入口与作业产出等价（AC-US-11.4）
uv run python scripts/scriptmgr.py run health_check
→ 预期输出：结论字段与既有作业执行记录一致

# 验收 5：回归清单存在（AC-US-11.5）
grep -rn "回归" docs/Manual/技术踩坑备忘录.md
→ 预期输出：含自检手动化与回归条件说明
```

**失败处理**：验收 2 有命中说明耦合未解开，改读业务数据本身；验收 4 结论字段不一致说明改造改变了判定口径，须回退对齐。

---

## M10: 灰度与治理模块（PathSwitch & Governance）

**依赖**：M3、M7

**实现的需求**：US-09（AC-US-09.1~09.3）、US-10（AC-US-10.4）

**层级**：装配层（`bootstrap` / `config`）

**职责**：提供全局与按能力两级灰度开关及生效范围查询；负责旧链路退役与废弃代码清理；同步治理文档与版本上界登记。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `SessionPathRouter.use_session_graph() -> bool` | 方法 | 全局开关读取 | 兼容既有 `use_loop` 语义 |
| `SessionPathRouter.allowed_capabilities() -> set[str] \| None` | 方法 | 按能力放开清单 | None 表示不限制 |
| `SessionPathRouter.describe() -> dict` | 方法 | 生效范围查询 | 返回全局与按能力状态 |
| `retire_legacy_path() -> dict` | 方法 | 旧链路退役与清理 | 返回清理清单与结果 |

#### 配置项

| 配置键 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `session_graph_enabled` | bool | False | 全局开关（兼容既有 `session_loop_enabled`） |
| `session_graph_capabilities` | list[str] | [] | 按能力放开清单 |
| `langgraph_checkpointer` | str | "postgres" | 既有配置，不改语义 |

### 数据模型

无新增表。

### 模块验收检测

```bash
# 验收 1：两级开关已接线（AC-US-09.3）
grep -rn "session_graph_enabled" --include=*.py emily-core | wc -l
→ 预期输出：≥2（定义 + 至少一处读取）

# 验收 2：默认关，旧链路行为不变（AC-US-09.1）
uv run python scripts/golden_session_loop.py --cases emily-data/golden/session_kernel_cases.yaml --legacy
→ 预期输出：旧链路四类断言与迁移前一致

# 验收 3：切回不需数据回滚（AC-US-09.2）
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml --switch-back
→ 预期输出：切换后两路径均可完成，业务记录无重复

# 验收 4：清理到位（Q4 / AC-US-10.4）
grep -rn "PipelineBUS\|RealGuardian" emily-core/emily_core --include=*.py
→ 预期输出：0 命中

# 验收 5：版本上界已登记
grep -n "langgraph" emily-core/requirements.txt
→ 预期输出：含上界（如 `langgraph>=1.2,<2.0`）
```

**失败处理**：验收 1 仅 1 处说明开关未接线（宪法 Q6 不合格），补读取方；验收 2 出现差异说明迁移改动了旧路径行为，须回退。

---

## M11: 语料与度量模块（GoldenSet）

**依赖**：M3~M7、M9

**实现的需求**：全部 US 的回验支撑

**层级**：验收面（脚本）

**职责**：扩展验收语料覆盖新形态与新增场景（跨进程恢复、幂等重放、异常注入、门禁否决、部分完成），并输出可核对度量。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `load_cases(path: str) -> list[dict]` | 方法 | 加载语料 | 校验格式 |
| `run_case(case: dict, *, tag: str = "") -> dict` | 方法 | 执行单条并返回度量 | 含回复、调用清单、耗时 |
| `summarize(results: list[dict]) -> dict` | 方法 | 汇总度量 | 输出断言通过率 |

#### 语料结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `case_id` / `tag` | str | 标识与分组 |
| `actor` | dict | 操作者（真实 UUID） |
| `input` | str | 用户输入 |
| `expect` | dict | 期望：回复断言、调用清单断言、归档断言、耗时上限 |
| `restart` | bool | 是否要求重启后恢复 |

### 数据模型

无新增表。语料为文件，度量输出为文件。

### 模块验收检测

```bash
# 验收 1：语料可加载且格式正确
uv run python -c "from scripts.kernel_replay import load_cases; print(len(load_cases('emily-data/golden/session_kernel_cases.yaml')))"
→ 预期输出：≥ 12（覆盖 11 个 US）

# 验收 2：四类断言全过
uv run python scripts/kernel_replay.py --cases emily-data/golden/session_kernel_cases.yaml
→ 预期输出：能力命中、权限拦截、挂起续接、归档完整性四类断言全过

# 验收 3：恢复与幂等用例留痕
uv run python scripts/kernel_resume_probe.py --case suspend --restart
→ 预期输出：恢复成功且业务记录无重复
```

**失败处理**：验收 2 有用例失败先判定是编排问题还是语料期望偏差；语料期望偏差属规格问题，回退 req-review。

---

## 组装验证

所有模块完成后，运行端到端组装验证：

| 验证项 | 验证方式 | 预期结果 |
|--------|---------|---------|
| 规格覆盖完整 | 对照"追溯矩阵（US → 模块）" | 11 个 US 均有实现模块，无遗漏 |
| 结构正确 | `kernel_state_lint.py` 扫描 | 状态无领域对象、无外部实例，违规 0 |
| 契约正确 | `capability_catalog.py` 枚举 | 三类能力齐备且带 schema |
| 核心流程正确 | 端到端单轮业务消息 + 复合请求 | 回复自然衔接，归档含能力清单，无降级记录 |
| 异常路径正确 | 故障注入与越权演练 | 越权被拒留痕；异常收敛为可读回复；质量门有上限 |
| 恢复路径正确 | 挂起后重启续接 | 恢复成功且无重复业务记录 |
| 接线闭合 | 静态可达性扫描 | 新增配置、方法、注册项均有读取方（≥2 处命中） |
| 回退可用 | 开关关闭对照 | 旧链路行为与迁移前一致，无需数据回滚 |
| 治理不减 | 治理演练 | 权限、兜底、质量门、评审、审计逐条生效 |

```bash
# 端到端组装验证命令
uv run python scripts/cold_start.py --verify-kernel
→ 预期输出：初始化检查通过 → 语料回放全部断言通过 → 状态扫描 0 违规 → 汇总报告含各 US 覆盖状态
```

---

## 阶段反思指令

每完成一个模块的设计，在进入下一个模块之前，执行以下反思：

1. **检查设计完整性**：本模块的接口契约、数据模型、验收检测是否完整。
2. **检查追溯闭合**：本模块声明的 US 是否都有接口与验收覆盖；对照追溯矩阵有无遗漏。
3. **检查设计偏差**：是否有与规格（PRD）不符的设计。如发现规格缺陷，**回退 req-review**，不在计划内扩需。
4. **判断是否继续**：
   - 偏差 ≤ 1 个接口调整：直接修改本设计对应模块，继续。
   - 偏差 2~4 个接口或模块职责调整：在本设计末尾追加"v1.1 修订记录"，继续。
   - 偏差 > 4 个接口或架构方向变化：**停止**，报告用户，等用户决定是否重新生成设计。

---

## v1.1 修订记录

### v1.1（2026-09-11）：专家评审体系暂转休眠（依据需求基线 D10）

项目所有者决定：由专家手册驱动的多专家 Agent 体系暂不启用，保留代码、数据与既有开关，不删除、不改写。对本设计的范围影响如下：

| 影响对象 | 原设计 | 修订后 |
|---|---|---|
| M6 门禁判定 | 收敛三处判定 + 保留质量门与专家评审的否决效力 | 保留三处判定收敛与质量门；**专家评审不进入执行路径**，其判定点暂不接线（代码与节点保留） |
| M2 能力契约 | 全量契约含 4 个专家管理能力（`create_expert` / `approve_expert` / `toggle_expert` / `query_experts`） | 休眠期间这 4 项**移出会话可见能力集**；契约注册总数由 55 变为 51（45 工具 − 4 专家工具 + 8 SOP 能力 + 2 控制工具） |
| 既有开关 | `expert_review_enabled` 默认开启 | 休眠期间默认关闭；开关与接线保留，启封时置回 |
| US-06 / US-10 | 含评审否决效力相关验收 | 评审相关验收项随休眠暂缓，其余不变；PRD 需在下一次规格修订时登记（宪法 §1.4） |
| 测试报告 V1 | 基于 PRD V1 | 视为该次运行的历史快照，不追溯修改；评审相关结论在启封后单独复验 |

**启封条件**：后续独立需求完成设计并明确评审节点的归属（编排内判定分支，或可独立调用的能力）后，按该需求重新接线。

---

*本设计为概要设计（SD），由 req-plan 技能生成，遵循项目宪法 v1.1，产出 US→模块追溯。*
