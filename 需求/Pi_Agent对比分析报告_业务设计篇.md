# Pi Agent 与 Emily 对比分析报告（业务设计篇）

> **对比对象**：Pi Agent Harness（`D:\app\PI_agent\pi`） vs Emily（`D:\app\Emily`）
> **Pi 版本锚定**：`v0.85.1-41-g400d6905c`（commit `400d6905`，2026-09-09，`main` 分支，工作树与上游一致）
> **互补文档**：`需求/Pi_Agent对比分析报告.md`（工程闭环视角：测试 / CI / 迁移 / 包边界）
> **分析日期**：2026-09-11
> **本报告定位**：**只聚焦 Pi 明确强于 Emily 的设计**，作为改进靶子。Emily 相对占优的设计（编排 DAG、认知装配层、RAG、权限分级）不在本报告展开。
> **另含**：§6 单独列出**与 Pi 无关的 Emily 自有缺陷**，便于统一治理。
> **口径**：代码结论一律以**当时代码**为准并附 `文件:行`；社区共识结论单独标注为"社区说法"，不与其混同。

---

## 零、总览

### 0.1 社区共识：Pi 的公认强项（联网调研 × 代码双向核对）

| # | 社区公认特性 | 社区说法（原文口径） | 我方代码核实结果 |
|---|---|---|---|
| 1 | **极简内核** | "Primitives, not features"；内置仅 4 工具（read/write/edit/bash）；提示词 < 1000 token | ✅ 内置工具实测 **8 个**（含 `find/grep/ls/powershell`，社区口径的"4 工具"为核心四件）；见 §1.3 |
| 2 | **Token 效率高** | 上下文效率高、成本减半；配 DeepSeek 时缓存命中率极高（一手来源不可访问，仅见二手转述） | ✅ 机制成立：极简工具集 + 稳定前缀 + token 预算压缩，见 §1.2 |
| 3 | **上下文压缩（compaction）** | 保留约 2 万 token 近期上下文；LLM 生成结构化交接记录；支持模型迁移、会话可移植 | ✅ 逐项对上，且比社区描述更细（切点保护 / 迭代合并 / 指针回填），见 §1.2 |
| 4 | **会话树与分叉** | "Branch + Compaction"：历史视为树结构，可显式管理分支与历史 | ✅ `session-manager.ts` 的 append-only 树 + `/tree` 遍历 fork，见 §2.2 |
| 5 | **自扩展（self-extensible）** | Extension 用 TS 模块注册工具/命令；热重载；"There are many agent harnesses but this one is yours" | ✅ `ExtensionAPI` 36 事件 + jiti 热加载 + 79 示例，见 §3.2 |
| 6 | **多模型 / 无厂商锁定** | 兼容 15~30+ 模型；MIT 开源；可接订阅免配额 | ✅ 约 40 家 provider、10 种 API 适配器、模型目录为生成物，见 §5.2 |
| 7 | **不内置但有边界** | 明确不内置 MCP / 子 Agent / 权限弹窗 / plan mode / todo，交由扩展 | ✅ 一手证据 `docs/usage.md:309`，见 §4.2 |

> ⚠️ **数据可信度提示**：社区文章中流传的 Star 数在 **8.6 万 ~ 9.8 万** 之间互相矛盾（皆为二手转述，且有一篇标题写"9.8万Star"另一篇写"94.8K万+"明显有误），**不作为本报告论据**。同理"被 Databricks 实测认可"仅为文章转述，未核实一手来源。

### 0.2 差距总表

| # | Pi 的强项 | Emily 现状 | 差距性质 | 借鉴判断 |
|---|---|---|---|---|
| 1 | 上下文**感知 → 预算 → 压缩**闭环（token 度量、窗口保留额触发、摘要+指针回填） | 只有 `>40 条` 阈值 + 单条截 2000 字符，**全库无 token 计量** | 机制缺口 | **强烈建议借鉴**（§1.4） |
| 2 | 会话**树状分叉** + 压缩节点可 fork + **崩溃可恢复**状态机 | 状态在 DB 态 + LangGraph `MemorySaver()`（**内存**，重启即丢） | 机制缺口 | **建议借鉴**（§2.4） |
| 3 | 扩展是**公开契约**：36 事件 + 文档 + 79 示例 + 回归测试 + 热加载 | `hook_config.json` + 注册表，无 schema 版本、无示例、无测试、无热加载 | 契约缺口 | **建议借鉴**（§3.4） |
| 4 | **内核零内建 + 可插拔委派**：Agent 定义外置、编排全在扩展层 | 编排内建在 LangGraph 图内，专家硬编码为节点，开关未接线 | 契约缺口 | **部分借鉴**（§4.4） |
| 5 | **多 Provider 统一层**：40 家 provider / 10 适配器 / 模型目录生成物 | 单一 OpenAI 兼容客户端，硬编码 DeepSeek 模型名，按模型名分支传参 | 机制缺口 | **建议借鉴**（§5.4） |

### 0.3 与本报告配套的 Emily 自有缺陷

见 **§6**（15 项，含 3 项死代码/断线 🔴、7 项半成品 🟠、5 项文档滞后/待核实 🟡）。

---

## 一、长上下文感知与压缩【Pi 明确强项】

### 1.1 社区共识

社区对 Pi 这一块的评价集中在两点：**① Token 效率**（"上下文效率高""成本减半"）；**② 压缩机制可迁移**——"保留约 2 万 Token 近期消息，LLM 生成结构化交接记录，含目标、进度与决策；避免清空任务，支持模型迁移，保持会话可移植"。

社区同时指出一个**代价**：压缩会**破坏 Prompt Cache 前缀**，压缩后首次请求需重新计算（成本上升）。这一点对借鉴决策很重要，见 1.4。

### 1.2 Pi 的设计（代码实证）

**① 感知：真实 usage 锚点 + 估算补尾 + 前缀计入**

```ts
// packages/ai/src/utils/estimate.ts:114
export function estimateContextTokens(context: Context | readonly Message[]): ContextUsageEstimate
// :17
export function calculateContextTokens(usage: Usage): number {
	return usage.totalTokens || usage.input + usage.output + usage.cacheRead + usage.cacheWrite;
}
```
- 以最后一条有效 assistant 的**真实 provider usage** 为锚点，其后消息按 `chars/4` 估算累加，并补上 `systemPrompt` 与 `tools` 定义的 token（`estimate.ts:89-143`）。
- 窗口元数据来自 `Model.contextWindow`（`packages/ai/src/types.ts:857`），由 `scripts/generate-models.ts` 生成、可被用户覆盖。
- **动态压低输出上限**：`clampMaxTokensToContext()` 用 `窗口 - 已用 - 4096` 反推本次可用输出（`packages/ai/src/api/simple-options.ts:15`）——长上下文时自动收缩输出，避免"输入挤爆输出"。
- **溢出感知独立于估算**：`packages/ai/src/utils/overflow.ts:37-163` 内置 20+ 家 provider 溢出正则，并覆盖 z.ai「静默溢出」、小米「length + output=0 且 input≥99% 窗口」等非标准情形。

**② 触发：三种语义分离，判据是"窗口 - 保留额"**

```ts
// packages/coding-agent/src/core/compaction/compaction.ts:132
export const DEFAULT_COMPACTION_SETTINGS = { enabled: true, reserveTokens: 16384, keepRecentTokens: 20000 };
// :235
export function shouldCompact(contextTokens, contextWindow, settings) {
	return contextTokens > contextWindow - settings.reserveTokens;
}
```
触发路径集中在 `_checkCompaction()`（`packages/coding-agent/src/core/agent-session.ts:2153`）：**溢出且可重试** → 压缩 + retry 一次；**溢出但已完成** → 压缩不 retry；**阈值触发** → 压缩不 retry。另有回合中检查（`:545-563`）、新 prompt 前检查（`:1262`）、手动 `/compact [instructions]`。

**③ 压缩：LLM 结构化摘要 + 迭代合并，不截断、不分层**

- 保尾按 **token 预算**从最新往回累计 `keepRecentTokens = 20000`；切点只落 user / assistant / bash / custom，**绝不切 toolResult**（`compaction.ts:388-461`），保住 tool-call / tool-result 配对。
- 单回合即超预算走 **split-turn**：生成「历史摘要 + 前缀摘要」再合并（`:887-947`）。
- **重复压缩以 `firstKeptEntryId` 为起点迭代更新**，不重复处理已摘要区间（`:768-776`）——这正是社区所说"可迁移、可移植"的实现基础。

**④ 回填：指针 + 动态重建**

```ts
// compaction.ts:88 — CompactionResult{ summary, firstKeptEntryId, tokensBefore, usage, details{readFiles, modifiedFiles} }
// packages/coding-agent/src/core/session-manager.ts:418
// buildContextEntries -> [最新 compaction, ...从 firstKeptEntryId 起的保留 entry]
```
- 旧消息彻底不再进 LLM，重放时按指针重建；摘要作为 `compactionSummary` role 注入。
- 摘要末尾维护 `<read-files> / <modified-files>` **跨压缩累积**（`compaction/utils.ts:62-82`），弥补"旧消息被删导致的状态丢失"。
- 压缩产物是**普通会话节点**：可被 `/tree` 遍历与 fork（见 §2.2）。

**⑤ 健壮性**：拒绝 `length/error` 残摘要入库；摘要调用统一重试并禁用 cache 写入；压缩后旧 usage 失效，显式暴露 `percent: null` 避免误触发二次压缩（`agent-session.ts:3416-3443`）。

### 1.3 Emily 现状

| 项 | Emily 实况 | 证据 |
|---|---|---|
| token 计量 | **全库 0 命中**（`tiktoken / count_tokens / context_window / token_budget`）；`token_count` 仅三书构建器元数据估算 `int(len/1.5)`，**不参与决策** | 复现命令见 §8 |
| 压缩触发 | `len(message_history) > 40`（条数） | `session/session_context.py:320` |
| 压缩方式 | 取最旧 **20 条** → LLM 摘要 → **文本插回**一条 `[对话历史摘要]` | `session_context.py:556-588` |
| 单条边界 | content 硬截 `[:2000]` | `session_context.py:312,317` |
| 三书摘要 | 按字符截断（世界书摘要 `max_chars=200`） | `fetch_world_book.py:60,104` |
| 溢出感知 | 无，直接撞 API 报错 | — |
| 输出上限 | 固定 `max_tokens` | `config.py:42-46` |
| 回填 | 文本插回消息数组，**无指针、无法回溯到压缩前状态、无法 fork** | — |
| 摘要演进 | 摘要会被再次摘要（信息持续衰减），无累积机制 | — |

### 1.4 借鉴判断：**建议借鉴，但只借机制、不借"纯摘要"的数学**

| 借鉴点 | 具体做法 | 收益 |
|---|---|---|
| **① 补"感知"层** | 引入 token 计量：真实 usage 优先 + 估算补尾；记录 `contextTokens / contextWindow / percent` | 让"是否该压缩"可度量，取代拍脑袋的 40 条 |
| **② 判据换成窗口预算** | 触发改为 `已用 > 窗口 - reserveTokens`，`reserveTokens` 同时充当摘要输出预算来源 | 换模型（32k→200k）行为自动适配，不再浪费窗口 |
| **③ 压缩产物带指针与累积** | 压缩结果记录 `firstKeptEntryId` + 关键成果/文件清单，重放时动态重建 | 支持 fork、可回溯、早期业务约束（节点/权限/成果）不丢 |
| **④ 溢出感知** | 采集主流 provider 的溢出报错特征，建立「溢出 → 压缩 → 重试一次」闭环 | 消除"直接撞报错"的不可控 |
| **⑤ 切点保护** | 切点不落在工具调用/结果之间 | 摘要后不产生悬空引用 |

> ⚠️ **借鉴注意（社区反馈的代价）**：Pi 的压缩会**破坏 Prompt Cache 前缀**。Emily 现有的「base 提示词字节稳定 + 尾部变量实时渲染」设计正是缓存友好的（`session/session_agent.py:183-232`）——**引入 token 预算压缩时，必须保住这个稳定前缀**，把压缩只作用于 message 历史层，不要动 base 提示词。

### 1.5 本节连带的 Emily 自有缺陷

- 压缩与三书/权限层**完全解耦**：压缩只动 `message_history`，不感知三书与权限上下文，长会话下业务约束优先丢失。
- WorkItem 侧有**另一套独立装配器**（`langgraph_engine/agent/prompt_builder.py:16-142`），与 Session 侧不共享代码，改一处易漏。

---

## 二、会话模型：树状分叉 + 崩溃可恢复【Pi 明确强项】

### 2.1 社区共识

"**Branch + Compaction**：将历史视为树结构"；"相比 Claude 的分支压缩和 Codex 的黑盒远程压缩，Pi 的优势在于设计简约、可显式管理分支与历史树"。会话树是 Pi 被反复提及的差异化能力。

### 2.2 Pi 的设计

**① 会话是 append-only 树，不是线性数组**

```ts
// packages/coding-agent/src/core/session-manager.ts:1058
private _appendEntry(entry: SessionEntry): void {
  this.fileEntries.push(entry); this.byId.set(entry.id, entry);
  this.leafId = entry.id; this._persist(entry);
}
// :418 buildContextEntries -> [最新 compaction, ...firstKeptEntryId 起的保留项]
// :514 loadEntriesFromFile  流式逐行解析（含 torn line 修尾）
```
- 每个 entry 有 `id` + 父指针，压缩条目也是普通节点 → **可在任意历史点 fork**，todo/计划状态随之自动正确。
- **持久化契约化**：`Storage` / `SessionRepo` 抽象（`packages/agent/src/harness/session/types.ts:455,592`），由 JSONL / 内存 / SQLite 三后端实现，且必须通过**同一套一致性测试套件**。

**② 崩溃可恢复的持久化状态机（这是 Emily 完全没有的能力）**

```ts
// packages/agent/src/harness/session/types.ts:316
export type OperationState =  // 13 个 leaf
  | StartingOperation | CheckpointOperation
  | AssistantReadyOperation | AssistantEffectPendingOperation | AssistantRetryWaitOperation
  | ToolsOperation | DeferredSuspendedOperation | DeferredEffectPendingOperation
  | SummaryDecidingOperation | SummaryReadyOperation | SummaryEffectPendingOperation
  | SummaryRetryWaitOperation | NavigationReadyToCommitOperation;
```
- 转移由 `harness/runtime/drive.ts:48-105` 的 `switch(state.at)` 分派，每次**整体替换**状态值。
- **价值在 effect-pending / reconcile / recovery**：崩溃后用已提交 frame 前缀合成 interrupted 消息、**不重发 provider 请求**（`harness/runtime/drive/recovery.ts:44`）——即"不确定的副作用可被确定性结算"。
- resume：`harness/runtime/lane.ts:1327` `resume()` → 无操作返回 `NothingToResume`，否则 `drive({pollDeferred:true, waitForRetry:true})`。

### 2.3 Emily 现状

| 项 | Emily 实况 | 证据 |
|---|---|---|
| 会话结构 | **线性** `message_history` 数组，无父指针、无分支 | `session/session_context.py` |
| 分叉能力 | 无（无法回溯到某轮重试） | — |
| 图检查点 | **`MemorySaver()`——进程内存**，重启即丢 | `workitem/langgraph_engine/graph.py:110` |
| 崩溃恢复 | 无 effect-pending/reconcile 语义；长任务中断后靠 DB 字段兜底重建 | — |
| 归档 | `session_archive_writer.py` 写 md（只读回溯，非可续跑状态） | — |

> ⚠️ `graph.py:110` 实测：`graph = gs.compile(checkpointer=MemorySaver())`，`from langgraph.checkpoint.memory import MemorySaver`（`:13`）。LangGraph 的 `interrupt`/断点续跑依赖 checkpointer，**用内存实现意味着进程重启后中断点全部失效**。

### 2.4 借鉴判断：**建议借鉴，分两步**

1. **先换持久化 checkpointer**（低成本、高收益）：把 `MemorySaver()` 换成 SQLite/Postgres checkpointer，`interrupt` 与断点续跑立刻获得持久语义。这是 Emily 当前**最划算的一处修复**。
2. **再引入"会话即树"**（中期）：为消息历史引入 entry + 父指针，使"回到第 N 轮重试""从某点 fork 出另一条方案"成为可能；压缩条目作为普通节点，与 §1.4 的指针回填天然契合。

---

## 三、可插拔扩展体系【Pi 明确强项】

### 3.1 社区共识

"Extension 用 TS 模块注册工具/命令，支持热重载"；官方口号 "There are many agent harnesses but this one is yours"；"**官方称：让 Agent 适应你的工作流**"。社区同时把"默认无扩展，需 DIY"列为缺点——但这恰恰说明**扩展是其能力主战场**。

### 3.2 Pi 的设计

**① API 面：单对象宽接口，全链路可改**

```ts
// packages/coding-agent/src/core/extensions/types.ts:1252
export interface ExtensionAPI
```
- **36 个 `on()` 事件**（实测 `grep -c "^\s*on("` = 36）：`project_trust / resources_discover / session_*（含 before_compact / compact_failed）/ context / before_provider_request / after_provider_response / before_agent_start / agent_start|end|settled / turn_* / message_* / tool_execution_* / model_select / tool_call / tool_result / user_bash / input`。
- **注册面**：`registerTool / registerCommand / registerShortcut / registerFlag / registerMessageRenderer / registerMarkdownTransformer / registerEntryRenderer / registerProvider / unregisterProvider`，外加 `sendMessage / appendEntry / exec / getActiveTools / setActiveTools / setModel`。

**② 边界：扩展能改什么**

| 能力 | 机制 |
|---|---|
| 改 prompt | `before_agent_start` 返回 `systemPrompt`，**多扩展链式**（`types.ts:1156-1160`） |
| 改工具 | 注册/覆盖 + `setActiveTools` 整体换集 + `tool_call` 改 args 或 block |
| 改 provider | `registerProvider` 支持 `baseUrl / models / oauth / streamSimple` |
| 拦截消息 | `context` 替换 messages；`message_end` 替换已定稿消息；`before_provider_request` 改 payload |

**③ 热加载与发现路径**：`jiti/static` 运行时直载 TS，`moduleCache: false` + `reload()`；缓存以 **cwd + generation token** 校验（`core/extensions/loader.ts:17,158,501`）。三级发现：`cwd/.pi/extensions/` → `agentDir/extensions/` → settings 路径；识别 `*.ts` / 子目录 `index.ts` / `package.json` 的 `pi.extensions` 清单。

**④ 规模即证明**：**79 个示例扩展**、`docs/extensions.md` **3029 行**、带回归测试（如项目级 subagent 信任测试）。

### 3.3 Emily 现状

| 项 | Emily 实况 | 证据 |
|---|---|---|
| 扩展点 | **Hook 是唯一真扩展点**，方向正确 | `pipeline/hook_registry.py:18`、`langgraph_engine/hook_adapter.py:81` |
| 声明式配置 | `hook_config.json`：`before/after/on_error` × 节点，类型 `auth/archive/audit/progress` | `emily-data/config/hook_config.json` |
| schema / 版本 | **无**（无字段校验、无版本号） | — |
| 示例集 / 测试 | **无** | — |
| 热加载 | **无**（改扩展需改本仓代码 + 重启） | — |
| 工具扩展 | 只能进 `tools/registry.py`（实测 **19 处 `reg.register(`**，含 SOP 业务工具循环批量注册；`tools/` 共 **23 个模块**） | `tools/registry.py:161,181,333,415` |
| MCP | `emily-core` 内 **0 命中** | — |

### 3.4 借鉴判断：**建议借鉴"契约化"，不建议照搬"开放生态"**

垂直应用**不需要** Pi 那样的第三方生态，但需要**契约**：
1. **给 `hook_config.json` 加 schema + 版本号**（最小改动、最大收益）：字段可校验、可灰度、可回滚。
2. **补示例集与用例**：至少覆盖每类 hook（auth/archive/audit/progress）各一个可运行样例。
3. **渠道收敛**：把三处重复的 HTTP + SSE 契约收敛为**可注册的 Channel 抽象**（Emily 现无 `class *Channel`），接新渠道只改一处。
4. **不追求热加载**：收益低于复杂度，可用"配置热重载"替代"代码热加载"。

---

## 四、多 Agent 与任务编排：内核零内建 + 可插拔委派【Pi 强在机制分离】

> 社区口径下 Pi 的"多 Agent"并不突出（不在内置能力内）。但它在**架构取舍**上是明确强项：**内核保持稳定，把编排与委派完全推给扩展层，并给出足够的积木**。

### 4.1 设计哲学：公开声明"不做"，并给足积木

```text
// packages/coding-agent/docs/usage.md:309
It intentionally does not include built-in MCP, sub-agents, permission popups,
plan mode, to-dos, or background bash.
You can build or install those workflows as extensions or packages...
```

**内核提供的四块积木**：

| 积木 | 实现 |
|---|---|
| 循环可插拔 | `shouldStopAfterTurn` / `prepareNextTurn` / `beforeToolCall` / `afterToolCall`（`packages/agent/src/types.ts:223-293`）；harness 侧 hook map（`harness/agent-harness.ts:431-470`） |
| 工具集可切换 | `setActiveTools()` 整体换集 → 计划模式"只读化"（`examples/extensions/plan-mode/index.ts:108`） |
| 事件可拦截 | `tool_call` 返回 `{block:true}` 阻止写操作（`:164`）；`context` 过滤过期计划上下文（`:177`） |
| 命令可注册 | `registerCommand("plan")` / `registerCommand("todos")`（`:141/:146`） |

**Agent 定义是一等公民**（委派模式）：

```ts
// packages/coding-agent/examples/extensions/subagent/index.ts:4
//   Spawns a separate `pi` process for each subagent invocation, giving it an isolated context window.
// :33-34
const MAX_PARALLEL_TASKS = 8;  const MAX_CONCURRENCY = 4;
```
- 三种模式：`single` / `parallel`（并发限流）/ `chain`（`{previous}` 串行传递）。
- Agent 定义从 `~/.pi/agents` 与 `.pi/agents` 的 **Markdown 文件**加载，附 `scout / planner / reviewer / worker` 示例，对项目级 agent 做**信任确认**，**有回归测试**。

### 4.2 Emily 现状

| 项 | Emily 实况 | 证据 |
|---|---|---|
| 编排形态 | **内建在图里**：9 节点 + 宏观依赖 DAG | `workitem/langgraph_engine/graph.py:47-62`、`session/orchestrator.py:92` |
| Agent 定义 | **硬编码**：专家按 SOP 绑定 → 图节点，无外部定义文件 | `session/session_agent.py:716-719` |
| 专家形态 | 图节点，**单次 `chat_json`**，非 agent loop，无自有记忆 | `workitem/langgraph_engine/nodes.py:555-556` |
| 配置开关 | **死开关**：`expert_review_enabled` 全仓**无读取方** | `config.py:204` |
| 委派原语 | 仅 WorkItem 层内并行，**无"委派给 Agent"抽象** | `workitem/scheduler.py:97` |
| 状态源 | **三套并存**：WI 9 态 / 节点 3 态 / 图内字符串 | `workitem_state.py:22,38`、`node_state_machine.py:26-38` |
| 加新编排策略 | **必须改图 + 改代码** | — |

### 4.3 社区对 Pi 编排能力的评价

社区把"不内置 plan mode / todo"视为**缺点（开箱即用度低、需 DIY）**，但同一事实的另一面是：**Pi 的内核因此长期稳定，编排策略可以随扩展热插拔、互不干扰**。

### 4.4 借鉴判断：**部分借鉴——借"机制分离"，不借"零内建"**

| 借 | 不借 |
|---|---|
| ✅ **Agent 定义外置**：把"专家/角色"从图节点提为可配置定义（配置或 Markdown），新增角色不改核心代码 | ❌ 不要清空内建编排——Emily 的 DAG + 质量门 + 兜底是**真实业务资产**，社区也承认 Pi 的零内建是缺点 |
| ✅ **接上死开关**：`expert_review_enabled` 必须有读取方，否则"配置看起来生效、实际不生效" | ❌ 不要为了"可插拔"牺牲已跑通的业务路径 |
| ✅ **状态源归一**：三套状态 → 明确主从 + 映射表，让编排策略的替换有稳定地基 | — |
| ✅ **给编排留钩子**：把"加一类新编排策略"从"改图"降为"注册一个策略" | — |

---

## 五、多 Provider 与模型中立【Pi 明确强项】

### 5.1 社区共识

"支持 15+ 模型""兼容 15+ / 30+ 模型""多模型与扩展机制""**无厂商锁定**""支持订阅免配额""MIT 开源"。

### 5.2 Pi 的设计

- **约 40 家 provider、10 种 API 适配器**（anthropic-messages / openai-completions / google-generative-ai / bedrock-converse-stream 等），SDK 懒加载。
- **模型目录是生成物**：`models.generated.ts` 禁止手改，由 `scripts/generate-models.ts` 生成，并有 `check:model-data` 校验（`AGENTS.md` 明文约束）。
- 窗口/能力元数据随模型目录下发，直接喂给 §1.2 的上下文预算。
- `registerProvider` 允许扩展**新增 provider**（`baseUrl / models / oauth / streamSimple`），即"模型接入"本身也是可插拔的。

### 5.3 Emily 现状

| 项 | Emily 实况 | 证据 |
|---|---|---|
| 客户端 | **单一 OpenAI 兼容客户端**，`base_url` 默认 DeepSeek | `infrastructure/llm/client.py:14,27,36-37` |
| 模型名 | **硬编码**在 config：`deepseek-v4-flash` / `deepseek-v4-pro` / `deepseek-chat` | `config.py:36,48,51,54,207` |
| provider 抽象 | **无**（无 provider 接口 / 适配器分层；`infrastructure/llm/` 仅 `client.py` + `prompt_loader.py`） | 同上 |
| 能力差异处理 | 按**模型名分支**传参：推理类模型（`deepseek-reasoner` / `deepseek-v4-pro`）不传 `temperature`，否则 400 | `client.py:129-130` |
| 元数据 | 无模型能力/窗口元数据表 | — |

### 5.4 借鉴判断：**建议借鉴，与 §1.4 强耦合**

1. **抽象 provider 接口**：把"模型能力差异"从 `if` 分支提升为 provider 描述（支持哪些参数、窗口多大、是否支持 cache），`client.py:129` 这类硬编码分支随之消失。
2. **模型元数据表**：为每个可用模型声明 `contextWindow / 最大输出 / 支持参数 / 单价`，作为 §1.4 上下文预算的输入。
3. **不做**：不必追求 Pi 的 40 家 provider 广度——Emily 是垂直应用，**3~5 家主流 + 可切换**即可，重点是"换模型不改代码"。

---

## 六、Emily 自有缺陷清单（独立于 Pi 对比）

> 以下为调研中发现的 Emily 自身问题，**与 Pi 强弱无关**，属于就应治理的项。分级：`🔴 死代码/断线`｜`🟠 半成品`｜`🟡 文档滞后`。

| # | 缺陷 | 证据 | 影响 | 级别 |
|---|---|---|---|---|
| 1 | **图检查点用内存实现**：`MemorySaver()` 进程重启即丢，`interrupt` 与断点续跑失效 | `workitem/langgraph_engine/graph.py:110` | 长任务中断后不可续，可靠性硬伤 | 🔴 |
| 2 | **配置死开关**：`expert_review_enabled` 全仓仅定义处 1 命中，无读取方 | `config.py:204` | 配置"看似生效实际无效"，误导运维 | 🔴 |
| 3 | **写而不读**：`write_user_memory` 工具会写文件记忆，但读取函数 `load_memory_context` **零调用者** | `tools/memory_tool.py:76` ↔ `services/user_memory_service.py:171` | 记忆写了但从不进 prompt，功能空转 | 🔴 |
| 4 | **读有写无**：`users.long_term_memory` 被读入 `{user_memory}` 变量，但全库无任何写入方 | `session_data_fetcher.py:176`、`session_context.py:428` | `{user_memory}` 恒为空/陈数据 | 🟠 |
| 5 | **重排空壳**：`_rerank` 只记日志并原样返回，"可选重排"从未接入 | `providers/rag/pgvector_provider.py:167-176` | RAG 召回质量停在向量序 | 🟠 |
| 6 | **状态语义三份并存**：WI 9 态 / 节点 3 态 / 图内 `wi_state` 字符串，三处独立演化 | `workitem_state.py:22`、`node_state_machine.py:26-38` | 维护成本与不一致风险 | 🟠 |
| 7 | **渠道三处重复**：插件 / `wechat-gateway` / 小程序各写一遍 HTTP + SSE 契约；DTO 副本只拷 2/5（`command/result/route_decision` 未拷） | `data/plugins/emily_agent/`、`wechat-gateway/core_client.py:54,87` | 加渠道需改三处并手工同步 | 🟠 |
| 8 | **两套 prompt 装配器不共享**：Session 侧 `session_agent.py` 与 WorkItem 侧 `prompt_builder.py:16-142` 各自拼装 | 同上 | 改一处易漏；无分层抽象 | 🟠 |
| 9 | **权限语义四处独立实现**：装配层变量渲染 / 可见文件 allowlist / RAG 可见集 / 三书 `_visible` | `session_agent.py:225`、`knowledge_search_tool.py:98`、`fetch_world_book.py:50` | 改鉴权口径要改四处 | 🟠 |
| 10 | **业务事件日志上下文断线**：logger 自述上下文由已废弃的 `PipelineBUS.run()` 设置，LangGraph 路径下 `pipeline_run_id` 很可能为空 | `business_event_logger.py:19` vs `bus.py:147` | 事件日志缺失关联键 | 🟠 |
| 11 | **文档滞后 ①**：`CLAUDE.md` §6 约束 9 写"5 节点"，实际 **9 节点** | `graph.py:47-62` | 新人误判架构 | 🟡 |
| 12 | **文档滞后 ②**：`CLAUDE.md:33` 写"RAG = MaxKB hit_test"，实际已是 pgvector + TEI，MaxKB 不在代码路径 | 复现命令见 §8 | 同上 | 🟡 |
| 13 | **文档滞后 ③**：`README` / `docs/Manual/` 仍描述 `sm_nodes / sm_stages` 全局状态机，代码 **0 命中** | 同上 | 同上 | 🟡 |
| 14 | **需求未落地**：里程碑"成果反推"（"反推" 0 命中）、部门移除/签认机制（"签认"仅 1 处 SQL 种子值） | `需求/根据成果反推里程碑节点机制.md` 等 | 需求与实现脱节 | 🟡 |
| 15 | **待核实**：三书定时更新（`WorldBookUpdateHandler` 等已注册进 JobHandlerRegistry），但 `scheduler_config.json` 无条目且该文件无代码读取，真实调度源是 DB `scheduler_jobs`；本机 Docker 未运行，**未能核实是否真跑** | `__init__.py:582-591` | 存在"注册了不调度"风险 | 🟡 |

---

## 七、借鉴优先级路线

> 原则：**先闭合机制缺口，再谈契约化**。按"影响业务连续性 × 实现成本"排序。

| 优先级 | 动作 | 对应章节 | 成本 | 验收标准 |
|---|---|---|---|---|
| **P0** | **换持久化 checkpointer**：`MemorySaver()` → SQLite/Postgres | §2.4、§6-1 | 低 | 进程重启后 `interrupt` 断点仍可续跑 |
| **P0** | **上下文感知 + 预算触发**：token 计量 → 窗口预算判据 → 溢出感知 | §1.4 | 中 | 换模型时压缩行为自适应；长会话不撞报错 |
| **P1** | **压缩产物带指针与累积**：`firstKeptEntryId` + 成果/文件清单；**同时保住稳定前缀** | §1.4、§1.5 | 中 | 压缩后可回溯；业务约束不丢 |
| **P1** | **Provider 抽象 + 模型元数据表** | §5.4 | 中 | 换模型不改代码；窗口元数据供 P0 使用 |
| **P2** | **清理死代码/断线**：`expert_review_enabled` 接线、`load_memory_context` 接线或删除、`_rerank` 实现或删除、事件日志上下文修复 | §6-2/3/5/10 | 低 | 无"注册了不生效"的组件 |
| **P2** | **会话即树**（中期）：entry + 父指针，压缩条目作为普通节点 | §2.4 | 中高 | 可从任意轮 fork 重试 |
| **P3** | **编排状态源归一**：三套状态 → 主从 + 映射表 | §4.4、§6-6 | 中 | 状态可机械推导，无人工判断 |
| **P3** | **扩展契约化**：`hook_config.json` 加 schema + 版本 + 示例；渠道收敛 Channel 抽象 | §3.4、§6-7 | 中 | 接新渠道只改一处 |
| **P4** | **文档对齐代码**：9 节点 / pgvector / 移除 `sm_*` 描述 | §6-11/12/13 | 低 | 文档与代码 grep 一致 |

---

## 八、附：证据索引与复现

### Pi Agent（`D:\app\PI_agent\pi\`，锚定 `400d6905`）

| 主题 | 路径 |
|---|---|
| token 估算 / 溢出 | `packages/ai/src/utils/estimate.ts:17,114`、`utils/overflow.ts:37-163`、`api/simple-options.ts:15` |
| 压缩主逻辑 | `packages/coding-agent/src/core/compaction/compaction.ts:88,132,235,388`、`compaction/utils.ts:62-82` |
| 压缩触发 / 上下文用量 | `packages/coding-agent/src/core/agent-session.ts:545,1137,1262,2153,3416` |
| 会话树 / 重建 | `packages/coding-agent/src/core/session-manager.ts:418,514,1058` |
| 存储契约 / 13 态 | `packages/agent/src/harness/session/types.ts:316-329,455,592`、`harness/runtime/drive.ts:48-105`、`drive/recovery.ts:44`、`runtime/lane.ts:1327` |
| 扩展契约 / 热加载 | `packages/coding-agent/src/core/extensions/types.ts:1156,1252-1504`、`extensions/loader.ts:17,158,501` |
| 内核不做清单 | `packages/coding-agent/docs/usage.md:309` |
| 循环可插拔点 | `packages/agent/src/types.ts:223-293`、`harness/agent-harness.ts:431-470` |
| 计划 / todo / 子 Agent 示例 | `examples/extensions/plan-mode/index.ts:108,141,164,177`、`examples/extensions/todo.ts`、`examples/extensions/subagent/index.ts:4,33-34` |
| provider / 模型目录 | `packages/ai/src/models.ts`、`src/providers/all.ts`、`scripts/generate-models.ts`、`AGENTS.md`（模型目录约束） |

### Emily（`D:\app\Emily\`）

| 主题 | 路径 |
|---|---|
| 上下文装配 | `emily-core/emily_core/session/session_agent.py:183,225,453-494` |
| 压缩 / 边界 | `emily-core/emily_core/session/session_context.py:308,312,320,556-588` |
| 图 / checkpointer | `emily-core/emily_core/workitem/langgraph_engine/graph.py:13,47-62,110` |
| 编排 DAG | `emily-core/emily_core/session/orchestrator.py:42,92,133`、`workitem/scheduler.py:97` |
| 专家 / 死开关 | `workitem/langgraph_engine/nodes.py:555,623`、`config.py:204` |
| 状态机三份 | `workitem/workitem_state.py:22,38`、`node_state_machine.py:26-38` |
| LLM 客户端 | `infrastructure/llm/client.py:14,27,36-37,129-130`、`config.py:36,48,51,54,207` |
| 工具注册 | `tools/registry.py:161,333,415` |
| Hook 扩展 | `pipeline/hook_registry.py:18`、`langgraph_engine/hook_adapter.py:81`、`emily-data/config/hook_config.json` |
| 记忆链路 | `tools/memory_tool.py:76`、`services/user_memory_service.py:92,171`、`session_data_fetcher.py:176` |
| RAG | `providers/rag/pgvector_provider.py:138,167-176` |
| 渠道 | `Emily/data/plugins/emily_agent/`、`Emily/wechat-gateway/core_client.py:54,87` |

### 复现命令（关键口径）

```bash
# —— Pi（在 D:/app/PI_agent/pi 下）——
grep -c "^\s*on(" packages/coding-agent/src/core/extensions/types.ts        # => 36（扩展事件数）
grep -n "DEFAULT_COMPACTION_SETTINGS" -A 4 packages/coding-agent/src/core/compaction/compaction.ts
grep -n "does not include built-in" packages/coding-agent/docs/usage.md     # => :309
ls -1 packages/coding-agent/examples/extensions/ | wc -l                    # => 79
wc -l < packages/coding-agent/docs/extensions.md                            # => 3029

# —— Emily（在 D:/app/Emily/emily-core/emily_core 下）——
grep -rln "tiktoken\|count_tokens\|context_window\|token_budget" --include=*.py .   # => 空（无 token 计量）
grep -n "len(self.message_history) > 40" session/session_context.py         # => :320
grep -c "add_node" workitem/langgraph_engine/graph.py                       # => 9
grep -rn "MemorySaver" workitem/langgraph_engine/graph.py                   # => :13,:110
grep -rn "expert_review_enabled" --include=*.py .                           # => 仅 config.py:204
grep -rn "class .*Channel" --include=*.py .                                 # => 空
grep -rn "load_memory_context" --include=*.py .                             # => 仅定义，无调用
grep -c "reg\.register(" tools/registry.py                                  # => 19
```

### 联网调研来源

- [Pi Agent：9.8万Star，只有4个工具却干翻一切（博客园）](https://www.cnblogs.com/badhope/p/22757597/pi-agent-98k-stars-4-tools-minimalist-revolution)
- [2026 开源 AI 编程 Agent 对比：Hermes、OpenCode、Pi、omp、Kilo Code](https://blog.aihubplus.com/post/2026-open-source-ai-coding-agents-comparison/)
- [Pi Agent完整指南：极简终端编程助手，从安装到Extension开发（SegmentFault）](https://segmentfault.com/a/1190000048176674)
- [Pi Agent 简介（菜鸟教程）](http://www.runoob.com/pi-agent/pi-agent-intro.html)
- [Pi – 开源的终端编程 Agent，支持自定义工具与模型接入](https://ai-bot.cn/pi-agent-harness/)
- [Coding Agent 上下文压缩：从 Claude Code / Codex / Pi 到自研策略（CSDN）](https://blog.csdn.net/Zguigo/article/details/164398039)
- [Pi 的上下文压缩，到底是怎么工作的？（掘金）](https://juejin.cn/post/7675220071702560818)
- [Pi 不是大模型，而是一套可拆装、可嵌入的 Agent Harness](https://www.chenxutan.com/d/6395.html)
- [Pi Agent 深度解析：开源极简终端 AI 编码代理的终极指南（掘金）](https://juejin.cn/post/7675383652746264628)
