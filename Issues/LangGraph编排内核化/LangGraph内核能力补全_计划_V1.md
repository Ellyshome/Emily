# LangGraph 内核能力补全 — 概要设计（SD）

> **基于规格（PRD）**：[LangGraph内核能力补全_PRD_V2.md](LangGraph内核能力补全_PRD_V2.md)（`LangGraph内核能力补全_PRD_V2.md`）
> **宪法版本**：v1.1
> **设计版本**：v1.0
> **级别**：System Design（概要设计）
> **目标**：把四项内核机制缺口按三批次（B1/B2/B3）一次改造到位，对外行为不变。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **系统设计师**（叠加：资深测试工程师，负责回归语料扩展），严格按模块顺序设计，逐模块验收，验证不通过不进入下一个模块。具体代码在编码阶段落地，本设计给出接口契约与实现约束。

---

## 硬约束（违反即失败）

> 项目铁律直接引用 [宪法 §2](../../.trae/skills/_shared/constitution.md)，不自行发明。

1. **禁止修改已有对外行为**：回复语义、权限裁剪、审计与归档口径、SOP 生效方式不得改变（PRD §4.4-3）。
2. **遵循宪法铁律 C0~C11**：逐条自检，并列出本设计涉及的铁律及遵守方式：

   | 铁律 | 本设计如何遵守 |
   |------|--------------|
   | C0 根治而非迁就 | 计划执行改走框架子图挂载，而非"检查点里手写一份计划游标" |
   | C2 分层不可跳 | 图只调用端口（能力执行、门禁、归档），不直接访问 Repository |
   | C8 唯一执行引擎 | 改动全部发生在既有唯一执行引擎内，不引入第二套编排 |
   | C11 功能注册接入 | 新增内核模块为引擎内部组件，不对外暴露注册项 |
   | Q4 无残留 | 被替换的节点内重试循环、自研上下文入口删除或显式标注 |
   | Q6 接线闭合 | 共享循环、节点级策略、上下文入口均有消费者（见接线矩阵） |

3. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告。
4. **遵循接口契约**：跨模块调用必须走约定接口，不绕过。
5. **不私自扩需**：发现规格缺陷回退 req-review，不在本设计里扩需。
6. **接线闭合（宪法 §3 Q6）**：任何产出物必须有消费者，无消费者不得进入编码。

---

## PRD 约束落实

| # | PRD 约束（原文摘录） | 本设计如何遵守 | 落在哪个模块 | 若无法遵守 |
|---|-------------------|--------------|------------|-----------|
| 1 | 必须复用既有检查点持久化能力，不得回退到进程内存态 | 子图不单独 compile 检查点，挂入父图后继承父线程检查点 | M1 | — |
| 2 | 不得引入 LangChain 主包；框架版本沿用既有锁定区间 | 仅用 `langgraph.types.RetryPolicy` / `langgraph.runtime.Runtime`（已在 1.2.11 内） | M3、M4 | — |
| 3 | 必须保持对外行为不变 | 两侧适配保持既有调用通道与文案；新增回归断言逐条比对 | M1~M5 | — |
| 4 | 回退颗粒度三档（B1 整组、B2、B3） | M1+M2 同属 B1；M3=B2；M4=B3；各模块独立可关 | 全部 | — |
| 5 | 不得把业务分支写入图的拓扑 | 子图/循环内核只承载机制，业务差异走注入点 | M1、M2 | — |
| 6 | 被替换实现不得保留为并列路径 | 节点内重试计数降级为终态判定；自研上下文入口为唯一标注兼容点 | M3、M4 | — |
| 7 | 同步更新约束 13 所指向的形态描述，与 B1 绑定 | 交付时同步更新 `CLAUDE.md` 约束 8/9 与全景文档 | M5 | — |
| 8 | 不得引入框架侧长期记忆存储 | 只切换运行时上下文通道，不引入 Store | M4 | — |

**未落实约束清单**：无。

---

## 追溯矩阵（US → 模块）

| US-ID | 需求一句话 | 实现模块 | 覆盖状态 |
|-------|-----------|---------|---------|
| US-01 | 计划执行纳入主图生命周期，可恢复、不重复副作用 | M1（主体）、M5（验收） | ✅ |
| US-02 | 重试与超时由节点级策略承担且可观测 | M3（主体）、M5（验收） | ✅ |
| US-03 | 会话与工单共享同一对话循环实现 | M2（主体）、M5（验收） | ✅ |
| US-04 | 内核上下文切换为框架官方机制并落地 | M4（主体）、M5（验收） | ✅ |

**缺失清单**：无。

---

## 系统架构概览

### 架构图

```
┌───────────────────────────────────────────────────────────────┐
│                        emily_core                              │
│                                                                │
│  ┌─────────────────── 内核层（本次改动落点）────────────────┐  │
│  │  emily_core/kernel/          ← 【新增 M2】共享对话循环内核 │  │
│  │    react_kernel.py                                        │  │
│  │  emily_core/session/                                      │  │
│  │    session_graph.py   ← 【改 M1/M2/M3/M4】父图 + 子图节点  │  │
│  │    plan_graph.py      ← 【改 M1】计划子图（状态键对齐）    │  │
│  │    kernel_state.py    ← 【改 M1/M4】状态键 + 上下文 schema │  │
│  │  emily_core/workitem/langgraph_engine/                    │  │
│  │    graph.py           ← 【改 M2/M3/M4】复用共享循环内核    │  │
│  │    agent/loop.py      ← 【改 M2】退化为适配层              │  │
│  └───────────────────────────┬───────────────────────────────┘  │
│                              │ 端口调用（不变）                  │
│  ┌───────────────────────────┴───────────────────────────────┐  │
│  │  能力层（不变）：tools/registry、capability_runner          │  │
│  │  领域层（不变）：services/、repositories/、permission/      │  │
│  │  外壳层（不变）：session/loop.py、scheduler/               │  │
│  └───────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

### 分层关系

| 新/改模块 | 所在分层 | 上层依赖 | 下层被依赖 |
|--------|---------|----------|-----------|
| M2 共享循环内核 `kernel/react_kernel.py` | 内核层（新增顶层包） | 无 | 会话图、工单图 |
| M1 计划执行纳图 `session/session_graph.py` + `plan_graph.py` | 内核层 | M2 | — |
| M3 节点级策略 | 内核层（两张图的节点注册处） | M1、M2 | — |
| M4 上下文官方通道 | 内核层（`kernel_state.py` + 两张图 + 外壳调用点） | M2 | — |

---

## 数据流设计

```
入站消息 → 会话池 → graph_wiring(端口装配) → SessionGraphRunner
                                                    │ context=KernelContext（M4）
                                                    ↓
   fast_reply → understand ⇄ execute → summarize     ← M2 共享循环内核
                    │
                    ├─ _should_plan → plan_build → [计划子图节点] → plan_echo   ← M1
                    └─ _should_suspend → suspend → understand
```

### 核心流程

| 流程 | 触发条件 | 参与者 | 数据流向 | 异常路径 |
|------|---------|--------|---------|---------|
| 计划执行（M1） | 复合诉求命中计划门 | 父图 → 计划子图 → 父图 | 父图写 `plan_steps` → 子图逐层执行 → 回灌 messages | 步骤失败 → 级联跳过（既有语义）；子图异常 → 父图收口 |
| 循环单轮（M2） | 每次模型调用 | 共享内核 → 两侧适配 | 内核产出中性结果 → 适配层映射为各自状态键 | 连续文本 → 纠错策略终止 |
| 瞬时失败重试（M3） | LLM 瞬时异常 | 框架节点策略 | 异常抛出 → 框架重试 → 耗尽后节点抛错 | 调用方兜底出可读回复 |
| 上下文传递（M4） | 图启动 | 外壳 → 图配置 → 节点 | `context=KernelContext` → 节点 → 兼容桥（标注） | 未传上下文 → 显式报错（不静默降级） |

---

## 模块依赖图

```
M5(回归语料扩展) ──────────────┐（验收全模块）
                              ↓
M1(计划纳图) ─→ M2(共享循环内核) ─→ M3(节点级策略) ─→ M4(上下文官方通道)
```

---

## 交付物总览

| 模块 | 实现的 US | 交付物类型 | 新增/修改 | 核心接口/类 |
|------|----------|-----------|----------|---------------|
| M1 | US-01 | 图拓扑 + 状态键 | 修改 | `build_plan_subgraph()` 挂为节点；`plan_build` / `plan_echo` 节点；计划状态键 |
| M2 | US-03 | 内核模块 + 两侧适配 | 新增+修改 | `react_kernel.py`：`LoopPorts`、`llm_step()`、`tool_step()`、`decide_next()` |
| M3 | US-02 | 节点级策略声明 | 修改 | `build_retry_policy()` / `node_timeout()`（配置驱动） |
| M4 | US-04 | 上下文 schema + 调用点 | 新增+修改 | `KernelContext`、`context_schema=`、`bind_from_runtime()` 兼容桥 |
| M5 | 全部 | 回归语料 + 文档 | 新增+修改 | `scripts/kernel_regression.py`（新增断言）；`CLAUDE.md` 形态描述 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily_core/session/session_graph.py` | 修改 | 计划节点改为子图挂载；循环节点改用共享内核；节点声明重试/超时；接入上下文 schema |
| `emily_core/session/plan_graph.py` | 修改 | 状态键与父图对齐（键名统一），子图不再单独持有执行循环 |
| `emily_core/session/kernel_state.py` | 修改 | 新增计划子图状态键；新增 `KernelContext` 上下文 schema |
| `emily_core/session/graph_wiring.py` | 修改 | 传 `context=` 而非仅依赖 ContextVar 绑定 |
| `emily_core/workitem/langgraph_engine/graph.py` | 修改 | 循环节点改用共享内核；节点声明重试/超时；接入上下文 schema |
| `emily_core/workitem/langgraph_engine/agent/loop.py` | 修改 | 退化为适配层：只保留工单侧专有控制工具与状态映射 |
| `emily_core/workitem/langgraph_engine/state.py` | 修改 | 上下文入口改为由官方通道注入（兼容桥标注） |
| `emily_core/workitem/scheduler.py` | 修改 | 传 `context=`；图级异常兜底为可读回复与失败收尾（M3 配套） |
| `scripts/session_graph_replay.py` | 修改 | 保留 10 项既有断言，另接 M5 新增断言 |
| `CLAUDE.md` / `docs/Manual/业务模块与运转全景.md` | 修改 | 同步内核形态描述（约束 13 适用条件） |

---

## 接线矩阵（Producer → Consumer）

| 产出物 | 类型 | 生产模块 | 消费方（模块 / 调用点） | 接线点 | 状态 |
|--------|------|---------|----------------------|--------|------|
| 计划子图（编译产物） | 图 | M1 | 会话父图 | `add_node(NODE_PLAN, ...)` | ✅ |
| 计划状态键 | 状态字段 | M1 | 父图路由与回灌节点 | `route_after_plan` / `plan_echo` | ✅ |
| 共享循环内核 | 模块 | M2 | 会话图 + 工单图 | 两处节点工厂调用 | ✅（≥2 消费者） |
| 节点级重试/超时策略 | 配置 | M3 | 两图节点注册 | `add_node(..., retry_policy=, timeout=)` | ✅ |
| 上下文 schema | 图配置 | M4 | 两张图 + 外壳调用点 | `ainvoke(..., context=)` | ✅ |
| 回归断言 | 脚本 | M5 | 内核维护者 / 测试报告 | `scripts/kernel_regression.py` | ✅ |

**未接线清单**：无。

---

## 断线模式自查（宪法 Q6 / C11）

| 断线模式 | 本次是否新增 | 消费方已接线？ | 接线点 / 证据 |
|---------|------------|--------------|--------------|
| 新增配置项 | 是（重试/超时参数） | 是 | 节点注册处读取 + 容器实测 |
| 新增工具 / 公开方法 | 是（共享内核 API） | 是 | 会话图与工单图各一处调用 |
| 新增 DB 列 | 否 | — | 本次无数据模型变更 |
| 新增文件产出 | 否 | — | — |
| 新增注册项 | 否 | — | 内核内部组件，不经注册通道 |

---

## 独立脚本架构设计

### 独立脚本清单

| # | 脚本（建议命名） | 职责 | 关键参数 | `--dry-run` 行为 |
|---|----------------|------|---------|------------------|
| 1 | `scripts/kernel_regression.py` | 内核回归断言（含 M1~M4 新增项） | `--json` | 仅打印将执行的断言清单 |

### 聚合薄壳

| # | 脚本（建议命名） | 串联逻辑 |
|---|----------------|---------|
| 1 | `scripts/session_graph_replay.py` | 既有 10 项 → 调 `kernel_regression.py` 的断言集 → 汇总 |

### 脚本交互关系

```
session_graph_replay.py（既有回放入口，保持可独立运行）
  ├── 既有 10 项断言            → 行为不变证明
  └── kernel_regression.py      → M1/M2/M3/M4 新增断言
```

---

## M1: 计划执行纳父图生命周期（B1）

**依赖**：无

**实现的需求**：US-01（覆盖 AC-US-01.1 ~ 01.5）

**层级**：内核层（会话编排图 + 计划子图）

**职责**：把计划子图从"节点内手工调用"改为"父图的一个节点"，使计划执行状态进入父图检查点命名空间，并使步骤级进度可被父图事件流观察。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `build_plan_subgraph` | 方法 | `(*, capability_executor, specs=None, max_depth=3, on_layer_done=None) -> StateGraph` | 编译产物**不传 checkpointer**，由父图承载 |
| `build_plan_build_node` | 节点工厂 | `(*, plan_builder, capability_spec_provider) -> node` | 生成计划并写入父图状态（含契约校验结果） |
| `build_plan_echo_node` | 节点工厂 | `() -> node` | 把子图结果回灌 `messages` / `capability_calls`，置 `_plan_done` |
| `PlanRunner` | 类 | `.run(steps, *, plan_id) -> dict` | **保留为独立/回放执行入口**，生产路径不再经它调用 |

#### 依赖接口（本模块需要调用哪些已有接口）

| 现有接口 | 来源模块 | 调用目的 |
|---------|---------|---------|
| `validate_steps(steps, specs)` | `plan_graph` | 入计划前契约校验（不丢参） |
| `cascade_skip` / `next_layer` | `plan_graph` | 分层与级联跳过（既有语义不变） |

### 状态键契约

| 键 | 类型 | 归属 | 说明 |
|----|------|------|------|
| `plan_steps` | list | 父图写、子图读 | 计划步骤（步骤级恢复的输入） |
| `plan_step_results` | list（add 归约） | 子图写、父图读 | 并行回填结果 |
| `plan_done` / `plan_failed` / `plan_skipped` | list | 子图单点写 | 终态集合 |
| `plan_text` | str | 子图写、父图读 | 供对话回灌的成果摘要 |
| `plan_depth` / `plan_max_depth` / `plan_layer` / `plan_processed` / `plan_problems` | int / list | 子图写 | 深度守卫与游标 |

### 数据模型

无数据模型变更（状态键为进程内图状态，随检查点持久化，不新增业务表）。

### 模块验收检测

```bash
# 验收 1：子图已挂为父图节点（非手工调用）
docker exec emily-core python -c "from emily_core.session.session_graph import build_session_graph; ..."
→ 预期输出：父图节点集合包含 plan 节点，且该节点为编译后的图对象（非普通协程函数）

# 验收 2：回放用例 6（计划层内并行）仍通过
docker exec emily-core python /app/scripts/session_graph_replay.py
→ 预期输出：[PASS] 6 计划层内并行且分两层完成

# 验收 3：计划执行的检查点命名空间归属父线程
docker exec emily-core python /app/scripts/kernel_regression.py --case plan-checkpoint
→ 预期输出：子图状态可在父 thread 的 checkpoints 中查到（ns 以父节点名开头）
```

**失败处理**：若子图状态键与父图 schema 不匹配（键被静默丢弃），检查键名对齐表并修正 `KernelState` 声明；若父图节点不接受图对象，回退为"节点内 `ainvoke` + 传入父 config 的 checkpoint_ns"并记录偏差。

---

## M2: 共享对话循环内核（B1）

**依赖**：M1（形态定稿后接入）

**实现的需求**：US-03（覆盖 AC-US-03.1 ~ 03.5）

**层级**：内核层（新增顶层包 `emily_core/kernel/`）

**职责**：把"提示装配 → 模型调用 → 结果归一 → 消息回填 → 工具执行 → 迭代记账 → 文本纠错"这套 ReAct 循环机制实现**一份**，会话图与工单图各以薄适配层消费之。

### 接口契约

#### 对外接口

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `LoopPorts` | dataclass | `llm_call / tool_specs / prompt / history / user_input / execute / max_iterations / text_correction` | 循环的全部差异点（注入） |
| `llm_step` | 方法 | `async (state, *, ports, keys) -> dict` | 单轮模型调用 + 归一 + 记账；返回中性补丁 |
| `tool_step` | 方法 | `async (state, *, ports, keys) -> dict` | 执行待处理调用 + 回填工具消息；返回中性补丁 |
| `decide_next` | 方法 | `(state, *, keys, max_iterations) -> str` | 返回 `tool` / `retry` / `final` / `cap` / `terminal` 中性判定 |

#### 依赖接口

| 现有接口 | 来源模块 | 调用目的 |
|---------|---------|---------|
| `llm_client.chat_messages` / 会话侧 `_llm_call` | 基础设施 / 会话循环 | 由 `ports.llm_call` 注入，内核不直接依赖 |

### 数据模型

无数据模型变更。

### 模块验收检测

```bash
# 验收 1：消费者可达性（≥2）
docker exec emily-core python -c "import inspect,emily_core.session.session_graph as s,emily_core.workitem.langgraph_engine.graph as g; ..."
→ 预期输出：会话图与工单图各命中共享内核构造函数一次（≥2 处）

# 验收 2：循环本体内无按侧分支
grep -rn "workitem\|session" emily-core/emily_core/kernel/react_kernel.py
→ 预期输出：0 命中（内核不感知调用方）

# 验收 3：两侧回归全通过
docker exec emily-core python /app/scripts/session_graph_replay.py ; docker exec emily-core python /app/scripts/kernel_regression.py --case workitem-loop
→ 预期输出：会话侧 10 项 PASS；工单侧循环路径 PASS
```

**失败处理**：若两侧对"文本回复"的处置差异无法由注入点表达，允许把该差异显式登记为端口字段（不改为内核分支）；若回归出现差异，逐条比对消息序列，定位到具体字段后修正映射。

---

## M3: 节点级重试与超时（B2）

**依赖**：M2

**实现的需求**：US-02（覆盖 AC-US-02.1 ~ 02.5）

**层级**：内核层（两张图的节点注册处 + 循环内核异常分类）

**职责**：把瞬时失败的**重试**与单节点**超时**交给框架声明式策略；节点内保留的计数只用于终态判定。

### 接口契约

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `build_retry_policy` | 方法 | `(config) -> RetryPolicy` | 次数与退避由配置驱动（默认：2 次、指数退避、仅瞬时异常） |
| `node_timeout` | 方法 | `(config, node_name) -> int` | 单节点超时秒数（配置可覆盖，默认不早于既有业务超时） |
| `classify_llm_error` | 方法 | `(exc) -> "transient" \| "terminal"` | 瞬时异常上抛交给框架重试；终态异常由内核转终态判定 |

### 数据模型

无数据模型变更。

### 模块验收检测

```bash
# 验收 1：策略声明可达（≥1 消费者）
grep -rn "retry_policy=" emily-core/emily_core
→ 预期输出：≥2 处命中（会话图 + 工单图节点注册）

# 验收 2：节点内不再有重试循环
grep -rn "_llm_fail_count" emily-core/emily_core
→ 预期输出：仅剩终态判定处使用，无 sleep/循环式重试

# 验收 3：瞬时失败可观测重试
docker exec emily-core python /app/scripts/kernel_regression.py --case retry-transient
→ 预期输出：框架重试次数与事件可观测；耗尽后用户仍得可读回复
```

**失败处理**：若框架重试与既有"连续失败转 error_analysis"冲突，以"瞬时异常走框架、终态异常走内核"为界；若超时设置导致正常长任务被中断，调高默认值并记录取值依据。

---

## M4: 内核上下文官方通道（B3）

**依赖**：M2

**实现的需求**：US-04（覆盖 AC-US-04.1 ~ 04.5）

**层级**：内核层（状态模块 + 两张图 + 外壳调用点）

**职责**：内核上下文的**入口**改由图配置承载（声明 `context_schema`、经 `context=` 传入、节点从运行时上下文读取）；能力层所需的进程内绑定保留为**唯一且显式标注的兼容桥**，不作为入口。

### 接口契约

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `KernelContext` | dataclass | `conversation_id / actor_ref / user_id / bus / message_ref / db_message_id` | 图运行时上下文（不进状态、不落检查点） |
| `bind_from_runtime` | 方法 | `(runtime) -> KernelContext` | 从官方运行时上下文取出并绑定兼容桥（标注点唯一） |
| `SessionGraphRunner` | 类 | `.run(..., context=KernelContext)` | 外壳经官方通道传入 |

### 数据模型

无数据模型变更；不引入框架侧长期记忆存储（PRD 约束 8）。

### 模块验收检测

```bash
# 验收 1：上下文入口为官方通道
grep -rn "context=KernelContext\|context_schema" emily-core/emily_core
→ 预期输出：两张图各声明 schema；外壳调用点传 context

# 验收 2：自研入口读取点收敛为唯一标注兼容点
grep -rn "set_bus_context\|bind_tool_context" emily-core/emily_core
→ 预期输出：仅剩显式标注的兼容桥处调用（其余为定义与测试）

# 验收 3：跨进程续接不回归
docker exec emily-core python /app/scripts/kernel_regression.py --case suspend-across-restart
→ 预期输出：挂起 → 重启 → 续接全路径可用
```

**失败处理**：若领域对象经官方上下文传递在容器内不可行，回退为"config 承载标识 + 按标识重建"，并把结论写入执行报告（不静默降级）。

---

## M5: 回归语料与形态描述同步（贯穿）

**依赖**：M1~M4

**实现的需求**：全部（验收支撑 + 约束 13 的形态描述更新）

**职责**：为 M1~M4 各新增可执行断言，并同步更新内核形态描述文档。

### 接口契约

| 接口/类 | 类型 | 签名 | 说明 |
|---------|------|------|------|
| `kernel_regression.py` | 脚本 | `--case {plan-checkpoint, workitem-loop, retry-transient, suspend-across-restart, all}` | 内核专用回归断言，零副作用 |

### 模块验收检测

```bash
docker exec emily-core python /app/scripts/kernel_regression.py --case all
→ 预期输出：各断言 PASS，退出码 0
```

---

## 组装验证

| 验证项 | 验证方式 | 预期结果 |
|--------|---------|---------|
| 规格覆盖完整 | 对照追溯矩阵 | 4 个 US 均有实现模块 |
| 计划执行可恢复 | 重启续接（TC-01-01） | 已完成步骤不重复，业务记录无重复 |
| 节点级策略生效 | 静态扫描 + 故障注入（TC-02-01/02） | 策略有消费者；重试/超时可观测 |
| 循环单份实现 | 静态可达性（TC-03-01/04） | 消费者 ≥2；差异清单为 0 |
| 上下文入口切换 | 静态扫描 + 跨进程续接（TC-04-01/02） | 入口唯一；续接可用 |
| 对外行为不变 | 回放 10 项 + 容器实战 | 全通过 |

```bash
# 端到端组装验证
docker exec emily-core python /app/scripts/session_graph_replay.py
docker exec emily-core python /app/scripts/kernel_regression.py --case all
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下项目里最近的事件" --sender "<真实用户名>"
→ 预期输出：回放全 PASS；真实链路 HTTP 200 + SSE 回复文本正常
```

---

## 阶段反思指令

每完成一个模块，进入下一个之前执行：

1. **检查设计完整性**：接口契约、状态键、验收检测是否完整
2. **检查追溯闭合**：模块声明的 US 是否有接口/验收覆盖
3. **检查设计偏差**：与 PRD 不符处回退 req-review；偏差 ≤1 处接口调整直接改，2~4 处追加修订记录，>4 处停止报告
4. **判断是否继续**

---

*本设计为概要设计（SD），由 req-plan 技能模板生成，遵循项目宪法，产出 US→模块追溯。*
