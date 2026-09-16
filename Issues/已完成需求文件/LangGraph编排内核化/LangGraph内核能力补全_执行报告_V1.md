# LangGraph 内核能力补全 — 执行报告 V1

> **日期**：2026-09-15
> **依据**：[需求基线_V1](LangGraph内核能力补全_需求基线_V1.md) → [PRD_V2](LangGraph内核能力补全_PRD_V2.md) → [计划_V1](LangGraph内核能力补全_计划_V1.md)
> **执行环境**：emily-core 容器（langgraph 1.2.11）+ emily-postgres（生产检查点）
> **结论**：B1 / B2 / B3 三批次全部落地，新增 7 组内核回归断言 **27 项全通过**、既有回放 **11 项全通过**、容器真实链路两次端到端通过。**遗留 2 项未做**（见 §七），其中 1 项为既有的 planner JSON 缺陷导致生产计划支路未端到端打通。

---

## 一、批次状态

| 批次 | US | 模块 | 状态 | 证据 |
|------|----|------|------|------|
| **B1** | US-01 计划执行纳主图生命周期 | M1 | ✅ 完成 | `plan-node` 6 项 + `plan-node-pg` 2 项 + 生产检查点 SQL |
| **B1** | US-03 两条主循环共享对话循环 | M2 | ✅ 完成 | `loop-shared` 3 项 + `workitem-loop` 3 项 |
| **B2** | US-02 节点级重试与超时 | M3 | ✅ 完成 | `retry-policy` 3 项 + `retry-transient` 2 项 + `node-timeout` 1 项 |
| **B3** | US-04 内核上下文官方通道 | M4 | ✅ 完成 | `ctx-official` 5 项 |
| — | 回归语料与形态描述 | M5 | ✅ 完成 | 新增 `scripts/kernel_regression.py`；`CLAUDE.md` 约束 9b |

---

## 二、执行前形态 vs 执行后形态（核心对照）

### 2.1 图拓扑

**执行前**

```
会话图（父图）:  fast_reply → understand ⇄ execute → summarize
                                    ↘ error_analysis
   plan 节点 = 普通节点，内部手工 build_plan_subgraph() + PlanRunner().run()
                → subgraph.ainvoke(state, {"recursion_limit": 200})   ← 无 thread、无 checkpointer

工单图:  created → routing → executing → agent_node ⇄ tool_node → summarizing → quality_gate
                                              ↘ error_analysis / expert_review
```

**执行后**

```
会话图:  fast_reply → understand ⇄ execute → summarize → (END)
                              ↘ error_analysis
         understand ──▶ plan_build ──▶ **plan（子图节点）** ──▶ plan_echo ──▶ understand
                       （生成计划+契约校验）  （编译产物直接挂为节点，继承父线程检查点）

工单图:  节点集合不变；agent_node / tool_node 退化为共享内核的**适配层**
```

| 维度 | 执行前 | 执行后 |
|------|--------|--------|
| 计划执行的图可见性 | 父图上只有 1 个 `plan` 节点，子图不可见 | 父图上 `plan_build / plan / plan_echo` 三段可见，`plan` 节点**挂有 CompiledStateGraph 子图** |
| 计划执行的检查点 | **完全没有**（子图 `compile()` 不带 checkpointer，且 ainvoke 未传 thread） | 落在父线程的子命名空间：`thread=kr-plan-pg` 下 `checkpoint_ns=plan:75b37bcb…` 共 **6 条** checkpoint（父线程 9 条） |
| 计划步骤进度粒度 | 只有"计划整体执行中" | 计划步骤级（子图节点纳入父图执行流） |
| 计划状态键 | 子图内部 `steps/done/failed/_processed`，与父图 schema 不通 | 父图 schema 声明 `plan_*`（回写）＋子图内部 `_plan_*`（刻意不进父图，防跨轮残留） |

### 2.2 对话循环实现

| 维度 | 执行前 | 执行后 |
|------|--------|--------|
| 实现份数 | **2 份**：`session/session_graph.py`（understand ≈100 行 + execute ≈60 行）、`workitem/langgraph_engine/agent/loop.py`（agent_node ≈200 行 + tool_node ≈140 行） | **1 份**：`kernel/react_kernel.py`（`llm_step` / `tool_step` / `decide_next`） |
| 差异表达 | 两份代码各自分支（工单侧有 DSML 纠错与 `complete_work`/`ask_user`，会话侧有 plan/gate/suspend） | `LoopPorts` 注入：提示/工具集/执行通道/控制工具/纠错策略/异常分类 |
| 内核是否感知调用方 | — | 否（静态断言：内核源码内无 `session` / `workitem` 字面量，`leaks=[]`） |
| 消费者 | — | 2 处（会话图 + 工单图），静态可达性断言通过 |

### 2.3 重试与超时

| 维度 | 执行前 | 执行后 |
|------|--------|--------|
| 重试位置 | **节点内手写**：`_llm_fail_count` 连续 3 次失败 → abort；异常被 try/except 吞掉转 error_analysis | **框架节点级**：`RetryPolicy`（瞬时故障、`max_attempts=2`、指数退避、jitter）挂在 `understand` / `agent_node` / `plan_build` / `expert_review` |
| 异常分类 | 无（一律转 error_analysis） | `default_classify_error`：transient → **上抛交框架重试**；fatal → 内核转结构化终态 |
| 超时 | **无** | 节点级 `timeout=`（默认 300s，工具/执行类 180s，配置可覆盖）；实测 1s 设置下 `NodeTimeoutError` 在 **1.01s** 中断 |
| 安全边界 | — | 执行类节点（`tool_node`/`execute`）**不挂重试**（避免重复副作用）；挂起类节点（`tool_node` 内含 `interrupt`、`suspend`）**不挂超时**（避免误杀挂起） |
| 重试耗尽 | 节点内 abort | 上抛 → 调用方兜底：会话侧 `handle_via_graph`、工单侧 `_readable_graph_failure()` 转用户可读文案 |

### 2.4 上下文入口

| 维度 | 执行前 | 执行后 |
|------|--------|--------|
| 入口 | 自研 ContextVar：`set_bus_context(ctx)`（工单）、`bind_tool_context(ToolContext(...))`（会话，含续接路径） | 框架官方运行时上下文：图声明 `context_schema=KernelContext`，调用方 `ainvoke(..., context=KernelContext(...))` |
| 节点读取 | 只能读进程内通道 | `runtime.context` 官方读取：会话侧 `triggered_by` 取自官方 actor（实测取到 `OFFICIAL` 而非 state 回退值）；工单侧 `_ctx_from_runtime` 优先官方 bus |
| 自研通道调用点 | 分散在多处（scheduler / runner / graph_wiring） | **仅 1 处**：`KernelContext.bind_compat()`（唯一标注兼容桥，静态扫描 `offenders=[]`），解释见 `kernel/context.py` |
| 检查点/观测可见性 | 上下文不在图配置内 | 上下文是图配置的一部分（`context_schema`），检查点记录可见 `plan:*` 等命名空间与线程归属 |

---

## 三、改动清单

**新增**

| 文件 | 说明 |
|------|------|
| `emily-core/emily_core/kernel/__init__.py` | 内核共享组件包 |
| `emily-core/emily_core/kernel/react_kernel.py` | 共享对话循环内核（M2）：`LoopPorts` / `NudgeOutcome` / `LoopKeys` / `llm_step` / `tool_step` / `decide_next` / `default_classify_error` |
| `emily-core/emily_core/kernel/policies.py` | 节点级策略（M3）：`build_retry_policy` / `node_timeout` / `describe` |
| `emily-core/emily_core/kernel/context.py` | 运行时上下文（M4）：`KernelContext` + `bind_compat()`（唯一兼容桥） |
| `scripts/kernel_regression.py` | 内核回归断言 27 项（7 组 case），零副作用 |

**修改**

| 文件 | 改动 |
|------|------|
| `session/session_graph.py` | 计划支路改三段式（`plan_build`/`plan`子图/`plan_echo`）；节点改用共享内核；节点级策略声明；`context_schema` + 官方 actor 读取 |
| `session/plan_graph.py` | 状态键与父图对齐（`plan_*` 回写 + `_plan_*` 内部）；`PlanRunner` 降级为独立/回放入口 |
| `session/kernel_state.py` | 新增计划键、`_kernel_*` 键；**每轮瞬态标记显式重置**（修跨轮残留缺陷） |
| `session/graph_wiring.py`、`session/suspend_interrupt.py` | 续接路径改官方上下文入口；`resume_graph` 支持 `context=` |
| `workitem/langgraph_engine/agent/loop.py` | 由"自持循环实现"改为**适配层**（工单侧差异注入） |
| `workitem/langgraph_engine/graph.py` | 节点级策略声明；`context_schema`；`runtime` 透传 |
| `workitem/langgraph_engine/state.py` | 新增 `_kernel_*` 状态键 |
| `workitem/scheduler.py` | 官方上下文入口 + 兼容桥；图级异常转可读收尾 `_readable_graph_failure` |
| `emily_core/config.py` | 新增 `node_retry_*` / `node_timeout_seconds` / `node_timeout_overrides` |
| `CLAUDE.md` | 约束 9b（形态描述）＋ §1 当前架构一句话 |

---

## 四、测试用例：用了哪些、结果如何

### 4.1 既有回放（行为不变证据）

`docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/session_graph_replay.py`

| # | 用例 | 结果 |
|---|------|------|
| 1 | 快速短路零模型调用 | PASS |
| 2 | 工具回环产出最终回复 | PASS |
| 3 | 迭代上限收敛为可读收尾 | PASS |
| 4 | 门禁拒绝时不执行能力 | PASS |
| 5 | 挂起以提问收尾 / 可续接并产出最终回复 | PASS / PASS |
| 6 | 计划层内并行且分两层完成 | PASS |
| 7 | 事件端口产出进度且同源 | PASS |
| 8 | 端口装配可降级 | PASS |
| 9 | 状态可序列化 | PASS |
| 10 | 契约校验拦截缺参与未声明参数 | PASS |

**共 11 项，通过 11，失败 0。**

### 4.2 新增内核回归断言（本次验收）

`docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/kernel_regression.py --case all`

| case | 断言 | 对应 PRD 用例 | 结果 |
|------|------|--------------|------|
| `plan-node` | ① `plan` 节点挂有子图（`understand` 无）② 父图含计划三节点 ③ 计划步骤经子图执行 ④ 层内并行语义保持 ⑤ 计划执行落父线程**子命名空间检查点** ⑥ 跨轮不残留 | TC-01-01 / 01-02 | 6/6 PASS |
| `plan-cap` | 无可用计划不进子图且给出可读回复 | TC-01-03 | PASS |
| `plan-validate` | 非法步骤不执行、下游级联跳过、合法步骤执行 | TC-01-04 | PASS |
| `plan-node-pg` | 生产 Postgres 检查点可用 + 计划步骤经子图执行 | TC-01-01（生产 saver） | 2/2 PASS |
| `loop-shared` | ① 内核模块存在 ② 两侧消费者可达（≥2）③ 内核无按侧字面量 | TC-03-01 / 03-04 | 3/3 PASS |
| `workitem-loop` | ① `complete_work` 经共享内核收口 ② 执行通道失败结构化回填后继续循环 ③ 文本回复走纠错策略 | TC-03-03 | 3/3 PASS |
| `retry-policy` | ① 重试声明可达（两张图）② 超时声明可达（两张图）③ 执行类节点不挂重试 | TC-02-04 | 3/3 PASS |
| `retry-transient` | ① 瞬时故障由框架重试后正常收口（`llm_calls=2`）② 重试耗尽后上抛 | TC-02-01 / 02-03 | 2/2 PASS |
| `node-timeout` | 单节点超时在设定时间内中断（1.01s < 2.5s） | TC-02-02 | PASS |
| `ctx-official` | ① 会话图声明 context_schema ② 工单图声明 ③ 节点从官方上下文取操作者（非 state 回退值）④ 工单节点优先取官方 bus ⑤ 自研通道调用点唯一 | TC-04-01 / 04-03 / 04-04 | 5/5 PASS |

**共 27 项，通过 27，失败 0。**

### 4.3 容器真实链路（生产路径）

| # | 输入 | 用户 | 结果 |
|---|------|------|------|
| 1 | 「帮我查一下项目里最近的事件」 | 李景利（L4） | ✅ 正常回复 13 条事件（工具回环 = understand→execute→understand→summarize 在共享内核上运行，权限裁剪生效） |
| 2 | 「先帮我记一条事件：样板段放线完成；然后再帮我记一条任务：复核样板段标高」 | 李景利（L4） | ✅ 任务 `TSK-20260915-0001` 已建；事件 `EVT-20260915-0001` 转待确认；多步执行与确认链路正常 |

### 4.4 生产检查点落库证据（AC-US-01.1 硬证据）

```sql
SELECT thread_id, checkpoint_ns, count(*) FROM checkpoints
WHERE thread_id='kr-plan-pg' GROUP BY 1,2;
--  kr-plan-pg |                                           | 9
--  kr-plan-pg | plan:75b37bcb-b117-e9f5-5bb0-c2da6405c57c | 6      ← 计划子图状态落在父线程下
```

---

## 五、结果对比：执行前 / 执行后

| 指标 | 执行前 | 执行后 |
|------|--------|--------|
| 计划执行的检查点条数（同一线程） | 0（无持久化） | 6（子命名空间 `plan:*`） |
| 对话循环实现份数 | 2 | 1 |
| 内核中调用方字面量 | — | 0 |
| 节点级重试声明 | 0 处 | 4 个节点（`policy` 声明可达） |
| 节点级超时声明 | 0 处 | 两张图全部普通节点（执行类/挂起类按设计豁免） |
| 瞬时故障存活率（桩件注入） | 无重试机制 | 单次瞬时失败重试后成功；持续失败按可读文案中止 |
| 自研上下文通道调用点 | 3 处以上（scheduler / runner / wiring） | 1 处（兼容桥） |
| 既有回放通过数 | 11/11 | 11/11（**行为不变**） |
| 内核回归通过数 | 0（无此语料） | 27/27 |

---

## 六、执行过程中的发现

1. **缺陷（已修）：每轮瞬态标记随检查点残留。**
   回归 `plan-node 6` 首次运行即暴露：`_plan_done` 经检查点带到下一轮 → **同一会话第二轮不再触发计划**。修复：`make_initial_state` 显式重置 `_should_plan/_plan_done/_should_suspend/_fast/_capped/plan_*/_kernel_*`。这是既有缺陷（非本次引入），修复后用例 6 通过。
2. **行为微调（已记录，非等价但更保守）**：
   - 会话侧 `llm_caller` 返回空结果：旧为直接 `EMPTY_REPLY`；新为走一次纠错重试、由迭代上限收口 `CAP_REPLY`。
   - 工单侧上下文溢出且恢复不可用：旧为异常上抛；新为按 fatal 分类转 error_analysis（结构化失败）。
3. **P1 偏差（已按停损规则记录，未处理）**：`scripts/verify_langgraph_engine.py` 仍调用旧版 `build_workitem_graph(agent, adapter, max_replan=1)` 签名，早已失效；本次以新写的 `kernel_regression.py --case workitem-loop` 替代其职能。

---

## 七、未覆盖与遗留（诚实清单）

| # | 项 | 性质 | 建议 |
|---|----|------|------|
| 1 | **生产计划支路未端到端打通** | 既有缺陷（非本次引入） | 容器实测中 `SessionPlanner` 返回非法 JSON（`LLM planning failed: LLM response is not valid JSON`）→ `plan_build` 无步骤 → 回退单步循环。故计划支路的正确性由 harness（桩件 planner + 真实图 + **真实 Postgres 检查点**）证明；生产打通需先修 planner JSON 解析 |
| 2 | **挂起→重启→续接的两进程实测未做** | 缺口 | 容器内已验证计划执行检查点落库；但"挂起跨进程续接"需可构造"需补充信息"的能力，`复盘_V1` 已记录该缺口。建议补一条可控的 needs_input 测试能力后执行 TC-04-02 |
| 3 | 工单侧真实链路 e2e 未做 | 验证缺口 | 工单侧以桩件 harness（3 场景）验证；真实链路需走调度器 + 真实 WI 队列 |
| 4 | `docs/Manual/业务模块与运转全景.md` 图形态描述未同步 | 文档遗留 | 该文档仍描述旧 PipelineBUS 4 节点架构（早于 2026-07-28 的引擎替换），属更大范围的文档债；本次已同步 `CLAUDE.md` 约束 9b 作为权威形态描述 |
| 5 | 首次消息的 `plan` 分支判定依赖 `_looks_compound` 关键词 | 既有行为 | 未改动 |

---

## 八、回退方式（三档独立）

| 档 | 覆盖 | 回退手段 |
|----|------|---------|
| B1 | M1 + M2 | 代码回滚 `session/session_graph.py`、`session/plan_graph.py`、`session/kernel_state.py`、`workitem/langgraph_engine/agent/loop.py`、`graph.py`（整组回退，不拆） |
| B2 | M3 | 配置层即可关闭：`node_retry_max_attempts=1` + `node_timeout_seconds=0`（无需改码）；彻底回退则去掉 `retry_policy=`/`timeout=` 声明 |
| B3 | M4 | 不传 `context=` 即走兼容回退（`KernelContext.from_runtime(None)` → 空上下文 + 兼容桥），节点回退读 `state.actor_ref` / 进程内通道 |

---

## 九、验证命令汇总（可复现）

```bash
# 既有回放（行为不变）
docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/session_graph_replay.py

# 内核回归（本次验收，27 项）
docker exec -e PYTHONPATH=/app -w /app emily-core python /app/scripts/kernel_regression.py --case all

# 生产检查点落库证据
docker exec emily-postgres psql -U emily -d emily -c \
  "SELECT thread_id, checkpoint_ns, count(*) FROM checkpoints WHERE thread_id='kr-plan-pg' GROUP BY 1,2;"

# 容器真实链路
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下项目里最近的事件" --sender "李景利"
```

---

*本报告基于 2026-09-15 容器实测数据；未通过项与未覆盖项已在 §七 逐条列出，不做"全部通过"的笼统结论。*
