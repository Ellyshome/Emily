# LangGraph 执行引擎替换 — AI 执行计划

> **基于需求**：对话架构分析（引入 LangGraph 替换 WorkItem 内部执行引擎）
> **计划版本**：v1.1
> **目标**：用 LangGraph StateGraph + Checkpoint + interrupt() 替换现有 PipelineBUS + BusContext + WorkItemState + confirm_queue，保留 SessionAgent/Hook/SchedulerEngine/SessionContext/_real_execute 不变，通过 feature flag 实现新旧引擎并行可切换。**内置 error_analysis 节点实现 Self-Reflection 智能纠错闭环**。

---

## v1.1 修订记录

**v1.0 → v1.1 变更**：将 error_analysis（错误分析）节点从一开始就设计进 graph 拓扑，实现 Self-Reflection 智能纠错闭环。

| 变更项 | v1.0 | v1.1 |
|--------|------|------|
| M1 State | 无错误分析字段 | 新增 `error_analysis` / `replan_hint` / `error_type` 3 个字段 |
| M2 节点 | 4 节点适配 | 新增 `error_analysis.py` 模块（ErrorAnalyzer + 错误分类）+ `make_error_analysis` 工厂 + `error_analysis.md` prompt |
| M2 _llm_plan | 不改 | 内部注入 replan_hint（3 行，不改签名，向后兼容） |
| M3 graph | node3→node2 直接重规划 | node3→error_analysis→[route_after_analysis]→node2/node3/END（智能路由） |
| M3 条件边 | route_after_node3 | 新增 route_after_analysis（按错误类型路由） |
| 纠错模式 | 无信息重试（LLM 相同输入再跑） | Self-Reflection（错误上下文反馈给重规划） |

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **LangGraph 集成工程师**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：`WorkItemAgent.node1_intent/node2_plan/node3_execute/node4_summary` 签名保持 `async fn(self, BusContext) -> None` 不变；`Hook.execute(context)` 签名不变；`SessionScheduler._run_one` 签名不变。**允许**：`_llm_plan` 内部新增 replan_hint 注入（3 行，不改签名，无 replan_hint 时行为不变）
2. **业务内核独立**：`emily_core` 不 import 任何 `astrbot.*` 包（CLAUDE.md 约束 1）
3. **分层不可跳**：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`（CLAUDE.md 约束 2）
4. **Sync repo + asyncio.to_thread**：Repository 全 sync，async 用 `asyncio.to_thread()` 包裹（CLAUDE.md 约束 6）
5. **Hook 声明式 JSON 不变**：`hook_config.json` 的 12 个挂载点格式与内容不变，业务人员编辑即生效（CLAUDE.md 约束 4）
6. **保留旧引擎作为 feature flag 回退**：本期不删除 `PipelineBUS` / `BusContext` / `WorkItemState` / `confirm_queue` 任何代码，新旧引擎通过 `config.workitem_engine` 切换
7. **tenacity 不包 node3 整体**：node3 含工具循环（副作用），只能用 tenacity 包单个 `tool.handler()`，不能用 LangGraph RetryPolicy 整体重试 node3（会重复执行已成功的录入步骤）
8. **thread_id = pipeline_run_id**：LangGraph 的 `thread_id` 必须等于现有 `pipeline_run_id`，确保 trace/归档/LLM 日志可互查
9. **node3/node4/error_analysis 不配 RetryPolicy**：node3 含副作用、node4 审核修正由条件边驱动、error_analysis 本身是错误处理（重试它无意义，LLM 失败走代码兜底分类）
10. **DeepSeek 兼容**：LLM 调用仍走现有 `LLMClient`（openai SDK 兼容 DeepSeek），不引入 LangGraph 的 model binding
11. **error_analysis 代码预分类省 LLM**：权限失败 / L3 副作用已执行 → 代码直接判定 abort，不调 LLM（省钱 + 安全）。只有不确定的错误才调 LLM 分析
12. **L3 工具失败不重规划**：`discard_nodes` / `return_node_deliverable` 失败 → `permanent_failure` → abort（避免二次副作用）

---

## 上下文（执行前必读）

### 与已有计划的关系

| 已有计划 | 位置 | 关系 |
|---------|------|------|
| `AgentHarness补齐_计划_V1.md` | [需求/已完成需求文件/](需求/已完成需求文件/AgentHarness补齐_计划_V1.md) | **部分替代**。本计划的 error_analysis 节点替代其 M2（step 级重试）的"计划级纠错"部分；其 M3（node4 审核修正）仍为后续演进。若该计划已部分执行，step 级重试（tenacity 包 `tool.handler`）与本计划 error_analysis 互补共存：参数级纠错失败→计划级纠错 |

### 现有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `WorkItemAgent` | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | `node1_intent` / `node2_plan` / `node3_execute` / `node4_summary` / `node_handlers()` | 直接调用，包装为 LangGraph 节点。`_llm_plan` 内部注入 replan_hint（3 行微调） |
| `BusContext` | [pipeline/context.py](emily-core/emily_core/workitem/pipeline/context.py) | 18 字段 + `get_session_context()` / `get_actor_snapshot()` / `get_auth_context()` / `add_warning()` / `get()` / `set()` | 作为 State 的 `context` 字段，Hook 和节点 handler 通过它交互（零改动） |
| `Hook` 体系 | [pipeline/hook.py](emily-core/emily_core/workitem/pipeline/hook.py) | `AuthHook` / `AuditHook` / `ProgressHook` / `ArchiveHook` | 保留全部 Hook 子类，通过 `HookAdapter` 桥接到 graph 节点回调 |
| `HookRegistry` | [pipeline/hook_registry.py](emily-core/emily_core/workitem/pipeline/hook_registry.py) | `register(mount_point, hook)` / `get_enabled(mount_point)` | 直接复用，`HookAdapter` 内部持有 `HookRegistry` 实例 |
| `PipelineBUS.register_hooks_from_config` | [pipeline/bus.py:68](emily-core/emily_core/workitem/pipeline/bus.py#L68) | 从 `hook_config.json` 构建 Hook | 复用配置加载逻辑 |
| `WorkItem` + `WorkItemState` | [workitem.py](emily-core/emily_core/workitem/workitem.py) / [workitem_state.py](emily-core/emily_core/workitem/workitem_state.py) | `transition_to(state)` / `is_terminal` / `step_results` / `execution_plan` | 保留，graph 外层（Scheduler）驱动状态转换。error_analysis 读 `step_results` 找失败 step |
| `SessionScheduler._run_one` | [scheduler.py:97](emily-core/emily_core/workitem/scheduler.py#L97) | `async _run_one(wi, message, db_message_id) -> WorkItem` | 改造：根据 `config.workitem_engine` 选择 `bus.run` 或 `graph.ainvoke` |
| `LLMClient.chat_messages` | [client.py](emily-core/emily_core/infrastructure/llm/client.py) | `async chat_messages(messages, json_mode=True)` | error_analysis 节点调 LLM 分析错误（走现有 client，DeepSeek 兼容） |
| `load_prompt` | [prompt_loader.py](emily-core/emily_core/infrastructure/llm/prompt_loader.py) | `load_prompt(name)` 惰性加载 + 缓存 | error_analysis.md prompt 加载 |
| `PipelineExecutionLogger` | [pipeline_logger.py](emily-core/emily_core/infrastructure/logging/pipeline_logger.py) | `async log(context, started_at, node_timings)` | graph 完成后调用 |
| `LLMInteractionLogger` | [llm_logger.py](emily-core/emily_core/infrastructure/logging/llm_logger.py) | `set_context(pipeline_run_id, ...)` / `set_stage(name)` / `clear_context()` | graph 节点回调内调用，`call_category="error_analysis"` |

### 架构决策

**为什么选 LangGraph StateGraph 而非自研扩展 PipelineBUS**：

1. **断点续传**：LangGraph `PostgresSaver` 复用 emily-postgres，WI 崩溃可恢复
2. **重规划闭环**：LangGraph 条件边天然支持 `node3 → error_analysis → node2` 循环（PipelineBUS 是单向线性）
3. **AI 工具友好**：LangGraph 是 AI 生态主流，Claude/GPT 对其 API 覆盖深，生成代码准确率高
4. **Human-in-the-loop**：`interrupt()` 状态持久化，比 confirm_queue 内存堆可靠

**为什么把 error_analysis 设计成独立节点（而非 node3 内部重试）**：

1. **Self-Reflection 模式**：LangGraph 官方推荐的纠错模式——失败后先 Reflect（分析根因）再 Revise（重规划），而非盲目重试。node3 内部重试是"无信息重试"（相同输入再跑），LLM 大概率给出相同失败结果
2. **错误分类驱动路由**：error_analysis 输出 error_type，条件边按类型路由——param_error→重规划、transient→直接重试、permission_denied→abort。node3 内部重试无法做这种细粒度路由
3. **replan_hint 反馈**：error_analysis 产出 replan_hint 注入 node2 的 `_llm_plan`，让重规划"知道为什么失败"，这是 ReAct 闭环的核心（Observation→Reason→Action）
4. **代码预分类省 LLM**：权限失败 / L3 副作用 → 代码直接判定，不调 LLM。只有不确定错误才调 LLM，成本可控
5. **关注点分离**：node3 只管执行，error_analysis 只管分析，node2 只管规划。各节点单一职责，可独立测试

**为什么用 State 持有 BusContext（而非 State 替代 BusContext）**：

- Hook 子类全部读 `BusContext` 字段，若 State 替代 BusContext 需改所有 Hook——违反"保留 Hook"原则
- State 作为 BusContext 容器 + graph 控制字段（replan_count/error_analysis/replan_hint），节点函数从 `state["context"]` 取 BusContext 传给现有 handler——**handler 零改动**

**为什么 node3 不配 RetryPolicy**：

- node3 是工具循环（`_real_execute` 内 `for step in plan.steps: await tool.handler(...)`），含 L2 录入类非幂等操作
- LangGraph RetryPolicy 重试整个 node3 → 已成功的 step1 record_event 重复执行 → 数据库重复记录
- node1/node2 是纯 LLM 无副作用，可配 RetryPolicy（node 级重试安全）

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| dataclass 配置 | [config.py](emily-core/emily_core/config.py) | `@dataclass` + `field(default_factory=...)` + 中文 docstring |
| 状态枚举 | [workitem_state.py](emily-core/emily_core/workitem/workitem_state.py) | `Enum` + TRANSITIONS + TERMINAL_STATES |
| Hook 触发 | [pipeline/bus.py:233-274](emily-core/emily_core/workitem/pipeline/bus.py#L233-L274) | `_fire_before_hooks` 返回 bool / `_fire_after_hooks` fire-and-forget |
| 节点 handler 签名 | [workitem_agent.py:226](emily-core/emily_core/workitem/workitem_agent.py#L226) | `async def node1_intent(self, context: BusContext) -> None` |
| LLM 调用 + JSON 解析 | [workitem_agent.py:365-379](emily-core/emily_core/workitem/workitem_agent.py#L365-L379) | `chat_messages(json_mode=True)` + `result.get("data", {})` + try/except fallback |
| prompt 加载 | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) `_load_planner_prompt` | 模块级 `_PROMPT = None` + 惰性加载函数 |
| Scheduler 状态驱动 | [scheduler.py:97-155](emily-core/emily_core/workitem/scheduler.py#L97-L155) | `transition_to(PLANNING)` → `EXECUTING` → `should_abort ? FAILED : DONE` |
| 日志上下文注入 | [pipeline/bus.py:141-150](emily-core/emily_core/workitem/pipeline/bus.py#L141-L150) | `LLMInteractionLogger.set_context` + finally `clear_context` |

---

## 模块依赖图

```
M1(依赖+State+兼容层) ──→ M2(节点适配+error_analysis+RetryPolicy) ──→ M3(StateGraph+条件边) ──┐
                                                                  │                          │
                                                                  ↓                          ↓
                                                           M4(Hook适配层) ──→ M5(Scheduler集成) ──→ M6(端到端验证)
```

构建顺序：M1 → M2 → M3 → M4 → M5 → M6（严格串行，每模块验收通过才进下一个）

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M1 | `emily-core/requirements.txt` | 修改 | +`langgraph` +`tenacity` |
| M1 | `emily-core/emily_core/workitem/langgraph_engine/__init__.py` | 新增 | 包初始化 |
| M1 | `emily-core/emily_core/workitem/langgraph_engine/state.py` | 新增 | `WorkItemGraphState` |
| M2 | `emily-core/emily_core/workitem/langgraph_engine/nodes.py` | 新增 | `make_node1~4()` / `make_error_analysis()` |
| M2 | `emily-core/emily_core/workitem/langgraph_engine/error_analysis.py` | 新增 | `ErrorAnalyzer` / `ErrorType` |
| M2 | `emily-data/prompts/error_analysis.md` | 新增 | 错误分析 prompt |
| M2 | `emily-core/emily_core/workitem/workitem_agent.py` | 修改 | `_llm_plan` 内部注入 replan_hint（3 行） |
| M3 | `emily-core/emily_core/workitem/langgraph_engine/graph.py` | 新增 | `build_workitem_graph()` / `route_after_node3()` / `route_after_analysis()` |
| M4 | `emily-core/emily_core/workitem/langgraph_engine/hook_adapter.py` | 新增 | `HookAdapter` |
| M5 | `emily-core/emily_core/config.py` | 修改 | +`workitem_engine` / +`langgraph_max_replan` |
| M5 | `emily-core/emily_core/workitem/scheduler.py` | 修改 | `_run_one` 内新增 graph 分支 |
| M5 | `emily-core/emily_core/__init__.py` | 修改 | `_build_pipeline_bus` 旁路构建 graph |
| M6 | `scripts/verify_langgraph_engine.py` | 新增 | 验证脚本（`--dry-run` + `--mock` + `--mock-failure`） |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| [emily-core/requirements.txt](emily-core/requirements.txt) | 修改 | 追加 `langgraph>=0.2.0` + `tenacity>=8.2.0` |
| [emily-core/emily_core/config.py](emily-core/emily_core/config.py) | 扩展 | 新增 `workitem_engine` / `langgraph_max_replan` |
| [emily-core/emily_core/workitem/scheduler.py](emily-core/emily_core/workitem/scheduler.py) | 修改 | `_run_one` 内根据 `config.workitem_engine` 选择 `bus.run` 或 `graph.ainvoke`，旧逻辑保留 |
| [emily-core/emily_core/__init__.py](emily-core/emily_core/__init__.py) | 扩展 | `_build_pipeline_bus` 内新增 graph 构建（旁路） |
| [emily-core/emily_core/workitem/workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | 修改 | `_llm_plan` 内部新增 replan_hint 注入（3 行，不改签名，无 hint 时行为不变） |
| [emily-core/emily_core/workitem/pipeline/bus.py](emily-core/emily_core/workitem/pipeline/bus.py) | 不变 | — |
| [emily-core/emily_core/workitem/pipeline/context.py](emily-core/emily_core/workitem/pipeline/context.py) | 不变 | — |
| [emily-core/emily_core/workitem/pipeline/hook.py](emily-core/emily_core/workitem/pipeline/hook.py) | 不变 | — |
| [emily-core/emily_core/workitem/workitem_state.py](emily-core/emily_core/workitem/workitem_state.py) | 不变 | — |
| [emily-core/emily_core/session/session_agent.py](emily-core/emily_core/session/session_agent.py) | 不变 | — |
| [emily-core/emily_core/session/confirm_queue.py](emily-core/emily_core/session/confirm_queue.py) | 不变 | — |
| [emily-data/config/hook_config.json](emily-data/config/hook_config.json) | 不变 | — |

---

## 脚本结构约定

> 本期为架构替换，非数据处理流程，不强制"独立脚本+聚合薄壳"。但提供验证脚本支持 `--dry-run`，符合"双通道"精神。

### 独立脚本清单

| # | 脚本 | 职责 | 关键参数 | `--dry-run` 输出 |
|---|------|------|---------|------------------|
| 1 | `scripts/verify_langgraph_engine.py` | 验证 graph 构建 + mock WorkItem 执行（含失败纠错路径） | `--dry-run` `--mock` `--mock-failure` | 打印 Mermaid graph + 节点拓扑，不执行 |

### 脚本交互关系

```
scripts/verify_langgraph_engine.py（独立验证）
  ├── --dry-run           → 打印 graph Mermaid + 节点列表 + 条件边
  ├── --mock              → 正常路径 mock invoke（node1→node2→node3→node4）
  └── --mock-failure      → 失败路径 mock invoke（node3 失败→error_analysis→node2 重规划→node3 成功）

EmilyCore 集成
  └── SessionScheduler._run_one  → graph.ainvoke(state, thread_id=wi.id)（系统调用通道）
```

---

## M1: 依赖引入 + State 定义 + 兼容层骨架

**依赖**：无（本模块为首建模块）

**职责**：引入 langgraph/tenacity 依赖，定义 `WorkItemGraphState`（BusContext 容器 + graph 控制字段 + 错误分析字段），创建 `langgraph_engine` 包骨架。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | requirements.txt 追加依赖 | `emily-core/requirements.txt` |
| 2 | langgraph_engine 包初始化 | `emily-core/emily_core/workitem/langgraph_engine/__init__.py` |
| 3 | State 定义 | `emily-core/emily_core/workitem/langgraph_engine/state.py` |

### 代码

#### `emily-core/requirements.txt` — 在文件末尾追加

```python
# emily-core/requirements.txt（末尾追加）

# ── LangGraph 执行引擎（WorkItem 内部执行引擎替换）──
langgraph>=0.2.0                       # StateGraph + Checkpoint + interrupt
tenacity>=8.2.0                        # 细粒度重试（langgraph 传递依赖，显式声明便于直接 import）
```

#### `emily-core/emily_core/workitem/langgraph_engine/__init__.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/__init__.py
"""LangGraph 执行引擎 —— 替换 PipelineBUS 的 WorkItem 内部执行层。

架构关系：
  SessionAgent（编排者，不变）
    └─ SessionScheduler._run_one（feature flag 切换）
         ├─ workitem_engine="pipeline_bus" → PipelineBUS.run（旧引擎，保留回退）
         └─ workitem_engine="langgraph"    → graph.ainvoke（新引擎）

新引擎组件：
  - state.py           WorkItemGraphState（BusContext 容器 + graph 控制字段 + 错误分析字段）
  - error_analysis.py  ErrorAnalyzer（错误分类 + LLM 分析根因）
  - nodes.py           5 节点适配函数（node1~node4 + error_analysis）
  - graph.py           StateGraph 构建 + 条件边（node3 失败→error_analysis→node2 重规划）
  - hook_adapter.py    声明式 Hook 桥接到 graph 节点回调

纠错闭环（Self-Reflection）：
  node3 失败 → error_analysis（分析根因+分类）→ [route_after_analysis]
    ├─ param_error / tool_mismatch → node2（带 replan_hint 重规划）
    ├─ transient_failure → node3（直接重试，省 LLM 重新规划）
    └─ permission_denied / permanent_failure / missing_info → END

保留不变：WorkItemAgent / BusContext / Hook 体系 / WorkItem 状态机 / SessionAgent
"""

from .state import WorkItemGraphState
from .graph import build_workitem_graph

__all__ = ["WorkItemGraphState", "build_workitem_graph"]
```

#### `emily-core/emily_core/workitem/langgraph_engine/state.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/state.py
"""WorkItemGraphState —— LangGraph State，BusContext 容器 + graph 控制字段 + 错误分析字段。

设计决策（方案 B：State 持有 BusContext 引用）：
  - Hook 子类全部读 BusContext 字段，若 State 替代 BusContext 需改所有 Hook
  - State 作为 BusContext 容器，节点函数从 state["context"] 取 BusContext
    传给现有 handler —— handler 零改动

字段说明：
  - context: BusContext 实例（节点 handler 和 Hook 通过它交互）
  - replan_count: 重规划次数（条件边防死循环，上限由 config.langgraph_max_replan 控制）
  - node_timings: 节点耗时 ms（对接 PipelineExecutionLogger）
  - started_at: graph 开始时间 ISO
  - error_analysis: error_analysis 节点的分析结果 dict（error_type/root_cause/replan_hint/...）
  - replan_hint: 给 node2 的修复建议（由 error_analysis 产出，注入 _llm_plan）
  - error_type: 错误分类（由 error_analysis 产出，route_after_analysis 据此路由）
"""

from __future__ import annotations

from typing import TypedDict

from ..pipeline.context import BusContext


class WorkItemGraphState(TypedDict, total=False):
    """LangGraph State —— BusContext 容器 + graph 控制字段 + 错误分析字段。

    total=False：所有字段可选，初始 invoke 只传 context，其余由节点逐步填充。
    """
    # ── 核心载体 ──
    context: BusContext                    # 现有 BusContext，节点 handler 和 Hook 通过它交互

    # ── graph 控制字段 ──
    replan_count: int                      # 重规划次数（node3→error_analysis→node2 循环计数）
    node_timings: dict[str, int]           # 各节点耗时 ms
    started_at: str                        # graph 开始时间 ISO
    _entered_node2: bool                   # 是否已进入过 node2（区分首次规划 vs 重规划）

    # ── 错误分析字段（error_analysis 节点产出）──
    error_analysis: dict                   # 完整分析结果（error_type/root_cause/replan_hint/should_*/user_prompt）
    replan_hint: str                       # 给 node2 的修复建议（注入 _llm_plan prompt）
    error_type: str                        # 错误分类（route_after_analysis 据此路由）

    # ── 日志/trace 对接 ──
    pipeline_run_id: str                   # = BusContext.pipeline_run_id = thread_id
    current_stage: str                     # 当前节点名（对接 LLMInteractionLogger.set_stage）

    # ── 内部控制（非持久化）──
    _max_replan: int                       # 最大重规划次数（由 build_workitem_graph 注入）
```

### 模块验收检测

```bash
# 验收 1：依赖安装成功
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core python -c "import langgraph; print('langgraph', langgraph.__version__)"
→ 预期输出：langgraph 0.2.x（或更高）

# 验收 2：tenacity 可 import
docker exec emily-core python -c "import tenacity; print('tenacity', tenacity.__version__)"
→ 预期输出：tenacity 8.2.x（或更高）

# 验收 3：State 定义可 import + 字段完整（含错误分析字段）
docker exec emily-core python -c "
from emily_core.workitem.langgraph_engine.state import WorkItemGraphState
import typing
hints = typing.get_type_hints(WorkItemGraphState)
required = {'context', 'replan_count', 'node_timings', 'started_at',
            'error_analysis', 'replan_hint', 'error_type', 'pipeline_run_id', 'current_stage'}
missing = required - set(hints.keys())
assert not missing, f'缺失字段: {missing}'
print('State ok:', sorted(hints.keys()))
"
→ 预期输出：State ok: [..., 'error_analysis', 'error_type', ..., 'replan_hint', ...]

# 验收 4：__pycache__ 清除
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
```

**失败处理**：
- 依赖安装失败：`docker compose -f docker-compose-napcat.yml build emily-core` 重建镜像
- import 失败：确认 `langgraph_engine/` 下文件已在容器内 `/app/emily_core/workitem/langgraph_engine/`（bind-mount 生效）
- State 字段缺失：检查 `total=False` TypedDict 的 `get_type_hints`（字段应在 hints 中）

---

## M2: 节点适配函数 + ErrorAnalyzer + RetryPolicy

**依赖**：M1

**职责**：
1. 将 `WorkItemAgent` 的 4 个 node handler 包装为 LangGraph 节点函数
2. 新增 `ErrorAnalyzer`（错误分类 + LLM 分析根因）+ `error_analysis` 节点
3. node1/node2 配 RetryPolicy（纯 LLM 无副作用），node3/node4/error_analysis 不配
4. `_llm_plan` 内部注入 replan_hint（3 行微调，不改签名）

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | ErrorAnalyzer + 错误分类 | `emily-core/emily_core/workitem/langgraph_engine/error_analysis.py` |
| 2 | 错误分析 prompt | `emily-data/prompts/error_analysis.md` |
| 3 | 5 节点适配函数 | `emily-core/emily_core/workitem/langgraph_engine/nodes.py` |
| 4 | _llm_plan 注入 replan_hint | `emily-core/emily_core/workitem/workitem_agent.py`（修改） |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/error_analysis.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/error_analysis.py
"""ErrorAnalyzer —— node3 失败后的错误分析器（Self-Reflection 模式）。

职责：
  1. 从 BusContext.work_item.step_results 找到失败的 step
  2. 代码预分类：权限失败 / L3 副作用已执行 → 直接 abort（不调 LLM，省钱+安全）
  3. LLM 分析：不确定的错误 → 加载 error_analysis.md prompt → chat_json → 结构化分析
  4. LLM 失败兜底：默认 transient_failure（允许重试一次）

错误分类 taxonomy（route_after_analysis 据此路由）：
  - param_error        → 重规划（replan_hint 指出参数问题）
  - tool_mismatch      → 重规划（replan_hint 建议换工具）
  - transient_failure  → 直接重试 node3（省 LLM 重新规划）
  - missing_info       → abort + 回复追问用户
  - permission_denied  → abort（重规划无意义）
  - permanent_failure  → abort（L3 副作用已发生，避免二次副作用）
"""

from __future__ import annotations

import logging
from typing import Any

from ..infrastructure.logging.llm_logger import LLMInteractionLogger

logger = logging.getLogger("emily.langgraph.error_analysis")


# ════════════════════════════════════════════════════════════════════
# 错误分类
# ════════════════════════════════════════════════════════════════════


class ErrorType:
    """错误类型枚举（字符串常量，便于 JSON 序列化）。"""
    PARAM_ERROR = "param_error"              # 参数错误（缺字段/类型错/值非法）
    TOOL_MISMATCH = "tool_mismatch"          # 选错工具（该查询却录入/该录入却查询）
    TRANSIENT_FAILURE = "transient_failure"  # 瞬时故障（网络超时/服务暂时不可用）
    MISSING_INFO = "missing_info"            # 用户信息不足，需追问
    PERMISSION_DENIED = "permission_denied"  # 权限不足，不可恢复
    PERMANENT_FAILURE = "permanent_failure"  # 不可恢复（L3 副作用已执行）


# 路由分组（route_after_analysis 使用）
REPLAN_TYPES = {ErrorType.PARAM_ERROR, ErrorType.TOOL_MISMATCH}    # → node2 重规划
RETRY_TYPES = {ErrorType.TRANSIENT_FAILURE}                        # → node3 直接重试
ABORT_TYPES = {ErrorType.MISSING_INFO, ErrorType.PERMISSION_DENIED, ErrorType.PERMANENT_FAILURE}  # → END

# L3 高风险工具（失败即 permanent_failure，不重试不重规划）
L3_TOOLS = {"discard_nodes", "return_node_deliverable"}

# 权限失败关键词（代码预分类，不调 LLM）
_PERMISSION_KEYWORDS = ("权限", "无权", "permission", "forbidden", "未授权", "没有相应权限")


# ════════════════════════════════════════════════════════════════════
# prompt 惰性加载
# ════════════════════════════════════════════════════════════════════

_ERROR_ANALYSIS_PROMPT: str | None = None


def _load_error_analysis_prompt() -> str:
    """惰性加载 error_analysis.md prompt（参照 _load_planner_prompt 模式）。"""
    global _ERROR_ANALYSIS_PROMPT
    if _ERROR_ANALYSIS_PROMPT is None:
        from ..infrastructure.llm.prompt_loader import load_prompt
        _ERROR_ANALYSIS_PROMPT = load_prompt("error_analysis")
    return _ERROR_ANALYSIS_PROMPT


# ════════════════════════════════════════════════════════════════════
# ErrorAnalyzer
# ════════════════════════════════════════════════════════════════════


class ErrorAnalyzer:
    """错误分析器 —— 分析 node3 失败原因，产出 error_type + replan_hint。

    使用方式：
        analyzer = ErrorAnalyzer(llm_client=self._llm, config=config)
        result = await analyzer.analyze(ctx)
        # result = {"error_type": ..., "replan_hint": ..., "should_replan": ..., ...}
    """

    def __init__(self, llm_client=None, config=None):
        self._llm = llm_client
        self._config = config

    # ── 入口 ──

    async def analyze(self, ctx) -> dict:
        """分析 node3 失败原因。

        Args:
            ctx: BusContext（读 ctx.work_item.step_results 找失败 step）

        Returns:
            分析结果 dict：
            {
                "error_type": str,        # ErrorType 枚举值
                "root_cause": str,        # 根因描述
                "replan_hint": str,       # 给 node2 的修复建议（should_replan=True 时非空）
                "should_replan": bool,    # 是否重规划（→ node2）
                "should_retry": bool,     # 是否直接重试（→ node3）
                "should_abort": bool,     # 是否终止（→ END）
                "user_prompt": str,       # 给用户的追问（missing_info 时非空）
            }
        """
        wi = ctx.work_item
        failed_step = self._find_failed_step(wi)
        if failed_step is None:
            # 无失败 step（不应进入 error_analysis），兜底放行
            logger.warning("error_analysis: no failed step found, fallback to transient")
            return self._build_result(ErrorType.TRANSIENT_FAILURE, should_retry=True)

        # ① 代码预分类（省 LLM 调用）
        pre_type = self._code_pre_classify(failed_step)
        if pre_type == ErrorType.PERMISSION_DENIED:
            logger.info("error_analysis: code-classified as PERMISSION_DENIED (no LLM)")
            return self._build_result(
                ErrorType.PERMISSION_DENIED,
                root_cause="权限不足，重规划无法解决",
                should_abort=True,
            )
        if pre_type == ErrorType.PERMANENT_FAILURE:
            logger.info("error_analysis: code-classified as PERMANENT_FAILURE (L3 executed, no LLM)")
            return self._build_result(
                ErrorType.PERMANENT_FAILURE,
                root_cause="L3 高风险工具已执行，不可重试（避免二次副作用）",
                should_abort=True,
            )

        # ② LLM 分析（不确定的错误）
        if not self._llm:
            logger.info("error_analysis: no LLM, fallback to transient_failure")
            return self._build_result(ErrorType.TRANSIENT_FAILURE, should_retry=True)

        try:
            return await self._llm_analyze(ctx, failed_step)
        except Exception as e:
            logger.warning("error_analysis LLM failed: %s, fallback to transient_failure", e)
            return self._build_result(ErrorType.TRANSIENT_FAILURE, should_retry=True)

    # ── 内部方法 ──

    def _find_failed_step(self, wi) -> Any:
        """从 step_results 找第一个失败的 step。"""
        for sr in getattr(wi, "step_results", []) or []:
            if not getattr(sr, "success", True):
                return sr
        return None

    def _code_pre_classify(self, failed_step) -> str | None:
        """代码预分类：权限失败 / L3 副作用 → 直接返回，不调 LLM。

        判定依据：
          - 失败 step 的 output 含权限关键词 → PERMISSION_DENIED
          - 失败 step 的 tool_name ∈ L3_TOOLS → PERMANENT_FAILURE
          - 否则 → None（交给 LLM 分析）
        """
        output = getattr(failed_step, "output", "") or ""
        tool_name = self._get_step_tool_name(failed_step)

        # 权限失败
        if any(kw in output for kw in _PERMISSION_KEYWORDS):
            return ErrorType.PERMISSION_DENIED

        # L3 副作用已执行
        if tool_name and tool_name in L3_TOOLS:
            return ErrorType.PERMANENT_FAILURE

        return None

    def _get_step_tool_name(self, step_result) -> str:
        """从 StepResult 取工具名（tool_calls[0].tool_name 或空）。"""
        for tc in getattr(step_result, "tool_calls", []) or []:
            tn = getattr(tc, "tool_name", "") or ""
            if tn:
                return tn
        return ""

    async def _llm_analyze(self, ctx, failed_step) -> dict:
        """LLM 分析错误根因。"""
        wi = ctx.work_item

        # 收集失败 step 信息
        failed_step_id = getattr(failed_step, "step_id", "?")
        failed_tool_name = self._get_step_tool_name(failed_step) or "（无工具）"
        failed_output = (getattr(failed_step, "output", "") or "")[:500]
        # tool_params 从 tool_calls 取
        failed_tool_params = "{}"
        for tc in getattr(failed_step, "tool_calls", []) or []:
            failed_tool_params = str(getattr(tc, "tool_input", "{}"))[:500]
            break

        # 原始计划摘要
        plan_summary = self._summarize_plan(getattr(wi, "execution_plan", None))

        # 可用工具列表（从 session_context）
        session_ctx = ctx.get_session_context() if ctx else None
        available_tools = self._list_available_tools(session_ctx)

        # 加载 + format prompt
        prompt_template = _load_error_analysis_prompt()
        system_prompt = prompt_template.format(
            failed_step_id=failed_step_id,
            failed_tool_name=failed_tool_name,
            failed_tool_params=failed_tool_params,
            error_output=failed_output,
            original_plan=plan_summary,
            user_input=(wi.user_input or "")[:300],
            available_tools=available_tools,
        )

        # 设置 LLM 日志上下文
        LLMInteractionLogger.set_context(
            pipeline_run_id=ctx.pipeline_run_id,
            conversation_id=ctx.message.conversation_id if ctx.message else "",
            user_id=ctx.user_id,
            call_category="error_analysis",
        )
        try:
            result = await self._llm.chat_messages(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": "分析上述失败原因并返回 JSON。"}],
                json_mode=True,
            )
            data = result.get("data", {}) or {}
        finally:
            LLMInteractionLogger.clear_context()

        # 解析 LLM 输出
        return self._parse_llm_result(data)

    def _summarize_plan(self, plan) -> str:
        """摘要原始计划（step_id + tool_name 列表）。"""
        if plan is None:
            return "（无计划）"
        steps = getattr(plan, "steps", []) or []
        lines = []
        for s in steps:
            sid = getattr(s, "step_id", "?")
            tn = getattr(s, "tool_name", "") or "（无工具）"
            lines.append(f"  - {sid}: {tn}")
        return "\n".join(lines) if lines else "（空计划）"

    def _list_available_tools(self, session_ctx) -> str:
        """列出用户有权限的工具（从 session_context.available_tools）。"""
        if session_ctx is None:
            return "（无 SessionContext）"
        tools = getattr(session_ctx, "available_tools", []) or []
        names = []
        for t in tools:
            if isinstance(t, dict):
                api_id = t.get("api_id", "")
                if api_id:
                    names.append(api_id)
            else:
                names.append(str(t))
        return ", ".join(sorted(names)) if names else "（无可用工具）"

    def _parse_llm_result(self, data: dict) -> dict:
        """解析 LLM 返回的 JSON，校验 error_type 合法性。"""
        error_type = data.get("error_type", "")
        # 校验 error_type 合法
        valid_types = {
            ErrorType.PARAM_ERROR, ErrorType.TOOL_MISMATCH, ErrorType.TRANSIENT_FAILURE,
            ErrorType.MISSING_INFO, ErrorType.PERMISSION_DENIED, ErrorType.PERMANENT_FAILURE,
        }
        if error_type not in valid_types:
            logger.warning("error_analysis: LLM returned invalid error_type=%r, fallback to transient", error_type)
            error_type = ErrorType.TRANSIENT_FAILURE

        replan_hint = data.get("replan_hint", "") or ""
        should_replan = error_type in REPLAN_TYPES
        should_retry = error_type in RETRY_TYPES
        should_abort = error_type in ABORT_TYPES

        return self._build_result(
            error_type=error_type,
            root_cause=data.get("root_cause", ""),
            replan_hint=replan_hint,
            should_replan=should_replan,
            should_retry=should_retry,
            should_abort=should_abort,
            user_prompt=data.get("user_prompt", ""),
        )

    def _build_result(
        self,
        error_type: str,
        root_cause: str = "",
        replan_hint: str = "",
        should_replan: bool = False,
        should_retry: bool = False,
        should_abort: bool = False,
        user_prompt: str = "",
    ) -> dict:
        """构建标准化分析结果 dict。"""
        return {
            "error_type": error_type,
            "root_cause": root_cause,
            "replan_hint": replan_hint,
            "should_replan": should_replan,
            "should_retry": should_retry,
            "should_abort": should_abort,
            "user_prompt": user_prompt,
        }
```

#### `emily-data/prompts/error_analysis.md` — 新建

```markdown
# emily-data/prompts/error_analysis.md

你是一个错误分析专家。WorkItem 执行过程中某个步骤失败了，你需要分析失败原因、分类错误类型、给出修复建议。

## 失败步骤信息

- 步骤 ID：{failed_step_id}
- 工具名：{failed_tool_name}
- 工具参数：{failed_tool_params}
- 错误输出：{error_output}

## 原始执行计划

{original_plan}

## 用户输入

{user_input}

## 用户可用的工具

{available_tools}

## 错误分类（必须从以下选其一）

- `param_error`：参数错误（缺必填字段/类型错/值非法），重新推导参数即可修复
- `tool_mismatch`：选错工具（该查询却录入了/该录入却查询/工具不适用此场景），需要换工具
- `transient_failure`：瞬时故障（网络超时/服务暂时不可用/数据库锁冲突），重试即可
- `missing_info`：用户输入信息不足，无法继续（如未指明项目/对象），需追问用户
- `permission_denied`：权限不足，不可恢复（用户无权执行此操作）
- `permanent_failure`：不可恢复错误（如高风险操作已部分执行，重试会造成二次副作用）

## 输出格式（只返回 JSON，不要其他内容）

```json
{{
  "error_type": "param_error",
  "root_cause": "缺失必填字段 project_id，record_event 无法定位事件归属项目",
  "replan_hint": "重新规划时，record_event 步骤需补充 project_id 参数。可从 SessionContext.project_ids 获取当前用户的默认项目",
  "should_replan": true,
  "should_retry": false,
  "should_abort": false,
  "user_prompt": ""
}}
```

## 判定规则

1. **error_type 与 should_* 字段必须一致**：
   - `param_error` / `tool_mismatch` → `should_replan=true`（重规划）
   - `transient_failure` → `should_retry=true`（直接重试）
   - `missing_info` → `should_abort=true` + `user_prompt` 填追问内容
   - `permission_denied` / `permanent_failure` → `should_abort=true`

2. **replan_hint 规则**（仅 should_replan=true 时填）：
   - 指出具体问题（哪个参数错/哪个工具不合适）
   - 给出修复方向（换什么工具/补什么参数/从哪取数据）
   - 简洁，不超过 200 字

3. **user_prompt 规则**（仅 missing_info 时填）：
   - 用中文向用户追问缺失的信息
   - 简洁友好，不超过 100 字

4. **root_cause 规则**：
   - 一句话说明失败根因
   - 不超过 150 字
```

#### `emily-core/emily_core/workitem/langgraph_engine/nodes.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/nodes.py
"""LangGraph 节点适配函数 —— 包装 WorkItemAgent 的 4 个 node handler + error_analysis 节点。

每个节点函数签名：async fn(state: WorkItemGraphState) -> dict
  - 从 state["context"] 取 BusContext
  - 调用现有 handler（handler 零改动）
  - 返回 dict（State 增量更新）

RetryPolicy 策略：
  - node1/node2（纯 LLM 无副作用）：配 RetryPolicy，node 级重试安全
  - node3（工具循环，含 L2 录入非幂等）：不配 RetryPolicy
  - node4（成果总结）：不配 RetryPolicy（审核修正由条件边驱动）
  - error_analysis：不配 RetryPolicy（本身是错误处理，LLM 失败走代码兜底分类）
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.pregel import RetryPolicy

from ..infrastructure.logging.llm_logger import LLMInteractionLogger
from ..workitem.workitem_agent import WorkItemAgent
from .state import WorkItemGraphState
from .error_analysis import ErrorAnalyzer
from .hook_adapter import HookAdapter

logger = logging.getLogger("emily.langgraph.nodes")


# ── RetryPolicy 实例（node1/node2 共用，纯 LLM 调用安全重试）──
_LLM_NODE_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=1.0,
    backoff_factor=2.0,
    max_interval=10.0,
    jitter=True,
    retry_on=lambda exc: isinstance(exc, (TimeoutError, ConnectionError, OSError)),
)


def _get_context(state: WorkItemGraphState):
    """从 state 取 BusContext，缺失则抛错。"""
    ctx = state.get("context")
    if ctx is None:
        raise RuntimeError("WorkItemGraphState missing 'context' field")
    return ctx


def _enter_stage(state: WorkItemGraphState, stage_name: str) -> float:
    """节点入口：设置日志 stage + current_stage，返回开始时间戳。"""
    ctx = _get_context(state)
    ctx.current_stage = stage_name
    LLMInteractionLogger.set_stage(stage_name)
    state["current_stage"] = stage_name
    return time.monotonic()


def _exit_stage(state: WorkItemGraphState, stage_name: str, t_start: float) -> dict:
    """节点出口：记录耗时，返回增量 dict。"""
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    timings = state.get("node_timings", {})
    timings[stage_name] = elapsed_ms
    return {"node_timings": timings}


# ════════════════════════════════════════════════════════════════════
# 节点工厂函数（闭包绑定 WorkItemAgent + HookAdapter）
# ════════════════════════════════════════════════════════════════════


def make_node1(agent: WorkItemAgent, hook_adapter: HookAdapter):
    """构建 node1 节点函数（意图验证+注入）。配 RetryPolicy（纯 LLM）。"""

    async def node1(state: WorkItemGraphState) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node1")
        if not await hook_adapter.fire_before("wi_node1", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node1", t_start)
        try:
            await agent.node1_intent(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node1", ctx, e)
            ctx.should_abort = True
            ctx.abort_reason = str(e)
            if ctx.work_item is not None:
                ctx.work_item.error_message = str(e)
            return _exit_stage(state, "wi_node1", t_start)
        await hook_adapter.fire_after("wi_node1", ctx)
        return _exit_stage(state, "wi_node1", t_start)

    node1.__name__ = "node1_intent"
    return node1


def make_node2(agent: WorkItemAgent, hook_adapter: HookAdapter):
    """构建 node2 节点函数（计划+标准）。配 RetryPolicy（纯 LLM）。

    重规划时（从 error_analysis 回来）递增 replan_count，并把 replan_hint
    写入 BusContext.baggage，供 _llm_plan 读取注入 prompt。
    """

    async def node2(state: WorkItemGraphState) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node2")

        # 递增 replan_count（仅当从 error_analysis 回来时）
        entered_before = state.get("_entered_node2", False)
        if entered_before:
            state["replan_count"] = state.get("replan_count", 0) + 1
        state["_entered_node2"] = True

        # 注入 replan_hint 到 baggage（_llm_plan 会读取并追加到 prompt）
        replan_hint = state.get("replan_hint", "")
        if replan_hint:
            ctx.set("replan_hint", replan_hint)
            logger.info("node2: replan_hint injected (replan_count=%d)", state["replan_count"])

        if not await hook_adapter.fire_before("wi_node2", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node2", t_start)
        try:
            await agent.node2_plan(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node2", ctx, e)
            ctx.should_abort = True
            ctx.abort_reason = str(e)
            if ctx.work_item is not None:
                ctx.work_item.error_message = str(e)
            return _exit_stage(state, "wi_node2", t_start)
        await hook_adapter.fire_after("wi_node2", ctx)
        return _exit_stage(state, "wi_node2", t_start)

    node2.__name__ = "node2_plan"
    return node2


def make_node3(agent: WorkItemAgent, hook_adapter: HookAdapter):
    """构建 node3 节点函数（执行+验收）。不配 RetryPolicy（含工具副作用）。"""

    async def node3(state: WorkItemGraphState) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node3")
        if not await hook_adapter.fire_before("wi_node3", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node3", t_start)
        try:
            await agent.node3_execute(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node3", ctx, e)
            ctx.should_abort = True
            ctx.abort_reason = str(e)
            if ctx.work_item is not None:
                ctx.work_item.error_message = str(e)
            return _exit_stage(state, "wi_node3", t_start)
        await hook_adapter.fire_after("wi_node3", ctx)
        return _exit_stage(state, "wi_node3", t_start)

    node3.__name__ = "node3_execute"
    return node3


def make_node4(agent: WorkItemAgent, hook_adapter: HookAdapter):
    """构建 node4 节点函数（成果总结）。不配 RetryPolicy。"""

    async def node4(state: WorkItemGraphState) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "wi_node4")
        if not await hook_adapter.fire_before("wi_node4", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "wi_node4", t_start)
        try:
            await agent.node4_summary(ctx)
        except Exception as e:
            await hook_adapter.fire_error("wi_node4", ctx, e)
            # node4 非必经（对齐 PipelineBUS required=False），异常只记录不 abort
            if ctx.work_item is not None:
                ctx.work_item.add_warning(f"node4 失败: {e}")
        await hook_adapter.fire_after("wi_node4", ctx)
        return _exit_stage(state, "wi_node4", t_start)

    node4.__name__ = "node4_summary"
    return node4


def make_error_analysis(agent: WorkItemAgent, hook_adapter: HookAdapter):
    """构建 error_analysis 节点函数（错误分析，Self-Reflection）。

    从 BusContext.work_item.step_results 找失败 step → ErrorAnalyzer.analyze
    → 写入 state.error_analysis / replan_hint / error_type。
    不配 RetryPolicy（LLM 失败走代码兜底 transient_failure）。
    """
    analyzer = ErrorAnalyzer(llm_client=getattr(agent, "_llm", None), config=getattr(agent, "_config", None))

    async def error_analysis(state: WorkItemGraphState) -> dict:
        ctx = _get_context(state)
        t_start = _enter_stage(state, "error_analysis")

        # before hook（error_analysis 节点也支持 hook 挂载，便于审计）
        if not await hook_adapter.fire_before("error_analysis", ctx):
            ctx.should_abort = True
            return _exit_stage(state, "error_analysis", t_start)

        try:
            result = await analyzer.analyze(ctx)
        except Exception as e:
            logger.error("error_analysis crashed: %s, fallback to transient", e, exc_info=True)
            result = {
                "error_type": "transient_failure",
                "root_cause": f"分析器异常: {e}",
                "replan_hint": "",
                "should_replan": False,
                "should_retry": True,
                "should_abort": False,
                "user_prompt": "",
            }

        logger.info(
            "error_analysis: type=%s replan=%s retry=%s abort=%s hint=%s",
            result.get("error_type"),
            result.get("should_replan"),
            result.get("should_retry"),
            result.get("should_abort"),
            (result.get("replan_hint", "") or "")[:60],
        )

        # 写入 State（条件边 route_after_analysis 据此路由）
        state_update = {
            "error_analysis": result,
            "error_type": result.get("error_type", ""),
            "replan_hint": result.get("replan_hint", ""),
        }

        # should_abort 时设 ctx（route_after_node3/analysis 会检查）
        if result.get("should_abort"):
            ctx.should_abort = True
            ctx.abort_reason = result.get("root_cause", "error_analysis abort")
            # missing_info 时把 user_prompt 写入 work_item 供回复层使用
            user_prompt = result.get("user_prompt", "")
            if user_prompt and ctx.work_item is not None:
                ctx.work_item.add_warning(f"需追问用户: {user_prompt}")

        await hook_adapter.fire_after("error_analysis", ctx)
        state_update.update(_exit_stage(state, "error_analysis", t_start))
        return state_update

    error_analysis.__name__ = "error_analysis"
    return error_analysis


def node_retry_policies() -> dict[str, RetryPolicy]:
    """返回各节点的 RetryPolicy（None 表示不重试）。"""
    return {
        "wi_node1": _LLM_NODE_RETRY,
        "wi_node2": _LLM_NODE_RETRY,
        "wi_node3": None,            # 工具循环含副作用，不整体重试
        "wi_node4": None,            # 审核修正由条件边驱动
        "error_analysis": None,      # 本身是错误处理，LLM 失败走代码兜底
    }
```

#### `emily-core/emily_core/workitem/workitem_agent.py` — `_llm_plan` 内部注入 replan_hint

在 [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) 的 `_llm_plan` 方法内，`full_messages.append({"role": "user", "content": f"Plan for: {wi.user_input[:200]}"})` 之后、`self._llm.chat_messages(full_messages, ...)` 之前，插入 replan_hint 注入逻辑。

定位 `_llm_plan` 内这一段（约 [workitem_agent.py:358-366](emily-core/emily_core/workitem/workitem_agent.py#L358-L366)）：

```python
        # 组装多轮 messages: [system] + message_history + [plan_request]
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(message_history)
        full_messages.append({
            "role": "user",
            "content": f"Plan for: {wi.user_input[:200]}",
        })

        try:
            result = await self._llm.chat_messages(full_messages, json_mode=True)
```

在 `full_messages.append(...)` 之后、`try:` 之前插入：

```python
# emily-core/emily_core/workitem/workitem_agent.py（_llm_plan 内插入 replan_hint 注入）

        # ── 重规划反馈：若 error_analysis 产出了 replan_hint，注入到 prompt ──
        # 由 LangGraph node2 适配函数写入 context.baggage["replan_hint"]
        # 无 replan_hint 时（首次规划）此段不执行，行为不变
        replan_hint = context.get("replan_hint", "") if context else ""
        if replan_hint:
            full_messages.insert(-1, {
                "role": "system",
                "content": (
                    f"⚠️ 上次执行失败，错误分析建议：{replan_hint}\n"
                    f"请在重新规划时参考此建议调整工具选择或参数。"
                ),
            })
            logger.info("_llm_plan: replan_hint injected (hint=%s)", replan_hint[:80])
```

> **不改签名**：`_llm_plan(self, wi, context)` 签名不变。仅在内部读取 `context.get("replan_hint")`（BusContext 已有 `get` 方法，见 [context.py:138](emily-core/emily_core/workitem/pipeline/context.py#L138)）。无 replan_hint 时此段不执行，旧引擎行为完全不变。

### 模块验收检测

```bash
# 验收 1：ErrorAnalyzer 可 import + 错误分类常量完整
docker exec emily-core python -c "
from emily_core.workitem.langgraph_engine.error_analysis import ErrorAnalyzer, ErrorType, REPLAN_TYPES, RETRY_TYPES, ABORT_TYPES, L3_TOOLS
assert ErrorType.PARAM_ERROR == 'param_error'
assert ErrorType.PERMISSION_DENIED in ABORT_TYPES
assert ErrorType.TRANSIENT_FAILURE in RETRY_TYPES
assert ErrorType.TOOL_MISMATCH in REPLAN_TYPES
assert 'discard_nodes' in L3_TOOLS
print('ErrorAnalyzer ok, types:', sorted([ErrorType.PARAM_ERROR, ErrorType.TOOL_MISMATCH, ErrorType.TRANSIENT_FAILURE, ErrorType.MISSING_INFO, ErrorType.PERMISSION_DENIED, ErrorType.PERMANENT_FAILURE]))
"
→ 预期输出：ErrorAnalyzer ok, types: ['missing_info', 'param_error', 'permanent_failure', 'permission_denied', 'tool_mismatch', 'transient_failure']

# 验收 2：代码预分类（权限失败不调 LLM）
docker exec emily-core python -c "
import asyncio
from emily_core.workitem.langgraph_engine.error_analysis import ErrorAnalyzer, ErrorType
from emily_core.workitem.pipeline.context import BusContext
from emily_core.workitem.workitem import WorkItem

class MockStepResult:
    def __init__(self, output, tool_name=''):
        self.success = False
        self.output = output
        self.tool_calls = []
        if tool_name:
            class MockTC:
                pass
            tc = MockTC()
            tc.tool_name = tool_name
            tc.tool_input = {}
            self.tool_calls = [tc]
        self.step_id = 'step-01'

async def main():
    analyzer = ErrorAnalyzer(llm_client=None)  # 无 LLM，测代码预分类
    ctx = BusContext()
    ctx.work_item = WorkItem()
    # 权限失败
    ctx.work_item.step_results = [MockStepResult('您没有相应权限。')]
    r = await analyzer.analyze(ctx)
    assert r['error_type'] == ErrorType.PERMISSION_DENIED and r['should_abort'], f'权限失败应 abort: {r}'
    # L3 副作用
    ctx.work_item.step_results = [MockStepResult('执行出错', tool_name='discard_nodes')]
    r = await analyzer.analyze(ctx)
    assert r['error_type'] == ErrorType.PERMANENT_FAILURE and r['should_abort'], f'L3 应 permanent: {r}'
    # 无 LLM 兜底 transient
    ctx.work_item.step_results = [MockStepResult('参数缺失', tool_name='record_event')]
    r = await analyzer.analyze(ctx)
    assert r['error_type'] == ErrorType.TRANSIENT_FAILURE and r['should_retry'], f'无 LLM 应 transient: {r}'
    print('pre-classify ok')

asyncio.run(main())
"
→ 预期输出：pre-classify ok

# 验收 3：5 节点工厂函数存在 + RetryPolicy 策略
docker exec emily-core python -c "
from emily_core.workitem.langgraph_engine.nodes import make_node1, make_node2, make_node3, make_node4, make_error_analysis, node_retry_policies
policies = node_retry_policies()
assert policies['wi_node1'] is not None, 'node1 应配 RetryPolicy'
assert policies['wi_node3'] is None, 'node3 不配'
assert policies['error_analysis'] is None, 'error_analysis 不配'
print('5 nodes ok, policies:', {k: 'retry' if v else 'no-retry' for k, v in policies.items()})
"
→ 预期输出：5 nodes ok, policies: {'error_analysis': 'no-retry', 'wi_node1': 'retry', 'wi_node2': 'retry', 'wi_node3': 'no-retry', 'wi_node4': 'no-retry'}

# 验收 4：error_analysis.md prompt 可加载
docker exec emily-core python -c "
from emily_core.workitem.langgraph_engine.error_analysis import _load_error_analysis_prompt
p = _load_error_analysis_prompt()
assert '{failed_step_id}' in p and '{error_output}' in p, 'prompt 变量缺失'
assert 'param_error' in p and 'permission_denied' in p, '错误分类缺失'
print('prompt ok, len=', len(p))
"
→ 预期输出：prompt ok, len= <字数>

# 验收 5：_llm_plan replan_hint 注入（确认 workitem_agent.py 改动生效）
docker exec emily-core python -c "
import inspect
from emily_core.workitem.workitem_agent import WorkItemAgent
src = inspect.getsource(WorkItemAgent._llm_plan)
assert 'replan_hint' in src, '_llm_plan 未注入 replan_hint'
assert 'context.get(\"replan_hint\"' in src, '未从 context 读取 replan_hint'
print('_llm_plan replan_hint injection ok')
"
→ 预期输出：_llm_plan replan_hint injection ok

# 验收 6：执行前确认 WorkItemAgent.node4_summary 方法名
docker exec emily-core python -c "
from emily_core.workitem.workitem_agent import WorkItemAgent
assert hasattr(WorkItemAgent, 'node4_summary'), 'node4_summary 方法不存在，请确认真实方法名'
print('node4_summary exists')
"
→ 预期输出：node4_summary exists
→ 若不存在：运行 `docker exec emily-core python -c "print([m for m in dir(WorkItemAgent) if m.startswith('node')])"` 确认真实名，替换 nodes.py 中 make_node4 的调用

# 验收 7：清缓存
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
```

**失败处理**：
- `node4_summary` 方法名不存在：验收 6 确认真实方法名，替换 `make_node4` 内调用
- RetryPolicy 参数不兼容：检查 langgraph 版本，`retry_on` 在旧版可能叫 `retry_on_exception`
- prompt 加载失败：确认 `emily-data/prompts/error_analysis.md` 已在容器内 `/app/prompts/` 下（参照 [prompts/](emily-data/prompts/) 挂载路径）
- `_llm_plan` 注入未生效：确认插入位置在 `full_messages.append(user)` 之后、`chat_messages` 之前
- import 循环：`error_analysis.py` 不应 import `nodes.py`/`graph.py`（单向依赖）

---

## M3: StateGraph 构建 + 条件边（含 error_analysis 纠错闭环）

**依赖**：M1, M2

**职责**：构建 LangGraph `StateGraph`，5 节点（node1~node4 + error_analysis）+ 条件边实现 Self-Reflection 纠错闭环：`node3 失败 → error_analysis → [按错误类型路由] → node2 重规划 / node3 重试 / END`。先用 `MemorySaver`。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | StateGraph 构建 + 条件边路由 | `emily-core/emily_core/workitem/langgraph_engine/graph.py` |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/graph.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/graph.py
"""StateGraph 构建 —— 5 节点 + 条件边（Self-Reflection 纠错闭环）。

Graph 拓扑：
  START → node1 → node2 → node3 → [route_after_node3] → node4 → END
                     ↑       │            │
                     │       │            └→ error_analysis → [route_after_analysis]
                     │       │                      │
                     │       │                      ├→ node2（param_error/tool_mismatch，带 replan_hint 重规划）
                     ↑───────┘ ←── replan ──────────┤
                     │                              ├→ node3（transient_failure，直接重试）
                     │                              └→ END（permission_denied/permanent_failure/missing_info）
                     │
                     └── node3 ←── retry ────────────┘

条件边路由：
  route_after_node3:
    - should_abort → END
    - 有失败 step 且 replan_count < max_replan → error_analysis（先分析再决定重规划/重试）
    - 否则 → node4

  route_after_analysis:
    - should_abort / ABORT_TYPES → END
    - RETRY_TYPES (transient_failure) → node3（直接重试，省 LLM 重新规划）
    - REPLAN_TYPES (param_error/tool_mismatch) → node2（带 replan_hint 重规划）
    - 兜底 → END

Checkpoint：本期用 MemorySaver，后续切 PostgresSaver。
thread_id = pipeline_run_id，对接现有 trace/归档/LLM 日志。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from ..workitem.workitem_agent import WorkItemAgent
from .state import WorkItemGraphState
from .nodes import (
    make_node1, make_node2, make_node3, make_node4, make_error_analysis,
    node_retry_policies,
)
from .error_analysis import REPLAN_TYPES, RETRY_TYPES, ABORT_TYPES
from .hook_adapter import HookAdapter

logger = logging.getLogger("emily.langgraph.graph")


def _has_failed_step(ctx) -> bool:
    """检查 BusContext.work_item 是否有失败的 step。"""
    wi = ctx.work_item
    if wi is None:
        return False
    for sr in getattr(wi, "step_results", []) or []:
        if not getattr(sr, "success", True):
            return True
    return False


def route_after_node3(state: WorkItemGraphState) -> str:
    """node3 之后的条件边路由。

    返回值必须是 add_conditional_edges 映射中的 key：
      "error_analysis" / "node4" / "end"

    路由优先级：
      1. should_abort → "end"
      2. 有失败 step 且 replan_count < max_replan → "error_analysis"（先分析）
      3. 否则 → "node4"
    """
    ctx = state["context"]
    max_replan = state.get("_max_replan", 1)
    replan_count = state.get("replan_count", 0)

    # ① should_abort → 结束
    if ctx.should_abort:
        logger.info("route_after_node3: should_abort=True → end")
        return "end"

    # ② 有失败 step 且未超重规划上限 → error_analysis 分析
    if _has_failed_step(ctx) and replan_count < max_replan:
        logger.info("route_after_node3: failed step + replan_count=%d < %d → error_analysis",
                    replan_count, max_replan)
        return "error_analysis"

    # ③ 正常 → node4
    return "node4"


def route_after_analysis(state: WorkItemGraphState) -> str:
    """error_analysis 之后的条件边路由（按错误类型路由）。

    返回值： "node2" / "node3" / "end"

    路由规则：
      - should_abort / ABORT_TYPES → end
      - RETRY_TYPES (transient_failure) → node3（直接重试）
      - REPLAN_TYPES (param_error/tool_mismatch) → node2（带 replan_hint 重规划）
      - 兜底 → end
    """
    ctx = state["context"]
    # should_abort 由 error_analysis 节点在 ABORT_TYPES 时设置
    if ctx.should_abort:
        logger.info("route_after_analysis: should_abort=True → end")
        return "end"

    error_type = state.get("error_type", "")
    analysis = state.get("error_analysis", {})

    # ABORT_TYPES → end
    if error_type in ABORT_TYPES or analysis.get("should_abort"):
        logger.info("route_after_analysis: error_type=%s (ABORT) → end", error_type)
        return "end"

    # RETRY_TYPES → node3 直接重试
    if error_type in RETRY_TYPES or analysis.get("should_retry"):
        logger.info("route_after_analysis: error_type=%s (RETRY) → node3", error_type)
        return "node3"

    # REPLAN_TYPES → node2 重规划（replan_hint 已写入 state）
    if error_type in REPLAN_TYPES or analysis.get("should_replan"):
        logger.info("route_after_analysis: error_type=%s (REPLAN) → node2", error_type)
        return "node2"

    # 兜底
    logger.warning("route_after_analysis: unknown error_type=%s → end", error_type)
    return "end"


def route_after_node2(state: WorkItemGraphState) -> str:
    """node2 之后的路由：should_abort → end，否则 → node3。"""
    ctx = state["context"]
    if ctx.should_abort:
        return "end"
    return "node3"


def build_workitem_graph(
    agent: WorkItemAgent,
    hook_adapter: HookAdapter,
    max_replan: int = 1,
    checkpointer: Any = None,
) -> Any:
    """构建 WorkItem 执行 StateGraph（含 error_analysis 纠错闭环）。

    Args:
        agent: WorkItemAgent 实例（提供 4 个 node handler + _llm 供 ErrorAnalyzer）
        hook_adapter: HookAdapter 实例（从 hook_config.json 加载）
        max_replan: 最大重规划次数（node3→error_analysis→node2 循环上限）
        checkpointer: Checkpoint 实例，None 用 MemorySaver

    Returns:
        编译后的 LangGraph CompiledGraph
    """
    graph_builder = StateGraph(WorkItemGraphState)

    # ── 注册节点（5 个，带 RetryPolicy）──
    policies = node_retry_policies()
    graph_builder.add_node("wi_node1", make_node1(agent, hook_adapter), retry=policies["wi_node1"])
    graph_builder.add_node("wi_node2", make_node2(agent, hook_adapter), retry=policies["wi_node2"])
    graph_builder.add_node("wi_node3", make_node3(agent, hook_adapter), retry=policies["wi_node3"])
    graph_builder.add_node("wi_node4", make_node4(agent, hook_adapter), retry=policies["wi_node4"])
    graph_builder.add_node("error_analysis", make_error_analysis(agent, hook_adapter), retry=policies["error_analysis"])

    # ── 边 ──
    graph_builder.add_edge(START, "wi_node1")
    graph_builder.add_edge("wi_node1", "wi_node2")

    # node2 → node3（带 should_abort 条件）
    graph_builder.add_conditional_edges(
        "wi_node2",
        route_after_node2,
        {"node3": "wi_node3", "end": END},
    )

    # node3 → 条件路由（error_analysis / node4 / end）
    graph_builder.add_conditional_edges(
        "wi_node3",
        route_after_node3,
        {"error_analysis": "error_analysis", "node4": "wi_node4", "end": END},
    )

    # error_analysis → 条件路由（node2 重规划 / node3 重试 / end）
    graph_builder.add_conditional_edges(
        "error_analysis",
        route_after_analysis,
        {"node2": "wi_node2", "node3": "wi_node3", "end": END},
    )

    # node4 → END
    graph_builder.add_edge("wi_node4", END)

    # ── 编译 ──
    saver = checkpointer if checkpointer is not None else MemorySaver()
    graph = graph_builder.compile(checkpointer=saver)
    graph._max_replan = max_replan  # type: ignore[attr-defined]

    logger.info(
        "WorkItem graph built: 5 nodes (含 error_analysis), max_replan=%d, checkpointer=%s",
        max_replan, type(saver).__name__,
    )
    return graph


def make_initial_state(context, max_replan: int = 1) -> WorkItemGraphState:
    """构建 graph 初始 State。"""
    return WorkItemGraphState(
        context=context,
        replan_count=0,
        node_timings={},
        started_at="",
        error_analysis={},
        replan_hint="",
        error_type="",
        pipeline_run_id=context.pipeline_run_id,
        current_stage="",
        _entered_node2=False,
        _max_replan=max_replan,
    )
```

### 模块验收检测

```bash
# 验收 1：graph 可构建 + 5 节点注册
docker exec emily-core python -c "
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
class MockAgent:
    _llm = None
    _config = None
    async def node1_intent(self, ctx): pass
    async def node2_plan(self, ctx): pass
    async def node3_execute(self, ctx): pass
    async def node4_summary(self, ctx): pass
adapter = build_hook_adapter_from_config({}, {})
g = build_workitem_graph(MockAgent(), adapter, max_replan=1)
nodes = list(g.get_graph().nodes.keys())
print('nodes:', sorted(nodes))
assert 'wi_node1' in nodes and 'wi_node4' in nodes and 'error_analysis' in nodes, '节点缺失'
print('graph build ok, max_replan=', g._max_replan)
"
→ 预期输出：nodes: ['__end__', '__start__', 'error_analysis', 'wi_node1', 'wi_node2', 'wi_node3', 'wi_node4']
          graph build ok, max_replan= 1

# 验收 2：Mermaid 可视化（验证纠错闭环拓扑）
docker exec emily-core python -c "
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
class MockAgent:
    _llm = None
    _config = None
    async def node1_intent(self, ctx): pass
    async def node2_plan(self, ctx): pass
    async def node3_execute(self, ctx): pass
    async def node4_summary(self, ctx): pass
adapter = build_hook_adapter_from_config({}, {})
g = build_workitem_graph(MockAgent(), adapter, max_replan=1)
print(g.get_graph().draw_mermaid())
"
→ 预期输出：Mermaid 图含：
  - wi_node3 → error_analysis（失败分析）
  - error_analysis → wi_node2（重规划回边）
  - error_analysis → wi_node3（直接重试回边）
  - error_analysis → __end__（abort）
→ 关键检查：图中存在 error_analysis 节点 + 3 条出边（node2/node3/end）

# 验收 3：正常路径 mock invoke（node1→node2→node3→node4）
docker exec emily-core python -c "
import asyncio
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
from emily_core.workitem.pipeline.context import BusContext
call_log = []
class MockAgent:
    _llm = None
    _config = None
    async def node1_intent(self, ctx): call_log.append('node1')
    async def node2_plan(self, ctx): call_log.append('node2')
    async def node3_execute(self, ctx): call_log.append('node3')  # 不设失败 step
    async def node4_summary(self, ctx): call_log.append('node4')
async def main():
    g = build_workitem_graph(MockAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
    state = make_initial_state(BusContext(), max_replan=1)
    result = await g.ainvoke(state, config={'configurable': {'thread_id': 't1'}})
    print('call_order:', call_log)
    assert call_log == ['node1', 'node2', 'node3', 'node4'], f'正常路径错误: {call_log}'
    print('normal path ok')
asyncio.run(main())
"
→ 预期输出：
call_order: ['node1', 'node2', 'node3', 'node4']
normal path ok

# 验收 4：失败纠错路径（node3 失败→error_analysis→node2 重规划→node3 成功）
docker exec emily-core python -c "
import asyncio
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
from emily_core.workitem.pipeline.context import BusContext
from emily_core.workitem.workitem import WorkItem
call_log = []
class MockStepResult:
    def __init__(self, success, output='', tool_name=''):
        self.success = success
        self.output = output
        self.step_id = 'step-01'
        self.tool_calls = []
class MockAgent:
    _llm = None
    _config = None
    async def node1_intent(self, ctx): call_log.append('node1')
    async def node2_plan(self, ctx):
        call_log.append('node2')
        # 重规划时清掉失败 step（模拟重新规划后执行成功）
    async def node3_execute(self, ctx):
        call_log.append('node3')
        # 首次失败（param_error），重规划后成功
        if ctx.work_item and not getattr(ctx.work_item, '_second_run', False):
            ctx.work_item.step_results = [MockStepResult(False, '参数缺失 project_id', 'record_event')]
            ctx.work_item._second_run = True
        else:
            ctx.work_item.step_results = [MockStepResult(True)]
    async def node4_summary(self, ctx): call_log.append('node4')
async def main():
    g = build_workitem_graph(MockAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
    ctx = BusContext()
    ctx.work_item = WorkItem()
    state = make_initial_state(ctx, max_replan=1)
    result = await g.ainvoke(state, config={'configurable': {'thread_id': 't2'}})
    print('call_order:', call_log)
    print('error_type:', result.get('error_type'))
    print('replan_count:', result.get('replan_count'))
    # 应该出现 error_analysis（但因无 LLM，会兜底 transient_failure → 直接重试 node3）
    assert 'error_analysis' in call_log, f'应经过 error_analysis: {call_log}'
    print('error correction path ok')
asyncio.run(main())
"
→ 预期输出：call_order 含 'node3', 'error_analysis', 'node3'（失败→分析→重试/重规划→成功→node4）
          error correction path ok
→ 关键检查：error_analysis 出现在 node3 失败后，且最终到达 node4

# 验收 5：权限失败直接 abort（不走 error_analysis LLM）
docker exec emily-core python -c "
import asyncio
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
from emily_core.workitem.pipeline.context import BusContext
from emily_core.workitem.workitem import WorkItem
call_log = []
class MockStepResult:
    def __init__(self, success, output):
        self.success = success
        self.output = output
        self.step_id = 'step-01'
        self.tool_calls = []
class MockAgent:
    _llm = None
    _config = None
    async def node1_intent(self, ctx): call_log.append('node1')
    async def node2_plan(self, ctx): call_log.append('node2')
    async def node3_execute(self, ctx):
        call_log.append('node3')
        ctx.work_item.step_results = [MockStepResult(False, '您没有相应权限。')]
    async def node4_summary(self, ctx): call_log.append('node4')
async def main():
    g = build_workitem_graph(MockAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
    ctx = BusContext()
    ctx.work_item = WorkItem()
    state = make_initial_state(ctx, max_replan=1)
    result = await g.ainvoke(state, config={'configurable': {'thread_id': 't3'}})
    print('call_order:', call_log)
    print('error_type:', result.get('error_type'))
    print('should_abort:', ctx.should_abort)
    assert result.get('error_type') == 'permission_denied', '权限失败应分类为 permission_denied'
    assert ctx.should_abort, '权限失败应 abort'
    assert 'node4' not in call_log, '权限失败不应到 node4'
    print('permission abort ok')
asyncio.run(main())
"
→ 预期输出：
error_type: permission_denied
should_abort: True
permission abort ok

# 验收 6：清缓存
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
```

**失败处理**：
- Mermaid 无 error_analysis 节点：检查 `add_node("error_analysis", ...)` 是否调用
- error_analysis 无 3 条出边：检查 `add_conditional_edges("error_analysis", route_after_analysis, {...})` 映射 key 与返回值一致（"node2"/"node3"/"end"）
- 正常路径走了 error_analysis：`_has_failed_step` 误判，检查 MockAgent 的 node3 是否残留失败 step_results
- 权限失败未 abort：`_code_pre_classify` 的关键词匹配失败，检查 output 文本是否含 `_PERMISSION_KEYWORDS` 中的词
- 循环死锁：`replan_count` 未递增，检查 `make_node2` 的 `_entered_node2` 逻辑

---

## M4: Hook 适配层

**依赖**：M3

**职责**：将声明式 JSON Hook（`hook_config.json` 的 12 个挂载点）桥接到 LangGraph 节点回调。`HookAdapter` 封装 `HookRegistry`，在节点函数前后触发 `before/after/on_error` hook，保留 deny-wins 三态语义。**`hook_config.json` 不变**（error_analysis 节点的 hook 挂载点可选，业务人员按需在 `before:error_analysis` / `after:error_analysis` 添加）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Hook 适配器 | `emily-core/emily_core/workitem/langgraph_engine/hook_adapter.py` |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/hook_adapter.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/hook_adapter.py
"""HookAdapter —— 声明式 Hook 桥接到 LangGraph 节点回调。

复用现有 HookRegistry + Hook 子类（AuthHook/AuditHook/ProgressHook/ArchiveHook），
保留 hook_config.json 声明式配置和三态语义（ALLOW/WARN/BLOCK，deny-wins）。

语义映射：
  PipelineBUS._fire_before_hooks  →  HookAdapter.fire_before(node_name, ctx) -> bool
  PipelineBUS._fire_after_hooks   →  HookAdapter.fire_after(node_name, ctx) -> None
  PipelineBUS._fire_error_hooks   →  HookAdapter.fire_error(node_name, ctx, err) -> None

挂载点命名：before:{node} / after:{node} / on_error:{node}
  node ∈ {wi_node1, wi_node2, wi_node3, wi_node4, error_analysis}
  error_analysis 的挂载点为可选（hook_config.json 默认无，业务人员按需添加审计 hook）

before hook 返回 False = BLOCK（节点不执行，graph 走 should_abort 分支）。
after/error hook fire-and-forget（失败只记日志，不阻断）。
"""

from __future__ import annotations

import logging
from typing import Any

from ..pipeline.hook_registry import HookRegistry
from ..pipeline.hook import HookDecision

logger = logging.getLogger("emily.langgraph.hook_adapter")


class HookAdapter:
    """Hook 适配器 —— 桥接 HookRegistry 到 graph 节点回调。"""

    def __init__(self, registry: HookRegistry):
        self._registry = registry

    async def fire_before(self, node_name: str, ctx) -> bool:
        """触发 before:{node_name} hooks。返回 False 表示被阻断。

        deny-wins：任一 hook BLOCK 则返回 False。
        before hook 异常视为阻断（安全第一，对齐 PipelineBUS 语义）。
        """
        mount = f"before:{node_name}"
        for hook in self._registry.get_enabled(mount):
            try:
                result = await hook.execute(ctx)
                if result.is_blocked:
                    logger.info(
                        "graph blocked by hook '%s' at %s: %s",
                        hook.name, mount, result.message,
                    )
                    ctx.abort_reason = result.message
                    return False
                if result.decision == HookDecision.WARN:
                    ctx.add_warning(result.message)
            except Exception as e:
                logger.error("Before hook '%s' at %s failed: %s", hook.name, mount, e)
                ctx.abort_reason = f"鉴权/核验服务异常: {e}"
                return False
        return True

    async def fire_after(self, node_name: str, ctx) -> None:
        """触发 after:{node_name} hooks。fire-and-forget。"""
        mount = f"after:{node_name}"
        for hook in self._registry.get_enabled(mount):
            try:
                await hook.execute(ctx)
            except Exception as e:
                logger.warning(
                    "After hook '%s' at %s failed (non-blocking): %s",
                    hook.name, mount, e,
                )

    async def fire_error(self, node_name: str, ctx, error: Exception) -> None:
        """触发 on_error:{node_name} hooks。"""
        mount = f"on_error:{node_name}"
        for hook in self._registry.get_enabled(mount):
            try:
                await hook.execute(ctx)
            except Exception as e:
                logger.error("Error hook '%s' at %s also failed: %s", hook.name, mount, e)


def build_hook_adapter_from_config(
    hook_config: dict,
    injected_services: dict,
) -> HookAdapter:
    """从 hook_config.json 构建 HookAdapter。

    复用 PipelineBUS._build_hook_from_spec 的 Hook 构建逻辑，
    注册到独立 HookRegistry（不依赖 PipelineBUS 实例）。
    """
    from ..pipeline.hook import HOOK_TYPE_MAP

    registry = HookRegistry()
    hooks_section = hook_config.get("hooks", {})
    if not hooks_section:
        logger.info("No hooks found in config for graph engine")
        return HookAdapter(registry)

    count = 0
    for mount_point, hook_specs in hooks_section.items():
        if not isinstance(hook_specs, list):
            continue
        for spec in hook_specs:
            if not isinstance(spec, dict):
                continue
            hook_type = spec.get("type", "")
            hook_name = spec.get("name", "unnamed_hook")
            if hook_type not in HOOK_TYPE_MAP:
                logger.warning("Unknown hook type '%s' for '%s'", hook_type, hook_name)
                continue
            cls = HOOK_TYPE_MAP[hook_type]
            try:
                kwargs: dict = {}
                if hook_type == "auth":
                    kwargs["resource_type"] = spec.get("resource_type", "")
                    kwargs["action"] = spec.get("action", "")
                elif hook_type == "audit":
                    kwargs["event_type"] = spec.get("event_type", "")
                elif hook_type == "progress":
                    if "progress_sender" in injected_services:
                        kwargs["progress_sender"] = injected_services["progress_sender"]
                    if "progress_template" in injected_services:
                        kwargs["progress_template"] = injected_services["progress_template"]
                    kwargs["enable_progress"] = spec.get("enabled", True)
                elif hook_type == "archive":
                    if "archive_writer" in injected_services:
                        kwargs["archive_writer"] = injected_services["archive_writer"]
                kwargs["name"] = hook_name
                kwargs["priority"] = spec.get("priority", 10)
                kwargs["enabled"] = spec.get("enabled", True)
                hook = cls(**kwargs)
                registry.register(mount_point, hook)
                count += 1
            except Exception as e:
                logger.error("Failed to build hook '%s' (type=%s): %s", hook_name, hook_type, e)

    logger.info("HookAdapter registered %d hook(s)", count)
    return HookAdapter(registry)
```

### 模块验收检测

```bash
# 验收 1：HookAdapter 可从 hook_config.json 构建 + 12 挂载点
docker exec emily-core python -c "
import json
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
with open('/app/config/hook_config.json') as f:
    cfg = json.load(f)
adapter = build_hook_adapter_from_config(cfg, {})
mounts = adapter._registry.list_all()
print('mount_points:', sorted(mounts.keys()))
total = sum(len(v) for v in mounts.values())
assert total > 0, '应加载到 hook'
assert 'before:wi_node1' in mounts, 'before:wi_node1 缺失'
print('hook adapter ok, total_hooks=', total)
"
→ 预期输出：mount_points 含 before/after/on_error × 4 节点，total_hooks ≥ 10

# 验收 2：AuthHook BLOCK 阻断节点
docker exec emily-core python -c "
import asyncio
from emily_core.workitem.langgraph_engine.hook_adapter import HookAdapter
from emily_core.workitem.pipeline.hook_registry import HookRegistry
from emily_core.workitem.pipeline.hook import AuthHook, HookResult
from emily_core.workitem.pipeline.context import BusContext
reg = HookRegistry()
class AlwaysBlockHook(AuthHook):
    async def execute(self, ctx):
        return HookResult.block('测试阻断')
reg.register('before:wi_node1', AlwaysBlockHook(name='test.block'))
adapter = HookAdapter(reg)
async def main():
    ctx = BusContext()
    ok = await adapter.fire_before('wi_node1', ctx)
    assert ok is False and ctx.abort_reason == '测试阻断'
    print('block ok:', ctx.abort_reason)
asyncio.run(main())
"
→ 预期输出：block ok: 测试阻断

# 验收 3：graph 集成 Hook 后正常路径仍跑通
docker exec emily-core python -c "
import asyncio
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
from emily_core.workitem.pipeline.context import BusContext
call_log = []
class MockAgent:
    _llm = None; _config = None
    async def node1_intent(self, ctx): call_log.append('node1')
    async def node2_plan(self, ctx): call_log.append('node2')
    async def node3_execute(self, ctx): call_log.append('node3')
    async def node4_summary(self, ctx): call_log.append('node4')
async def main():
    adapter = build_hook_adapter_from_config({}, {})
    g = build_workitem_graph(MockAgent(), adapter, max_replan=1)
    state = make_initial_state(BusContext(), max_replan=1)
    await g.ainvoke(state, config={'configurable': {'thread_id': 't4'}})
    assert call_log == ['node1', 'node2', 'node3', 'node4']
    print('graph with hook_adapter ok')
asyncio.run(main())
"
→ 预期输出：graph with hook_adapter ok

# 验收 4：清缓存
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
```

**失败处理**：
- hook_config.json 路径不对：容器内 `/app/config/hook_config.json`，确认 volume 挂载
- AuthHook BLOCK 未阻断：检查节点函数内 `if not await hook_adapter.fire_before(...): ctx.should_abort = True`
- 循环 import：`hook_adapter.py` 不 import `nodes.py`/`graph.py`

---

## M5: Scheduler 集成 + Feature Flag 切换

**依赖**：M3, M4

**职责**：在 `Config` 新增 `workitem_engine` / `langgraph_max_replan` 字段；`SessionScheduler._run_one` 根据 flag 选择 `bus.run`（旧）或 `graph.ainvoke`（新）；`EmilyCore._build_pipeline_bus` 旁路构建 graph + HookAdapter。**旧引擎代码保留，可随时回退**。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Config 新增字段 | `emily-core/emily_core/config.py` |
| 2 | Scheduler graph 分支 | `emily-core/emily_core/workitem/scheduler.py` |
| 3 | EmilyCore graph 构建 | `emily-core/emily_core/__init__.py` |

### 代码

#### `emily-core/emily_core/config.py` — 在 `llm_*` 字段后追加

```python
# emily-core/emily_core/config.py（在 LLM 配置字段后追加）

    # ── WorkItem 执行引擎（LangGraph 替换）──
    workitem_engine: str = "pipeline_bus"
    """WorkItem 内部执行引擎选择。
    - "pipeline_bus"：旧引擎（PipelineBUS + 4 节点顺序总线，保留回退）
    - "langgraph"：新引擎（StateGraph + 5 节点含 error_analysis 纠错闭环 + Checkpoint）
    切换后重启 emily-core 生效。"""

    langgraph_max_replan: int = 1
    """LangGraph 引擎最大重规划次数（node3 失败→error_analysis→node2 循环上限，防死循环）。
    0 = 禁用重规划（node3 失败直接走 error_analysis 分类，但不重规划）。
    1 = 允许 1 次重规划（默认，平衡纠错能力与成本）。"""
```

#### `emily-core/emily_core/__init__.py` — 在 `_build_pipeline_bus` 方法末尾追加 graph 构建

在 [__init__.py](emily-core/emily_core/__init__.py) 的 `_build_pipeline_bus` 方法中，Hook 注册之后追加。**不替换 BUS，不删除 BUS 代码**。

定位 `_build_pipeline_bus` 方法末尾（`hook_config` 注册之后），追加：

```python
# emily-core/emily_core/__init__.py（_build_pipeline_bus 末尾追加）

        # ── LangGraph 引擎旁路构建（feature flag 控制）──
        self._workitem_graph = None
        self._hook_adapter = None
        if getattr(self.config, "workitem_engine", "pipeline_bus") == "langgraph":
            try:
                from .workitem.langgraph_engine.graph import build_workitem_graph
                from .workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config

                hook_cfg = self._load_hook_config() or {"hooks": {}}
                injected = self._collect_injected_services()
                self._hook_adapter = build_hook_adapter_from_config(hook_cfg, injected)

                self._workitem_graph = build_workitem_graph(
                    agent=self._workitem_agent,
                    hook_adapter=self._hook_adapter,
                    max_replan=getattr(self.config, "langgraph_max_replan", 1),
                    checkpointer=None,  # 本期 MemorySaver，后续切 PostgresSaver
                )
                logger.info(
                    "LangGraph engine built: 5 nodes (含 error_analysis), max_replan=%d, checkpointer=MemorySaver",
                    getattr(self.config, "langgraph_max_replan", 1),
                )
            except Exception as e:
                logger.error("LangGraph engine build failed, fallback to pipeline_bus: %s", e)
                self._workitem_graph = None
                self._hook_adapter = None
```

#### `emily-core/emily_core/workitem/scheduler.py` — `_run_one` 新增 graph 分支

在 [scheduler.py:97](emily-core/emily_core/workitem/scheduler.py#L97) `_run_one` 方法中，`wi.transition_to(WorkItemState.EXECUTING)` 之后、`await self._bus.run(context)` 处，改为根据 engine 选择。**旧 `bus.run` 逻辑保留**。

定位 `_run_one` 内的这一段：

```python
            # PLANNING → EXECUTING（节点内部经过 node1/node2 规划 + node3 执行）
            wi.transition_to(WorkItemState.EXECUTING)
            await self._bus.run(context)

            if context.should_abort:
                ...
```

替换为：

```python
# emily-core/emily_core/workitem/scheduler.py（_run_one 内替换 bus.run 调用段）

            # PLANNING → EXECUTING（节点内部经过 node1/node2 规划 + node3 执行）
            wi.transition_to(WorkItemState.EXECUTING)

            # ── 引擎选择：feature flag 切换 ──
            core = getattr(self, "_core", None)
            graph = getattr(core, "_workitem_graph", None) if core else None
            engine = getattr(getattr(core, "config", None), "workitem_engine", "pipeline_bus") if core else "pipeline_bus"

            if engine == "langgraph" and graph is not None:
                await self._run_graph(context, graph)
            else:
                await self._bus.run(context)

            if context.should_abort:
                wi.transition_to(WorkItemState.FAILED)
                wi.error_message = wi.error_message or context.abort_reason
                logger.warning("Scheduler[%s] WI %s FAILED: %s",
                               self.session_id, wi.id, wi.error_message)
            else:
                wi.transition_to(WorkItemState.DONE)
                logger.info("Scheduler[%s] WI %s DONE", self.session_id, wi.id)
```

并在 `SessionScheduler` 类内新增 `_run_graph` 方法（在 `_run_one` 方法之后）：

```python
# emily-core/emily_core/workitem/scheduler.py（SessionScheduler 类内新增 _run_graph 方法）

    async def _run_graph(self, context, graph) -> None:
        """通过 LangGraph 引擎执行 WorkItem（含 error_analysis 纠错闭环）。

        对齐 PipelineBUS.run 的日志上下文注入 + per-message progress_sender 注入，
        确保 Hook/归档/trace 行为与旧引擎一致。
        """
        from ..infrastructure.logging.llm_logger import LLMInteractionLogger
        from ..infrastructure.logging.business_event_logger import BusinessEventLogger
        from .workitem.langgraph_engine.state import make_initial_state

        # 注入日志上下文（对齐 bus.py:141-150）
        LLMInteractionLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
            user_id=context.user_id,
        )
        BusinessEventLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
        )

        # 注入 per-message progress_sender（对齐 bus.py:157-168）
        core = getattr(self, "_core", None)
        outbound_bus = getattr(core, "outbound_bus", None) if core else None
        if outbound_bus is not None and context.message is not None:
            _cid = context.message.conversation_id or ""

            def _send_progress(text: str, _bus=outbound_bus, _cid=_cid) -> None:
                _bus.publish("progress", {"content": text, "conversation_id": _cid})

            context.baggage.setdefault("progress_sender", _send_progress)

        try:
            max_replan = getattr(getattr(core, "config", None), "langgraph_max_replan", 1) if core else 1
            state = make_initial_state(context, max_replan=max_replan)
            # thread_id = pipeline_run_id，对接现有 trace/归档/LLM 日志
            config = {"configurable": {"thread_id": context.pipeline_run_id}}
            await graph.ainvoke(state, config=config)
        finally:
            LLMInteractionLogger.clear_context()
            BusinessEventLogger.clear_context()
```

> **执行前确认**：Read [scheduler.py](emily-core/emily_core/workitem/scheduler.py) 的 `__init__` 和 [session_pool.py](emily-core/emily_core/adapters/session/session_pool.py) 的 `SessionFactory`，确认 scheduler 是否已持有 `_core` 引用。若有，直接用 `self._core._workitem_graph`；若无，在 `__init__` 加 `core` 参数并在 `SessionFactory` 传入。

### 模块验收检测

```bash
# 验收 1：Config 字段存在 + 默认值
docker exec emily-core python -c "
from emily_core.config import Config
c = Config()
assert c.workitem_engine == 'pipeline_bus', '默认应为 pipeline_bus（回退安全）'
assert c.langgraph_max_replan == 1
print('config ok:', c.workitem_engine, c.langgraph_max_replan)
"
→ 预期输出：config ok: pipeline_bus 1

# 验收 2：旧引擎默认仍工作（feature flag 未开启）
docker exec emily-core python -c "
import json
print(json.load(open('/app/config/core_config.json')).get('workitem_engine', '未设置→默认 pipeline_bus'))
"
→ 预期输出：未设置→默认 pipeline_bus（或 core_config.json 显式值）

# 验收 3：切换到 langgraph 后 graph 构建
# 在 core_config.json 中加 "workitem_engine": "langgraph"，或通过 env 映射
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 30 emily-core 2>&1 | findstr "LangGraph engine built"
→ 预期输出：LangGraph engine built: 5 nodes (含 error_analysis), max_replan=1, checkpointer=MemorySaver

# 验收 4：清缓存
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
```

**失败处理**：
- Config 字段未生效：Read `bootstrap.py` 的 env→config 映射，可能需加 env 映射
- graph 构建失败回退：日志 "LangGraph engine build failed, fallback to pipeline_bus"，检查 M3/M4 验收
- scheduler 拿不到 graph：确认 `self._core._workitem_graph` 路径，或调整 `SessionFactory` 注入

---

## M6: 端到端验证脚本 + emy-test 实战

**依赖**：M1-M5 全部完成

**职责**：创建独立验证脚本（`--dry-run` / `--mock` / `--mock-failure`），并用 emy-test 端到端验证新引擎（含 error_analysis 纠错路径）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 验证脚本 | `scripts/verify_langgraph_engine.py` |

### 代码

#### `scripts/verify_langgraph_engine.py` — 新建

```python
# scripts/verify_langgraph_engine.py
"""LangGraph 执行引擎验证脚本（含 error_analysis 纠错路径）。

用法：
  uv run python scripts/verify_langgraph_engine.py --dry-run          # 打印 graph Mermaid
  uv run python scripts/verify_langgraph_engine.py --mock             # 正常路径
  uv run python scripts/verify_langgraph_engine.py --mock-failure     # 失败纠错路径
  uv run python scripts/verify_langgraph_engine.py --mock-permission  # 权限失败 abort 路径
  uv run python scripts/verify_langgraph_engine.py --status           # 查看引擎配置
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "emily-core"))


def cmd_dry_run() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config

    class MockAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): pass
        async def node2_plan(self, ctx): pass
        async def node3_execute(self, ctx): pass
        async def node4_summary(self, ctx): pass

    adapter = build_hook_adapter_from_config({}, {})
    g = build_workitem_graph(MockAgent(), adapter, max_replan=1)
    print("=== WorkItem LangGraph 结构（含 error_analysis）===")
    print(f"节点数: {len(g.get_graph().nodes)}")
    print(f"max_replan: {g._max_replan}")
    print("\n=== Mermaid ===")
    print(g.get_graph().draw_mermaid())
    return 0


def _make_mock_agents():
    """返回 (正常 agent, 失败纠错 agent, 权限失败 agent)。"""
    call_log = []

    class MockStepResult:
        def __init__(self, success, output='', tool_name=''):
            self.success = success; self.output = output
            self.step_id = 'step-01'; self.tool_calls = []
    class NormalAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): call_log.append('node1')
        async def node2_plan(self, ctx): call_log.append('node2')
        async def node3_execute(self, ctx):
            call_log.append('node3')
            ctx.work_item.step_results = [MockStepResult(True)]
        async def node4_summary(self, ctx): call_log.append('node4')
    class FailureAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): call_log.append('node1')
        async def node2_plan(self, ctx): call_log.append('node2')
        async def node3_execute(self, ctx):
            call_log.append('node3')
            if not getattr(ctx.work_item, '_replanned', False):
                ctx.work_item.step_results = [MockStepResult(False, '参数缺失', 'record_event')]
                ctx.work_item._replanned = True
            else:
                ctx.work_item.step_results = [MockStepResult(True)]
        async def node4_summary(self, ctx): call_log.append('node4')
    class PermissionAgent:
        _llm = None; _config = None
        async def node1_intent(self, ctx): call_log.append('node1')
        async def node2_plan(self, ctx): call_log.append('node2')
        async def node3_execute(self, ctx):
            call_log.append('node3')
            ctx.work_item.step_results = [MockStepResult(False, '您没有相应权限。')]
        async def node4_summary(self, ctx): call_log.append('node4')

    return call_log, NormalAgent, FailureAgent, PermissionAgent


def cmd_mock() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    call_log, NormalAgent, _, _ = _make_mock_agents()
    async def main():
        g = build_workitem_graph(NormalAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
        ctx = BusContext(); ctx.work_item = WorkItem()
        state = make_initial_state(ctx, max_replan=1)
        result = await g.ainvoke(state, config={'configurable': {'thread_id': 'mock-normal'}})
        print("=== 正常路径 ===")
        print(f"执行顺序: {call_log}")
        print(f"replan_count: {result.get('replan_count')}")
        assert call_log == ['node1', 'node2', 'node3', 'node4']
        print("✅ 正常路径通过")
    asyncio.run(main())
    return 0


def cmd_mock_failure() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    call_log, _, FailureAgent, _ = _make_mock_agents()
    async def main():
        g = build_workitem_graph(FailureAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
        ctx = BusContext(); ctx.work_item = WorkItem()
        state = make_initial_state(ctx, max_replan=1)
        result = await g.ainvoke(state, config={'configurable': {'thread_id': 'mock-failure'}})
        print("=== 失败纠错路径 ===")
        print(f"执行顺序: {call_log}")
        print(f"error_type: {result.get('error_type')}")
        print(f"replan_count: {result.get('replan_count')}")
        print(f"最终 should_abort: {ctx.should_abort}")
        assert 'error_analysis' in call_log, '应经过 error_analysis'
        print("✅ 失败纠错路径通过（error_analysis 触发）")
    asyncio.run(main())
    return 0


def cmd_mock_permission() -> int:
    from emily_core.workitem.langgraph_engine.graph import build_workitem_graph, make_initial_state
    from emily_core.workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
    from emily_core.workitem.pipeline.context import BusContext
    from emily_core.workitem.workitem import WorkItem

    call_log, _, _, PermissionAgent = _make_mock_agents()
    async def main():
        g = build_workitem_graph(PermissionAgent(), build_hook_adapter_from_config({}, {}), max_replan=1)
        ctx = BusContext(); ctx.work_item = WorkItem()
        state = make_initial_state(ctx, max_replan=1)
        result = await g.ainvoke(state, config={'configurable': {'thread_id': 'mock-perm'}})
        print("=== 权限失败路径 ===")
        print(f"执行顺序: {call_log}")
        print(f"error_type: {result.get('error_type')}")
        print(f"should_abort: {ctx.should_abort}")
        assert result.get('error_type') == 'permission_denied'
        assert ctx.should_abort
        assert 'node4' not in call_log
        print("✅ 权限失败路径通过（代码预分类 abort，未调 LLM）")
    asyncio.run(main())
    return 0


def cmd_status() -> int:
    try:
        from emily_core.config import Config
        c = Config()
        print(f"workitem_engine: {c.workitem_engine}")
        print(f"langgraph_max_replan: {c.langgraph_max_replan}")
    except Exception as e:
        print(f"Config 读取失败: {e}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LangGraph 执行引擎验证（含纠错闭环）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="打印 graph 结构")
    group.add_argument("--mock", action="store_true", help="正常路径 mock")
    group.add_argument("--mock-failure", action="store_true", help="失败纠错路径 mock")
    group.add_argument("--mock-permission", action="store_true", help="权限失败 abort 路径")
    group.add_argument("--status", action="store_true", help="查看引擎配置")
    args = parser.parse_args()

    handlers = {
        ("dry_run", cmd_dry_run), ("mock", cmd_mock), ("mock_failure", cmd_mock_failure),
        ("mock_permission", cmd_mock_permission), ("status", cmd_status),
    }
    for attr, fn in handlers:
        if getattr(args, attr):
            return fn()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 模块验收检测

```bash
# 验收 1：--dry-run 打印 graph（含 error_analysis 节点 + 3 条出边）
$env:PYTHONIOENCODING="utf-8"
uv run python scripts/verify_langgraph_engine.py --dry-run
→ 预期输出：Mermaid 图含 error_analysis 节点，3 条出边（wi_node2/wi_node3/__end__）

# 验收 2：--mock 正常路径
uv run python scripts/verify_langgraph_engine.py --mock
→ 预期输出：执行顺序 ['node1','node2','node3','node4']，✅ 正常路径通过

# 验收 3：--mock-failure 失败纠错路径
uv run python scripts/verify_langgraph_engine.py --mock-failure
→ 预期输出：执行顺序含 'node3','error_analysis'，✅ 失败纠错路径通过

# 验收 4：--mock-permission 权限失败 abort
uv run python scripts/verify_langgraph_engine.py --mock-permission
→ 预期输出：error_type: permission_denied，should_abort: True，✅ 权限失败路径通过

# 验收 5：切换到 langgraph 引擎 + emy-test 端到端
# core_config.json 中设置 "workitem_engine": "langgraph"
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 20 emily-core 2>&1 | findstr "LangGraph engine built"
→ 预期：LangGraph engine built: 5 nodes (含 error_analysis), max_replan=1

# 查真实用户
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"

# L1 查询类（正常路径）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查询最近的事件" --sender "李景利"
→ 预期：正常返回事件列表

# L2 录入类
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"
→ 预期：正常创建事件

# 验收 6：日志确认 error_analysis 触发（若本次执行有失败）
docker logs --tail 80 emily-core 2>&1 | findstr /R "error_analysis\|route_after\|replan_hint"
→ 预期：失败时出现 error_analysis 日志 + error_type 分类

# 验收 7：回退验证
# core_config.json 改回 "workitem_engine": "pipeline_bus"
docker compose -f docker-compose-napcat.yml restart emily-core
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查询最近的事件" --sender "李景利"
→ 预期：旧引擎正常工作（回退安全）
```

**失败处理**：
- emy-test 回复异常：先切回 `pipeline_bus` 确认旧引擎正常，再排查新引擎。查 `docker logs emily-core`
- error_analysis 未触发：确认 node3 有失败 step 且 `replan_count < max_replan`
- 权限失败走了 LLM：`_code_pre_classify` 关键词匹配失败，检查 step output 文本
- 回退失败：确认旧引擎代码未被删除（PipelineBUS/BusContext 完整）

---

## 组装验证

所有模块完成后，运行端到端组装验证（M6 已覆盖，此处汇总）：

```bash
# 1. 切换到 langgraph 引擎
# （core_config.json 中设置 "workitem_engine": "langgraph"）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 确认引擎构建（含 error_analysis）
docker logs --tail 20 emily-core 2>&1 | findstr "LangGraph engine built"
→ 预期：LangGraph engine built: 5 nodes (含 error_analysis), max_replan=1

# 3. graph 结构验证（含 error_analysis + 3 出边）
uv run python scripts/verify_langgraph_engine.py --dry-run

# 4. 三路径 mock 验证
uv run python scripts/verify_langgraph_engine.py --mock            # 正常
uv run python scripts/verify_langgraph_engine.py --mock-failure    # 失败纠错
uv run python scripts/verify_langgraph_engine.py --mock-permission # 权限 abort

# 5. emy-test 全场景实战
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查询最近的事件" --sender "李景利"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "废弃节点 SG-001" --sender "李景利"
→ 预期：三类场景行为与旧引擎一致；L3 失败时 error_analysis 分类为 permanent_failure 并 abort

# 6. error_analysis 纠错闭环验证（查日志）
docker logs --tail 100 emily-core 2>&1 | findstr /R "error_analysis\|route_after\|replan_hint\|error_type"
→ 预期：失败时出现 error_analysis → 分类 → 路由（replan/retry/abort）日志链

# 7. LLM 流量确认（error_analysis 的 LLM 调用）
docker exec mitmproxy tail -10 /app/logs/llm_trace.jsonl
→ 预期：失败时含 call_category=error_analysis 的 LLM 调用；权限失败时无该调用（代码预分类省 LLM）

# 8. 回退安全验证
# （core_config.json 改回 "workitem_engine": "pipeline_bus"）
docker compose -f docker-compose-napcat.yml restart emily-core
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查询最近的事件" --sender "李景利"
→ 预期：旧引擎正常工作
```

### 通过标准

| 指标 | 目标 | 验证方式 |
|------|------|---------|
| graph 构建 | 5 节点（含 error_analysis）+ MemorySaver | `--dry-run` Mermaid |
| 正常路径 | node1→node2→node3→node4 | `--mock` |
| 失败纠错路径 | node3 失败→error_analysis→重规划/重试 | `--mock-failure` |
| 权限失败 abort | 代码预分类 permission_denied，不调 LLM | `--mock-permission` |
| Hook 三态保留 | AuthHook BLOCK 阻断 | 日志 "graph blocked by hook" |
| thread_id 对齐 | = pipeline_run_id | 日志一致 |
| emy-test L1/L2/L3 | 行为同旧引擎 | 三类场景实战 |
| L3 失败不重规划 | permanent_failure → abort | L3 测试不出现 replan |
| error_analysis 省 LLM | 权限/L3 失败无 error_analysis LLM 调用 | LLM trace 无 call_category=error_analysis |
| 回退安全 | 切回 pipeline_bus 仍正常 | 切换后 emy-test 通过 |

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.2 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化（如 LangGraph API 不兼容需大改） → **停止**，报告给用户

### 已知风险点（执行时重点验证）

| 风险 | 模块 | 应对 |
|------|------|------|
| LangGraph 版本 API 差异（`RetryPolicy` 参数名 / `add_node(retry=)` vs `retry_policy=`） | M2/M3 | 验收时先 `docker exec emily-core python -c "import langgraph; print(langgraph.__version__)"`，按实际版本调整参数名 |
| `node4_summary` 方法名不符 | M2 | 验收 6 先确认 `WorkItemAgent` 的 node4 方法名 |
| `SessionScheduler` 未持有 `_core` 引用 | M5 | 执行前 Read `scheduler.py` `__init__` + `session_pool.py` `SessionFactory`，确认 graph 注入路径 |
| `core_config.json` 不支持 `workitem_engine` 字段 | M5 | Read `bootstrap.py` 的 env→config 映射 |
| Hook 配置路径（容器内 `/app/config/hook_config.json`） | M4 | 验收 1 先确认路径 |
| `error_analysis.md` prompt 路径（容器内 `/app/prompts/`） | M2 | 确认 `emily-data/prompts/` 的 volume 挂载，参照现有 planner.md / guardian_step.md |
| `_llm_plan` 的 replan_hint 注入位置偏差 | M2 | 确认插入在 `full_messages.append(user)` 之后、`chat_messages` 之前；无 replan_hint 时行为不变 |
| error_analysis 死循环 | M3 | `replan_count` 由 `make_node2` 递增，`route_after_node3` 检查 `< max_replan`；transient_failure 走 node3 不增 replan_count |

---

## 后续演进（不在本期范围）

| 演进项 | 依赖 | 收益 |
|--------|------|------|
| **PostgresSaver 替换 MemorySaver** | 本期 M5 完成 | 断点续传：容器重启后 WI 可从 checkpoint 恢复 |
| **confirm_queue → interrupt()** | 本期 M5 完成 | WAITING_CONFIRM 状态持久化，替代内存堆 |
| **tenacity 包 `tool.handler`** | 本期 M2 完成 | node3 工具调用参数级重试（与 error_analysis 计划级纠错互补：参数重试失败→error_analysis→重规划） |
| **node4 审核修正循环** | 本期 M3 条件边 | node4 自循环审核修正（Guardian issues 驱动 LLM 重新合成 reply） |
| **missing_info → interrupt()** | 本期 M3 完成 | error_analysis 分类 missing_info 时挂起问用户，恢复后继续（当前是 abort+追问） |
| **删除旧 PipelineBUS 代码** | 新引擎稳定运行 2 周 | 代码清理 |
| **WI 并发执行（Send API 扇出）** | 本期 M5 完成 | 多 WI 复合任务并发 |
| **error_analysis 多策略修复** | 本期 M3 完成 | Send API 并行尝试多种修复方案，取首个成功 |

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。v1.1 基于 2026-07-28 代码现状，融入 error_analysis 智能纠错闭环。执行前若 AgentHarness补齐_计划_V1 已部分落地，需确认其新增方法是否影响本计划的节点适配。*
