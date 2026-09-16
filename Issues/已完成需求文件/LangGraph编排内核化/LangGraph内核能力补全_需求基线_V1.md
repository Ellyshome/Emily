# LangGraph 内核能力补全 — 需求基线 V1

> **定位**：内核机制补全。对 [LangGraph编排内核化_需求基线_V1](LangGraph编排内核化_需求基线_V1.md) 落地后的复核结论是"图形态选对了、框架能力没用尽"，本基线把 4 项缺口登记为可验收事项。
> **状态**：草案，待评审
> **性质**：内核自身的机制升级，不属业务功能增长（`CLAUDE.md` 约束 13）。须以专项基线立项、评审通过后实施
> **日期**：2026-09-15
> **上游**：[LangGraph编排内核化_需求基线_V1](LangGraph编排内核化_需求基线_V1.md)（D1/D5/D6）、[LangGraph编排内核化_复盘_V1](LangGraph编排内核化_复盘_V1.md)、[LangGraph执行引擎替换_非完美项分析_V1](../../.claude/plans/LangGraph执行引擎替换_非完美项分析_V1.md)
> **下游**：req-review 产出 PRD
> **宪法版本**：v1.1

---

## 一、背景与目标

原基线要求"框架约束用满"（D6），并禁止"引入框架但以内存态与自研通道为默认的装饰性使用"（§六.9）。复核结论：**装饰性使用不成立，但 D6 未被完全满足**，缺口集中在执行引擎的**耐久性**（重试与计划执行不落检查点）与**复用性**（两条主循环两份实现），与业务正确性无关。

目标：将 4 项缺口逐条登记为可判定事项，形成独立于业务开发的专项。

---

## 二、事项速览

| 编号 | 一句话 | 缺口类型 | 触碰内核文件 | 优先级 | 依赖 |
|---|---|---|---|---|---|
| **I1** | 计划子图不是图内子图，而是节点内手工 `ainvoke` | 耐久性 | 是 | P1 | — |
| **I2** | 重试与超时未用节点级策略，节点内手写计数 | 耐久性 | 是 | P1 | — |
| **I3** | 两条主循环各写一份 ReAct，未抽成可复用子图 | 复用性 | 是 | P2 | I1 |
| **I4** | Runtime context / BaseStore 未用，ContextVar 自研替代 | 机制一致性 | 是 | P3 | 仅评估 |

---

## 三、逐条事项

### I1 计划子图改为真子图挂载

**现状与证据**
- [session_graph.py](../../emily-core/emily_core/session/session_graph.py) 的 `make_plan` 在节点内 `build_plan_subgraph(...)` + `PlanRunner(...).run()`
- [plan_graph.py](../../emily-core/emily_core/session/plan_graph.py) 的 `build_plan_subgraph` 编译时不传 checkpointer，`PlanRunner.run` 自行 `ainvoke`
- 原 PRD 将"子图边界"列为"实现结构，规格不预设"，故本项不算违规，属框架能力闲置

**代价（可验证）**
- 子图与父图不共享 thread/checkpointer → 计划步骤状态不落检查点，父图重放时计划执行不可恢复
- 子图节点不进入父图 `astream` → 前端最多看到"plan"一个粒度
- 子图内无法 `interrupt`

**修法方向**
编译好的计划子图直接作为节点挂入父图（LangGraph 原生支持）；其状态与检查点纳父图线程。

**验收判据**
1. 静态：`build_plan_subgraph` 的产物被 `add_node` 挂载，父图不再经手工 `ainvoke` 调用子图
2. 运行时：计划执行到中途重启容器，重放不产生重复副作用（业务表无重复记录，判据同原基线 A2）
3. 流式：计划步骤的节点级进度可从父图 `astream` 观察到

---

### I2 重试与超时改用节点级策略

**现状与证据**
- D6 明写"重试与超时用节点级策略"；实际为节点内手写：[loop.py](../../emily-core/emily_core/workitem/langgraph_engine/agent/loop.py) 的 `_llm_fail_count`、`_text_fallback_count` 计数与 try/except 分支
- 全仓无 `retry_policy=`、无 `add_node(..., timeout=)`；非完美项 G3 记录的"retry 参数被移除"实为旧参数名变更，1.x 的对应物是 `RetryPolicy` / `retry_policy=`

**代价（可验证）**
- 重试不落检查点、不参与框架调度，退避与次数在节点外不可观测
- 同一件事（失败后重试）在每个节点各写一套，行为易漂移

**修法方向**
节点声明式挂 `retry_policy` 与 `timeout`；节点内手写计数仅保留用于**终态判定**（如连续失败转 error_analysis），不承担重试本身。

**验收判据**
1. 静态：`grep -rn "retry_policy=" emily-core/emily_core` ≥ 1 处命中，且命中处为节点注册
2. 故障注入：构造 LLM 连续失败，重试由框架承担，次数可从检查点/日志观测
3. 行为不变：达到重试上限后的用户可见回复与现状一致（回归语料 `Issues/测试用例/12_内核编排专项.md`）

---

### I3 抽出共享 agent loop 子图，两张图复用

**现状与证据**
- 会话图 `understand ⇄ execute`（[session_graph.py](../../emily-core/emily_core/session/session_graph.py)）与工单图 `agent_node ⇄ tool_node`（[loop.py](../../emily-core/emily_core/workitem/langgraph_engine/agent/loop.py)）同构：LLM 出 tool_call → 执行 → 回灌 → 循环，各自带上限、纠错、收尾
- 原基线 D1 的目标是"两条主循环共享同一执行形态"：形态共享，**实现是两份**
- 已出现的行为分叉：工单侧有 DSML 文本纠错与 `complete_work`/`ask_user` 控制工具，会话侧没有；会话侧有 plan/gate/suspend，工单侧没有

**代价（可验证）**
- 同一机制两处维护，修复与改进需双份落地
- 差异以代码分叉形式存在，无法从配置看出"该有/不该有"

**修法方向**
把 ReAct 循环抽为一份可编译子图，两侧差异以**注入点**（工具集、控制工具、上限、收尾节点）表达，而非复制代码。依赖 I1 的子图挂载能力。

**验收判据**
1. 静态：循环子图构建函数被两张图同时消费（定义处 vs 使用处可达性扫描 ≥ 2 个消费者）
2. 行为差异清单为 0：两侧差异全部落在注入点声明中，循环本体单份
3. 回归：`Issues/测试用例/11_外壳稳定性与引擎回退.md`、`12_内核编排专项.md` 全通过；`scripts/session_graph_replay.py` 回放用例全通过

---

### I4 Runtime context / BaseStore 切换评估（仅评估，不强制实施）

**现状与证据**
- 领域对象经 contextvars 传递（[state.py](../../emily-core/emily_core/workitem/langgraph_engine/state.py) 的 `set_bus_context` / [kernel_state.py](../../emily-core/emily_core/session/kernel_state.py) 的 `bind_tool_context`），为 G1 时代绕开"领域对象进 state"的历史解法
- LangGraph 1.x 提供官方等价机制：runtime context（`context_schema` + `Runtime`）与 `BaseStore`；全仓无二者使用
- [studio_entry.py](../../studio_entry.py) 因不传 checkpointer 只能用桩件，无法以真机依赖观察内核

**代价**
- ContextVar 不是图的官方配置一部分 → 不被检查点识别，跨进程恢复需手动重放
- 接 LangGraph Studio / Platform 级观测会受阻

**修法方向**
先出评估结论（切官方机制的成本收益、是否需要 `BaseStore` 替代现有长期记忆表），再决定是否实施；不实施须显式记录理由。

**验收判据**
1. 产出评估结论并归档：收益 / 成本 / 是否实施 / 理由
2. 若实施：`grep -rn "context_schema\|Runtime"` 有命中，且跨进程续接回归通过
3. 若不实施：本项以"经评估不实施"关闭，不保留半接线代码

---

## 四、实施约束

1. **约束 13 合规**：本基线属"内核自身机制升级"，须评审通过后实施；实施后同步更新约束 13 所指向的形态描述与 `CLAUDE.md` 约束 8/9 的表述。
2. **对外行为不变**：回复语义、权限裁剪、审计与归档口径、SOP 生效方式不变。回归锚点：`Issues/测试用例/11_外壳稳定性与引擎回退.md`、`12_内核编排专项.md`、`scripts/session_graph_replay.py`。
3. **依赖纪律**：不引入 LangChain 主包（沿用原基线 D7），LangGraph 版本沿用 `langgraph>=1.2,<2.0`。
4. **可独立回退**：I1、I2、I3 各自可单独回退，不得互相绑定成一次不可拆的切换。
5. **证据驱动**：每条判据须有静态可达性或运行时证据（宪法 Q2）。
6. **无残留**：改动落地后不得保留被替换的双实现；确需保留须显式标注原因（宪法 Q4）。

---

## 五、非目标

1. 不重写业务能力本体（工具实现、SOP 内容、检索管线、全景节点状态机保持不变）。
2. 不改变权限模型与审计口径。
3. 不引入 LangGraph Platform 或任何云端托管依赖。
4. **不做业务拓扑图化**：禁止按业务切节点、把业务分支写入条件边——图的简单形态是设计意图，不因本基线而改变。
5. 流式 token 级输出不在本基线范围（列为观察项，取决于产品是否需要打字机效果）。

---

## 六、分期建议

| 阶段 | 范围 | 通过条件 |
|---|---|---|
| P1 | I1 + I2 | 各自判据通过，回归语料全通过 |
| P2 | I3 | 行为差异清单为 0，两张图回归全通过 |
| P3 | I4 | 评估结论归档（实施或经评估关闭） |

---

## 七、开放问题

| 编号 | 问题 | 待定于 |
|---|---|---|
| Q1 | I1 与 I3 是否合并为一次内核形态调整（子图挂载能力为 I3 前置，合并可省一次全量回归） | 评审 |
| Q2 | I2 的退避策略与超时取值如何与既有 iteration cap 对齐 | PRD |
| Q3 | I4 是否随"Studio 可观测"需求一并决策，或本基线内单独关闭 | 评审 |
| Q4 | I3 抽出共享子图后，会话侧 plan/gate/suspend 节点的归属：留在父图还是随子图 | PRD |

---

## 附录 现状证据索引

| 类型 | 位置 |
|---|---|
| 会话编排图 | `emily-core/emily_core/session/session_graph.py` |
| 计划子图 | `emily-core/emily_core/session/plan_graph.py` |
| 工单图与 agent loop | `emily-core/emily_core/workitem/langgraph_engine/graph.py`、`agent/loop.py` |
| 状态与上下文 | `emily-core/emily_core/workitem/langgraph_engine/state.py`、`emily-core/emily_core/session/kernel_state.py` |
| 检查点 | `emily-core/emily_core/workitem/langgraph_engine/checkpointer.py` |
| 依赖清单 | `emily-core/requirements.txt` |
| 回归语料 | `Issues/测试用例/11_外壳稳定性与引擎回退.md`、`12_内核编排专项.md`、`scripts/session_graph_replay.py` |
| 上游文档 | `Issues/LangGraph编排内核化/LangGraph编排内核化_需求基线_V1.md`、`_复盘_V1.md`、`.claude/plans/LangGraph执行引擎替换_非完美项分析_V1.md` |
