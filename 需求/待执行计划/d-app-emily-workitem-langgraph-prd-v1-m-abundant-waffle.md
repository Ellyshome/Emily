# WorkItem LangGraph 全迁移（L3 Agent Loop）— AI 执行计划

> **基于需求**：[WorkItem_LangGraph全迁移_PRD_V1.md](需求/WorkItem_LangGraph全迁移_PRD_V1.md)
> **计划版本**：v1.0
> **目标**：将 WorkItem 全生命周期迁移到 LangGraph 统一图 + L3 agent loop（`agent_node↔tool_node` ReAct 循环），用 function calling 替代结构化输出，SOP `.md` 作为指导注入 system prompt，消灭 Skill YAML/Executor/ParamMapping/ExecutionPlan 一整套结构化执行机制。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家** + **LangGraph 图引擎专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

**用户已确认的三项关键决策**（贯穿全计划）：
1. **回复链路**：保留 SessionAgent LLM 合成 —— summarizing 节点从 step_results 提取 StructuredResult，SessionAgent `_synthesize_final_reply` 不变。
2. **Guardian**：仅保留回复级审核（`_review_final_reply`），砍掉 step 级并行审核。
3. **切换策略**：大爆炸原子切换 —— M6 一次性替换旧 5 节点图，M7 一次性重写 scheduler，不保留 legacy 标志位。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：除非计划明确标注"修改方法签名"，否则只能在已有类中新增方法。
2. **`emily_core` 不 import 任何 `astrbot.*`**（CLAUDE.md #1）。
3. **分层不可跳**：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`（CLAUDE.md #2）。
4. **Sync repo + `asyncio.to_thread`**：Repository 全 sync，async Service 用 `asyncio.to_thread()` 包裹（CLAUDE.md #6）。
5. **每模块验收必须通过**，否则停止并报告。
6. **大爆炸切换**：M6 替换旧图后立即移除旧 `make_node1/2/3/4`；M7 重写 scheduler 后立即移除旧状态转换逻辑。不引入 `engine_mode` 标志位。
7. **工具必须带参数 schema**：所有 LLM 可调工具的 `parameters` 必须含完整 JSON Schema（CLAUDE.md #11）。
8. **根治而非迁就**：不保留并行旧路径做"回退"，回退靠 git revert（CLAUDE.md #0）。
9. **State 纯可序列化**：AgentLoopState 仅含基础类型（str/int/dict/list），BusContext 仍走 contextvars，保证 MemorySaver checkpoint 可用。
10. **参照模式**：所有新代码必须参照"代码模式参照表"中的源文件，风格不一致视为失败。

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法/字段 | 本次怎么用 |
|------|------|--------------|-----------|
| `LLMClient.chat_with_tools` | [client.py:275](emily-core/emily_core/infrastructure/llm/client.py#L275) | `chat_with_tools(messages, tools) → {"type":"tool_call","tool_name","tool_arguments","tool_call_id"} 或 {"type":"text","content"}` | `agent_node` 调用；返回值已含 tool_call_id |
| `BusinessFlowToolRegistry` | [business_flow_tools.py](emily-core/emily_core/tools/business_flow_tools.py) | `get(name)`, `list_names()`, `__contains__(name)` | `tool_adapter` 枚举工具 + `tool_node` 按 name 取 handler |
| `BusinessFlowTool` | [business_flow_tools.py:21](emily-core/emily_core/tools/business_flow_tools.py#L21) | `name`, `description`, `parameters`, `handler`, `permission_flag` | 转 OpenAI tool spec |
| `BusContext` | [context.py](emily-core/emily_core/workitem/pipeline/context.py) | `get_session_context()`, `get_actor_snapshot()`, `work_item`, `message`, `set/get`, `should_abort` | 节点间共享状态（contextvars） |
| `SessionContext` | [session_context.py](emily-core/emily_core/session/session_context.py) | `project_ids: list[str]`, `available_tools: list[dict]`(含 `api_id`), `message_history`, `get_prompt_variables()` | resolver 权限层 + prompt builder |
| `EventRepository.find_project_by_name` | [event_repo.py:235](emily-core/emily_core/repositories/event_repo.py#L235) | `@staticmethod find_project_by_name(name) → Project|None`（精确匹配） | 参照模式，新增 `ProjectRepository.find_by_name_fuzzy` |
| `_extract_structured_result` | [workitem_agent.py:730](emily-core/emily_core/workitem/workitem_agent.py#L730) | 从 `wi.step_results` 提取 `StructuredResult`（规则提炼，零 LLM） | **迁移**到 nodes.py 的 summarizing 节点复用 |
| `StepResult/ToolCallRecord/DbResult/RagResult/StructuredResult` | [pipeline/interfaces/execution.py](emily-core/emily_core/workitem/pipeline/interfaces/execution.py) | dataclass | `tool_node` 构建 StepResult（**保留**，不删） |
| `MemorySaver` | `langgraph.checkpoint.memory` | `compile(checkpointer=MemorySaver())` | graph 编译（PRD 决策 #10：内存，PostgreSQLSaver 留后续） |
| `ErrorAnalyzer` | [error_analysis.py](emily-core/emily_core/workitem/langgraph_engine/error_analysis.py) | `analyze(ctx) → dict`（含 should_abort/should_replan） | iteration cap 兜底节点复用 |
| `HookAdapter` | [hook_adapter.py](emily-core/emily_core/workitem/langgraph_engine/hook_adapter.py) | `fire_before(name,ctx)`, `fire_after`, `fire_error` | 新节点复用 hook 触发模式 |
| `WorkItemState` | [workitem_state.py](emily-core/emily_core/workitem/workitem_state.py) | 8 态枚举 + TRANSITIONS | **不改**（graph 的 wi_state 是独立字符串字段） |
| `load_prompt` | [prompt_loader.py](emily-core/emily_core/infrastructure/llm/prompt_loader.py) | `load_prompt(name) → str`（带缓存） | 加载 SOP .md 全文 |

### 架构决策

1. **L3 agent loop**：`agent_node`（调 `chat_with_tools`）↔ `tool_node`（调 handler）ReAct 循环。LLM 基于累积 `messages` 决策，直到 `type=="text"`。
2. **状态即对话历史**：`messages` list 是 agent loop 唯一状态。**保留 `wi.step_results`**：`tool_node` 每次追加 StepResult，供 summarizing 节点提取 StructuredResult + 归档兼容（ArchiveHook 读 step_results）。
3. **function calling**：`chat_with_tools`，LLM 看 tool_result 自纠。BUG-01 在源头预防（schema resolver hint）+ loop 自纠（看 tool_result）。
4. **SOP .md 指导**：system prompt = SOP .md 全文 + 可见工具 schema 摘要 + session 上下文 + 用户消息历史。
5. **WAITING_FOR_INPUT**：LangGraph `interrupt(question)` + `Command(resume=user_input)`。M7 scheduler 检测 interrupt 挂起，续接时 `Command(resume=...)`。
6. **iteration cap**：默认 12（config `agent_loop_max_iterations`），超限 → error_analysis 兜底 → abort 或问用户。
7. **resolver 三层权限**：LLM session 级决策 / resolver 超 session 读+输出过滤 / handler session 级兜底。resolver 始终对 LLM 可见，内部做权限约束。
8. **大爆炸切换**：M6 替换旧图后旧 node1-4 失效；M8 删除 WorkItemAgent 旧方法 + Skill 体系。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| Repository | [repositories/event_repo.py:234](emily-core/emily_core/repositories/event_repo.py#L234) | `@staticmethod` + `get_session()` + `Optional[Model]` 返回 + `with` 块 |
| LangGraph 节点工厂 | [langgraph_engine/nodes.py:74](emily-core/emily_core/workitem/langgraph_engine/nodes.py#L74) | `make_xxx(agent,hook_adapter)` 返回 `async fn(state)`，`_enter_stage`/`_exit_stage` + `fire_before/after` |
| 条件边路由 | [langgraph_engine/graph.py:41](emily-core/emily_core/workitem/langgraph_engine/graph.py#L41) | `route_after_xxx(state: dict) -> str`，从 state 读 flow_control |
| 图构建 | [langgraph_engine/graph.py:118](emily-core/emily_core/workitem/langgraph_engine/graph.py#L118) | `StateGraph(State)` + `add_node` + `add_conditional_edges` + `compile(checkpointer=MemorySaver())` |
| 工具注册 | [tools/registry.py:116](emily-core/emily_core/tools/registry.py#L116) | `_tool(name,desc,params,handler,category,permission_flag)` 构造 `BusinessFlowTool` |
| LLM 调用 + trace | [workitem_agent.py:395](emily-core/emily_core/workitem/workitem_agent.py#L395) | `chat_messages()` + `LLMInteractionLogger.set_context` + `try/except` + 回退 |
| 权限可见性过滤 | [workitem_agent.py:547](emily-core/emily_core/workitem/workitem_agent.py#L547) | 从 `session_ctx.available_tools` 提取 `api_id` 集合，fail-closed |
| SessionAgent 续接 | [session_agent.py:313](emily-core/emily_core/session/session_agent.py#L313) | `_paused_workitem` + `WorkItemState.WAITING_FOR_INPUT` 检测 + `additional_input` 注入 |

---

## 模块依赖图

```
M1(ParamSchema) ─→ M2(ParamResolver) ─→ M3(ToolAdapter) ─→ M4(AgentLoop) ─→ M6(UnifiedGraph) ─→ M7(Scheduler+SessionAgent) ─→ M8(Cleanup) ─→ M9(SOP审查)
                                              ↑
                                         M5(PromptBuilder) ─┘
```

构建顺序：M1 → M2 → M3 → M5 → M4 → M6 → M7 → M8 → M9（M3 与 M5 无相互依赖，可并行）。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M1 | `tools/event_tool.py` 等 | 修改 | `_EVENT_TOOL_SCHEMA` 加 `fk_target`/`resolvable_from`/`resolver` |
| M2 | `workitem/langgraph_engine/agent/__init__.py`, `agent/resolver.py`, `repositories/project_repo.py` | 新增 | `ParamResolver`, `ProjectResolver`, `ResolverRegistry`, `ProjectRepository` |
| M3 | `workitem/langgraph_engine/agent/tool_adapter.py` | 新增 | `build_tool_specs()` |
| M4 | `workitem/langgraph_engine/agent/loop.py`, `config.py` | 新增+修改 | `agent_node`, `tool_node`, `route_after_agent` |
| M5 | `workitem/langgraph_engine/agent/prompt_builder.py` | 新增 | `build_system_prompt()` |
| M6 | `workitem/langgraph_engine/state.py`, `graph.py`, `nodes.py`, `__init__.py`(_build_pipeline_bus) | 重写+修改 | `AgentLoopState`, `build_workitem_graph`, `make_created/routing/executing/summarizing/error_analysis` |
| M7 | `workitem/scheduler.py`, `session/session_agent.py` | 修改 | `_run_one`, `_run_graph`, `_handle_impl` |
| M8 | `skill/*`, `workitem_agent.py`, `pipeline/interfaces/planning.py`, `infrastructure/tools_consistency.py`, 10 份 Skill YAML | 删除+修改 | 删 `SkillExecutor`/`SkillDefinition`/`ParamExtractor`/`ExecutionPlan`；`SkillRegistry` 降级 |
| M9 | `emily-data/sops/*.md` | 审查 | 10 份 .md 验证/增强 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `tools/event_tool.py` | 修改 | `_EVENT_TOOL_SCHEMA.project_id` 加 `fk_target`/`resolvable_from`/`resolver` |
| `tools/task_tool.py`, `meeting_tool.py`, `file_tool.py` | 修改 | 同上模式（project_id 加 hint） |
| `config.py` | 修改 | 加 `agent_loop_max_iterations: int = 12` |
| `emily_core/__init__.py` | 修改 | `_build_pipeline_bus` 构造 resolvers + 调新 `build_workitem_graph`；`_init_skill_module` 适配 |
| `workitem/langgraph_engine/state.py` | 重写 | `WorkItemGraphState` → `AgentLoopState` |
| `workitem/langgraph_engine/graph.py` | 重写 | 5 节点 → 统一生命周期图（created/routing/executing/summarizing/error_analysis） |
| `workitem/langgraph_engine/nodes.py` | 重写 | `make_node1-4` → `make_created/routing/executing/summarizing/error_analysis` |
| `workitem/scheduler.py` | 修改 | `_run_one` + `_run_graph` 重写（interrupt/resume） |
| `session/session_agent.py` | 修改 | `_handle_impl` 续接逻辑适配 interrupt resume |
| `workitem/workitem_agent.py` | 修改(M8) | 删除 `node1-4`/`_real_execute`/`_llm_plan`/`_execute_skill`/`_extract_structured_result`(迁移到 nodes)/`_llm_synthesize_reply`/`authorize`/`grade_risk` |
| `workitem/injector.py` | 修改(M8) | 删除 planner 角色（保留 SOP 全文加载供 prompt_builder 用）或整体删除 |
| `skill/executor.py`, `definition.py`, `parser.py`, `validator.py`, `param_extractor.py` | 删除(M8) | 整文件删除 |
| `skill/registry.py` | 重写(M8) | 降级为 SOP .md 索引器（保留 `dump_as_text`/`get_by_sop_id` 接口） |
| `pipeline/interfaces/planning.py` | 删除(M8) | `ExecutionPlan`/`PlanStep` 删除（`StepResult` 等在 execution.py，保留） |
| `infrastructure/tools_consistency.py` | 修改(M8) | 删 V10/V11/V12（Skill YAML 检查）；`TOOL_SCHEMA_MAP` 不变 |
| `emily-data/skills/*.skill.yaml` | 删除(M8) | 10 份删除 |

---

## M1: ParamSchema 元数据

**依赖**：无（首建模块）

**职责**：为业务工具的 FK 参数（如 `project_id`）在 JSON Schema 中声明 `fk_target`/`resolvable_from`/`resolver` hint，让 LLM 读 schema 时主动先调 resolver 拿 UUID。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | event_tool schema 加 hint | `emily-core/emily_core/tools/event_tool.py` |
| 2 | task/meeting/file tool 同模式 | `emily-core/emily_core/tools/task_tool.py` 等 |

### 代码

#### `emily-core/emily_core/tools/event_tool.py` — 替换 `_EVENT_TOOL_SCHEMA`（第 19-79 行）

```python
# emily-core/emily_core/tools/event_tool.py
_EVENT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {
            "type": "string",
            "description": "项目名称（如 '翠湖庭院'）。若只知道名称不知 UUID，先调 resolve_project 拿 project_id",
        },
        "project_id": {
            "type": "string",
            "format": "uuid",
            "description": "项目 UUID。若未提供但有 project_name，必须先调 resolve_project 解析",
            "fk_target": "projects.id",
            "resolvable_from": "project_name",
            "resolver": "resolve_project",
        },
        "data": {
            "type": "object",
            "description": "事件详细参数",
            "properties": {
                "title": {"type": "string", "description": "事件简述（10字以内）"},
                "event_type": {
                    "type": "string",
                    "enum": ["construction_progress", "inspection", "material_arrival",
                             "quality_issue", "safety_issue", "weather", "design_change",
                             "decision", "general"],
                    "description": "事件类型（decision=决策事件）",
                },
                "event_date": {"type": "string", "description": "事件日期（YYYY-MM-DD）"},
                "description": {"type": "string", "description": "事件完整描述"},
            },
            "required": ["title", "event_type"],
        },
        "force": {"type": "boolean", "description": "是否强制录入（跳过核验，默认 false）"},
        "guardian_notes": {"type": "string", "description": "守护核验发现的问题（force=true 时填）"},
        "related_event_ids": {
            "type": "array", "items": {"type": "string"},
            "description": "关联事件编号列表（如 ['EVT-20260612-0001']）",
        },
    },
    "required": ["data"],
}
```

#### `task_tool.py` / `meeting_tool.py` / `file_tool.py` — 同模式追加 hint

对每个含 `project_id` 字段的工具 schema，在 `project_id` 属性对象内追加三个 hint 字段（`fk_target`/`resolvable_from`/`resolver`），值同上。若该工具同时有 `project_name` 字段，在 `project_name` 的 description 末尾追加"若只知道名称不知 UUID，先调 resolve_project 拿 project_id"。

**执行动作**：用 Grep 定位每个工具的 `_XXX_SCHEMA` 中 `project_id` 字段，逐个追加 hint。

### 模块验收检测

```bash
# 验收 1：event_tool schema 含 fk_target + resolver
uv run python -c "from emily_core.tools.event_tool import _EVENT_TOOL_SCHEMA as s; assert s['properties']['project_id']['fk_target']=='projects.id'; assert s['properties']['project_id']['resolver']=='resolve_project'; print('M1 event_tool OK')"
→ 预期输出：M1 event_tool OK

# 验收 2：task/meeting/file 同样含 hint（逐个验证）
uv run python -c "from emily_core.tools.task_tool import _TASK_TOOL_SCHEMA as s; assert s['properties']['project_id'].get('resolver')=='resolve_project'; print('M1 task_tool OK')"
→ 预期输出：M1 task_tool OK
# 对 meeting_tool._MEETING_TOOL_SCHEMA / file_tool._FILE_TOOL_SCHEMA 重复
```

**失败处理**：若某工具 schema 无 `project_id` 字段（如纯查询工具），跳过该工具。若 `import` 报错，检查 schema 变量名是否与 `tools_consistency.py:TOOL_SCHEMA_MAP` 一致。

---

## M2: ParamResolver as function-calling tool

**依赖**：M1（resolver hint 字段供 LLM 识别）

**职责**：新建 `ParamResolver` ABC + `ProjectResolver`（项目名→UUID，三层权限模型第二层）+ `ResolverRegistry`；作为 function-calling tool 暴露给 LLM。新增 `ProjectRepository.find_by_name_fuzzy`。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | agent 子包初始化 | `emily-core/emily_core/workitem/langgraph_engine/agent/__init__.py` |
| 2 | Resolver ABC + ProjectResolver + Registry | `emily-core/emily_core/workitem/langgraph_engine/agent/resolver.py` |
| 3 | 项目模糊查询 Repo | `emily-core/emily_core/repositories/project_repo.py` |

### 代码

#### `emily-core/emily_core/repositories/project_repo.py` — 新建

```python
# emily-core/emily_core/repositories/project_repo.py
"""ProjectRepository —— 项目查询（resolver 用）。

参照 event_repo.py:234 的 @staticmethod + get_session() 模式。
"""
from __future__ import annotations

import logging
from typing import Optional

from ..infrastructure.database.models import Project
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.repo.project")


class ProjectRepository:
    """项目查询仓储。"""

    @staticmethod
    def find_by_name_fuzzy(name: str, limit: int = 10) -> list[Project]:
        """按名称模糊匹配项目（ilike），返回候选列表。

        超范围读：此处不做 session 约束过滤，由 Resolver 第二层做输出过滤。
        仅返回未删除项目。
        """
        if not name or not name.strip():
            return []
        with get_session() as session:
            return (
                session.query(Project)
                .filter(Project.is_deleted == False)  # noqa: E712
                .filter(Project.name.ilike(f"%{name.strip()}%"))
                .limit(limit)
                .all()
            )

    @staticmethod
    def get_by_id(project_id: str) -> Optional[Project]:
        """按 UUID 查项目。"""
        if not project_id:
            return None
        with get_session() as session:
            return session.query(Project).filter(Project.id == project_id).first()
```

#### `emily-core/emily_core/workitem/langgraph_engine/agent/__init__.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/agent/__init__.py
"""LangGraph agent loop 子包 —— resolver / tool_adapter / loop / prompt_builder。"""
```

#### `emily-core/emily_core/workitem/langgraph_engine/agent/resolver.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/agent/resolver.py
"""ParamResolver —— 参数解析器，作为 function-calling tool 暴露给 LLM。

三层权限模型第二层：超 session 读（查全表）+ session 约束输出过滤。
不泄漏不可见资源的存在性（accessible 外项目返回 found=False，不返回候选）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ....repositories.project_repo import ProjectRepository

logger = logging.getLogger("emily.langgraph.resolver")


class ParamResolver(ABC):
    """参数解析器 ABC —— 作为 function-calling tool 暴露给 LLM。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名（如 'resolve_project'）。"""

    @property
    @abstractmethod
    def spec(self) -> dict:
        """OpenAI tool spec（{"type":"function","function":{...}}）。"""

    @abstractmethod
    async def handle(self, params: dict, session_ctx: Any) -> dict:
        """执行解析。返回 dict（含 found/project_id/candidates/error）。"""


class ProjectResolver(ParamResolver):
    """项目名 → UUID 解析器。三层权限模型第二层。

    ① 超范围读：ProjectRepository.find_by_name_fuzzy 查全表
    ② session 约束：只在 session_ctx.project_ids 集合内解析
    ③ 输出过滤：accessible 外项目不泄漏存在性
    """

    @property
    def name(self) -> str:
        return "resolve_project"

    @property
    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "resolve_project",
                "description": (
                    "项目名称 → UUID 解析。当工具参数需要 project_id（UUID）但你只有项目名时，"
                    "先调本工具拿 project_id。返回 found=true 时含 project_id；"
                    "返回 candidates 时表示有多个匹配，需向用户确认；found=false 表示未找到。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "项目名称（人类可读，如 '翠湖庭院'）",
                        }
                    },
                    "required": ["project_name"],
                },
            },
        }

    async def handle(self, params: dict, session_ctx: Any) -> dict:
        import asyncio

        value = (params.get("project_name") or "").strip()
        if not value:
            return {"found": False, "error": "未提供项目名称"}

        # ① 超范围读：模糊查全表
        matches = await asyncio.to_thread(ProjectRepository.find_by_name_fuzzy, value)
        if not matches:
            return {"found": False, "error": f"未找到项目'{value}'"}

        # ② session 约束：只在用户可访问项目集合内解析
        accessible = set(getattr(session_ctx, "project_ids", []) or []) if session_ctx else set()
        if accessible:
            in_scope = [m for m in matches if m.id in accessible]
        else:
            # session_ctx 无 project_ids（私聊超管等）→ 放行全部匹配
            in_scope = matches

        # ③ 输出过滤：accessible 外不泄漏
        if not in_scope:
            return {"found": False, "error": f"未找到项目'{value}'"}  # 不泄漏存在性

        if len(in_scope) > 1:
            return {
                "found": False,
                "candidates": [{"id": m.id, "name": m.name} for m in in_scope],
                "error": f"找到 {len(in_scope)} 个匹配项目，请确认具体是哪一个",
            }

        return {"found": True, "project_id": in_scope[0].id, "project_name": in_scope[0].name}


@dataclass
class ResolverRegistry:
    """Resolver 注册表。"""
    _resolvers: dict[str, ParamResolver] = field(default_factory=dict)

    def register(self, resolver: ParamResolver) -> None:
        self._resolvers[resolver.name] = resolver

    def list_all(self) -> list[ParamResolver]:
        return list(self._resolvers.values())

    def get(self, name: str) -> ParamResolver | None:
        return self._resolvers.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._resolvers


def build_default_resolvers() -> ResolverRegistry:
    """构建默认 resolver 集合（EmilyCore 启动时调用）。"""
    reg = ResolverRegistry()
    reg.register(ProjectResolver())
    return reg
```

### 模块验收检测

```bash
# 验收 1：ProjectResolver 有合法 OpenAI tool spec
uv run python -c "from emily_core.workitem.langgraph_engine.agent.resolver import ProjectResolver; r=ProjectResolver(); assert r.spec()['type']=='function'; assert r.spec()['function']['name']=='resolve_project'; print('M2 spec OK')"
→ 预期输出：M2 spec OK

# 验收 2：不泄漏不可见项目（accessible 外返回 found=False）
uv run python -c "
import asyncio
from emily_core.workitem.langgraph_engine.agent.resolver import ProjectResolver
class FakeCtx:
    project_ids=[]  # 空 accessible
r=ProjectResolver()
# 用 monkey patch 模拟 matches
from emily_core.repositories import project_repo
class FakeProject:
    def __init__(self,id,name): self.id=id; self.name=name
project_repo.ProjectRepository.find_by_name_fuzzy=lambda name,limit=10:[FakeProject('uuid-1','翠湖庭院')]
ctx=FakeCtx(); ctx.project_ids=['other-uuid']
result=asyncio.run(r.handle({'project_name':'翠湖'},ctx))
assert result['found']==False, f'expected found=False, got {result}'
print('M2 permission OK')
"
→ 预期输出：M2 permission OK

# 验收 3：ProjectRepository.find_by_name_fuzzy 存在且返回 list
uv run python -c "from emily_core.repositories.project_repo import ProjectRepository; import inspect; assert inspect.ismethod(ProjectRepository.find_by_name_fuzzy); print('M2 repo OK')"
→ 预期输出：M2 repo OK
```

**失败处理**：若 `find_by_name_fuzzy` 报 `is_deleted` 列不存在，检查 `models.py:193` 的 `Project.is_deleted` 字段（已确认存在）。若 import 循环，确认 `agent/__init__.py` 已创建。

---

## M3: Function-calling 工具适配

**依赖**：M1（schema）、M2（resolver）

**职责**：`build_tool_specs(business_tools, resolvers, session_api_ids)` 将 BusinessFlowTool + Resolver 转为 OpenAI tool spec 列表，按 session 权限过滤（fail-closed）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | tool spec 构建器 | `emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py` |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py
"""Function-calling 工具适配 —— BusinessFlowTool + Resolver → OpenAI tool spec。

按 session_api_ids 过滤（fail-closed：用户无权限的工具不暴露）。
参照 registry.py:116 的 _tool() 桥接模式 + workitem_agent.py:547 的权限过滤。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...tools.business_flow_tools import BusinessFlowToolRegistry
    from .resolver import ResolverRegistry

logger = logging.getLogger("emily.langgraph.tool_adapter")


def build_tool_specs(
    business_tools: "BusinessFlowToolRegistry",
    resolvers: "ResolverRegistry",
    session_api_ids: set[str],
) -> list[dict]:
    """构建 LLM 可见的 tool spec 列表，按 session 权限过滤。

    Args:
        business_tools: BusinessFlowToolRegistry 实例
        resolvers: ResolverRegistry 实例
        session_api_ids: 用户可见工具 api_id 集合（来自 SessionContext.available_tools）

    Returns:
        list[dict]: OpenAI tool spec 列表
    """
    specs: list[dict] = []

    if not session_api_ids:
        logger.warning("build_tool_specs: session_api_ids 为空，tool_registry 表可能未填充，fail-closed")
        # fail-closed：仅暴露 resolver（resolver 内部做权限约束），不暴露任何业务工具
    else:
        for name in business_tools.list_names():
            if name not in session_api_ids:
                continue  # fail-closed：用户无权限的工具不暴露
            tool = business_tools.get(name)
            if tool is None:
                continue
            specs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            })

    # resolver 始终可见（其内部做权限约束，第二层）
    for r in resolvers.list_all():
        specs.append(r.spec)

    logger.info("build_tool_specs: %d business tools + %d resolvers = %d specs",
                len(specs) - len(resolvers), len(resolvers), len(specs))
    return specs
```

### 模块验收检测

```bash
# 验收 1：session_api_ids 外的 tool 不出现在 specs
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.tool_adapter import build_tool_specs
from emily_core.workitem.langgraph_engine.agent.resolver import build_default_resolvers
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool
async def h(params): return {'success': True}
reg=BusinessFlowToolRegistry()
reg.register(BusinessFlowTool(name='record_event',description='d',parameters={'type':'object','properties':{}},handler=h))
reg.register(BusinessFlowTool(name='query_data',description='d',parameters={'type':'object','properties':{}},handler=h))
res=build_default_resolvers()
# 只授权 query_data
specs=build_tool_specs(reg,res,{'query_data'})
names=[s['function']['name'] for s in specs]
assert 'record_event' not in names, f'record_event should be filtered, got {names}'
assert 'query_data' in names
assert 'resolve_project' in names  # resolver 始终可见
print('M3 filter OK')
"
→ 预期输出：M3 filter OK

# 验收 2：session_api_ids 为空时 fail-closed（仅 resolver）
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.tool_adapter import build_tool_specs
from emily_core.workitem.langgraph_engine.agent.resolver import build_default_resolvers
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool
async def h(params): return {'success': True}
reg=BusinessFlowToolRegistry()
reg.register(BusinessFlowTool(name='record_event',description='d',parameters={'type':'object','properties':{}},handler=h))
specs=build_tool_specs(reg,build_default_resolvers(),set())
names=[s['function']['name'] for s in specs]
assert 'record_event' not in names
assert 'resolve_project' in names
print('M3 fail-closed OK')
"
→ 预期输出：M3 fail-closed OK
```

**失败处理**：若 `BusinessFlowTool` 构造报错，检查 `business_flow_tools.py:21` 的 dataclass 字段（name/description/parameters/handler 必填）。

---

## M5: SOP .md 指导注入（先于 M4，无依赖）

**依赖**：无

**职责**：`build_system_prompt(sop_text, tool_specs, session_ctx, message_history)` 构建 agent loop 的 system prompt = SOP .md 全文 + 可见工具 schema 摘要 + session 上下文。替代旧 `_llm_plan` 的 planner.md 注入。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | system prompt 构建器 | `emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py` |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py
"""Agent loop system prompt 构建器。

system prompt = 角色 + SOP .md 全文（指导）+ 可见工具表 + session 上下文 + 行为规则。
指导不控制：SOP .md 告诉 LLM 该做什么，不强制 exact steps（Anthropic harness 理念）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("emily.langgraph.prompt_builder")


def build_system_prompt(
    sop_text: str,
    tool_specs: list[dict],
    session_ctx: Any,
    user_input: str,
    result_constraints: dict | None = None,
    additional_input: str = "",
) -> str:
    """构建 agent loop system prompt。

    Args:
        sop_text: 匹配到的 SOP .md 全文（由 routing 节点加载）
        tool_specs: LLM 可见 tool spec 列表（build_tool_specs 产出）
        session_ctx: SessionContext（取 user_name/project_name 等上下文）
        user_input: 用户原始输入
        result_constraints: SessionAgent 解析的结果约束（scope/must_include/must_not）
        additional_input: 续接时用户上一轮补充的信息

    Returns:
        str: 完整 system prompt
    """
    # ── 工具表（从 tool_specs 提取 name + description + resolver hint）──
    tool_lines: list[str] = []
    for spec in tool_specs:
        fn = spec.get("function", {})
        name = fn.get("name", "")
        desc = (fn.get("description") or "").split("\n")[0][:120]
        tool_lines.append(f"- {name}: {desc}")
    tools_text = "\n".join(tool_lines) if tool_lines else "（无可用工具）"

    # ── session 上下文 ──
    ctx_text = ""
    if session_ctx is not None:
        ctx_text = (
            f"当前用户：{getattr(session_ctx,'user_name','') or '未知'}"
            f"（L{getattr(session_ctx,'level',0)}）\n"
            f"当前项目：{getattr(session_ctx,'project_name','') or '未指定'}\n"
            f"可访问项目数：{len(getattr(session_ctx,'project_ids',[]) or [])}"
        )

    # ── 结果约束 ──
    rc_text = ""
    if result_constraints:
        rc_text = f"\n\n【结果约束】\n{json.dumps(result_constraints, ensure_ascii=False)}"

    # ── 续接上下文 ──
    cont_text = ""
    if additional_input:
        cont_text = (
            f"\n\n【续接上下文】\n用户上一轮补充：{additional_input}\n"
            f"请基于新信息继续，跳过已收集的字段。"
        )

    prompt = f"""你是 Emily，企业公共大脑 Agent。你通过调用工具完成用户的企业工作流请求。

# 你的工作方式（agent loop）
1. 阅读下方 SOP 指导，理解该业务流的目标与字段要求
2. 查看可用工具表，选择合适工具
3. 若工具参数需要 UUID（如 project_id）但你只有名称，**必须先调 resolve_project 解析**，再填入业务工具
4. 调用工具后，查看 tool_result：成功则继续或回复用户；失败则根据错误自行调整重试
5. 完成后给出最终文本回复（不要调用工具，直接回复用户）
6. 若信息不足无法继续，调用 ask_user 工具向用户提问

# 业务流指导（SOP）
{sop_text or '（未匹配到 SOP，按通用方式处理）'}

# 可用工具
{tools_text}

# 会话上下文
{ctx_text}

# 行为规则
- 工具参数中的 UUID 字段（schema 标了 resolver hint 的）必须先调 resolver 解析，禁止把名称塞进 UUID 字段
- 看到 tool_result 报错时，分析原因并调整参数重试，不要原样重试
- 最终回复用中文，简洁明确，包含关键编号（如事件编号 EVT-xxx）
{rc_text}{cont_text}

# 用户请求
{user_input}
"""
    return prompt
```

### 模块验收检测

```bash
# 验收 1：prompt 含 SOP 全文 + 工具表 + resolve_project
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.prompt_builder import build_system_prompt
class FakeCtx:
    user_name='王建国'; level=3; project_name='翠湖庭院'; project_ids=['uuid-1']
prompt=build_system_prompt(
    sop_text='SOP-002 事件记录：title 必有，event_type 应有',
    tool_specs=[{'type':'function','function':{'name':'resolve_project','description':'项目名转UUID'}},
                {'type':'function','function':{'name':'record_event','description':'记录事件'}}],
    session_ctx=FakeCtx(), user_input='记一下放线完成',
    result_constraints={'must_include':['事件编号']})
assert 'SOP-002' in prompt
assert 'resolve_project' in prompt
assert '事件编号' in prompt
assert '王建国' in prompt
print('M5 prompt OK, len=',len(prompt))
"
→ 预期输出：M5 prompt OK, len= <数字>
```

**失败处理**：若 session_ctx 字段访问报错，确认 `session_context.py` 的字段名（user_name/level/project_name/project_ids 已确认存在）。

---

## M4: Agent loop

**依赖**：M3（tool_adapter）、M5（prompt_builder）

**职责**：`agent_node`（调 `chat_with_tools`）↔ `tool_node`（调 handler + 追加 StepResult）；`route_after_agent`（tool_call→tool_node / text→done / interrupt→waiting / cap→error_analysis）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | agent loop 核心 | `emily-core/emily_core/workitem/langgraph_engine/agent/loop.py` |
| 2 | config 加 iteration cap | `emily-core/emily_core/config.py` |

### 代码

#### `emily-core/emily_core/config.py` — 在 `langgraph_max_retry` 字段后追加（第 185 行后）

```python
    # ── Agent loop（L3）──
    agent_loop_max_iterations: int = 12
    """Agent loop 最大迭代次数（agent_node↔tool_node 循环上限，防 runaway）。
    超限升级外层 error_analysis 兜底。"""
```

#### `emily-core/emily_core/workitem/langgraph_engine/agent/loop.py` — 新建

```python
# emily-core/emily_core/workitem/langgraph_engine/agent/loop.py
"""Agent loop —— agent_node ↔ tool_node ReAct 循环。

agent_node: 调 chat_with_tools(messages, tools)，按返回 type 路由
tool_node:  执行 tool_call handler，追加 StepResult + tool_result message
route_after_agent: tool_call→tool_node / text→done / interrupt→waiting / cap→error_analysis

状态即对话历史：messages list 累积 system+user+assistant(tool_call)+tool_result。
"""
from __future__ import annotations

import json
import logging
import time as _time
from typing import Any

from ....infrastructure.logging.llm_logger import LLMInteractionLogger
from ...pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
from .tool_adapter import build_tool_specs
from .prompt_builder import build_system_prompt

logger = logging.getLogger("emily.langgraph.loop")


def _get_ctx():
    from ..state import get_bus_context
    return get_bus_context()


def _session_api_ids(ctx) -> set[str]:
    """从 SessionContext.available_tools 提取 api_id 集合。参照 workitem_agent.py:547。"""
    session_ctx = ctx.get_session_context() if ctx else None
    ids: set[str] = set()
    if session_ctx:
        for t in getattr(session_ctx, "available_tools", []) or []:
            api_id = t.get("api_id") if isinstance(t, dict) else None
            if api_id:
                ids.add(api_id)
    return ids


def _inject_runtime_params(tool_params: dict, ctx) -> dict:
    """注入运行时上下文到 tool_params。参照 workitem_agent.py:567。"""
    p = dict(tool_params or {})
    p["_user_id"] = ctx.user_id or ""
    p["_message_id"] = ctx.db_message_id or ""
    p["_conversation_id"] = ctx.message.conversation_id if ctx.message else ""
    if ctx.message is not None:
        raw = getattr(ctx.message, "attachments", None) or []
        if raw:
            p["_attachments"] = raw
            first = raw[0] if isinstance(raw[0], dict) else {}
            p["_attachment_url"] = first.get("url", "")
            p["_attachment_type"] = first.get("type", 0)
    return p


async def agent_node(state: dict, *, llm_client, business_tools, resolvers, sop_text, config) -> dict:
    """agent_node —— 调 chat_with_tools，返回增量 messages。

    首次进入时构建 system prompt 并初始化 messages。
    """
    ctx = _get_ctx()
    wi = ctx.work_item
    messages = list(state.get("messages", []))

    # ── 首次进入：构建 system prompt + 初始 messages ──
    if not messages:
        session_ctx = ctx.get_session_context()
        session_api_ids = _session_api_ids(ctx)
        tool_specs = build_tool_specs(business_tools, resolvers, session_api_ids)
        # 暂存 tool_specs 到 state 供后续轮复用（避免每轮重建）
        state["_tool_specs"] = tool_specs
        system_prompt = build_system_prompt(
            sop_text=sop_text,
            tool_specs=tool_specs,
            session_ctx=session_ctx,
            user_input=wi.user_input,
            result_constraints=getattr(wi, "result_constraints", {}) or {},
            additional_input=getattr(wi, "additional_input", "") or "",
        )
        messages = [{"role": "system", "content": system_prompt}]
        # 追加 session 消息历史（多轮上下文）
        if session_ctx is not None:
            messages.extend(getattr(session_ctx, "message_history", []) or [])
        messages.append({"role": "user", "content": wi.user_input})
    else:
        # 后续轮：tool_result 已由 tool_node 追加，直接调 LLM
        pass

    tool_specs = state.get("_tool_specs") or []

    # ── iteration cap 检查 ──
    iteration_count = state.get("iteration_count", 0)
    max_iter = getattr(config, "agent_loop_max_iterations", 12)
    if iteration_count >= max_iter:
        logger.warning("agent_node: iteration_count=%d >= cap=%d, escalate to error_analysis",
                       iteration_count, max_iter)
        state["error_analysis"] = {"should_abort": False, "should_escalate": True,
                                   "root_cause": f"agent loop 达到 iteration cap ({max_iter})"}
        return {"wi_state": "error_analysis", "iteration_count": iteration_count}

    # ── 调 LLM ──
    LLMInteractionLogger.set_context(
        pipeline_run_id=ctx.pipeline_run_id,
        conversation_id=ctx.message.conversation_id if ctx.message else "",
        user_id=ctx.user_id,
        call_category="agent_loop",
    )
    try:
        # 用 router_model（fast）做 agent loop 调用（PRD 决策 #12）
        # chat_with_tools 不接受 model=，故直接调 chat_messages（支持 model= + tools=）
        model = getattr(llm_client, "router_model", None) or llm_client.model
        result = await llm_client.chat_messages(messages, tools=tool_specs, model=model)
    except Exception as e:
        logger.error("agent_node LLM failed: %s", e, exc_info=True)
        state["error_analysis"] = {"should_abort": False, "should_escalate": True,
                                   "root_cause": f"LLM 调用异常: {e}"}
        return {"wi_state": "error_analysis"}
    finally:
        LLMInteractionLogger.clear_context()

    rtype = result.get("type", "")

    if rtype == "tool_call":
        # 追加 assistant tool_call message（OpenAI 格式）
        messages.append({
            "role": "assistant",
            "content": result.get("reasoning_content") or "",
            "tool_calls": [{
                "id": result.get("tool_call_id", ""),
                "type": "function",
                "function": {
                    "name": result.get("tool_name", ""),
                    "arguments": json.dumps(result.get("tool_arguments", {}),
                                            ensure_ascii=False),
                },
            }],
        })
        # 暂存当前 tool_call 供 tool_node 取
        state["_pending_tool_call"] = {
            "id": result.get("tool_call_id", ""),
            "name": result.get("tool_name", ""),
            "arguments": result.get("tool_arguments", {}),
        }
        wi.llm_call_count += 1
        return {"messages": messages, "wi_state": "executing",
                "iteration_count": iteration_count + 1}

    # type == "text" 或其他 → LLM 给最终回复
    content = result.get("content", "")
    # 检查是否是 ask_user（LLM 请求输入）—— 约定 LLM 回复含 [[ASK_USER]] 标记或调 ask_user 工具
    # 这里用简单约定：若 LLM 显式调用名为 ask_user 的工具，route 到 waiting
    # 由于 chat_with_tools 只返回首个 tool_call，ask_user 会在 tool_call 分支处理
    messages.append({"role": "assistant", "content": content})
    wi.llm_call_count += 1
    # 最终回复暂存到 baggage，summarizing 节点用
    ctx.set("agent_final_reply", content)
    return {"messages": messages, "wi_state": "summarizing",
            "iteration_count": iteration_count + 1}


async def tool_node(state: dict, *, llm_client, business_tools, resolvers) -> dict:
    """tool_node —— 执行 pending tool_call，追加 tool_result message + StepResult。

    支持 ask_user 工具 → 触发 interrupt（WAITING_FOR_INPUT）。
    """
    from langgraph.types import interrupt

    ctx = _get_ctx()
    wi = ctx.work_item
    tc = state.get("_pending_tool_call") or {}
    tool_name = tc.get("name", "")
    arguments = tc.get("arguments", {}) or {}
    tool_call_id = tc.get("id", "")

    messages = list(state.get("messages", []))

    # ── ask_user 工具 → interrupt ──
    if tool_name == "ask_user":
        question = arguments.get("question", "请补充信息")
        state["waiting_question"] = question
        # interrupt 挂起，用户续接时 Command(resume=...) 返回值作为 tool_result
        user_reply = interrupt(question)
        # resume 后 user_reply 是用户补充输入
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"用户回复：{user_reply}",
        })
        # 把用户回复追加为 user message，供 LLM 下一轮消费
        messages.append({"role": "user", "content": str(user_reply)})
        return {"messages": messages, "wi_state": "executing"}

    # ── resolver 工具 ──
    resolver = resolvers.get(tool_name)
    if resolver is not None:
        session_ctx = ctx.get_session_context()
        try:
            rresult = await resolver.handle(arguments, session_ctx)
        except Exception as e:
            logger.error("resolver %s failed: %s", tool_name, e, exc_info=True)
            rresult = {"found": False, "error": f"resolver 异常: {e}"}
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(rresult, ensure_ascii=False),
        })
        return {"messages": messages, "wi_state": "executing"}

    # ── 业务工具 ──
    t_start = _time.monotonic()
    tool = business_tools.get(tool_name) if tool_name in business_tools else None
    if tool is None:
        err_msg = f"工具 '{tool_name}' 未注册"
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": err_msg})
        _append_step_result(wi, tool_name, arguments, {"success": False, "reply": err_msg},
                            t_start, success=False)
        return {"messages": messages, "wi_state": "executing"}

    # 权限检查（fail-closed，参照 workitem_agent.py:590）
    session_api_ids = _session_api_ids(ctx)
    if not session_api_ids or tool_name not in session_api_ids:
        err_msg = "该操作无法执行，您可能没有相应权限。"
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": err_msg})
        _append_step_result(wi, tool_name, arguments, {"success": False, "reply": err_msg},
                            t_start, success=False)
        return {"messages": messages, "wi_state": "executing"}

    # 注入运行时上下文
    tool_params = _inject_runtime_params(arguments, ctx)

    try:
        import inspect
        sig = inspect.signature(tool.handler)
        handler_kwargs = {"params": tool_params}
        if "user_id" in sig.parameters:
            handler_kwargs["user_id"] = ctx.user_id
        if "message_id" in sig.parameters:
            handler_kwargs["message_id"] = ctx.db_message_id
        handler_result = await tool.handler(**handler_kwargs)
    except Exception as e:
        logger.error("tool_node %s failed: %s", tool_name, e, exc_info=True)
        handler_result = {"success": False, "reply": f"工具执行异常: {e}"}

    handler_dict = handler_result if isinstance(handler_result, dict) else {}
    _append_step_result(wi, tool_name, tool_params, handler_dict, t_start,
                        success=handler_dict.get("success", True))

    messages.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(handler_dict, ensure_ascii=False, default=str),
    })
    return {"messages": messages, "wi_state": "executing"}


def _append_step_result(wi, tool_name, tool_params, handler_dict, t_start, success=True):
    """构建 StepResult 追加到 wi.step_results。参照 workitem_agent.py:629。

    保留 step_results 供 summarizing 节点提取 StructuredResult + ArchiveHook 归档兼容。
    """
    elapsed_ms = int((_time.monotonic() - t_start) * 1000)
    tool_call = ToolCallRecord(
        tool_name=tool_name,
        tool_input=tool_params,
        tool_output=handler_dict,
        success=success,
        elapsed_ms=elapsed_ms,
    )
    db_results = []
    object_id = handler_dict.get("object_id", "") or ""
    if object_id:
        db_results.append(DbResult(
            operation="insert",
            table=tool_name.replace("record_", "") + "s",
            affected_rows=1,
            result_data=handler_dict,
        ))
    sr = StepResult(
        step_id=f"iter-{len(wi.step_results) + 1}",
        success=success,
        output=str(handler_dict.get("reply", "")),
        tool_calls=[tool_call],
        db_results=db_results,
        business_data=handler_dict,
    )
    wi.add_step_result(sr)


def route_after_agent(state: dict) -> str:
    """agent_node 之后的条件边路由。

    - wi_state == 'summarizing' → summarizing
    - wi_state == 'error_analysis' → error_analysis
    - 有 pending_tool_call → tool_node
    - 否则 → summarizing（兜底）
    """
    wi_state = state.get("wi_state", "")
    if wi_state == "summarizing":
        return "summarizing"
    if wi_state == "error_analysis":
        return "error_analysis"
    if state.get("_pending_tool_call"):
        return "tool_node"
    return "summarizing"


def route_after_tool(state: dict) -> str:
    """tool_node 之后 → 回 agent_node。"""
    # tool_node 执行后清 pending，回 agent_node 继续循环
    state["_pending_tool_call"] = None
    return "agent_node"
```

### 模块验收检测

```bash
# 验收 1：agent loop 迭代正常（mock LLM 返回 tool_call → tool 执行 → text → 结束）
uv run python -c "
import asyncio
from emily_core.workitem.langgraph_engine.agent import loop

class FakeCtx:
    class WI:
        user_input='测试'; additional_input=''; result_constraints={}; llm_call_count=0
        step_results=[]
        def add_step_result(self,sr): self.step_results.append(sr)
    work_item=WI()
    pipeline_run_id='test'; message=None; user_id='u'; db_message_id='m'
    def get_session_context(self): return None
    def set(self,k,v): pass
    def get(self,k,d=None): return d

# mock chat_messages: 第一次返回 tool_call, 第二次返回 text
class FakeLLM:
    router_model='fast'; model='main'
    calls=0
    async def chat_messages(self, messages, *, json_mode=False, tools=None, temperature=None, max_tokens=None, model=None):
        self.calls+=1
        if self.calls==1:
            return {'type':'tool_call','tool_name':'query_data','tool_arguments':{'q':'x'},'tool_call_id':'tc1','reasoning_content':''}
        return {'type':'text','content':'完成了'}

# 注入 fake ctx
from emily_core.workitem.langgraph_engine import state as st
st._bus_context.set(FakeCtx())

# mock business_tools
class FakeTool:
    handler=None; description='d'; parameters={}
async def h(params,**kw): return {'success':True,'reply':'ok'}
class FakeBT:
    def __contains__(self,n): return n=='query_data'
    def get(self,n):
        t=FakeTool(); t.handler=h; return t
    def list_names(self): return ['query_data']
from emily_core.workitem.langgraph_engine.agent.resolver import ResolverRegistry
res=ResolverRegistry()

llm=FakeLLM()
state={'messages':[],'iteration_count':0}
r1=asyncio.run(loop.agent_node(state,llm_client=llm,business_tools=FakeBT(),resolvers=res,sop_text='sop',config=type('C',(),{'agent_loop_max_iterations':12})()))
assert state.get('_pending_tool_call') is not None
assert loop.route_after_agent(state)=='tool_node'
# tool_node
r2=asyncio.run(loop.tool_node(state,llm_client=llm,business_tools=FakeBT(),resolvers=res))
assert loop.route_after_tool(state)=='agent_node'
# 第二轮 agent_node → text → summarizing
r3=asyncio.run(loop.agent_node(state,llm_client=llm,business_tools=FakeBT(),resolvers=res,sop_text='sop',config=type('C',(),{'agent_loop_max_iterations':12})()))
assert state.get('wi_state')=='summarizing', f'expected summarizing, got {state.get(\"wi_state\")}'
print('M4 loop OK, iterations=',state['iteration_count'])
"
→ 预期输出：M4 loop OK, iterations= 2

# 验收 2：iteration cap 触发兜底
uv run python -c "
import asyncio
from emily_core.workitem.langgraph_engine.agent import loop
# 把 iteration_count 设为 cap，agent_node 应路由到 error_analysis
state={'messages':[{'role':'user','content':'x'}],'iteration_count':12,'_tool_specs':[]}
# 不实际调 LLM（cap 优先）
class FakeCtx:
    work_item=type('W',(),{'llm_call_count':0,'user_input':'x','additional_input':'','result_constraints':{}})()
    pipeline_run_id='t'; message=None; user_id='u'; db_message_id='m'
    def get_session_context(self): return None
    def set(self,k,v): pass
from emily_core.workitem.langgraph_engine import state as st
st._bus_context.set(FakeCtx())
r=asyncio.run(loop.agent_node(state,llm_client=None,business_tools=None,resolvers=None,sop_text='',config=type('C',(),{'agent_loop_max_iterations':12})()))
assert state.get('wi_state')=='error_analysis'
assert state.get('error_analysis',{}).get('should_escalate')==True
print('M4 cap OK')
"
→ 预期输出：M4 cap OK
```

**失败处理**：
- 若 `interrupt` import 失败，确认 langgraph 版本（`docker exec emily-core python -c "from langgraph.types import interrupt"`）。若不支持，M6 改用 checkpoint + 条件边回退（见 M6 失败处理）。
- 若 `chat_messages` 报 `model` 不被支持：reasoner/v4-pro 模型不支持 `temperature`，`chat_messages` 内部已处理（client.py:127）。若 router_model 名错误，回退 `llm_client.model`。
- 若 mock 测试报 `_pending_tool_call` 未清，检查 `route_after_tool` 是否重置。

---

## M6: 统一生命周期图

**依赖**：M4（loop）、M5（prompt_builder）

**职责**：重写 `state.py`（AgentLoopState）+ `graph.py`（统一生命周期图 created→routing→executing(agent loop)→summarizing→done/failed + interrupt + error_analysis 兜底）+ `nodes.py`（新节点工厂）。修改 `EmilyCore._build_pipeline_bus` 调新图。**大爆炸**：删除旧 `make_node1/2/3/4`。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 新 State | `emily-core/emily_core/workitem/langgraph_engine/state.py` |
| 2 | 新 Graph | `emily-core/emily_core/workitem/langgraph_engine/graph.py` |
| 3 | 新 Nodes | `emily-core/emily_core/workitem/langgraph_engine/nodes.py` |
| 4 | EmilyCore 接线 | `emily-core/emily_core/__init__.py`（`_build_pipeline_bus`） |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/state.py` — 整文件替换

```python
# emily-core/emily_core/workitem/langgraph_engine/state.py
"""AgentLoopState —— 统一生命周期图 State，纯可序列化字段。

设计：State 仅含基础类型（str/int/dict/list），100% msgpack 可序列化 → MemorySaver 可用。
BusContext 通过 contextvars 传递（沿用旧设计）。
状态即对话历史：messages list 是 agent loop 唯一状态。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TypedDict

_bus_context: ContextVar = ContextVar("langgraph_bus_context", default=None)


def set_bus_context(ctx) -> None:
    _bus_context.set(ctx)


def get_bus_context():
    ctx = _bus_context.get()
    if ctx is None:
        raise RuntimeError("BusContext not set — call set_bus_context() before ainvoke")
    return ctx


def clear_bus_context() -> None:
    _bus_context.set(None)


class AgentLoopState(TypedDict, total=False):
    """统一生命周期图 State。

    messages 是 agent loop 唯一状态（system+user+assistant(tool_call)+tool_result）。
    """
    # ── 生命周期状态 ──
    wi_state: str               # created/routing/executing/waiting_for_input/summarizing/done/failed/error_analysis
    # ── agent loop 核心 ──
    messages: list              # 对话历史（OpenAI 格式）
    current_sop_id: str         # routing 匹配到的 SOP
    iteration_count: int        # agent loop 迭代次数
    _tool_specs: list           # 缓存 tool spec（避免每轮重建）
    _pending_tool_call: dict    # 当前待执行的 tool_call
    # ── WAITING_FOR_INPUT ──
    waiting_question: str       # interrupt 时的问题
    # ── 兜底 ──
    error_analysis: dict        # iteration cap / LLM 异常时的兜底分析
    # ── 元数据 ──
    node_timings: dict
    pipeline_run_id: str
    current_stage: str
    _max_iterations: int


def make_initial_state(*, pipeline_run_id: str, max_iterations: int = 12) -> dict:
    """构建 graph 初始 state。"""
    return {
        "wi_state": "created",
        "messages": [],
        "current_sop_id": "",
        "iteration_count": 0,
        "_tool_specs": [],
        "_pending_tool_call": None,
        "waiting_question": "",
        "error_analysis": {},
        "node_timings": {},
        "pipeline_run_id": pipeline_run_id,
        "current_stage": "",
        "_max_iterations": max_iterations,
    }
```

#### `emily-core/emily_core/workitem/langgraph_engine/nodes.py` — 整文件替换

```python
# emily-core/emily_core/workitem/langgraph_engine/nodes.py
"""统一生命周期图节点工厂。

节点：created / routing / executing(agent loop) / summarizing / error_analysis
executing 内嵌 agent loop（agent_node ↔ tool_node，在 loop.py 实现，graph.py 接线）。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("emily.langgraph.nodes")


def _get_context():
    from .state import get_bus_context
    return get_bus_context()


def _enter_stage(state: dict, stage_name: str) -> float:
    ctx = _get_context()
    ctx.current_stage = stage_name
    try:
        from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
        LLMInteractionLogger.set_stage(stage_name)
    except Exception:
        pass
    state["current_stage"] = stage_name
    return time.monotonic()


def _exit_stage(state: dict, stage_name: str, t_start: float) -> dict:
    ctx = _get_context()
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    timings = dict(state.get("node_timings", {}))
    timings[stage_name] = elapsed_ms
    return {"node_timings": timings}


def _load_sop_text(sop_id: str) -> str:
    """加载 SOP .md 全文。参照 injector.py:_load_sop_text。"""
    if not sop_id:
        return ""
    try:
        from emily_core.infrastructure.llm.prompt_loader import load_prompt
        # SOP .md 在 sops/ 目录，prompt_loader 按名加载
        # sop_id 形如 SOP-002-REC-event_record，文件名 SOP-002-REC-event_record.md
        return load_prompt(sop_id) or ""
    except Exception:
        pass
    # 回退：直接读 sops 目录
    try:
        from emily_core.infrastructure.paths import resolve_data_path
        from pathlib import Path
        sop_dir = resolve_data_path("", "/app/sops", "emily-data/sops")
        # 尝试精确文件名匹配
        for p in Path(sop_dir).glob(f"{sop_id}*.md"):
            return p.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("load SOP %s failed: %s", sop_id, e)
    return ""


def _extract_structured_result(wi, ctx) -> "StructuredResult":
    """从 step_results 提取 StructuredResult（从 workitem_agent.py:730 迁移，零改动）。

    M8 会从 WorkItemAgent 删除本方法，此处为唯一存留副本。
    """
    from ...pipeline.interfaces.execution import StructuredResult
    spec = getattr(wi, "output_spec", {}) or {}
    step_results = getattr(wi, "step_results", []) or []

    failed_steps = [sr for sr in step_results if not getattr(sr, "success", True)]
    if not step_results:
        status = "failed"
    elif failed_steps and len(failed_steps) == len(step_results):
        status = "failed"
    elif failed_steps:
        status = "partial"
    else:
        status = "success"

    data = {}
    for sr in step_results:
        bd = getattr(sr, "business_data", {}) or {}
        for field in spec.get("data_fields", []):
            if field in bd and field not in data:
                data[field] = bd[field]

    summary_facts = []
    for sr in step_results:
        output = (getattr(sr, "output", "") or "").strip()
        if output and len(summary_facts) < 8:
            summary_facts.append(output[:200])

    rag_sources = []
    for sr in step_results:
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in getattr(rr, "chunks", []) or []:
                doc = getattr(chunk, "doc_name", "") or ""
                if doc and doc not in rag_sources:
                    rag_sources.append(doc)
    for sr in step_results:
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in getattr(rr, "chunks", []) or []:
                content = (getattr(chunk, "content", "") or "")[:500]
                if content:
                    summary_facts.append(f"〔{getattr(chunk, 'doc_name', '?')}〕{content}")

    business_object_no = ""
    for sr in step_results:
        bd = getattr(sr, "business_data", {}) or {}
        for key in ("event_no", "task_no", "meeting_no", "object_id"):
            val = bd.get(key, "")
            if val:
                business_object_no = str(val)
                break
        if business_object_no:
            break

    issues = list(getattr(wi, "warnings", []) or [])
    for sr in step_results:
        guardian = getattr(sr, "guardian", None)
        if guardian and getattr(guardian, "reason", ""):
            issues.append(f"[{getattr(sr, 'step_id', '?')}] {guardian.reason}")

    # result_constraints 校验
    rc = getattr(wi, "result_constraints", {}) or {}
    if rc:
        must_include = rc.get("must_include", [])
        if must_include:
            combined = " ".join(summary_facts) if summary_facts else ""
            for item in must_include:
                if item not in combined:
                    issues.append(f"[constraint] 缺少必须信息: {item}")
        must_not = rc.get("must_not", [])
        if must_not:
            combined = " ".join(summary_facts) if summary_facts else ""
            for item in must_not:
                clean = item.replace("不要", "").replace("别", "").strip()
                if clean and clean in combined:
                    issues.append(f"[constraint] 包含违规内容: {item}")

    needs_confirm = any(
        getattr(getattr(sr, "business_data", {}), "needs_confirm", False)
        for sr in step_results
    )

    error_category = ""
    if status == "failed":
        for sr in failed_steps:
            output = (getattr(sr, "output", "") or "")
            if "权限" in output or "permission" in output:
                error_category = "permission"; break
            if "参数" in output or "param" in output:
                error_category = "param_error"; break
            if "不存在" in output or "未找到" in output:
                error_category = "not_found"; break
        error_category = error_category or "system"

    suggested_followup = ""
    if status == "success" and spec.get("intent", "").startswith("query"):
        suggested_followup = "需要看详情吗？"

    return StructuredResult(
        status=status,
        intent=spec.get("intent", wi.sop_id or "fallback"),
        sop_id=wi.sop_id or "",
        risk_level=getattr(wi, "risk_level", "L2") or "L2",
        data=data,
        summary_facts=summary_facts,
        rag_sources=rag_sources,
        business_object_no=business_object_no,
        issues=issues,
        needs_confirm=needs_confirm,
        error_category=error_category,
        suggested_followup=suggested_followup,
    )


def make_created(hook_adapter):
    """created 节点：初始化 BusContext + 灌注。"""
    async def created(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "created")
        if not await hook_adapter.fire_before("created", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "created", t), "wi_state": "failed"}
        # sop_id 已由 SessionAgent 设置；加载 SOP 全文暂存 baggage
        wi = ctx.work_item
        sop_id = wi.sop_id or ""
        if sop_id:
            sop_text = _load_sop_text(sop_id)
            ctx.set("sop_text", sop_text)
            state["current_sop_id"] = sop_id
        await hook_adapter.fire_after("created", ctx)
        return {**_exit_stage(state, "created", t), "wi_state": "routing"}
    created.__name__ = "created"
    return created


def make_routing(hook_adapter):
    """routing 节点：SOP .md 已加载，验证 route_decision。"""
    async def routing(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "routing")
        if not await hook_adapter.fire_before("routing", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "routing", t), "wi_state": "failed"}
        # route_decision 已由 SessionAgent 设置（node1_intent 的职责上移到 SessionAgent）
        wi = ctx.work_item
        if wi.route_decision is None:
            from ...adapters.standard.route_decision import RouteDecision, SubTask
            wi.route_decision = RouteDecision(
                intent_type=getattr(wi, "intent_type", "fallback") or "fallback",
                sop_id=wi.sop_id or None,
                confidence="medium" if wi.sop_id else "none",
                is_compound=False,
                sub_tasks=[],
                _source="session_agent",
            )
        ctx.intent = wi.route_decision
        await hook_adapter.fire_after("routing", ctx)
        return {**_exit_stage(state, "routing", t), "wi_state": "executing"}
    routing.__name__ = "routing"
    return routing


def make_executing(hook_adapter, *, llm_client, business_tools, resolvers, config):
    """executing 节点：包装 agent loop 入口（agent_node）。

    agent_node ↔ tool_node 的循环由 graph.py 的条件边驱动，本节点只触发首轮 agent_node。
    """
    async def executing(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "executing")
        if not await hook_adapter.fire_before("executing", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "executing", t), "wi_state": "failed"}
        from .agent.loop import agent_node
        sop_text = ctx.get("sop_text", "")
        result = await agent_node(
            state,
            llm_client=llm_client,
            business_tools=business_tools,
            resolvers=resolvers,
            sop_text=sop_text,
            config=config,
        )
        await hook_adapter.fire_after("executing", ctx)
        return {**_exit_stage(state, "executing", t), **result}
    executing.__name__ = "executing"
    return executing


def make_summarizing(hook_adapter):
    """summarizing 节点：从 step_results 提取 StructuredResult + 设 result_text。"""
    async def summarizing(state: dict) -> dict:
        ctx = _get_context()
        wi = ctx.work_item
        t = _enter_stage(state, "summarizing")
        if not await hook_adapter.fire_before("summarizing", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "summarizing", t), "wi_state": "failed"}
        try:
            wi.structured_result = _extract_structured_result(wi, ctx)
            # result_text 取 agent loop 最终回复（baggage），兜底用 step_results output
            wi.result_text = ctx.get("agent_final_reply", "") or (
                wi.step_results[-1].output if wi.step_results else ""
            )
            ctx.verified_reply = ""
        except Exception as e:
            logger.error("summarizing failed: %s", e, exc_info=True)
            wi.add_warning(f"summarizing 失败: {e}")
        await hook_adapter.fire_after("summarizing", ctx)
        return {**_exit_stage(state, "summarizing", t), "wi_state": "done"}
    summarizing.__name__ = "summarizing"
    return summarizing


def make_error_analysis(hook_adapter, *, llm_client, config):
    """error_analysis 节点：iteration cap / LLM 异常兜底。复用 ErrorAnalyzer。"""
    from .error_analysis import ErrorAnalyzer
    analyzer = ErrorAnalyzer(llm_client=llm_client, config=config)

    async def error_analysis(state: dict) -> dict:
        ctx = _get_context()
        t = _enter_stage(state, "error_analysis")
        if not await hook_adapter.fire_before("error_analysis", ctx):
            ctx.should_abort = True
            return {**_exit_stage(state, "error_analysis", t), "wi_state": "failed"}
        try:
            result = await analyzer.analyze(ctx)
        except Exception as e:
            logger.error("error_analysis crashed: %s", e, exc_info=True)
            result = {"error_type": "transient_failure", "should_abort": False,
                      "root_cause": f"分析器异常: {e}"}
        # iteration cap 触发的 should_escalate → 默认 abort
        if state.get("error_analysis", {}).get("should_escalate"):
            result = {**result, "should_abort": True,
                      "root_cause": result.get("root_cause", "iteration cap")}
        if result.get("should_abort"):
            ctx.should_abort = True
            ctx.abort_reason = result.get("root_cause", "error_analysis abort")
            user_prompt = result.get("user_prompt", "")
            if user_prompt and ctx.work_item is not None:
                ctx.work_item.add_warning(f"需追问用户: {user_prompt}")
        state["error_analysis"] = result
        await hook_adapter.fire_after("error_analysis", ctx)
        wi_state = "failed" if result.get("should_abort") else "executing"
        return {**_exit_stage(state, "error_analysis", t), "wi_state": wi_state,
                "iteration_count": 0}
    error_analysis.__name__ = "error_analysis"
    return error_analysis
```

#### `emily-core/emily_core/workitem/langgraph_engine/graph.py` — 整文件替换

```python
# emily-core/emily_core/workitem/langgraph_engine/graph.py
"""统一生命周期 StateGraph —— created→routing→executing(agent loop)→summarizing→done/failed。

executing 内嵌 agent loop：executing(agent_node) ↔ tool_node，由条件边驱动循环。
WAITING_FOR_INPUT 用 LangGraph interrupt()（在 tool_node 的 ask_user 分支触发）。
error_analysis 作 iteration cap 兜底。
"""
from __future__ import annotations

import logging

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import AgentLoopState
from .nodes import (
    make_created, make_routing, make_executing, make_summarizing, make_error_analysis,
)
from .agent.loop import route_after_agent, route_after_tool

logger = logging.getLogger("emily.langgraph.graph")


def build_workitem_graph(
    *,
    hook_adapter,
    llm_client,
    business_tools,
    resolvers,
    config,
    max_iterations: int = 12,
) -> "StateGraph":
    """构建统一生命周期图。

    Args:
        hook_adapter: HookAdapter 实例
        llm_client: LLMClient 实例
        business_tools: BusinessFlowToolRegistry 实例
        resolvers: ResolverRegistry 实例
        config: Config 实例
        max_iterations: agent loop 最大迭代数
    """
    gs = StateGraph(AgentLoopState)

    # ── 注册节点 ──
    gs.add_node("created", make_created(hook_adapter))
    gs.add_node("routing", make_routing(hook_adapter))
    gs.add_node("executing", make_executing(
        hook_adapter, llm_client=llm_client, business_tools=business_tools,
        resolvers=resolvers, config=config))
    gs.add_node("agent_node", _make_agent_loop_entry(
        llm_client=llm_client, business_tools=business_tools,
        resolvers=resolvers, config=config))
    gs.add_node("tool_node", _make_tool_loop_entry(
        llm_client=llm_client, business_tools=business_tools, resolvers=resolvers))
    gs.add_node("summarizing", make_summarizing(hook_adapter))
    gs.add_node("error_analysis", make_error_analysis(
        hook_adapter, llm_client=llm_client, config=config))

    # ── 边 ──
    gs.add_edge(START, "created")
    gs.add_edge("created", "routing")
    gs.add_edge("routing", "executing")

    # executing 触发首轮 agent_node
    gs.add_edge("executing", "agent_node")

    # agent_node → 条件路由（tool_node / summarizing / error_analysis）
    gs.add_conditional_edges(
        "agent_node",
        route_after_agent,
        {"tool_node": "tool_node", "summarizing": "summarizing",
         "error_analysis": "error_analysis"},
    )

    # tool_node → agent_node（循环）
    gs.add_conditional_edges(
        "tool_node",
        route_after_tool,
        {"agent_node": "agent_node"},
    )

    # summarizing → END
    gs.add_edge("summarizing", END)

    # error_analysis → 条件路由（failed→END / executing→agent_node 重试）
    gs.add_conditional_edges(
        "error_analysis",
        route_after_error,
        {"failed": END, "agent_node": "agent_node"},
    )

    graph = gs.compile(checkpointer=MemorySaver())
    logger.info("Unified lifecycle graph built: created→routing→executing(agent loop)→summarizing, "
                "max_iterations=%d, checkpointer=MemorySaver", max_iterations)
    return graph


def route_after_error(state: dict) -> str:
    """error_analysis 之后路由：should_abort→failed(END)，否则→agent_node 重试。"""
    ea = state.get("error_analysis", {}) or {}
    if ea.get("should_abort"):
        return "failed"
    return "agent_node"


def _make_agent_loop_entry(*, llm_client, business_tools, resolvers, config):
    """agent_node 节点入口（直接调 loop.agent_node，不经 hook 包装——executing 节点已 fire hook）。"""
    from .agent.loop import agent_node

    async def _node(state: dict) -> dict:
        ctx = None
        try:
            from .state import get_bus_context
            ctx = get_bus_context()
        except RuntimeError:
            pass
        sop_text = ctx.get("sop_text", "") if ctx else ""
        return await agent_node(
            state, llm_client=llm_client, business_tools=business_tools,
            resolvers=resolvers, sop_text=sop_text, config=config)
    _node.__name__ = "agent_node"
    return _node


def _make_tool_loop_entry(*, llm_client, business_tools, resolvers):
    """tool_node 节点入口（直接调 loop.tool_node）。"""
    from .agent.loop import tool_node

    async def _node(state: dict) -> dict:
        return await tool_node(state, llm_client=llm_client,
                               business_tools=business_tools, resolvers=resolvers)
    _node.__name__ = "tool_node"
    return _node
```

#### `emily-core/emily_core/__init__.py` — 替换 `_build_pipeline_bus` 方法（第 464-500 行）

```python
    def _build_pipeline_bus(self) -> None:
        """构建统一生命周期 LangGraph 引擎（L3 agent loop）。

        旧 5 节点图 + WorkItemAgent 4 节点 handler 已移除（大爆炸切换）。
        新图：created→routing→executing(agent loop)→summarizing→done/failed。
        """
        from .workitem.langgraph_engine.graph import build_workitem_graph
        from .workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
        from .workitem.langgraph_engine.agent.resolver import build_default_resolvers

        hook_cfg = self._load_hook_config() or {"hooks": {}}
        injected = self._collect_injected_services()
        self._hook_adapter = build_hook_adapter_from_config(hook_cfg, injected)

        self._resolvers = build_default_resolvers()

        self._workitem_graph = build_workitem_graph(
            hook_adapter=self._hook_adapter,
            llm_client=self._llm_client,
            business_tools=self._business_flow_tools,
            resolvers=self._resolvers,
            config=self.config,
            max_iterations=getattr(self.config, "agent_loop_max_iterations", 12),
        )
        logger.info(
            "Unified lifecycle graph built: agent loop, max_iterations=%d, checkpointer=MemorySaver",
            getattr(self.config, "agent_loop_max_iterations", 12),
        )
```

同时修改 `__init__.py` 顶部属性声明区（第 56-59 行附近）：把 `self._workitem_agent = None` 改为 `self._resolvers = None`（WorkItemAgent 不再使用）。`self._workitem_graph` / `self._hook_adapter` 保留。

### 模块验收检测

```bash
# 验收 1：图含 agent_node/tool_node/interrupt
grep -n "agent_node\|tool_node\|interrupt" emily-core/emily_core/workitem/langgraph_engine/graph.py
→ 预期输出：含 agent_node、tool_node 的 add_node / add_edge 行

# 验收 2：旧 make_node1/2/3/4 已删除
grep -n "make_node1\|make_node2\|make_node3\|make_node4" emily-core/emily_core/workitem/langgraph_engine/nodes.py
→ 预期输出：无匹配（大爆炸已删）

# 验收 3：图可编译
uv run python -c "
from emily_core.workitem.langgraph_engine.graph import build_workitem_graph
class FakeHook:
    async def fire_before(self,n,c): return True
    async def fire_after(self,n,c): pass
    async def fire_error(self,n,c,e): pass
class FakeCfg:
    agent_loop_max_iterations=12
class FakeLLM:
    router_model='f'; model='m'
    async def chat_with_tools(self,m,t): return {'type':'text','content':'ok'}
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry
from emily_core.workitem.langgraph_engine.agent.resolver import build_default_resolvers
g=build_workitem_graph(hook_adapter=FakeHook(),llm_client=FakeLLM(),business_tools=BusinessFlowToolRegistry(),resolvers=build_default_resolvers(),config=FakeCfg())
assert g is not None
print('M6 graph compiled OK')
"
→ 预期输出：M6 graph compiled OK

# 验收 4：AgentLoopState 字段
uv run python -c "
from emily_core.workitem.langgraph_engine.state import AgentLoopState, make_initial_state
s=make_initial_state(pipeline_run_id='t')
assert s['wi_state']=='created'
assert s['messages']==[]
assert s['_max_iterations']==12
print('M6 state OK')
"
→ 预期输出：M6 state OK
```

**失败处理**：
- 若 `from langgraph.types import interrupt` 失败（langgraph 版本 < 0.2.39）：M4 `tool_node` 的 ask_user 分支改用条件边回退——把 `interrupt(question)` 替换为设置 `state["wi_state"]="waiting_for_input"` + `state["waiting_question"]=question` 并 `return`，graph.py 加 `tool_node` 的条件边 `route_after_tool` 增判 `waiting_for_input → END`，scheduler 检测 `state["wi_state"]=="waiting_for_input"` 挂起，续接时重建 messages 注入用户回复后重新 ainvoke（用 MemorySaver checkpoint 恢复）。此回退需同步改 M4 + M6 + M7。
- 若 `_extract_structured_result` import `StructuredResult` 失败，确认 `pipeline/interfaces/execution.py` 的 `StructuredResult` 未被 M8 误删（M8 只删 `planning.py` 的 ExecutionPlan/PlanStep，不删 execution.py）。
- 若 `load_prompt(sop_id)` 找不到 SOP .md，确认 `prompt_loader.py` 的搜索路径含 `emily-data/sops/`（或用 `_load_sop_text` 的 Path.glob 回退）。

---

## M7: Scheduler 瘦身 + SessionAgent 续接适配

**依赖**：M6（新图）

**职责**：重写 `scheduler._run_one`（建 BusContext + 调图 + interrupt 检测/resume + 持久化，删手写状态转换）；重写 `_run_graph`（interrupt/resume）；修改 `SessionAgent._handle_impl` 续接逻辑适配 `Command(resume=...)`。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | scheduler 重写 | `emily-core/emily_core/workitem/scheduler.py` |
| 2 | SessionAgent 续接适配 | `emily-core/emily_core/session/session_agent.py` |

### 代码

#### `emily-core/emily_core/workitem/scheduler.py` — 替换 `_run_one` 和 `_run_graph` 方法（第 97-224 行）

```python
    async def _run_one(self, wi: WorkItem, message=None, db_message_id: str = "") -> WorkItem:
        """在统一生命周期图上执行单个 WorkItem。

        职责瘦身：建 BusContext + 调图 + interrupt 检测/resume + 持久化。
        不做手写状态转换（由图驱动）。
        """
        from .workitem_state import WorkItemState
        self._active[wi.id] = wi
        try:
            # 多轮续接：WAITING_FOR_INPUT 直接回 EXECUTING（图用 Command(resume=...) 恢复）
            is_resuming = (wi.state == WorkItemState.WAITING_FOR_INPUT)
            if is_resuming:
                wi.transition_to(WorkItemState.EXECUTING)
            else:
                wi.transition_to(WorkItemState.PLANNING)
                wi.transition_to(WorkItemState.EXECUTING)

            context = BusContext(
                work_item=wi,
                message=message,
                user_id=wi.user_id,
                is_admin=wi.is_admin,
                db_message_id=db_message_id,
                _session_context=self._session_context,
                _actor_snapshot=getattr(self, "_current_actor", None),
            )
            wi.pipeline_run_id = context.pipeline_run_id

            archive_md_path = getattr(self, "archive_md_path", "")
            if archive_md_path:
                context.baggage["archive_md_path"] = archive_md_path

            if context.message is None:
                stored = getattr(wi, '_source_message', None)
                if stored is not None:
                    context.message = stored

            # 调图（含 interrupt 检测/resume）
            await self._run_graph(context, is_resuming=is_resuming,
                                  resume_input=getattr(wi, "additional_input", "") or "")

            # 检查是否 interrupt 挂起（WAITING_FOR_INPUT）
            if wi.state == WorkItemState.WAITING_FOR_INPUT:
                logger.info("Scheduler[%s] WI %s WAITING_FOR_INPUT: %s",
                            self.session_id, wi.id, wi.question[:60])
                return wi

            if context.should_abort:
                wi.transition_to(WorkItemState.FAILED)
                wi.error_message = wi.error_message or context.abort_reason
                logger.warning("Scheduler[%s] WI %s FAILED: %s",
                               self.session_id, wi.id, wi.error_message)
            else:
                wi.transition_to(WorkItemState.DONE)
                logger.info("Scheduler[%s] WI %s DONE", self.session_id, wi.id)
        except Exception as e:
            logger.error("Scheduler[%s] WI %s crashed: %s",
                         self.session_id, wi.id, e, exc_info=True)
            if not wi.is_terminal:
                try:
                    wi.transition_to(WorkItemState.FAILED)
                except ValueError:
                    wi.state = WorkItemState.FAILED
            wi.error_message = str(e)
        finally:
            self._active.pop(wi.id, None)
            self._done.append(wi)
        return wi

    async def _run_graph(self, context, is_resuming: bool = False, resume_input: str = "") -> None:
        """通过统一生命周期图执行 WorkItem（含 interrupt/resume）。"""
        from langgraph.types import Command
        from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
        from emily_core.infrastructure.logging.business_event_logger import BusinessEventLogger
        from emily_core.workitem.langgraph_engine.state import (
            set_bus_context, clear_bus_context, make_initial_state,
        )
        from emily_core.workitem.workitem_state import WorkItemState

        core = getattr(self, "_core", None)
        graph = getattr(core, "_workitem_graph", None) if core else None
        if graph is None:
            raise RuntimeError("LangGraph engine not built — check EmilyCore._build_pipeline_bus()")

        set_bus_context(context)

        LLMInteractionLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
            user_id=context.user_id,
        )
        BusinessEventLogger.set_context(
            pipeline_run_id=context.pipeline_run_id,
            conversation_id=context.message.conversation_id if context.message else "",
        )

        core = getattr(self, "_core", None)
        outbound_bus = getattr(core, "outbound_bus", None) if core else None
        if outbound_bus is not None and context.message is not None:
            _cid = context.message.conversation_id or ""
            def _send_progress(text: str, _bus=outbound_bus, _cid=_cid) -> None:
                _bus.publish("progress", {"content": text, "conversation_id": _cid})
            context.baggage.setdefault("progress_sender", _send_progress)

        try:
            _cfg = getattr(core, "config", None) if core else None
            max_iter = getattr(_cfg, "agent_loop_max_iterations", 12) if _cfg else 12
            config = {"configurable": {"thread_id": context.pipeline_run_id}}

            if is_resuming and resume_input:
                # 续接：用 Command(resume=...) 把用户回复注入 interrupt 断点
                result = await graph.ainvoke(
                    Command(resume=resume_input), config=config,
                )
            else:
                state = make_initial_state(
                    pipeline_run_id=context.pipeline_run_id,
                    max_iterations=max_iter,
                )
                result = await graph.ainvoke(state, config=config)

            # 检测 interrupt 挂起
            _check_interrupt(self, context, config, graph)
        finally:
            clear_bus_context()
            LLMInteractionLogger.clear_context()
            BusinessEventLogger.clear_context()
```

在 `scheduler.py` 末尾（类外）追加 `_check_interrupt` 辅助函数：

```python
def _check_interrupt(scheduler, context, config, graph) -> None:
    """检测图是否因 interrupt 挂起（WAITING_FOR_INPUT），若是则标记 WorkItem 状态。"""
    from emily_core.workitem.workitem_state import WorkItemState
    try:
        # langgraph 1.x：get_state 读 checkpoint，interrupt 时 __interrupt__ 非空
        snap = graph.get_state(config)
        tasks = getattr(snap, "tasks", {}) or {}
        next_nodes = getattr(snap, "next", ()) or ()
        # interrupt 挂起时 next 含 tool_node（ask_user 在 tool_node 内 interrupt）
        if next_nodes or (hasattr(snap, "values") and snap.values.get("wi_state") == "executing"
                          and snap.values.get("waiting_question")):
            wi = context.work_item
            wi.state = WorkItemState.WAITING_FOR_INPUT
            wi.question = snap.values.get("waiting_question", "") or _extract_interrupt_question(snap)
            logger = logging.getLogger("emily.scheduler")
            logger.info("Scheduler interrupt detected: WI %s question=%s",
                        wi.id, wi.question[:60])
    except Exception as e:
        logging.getLogger("emily.scheduler").debug("interrupt check skipped: %s", e)


def _extract_interrupt_question(snap) -> str:
    """从 interrupt 快照提取问题文本。"""
    try:
        tasks = getattr(snap, "tasks", {}) or {}
        for t in tasks.values():
            interrupts = getattr(t, "interrupts", []) or []
            for intr in interrupts:
                val = getattr(intr, "value", None)
                if isinstance(val, str):
                    return val
    except Exception:
        pass
    return "请补充信息"
```

#### `emily-core/emily_core/session/session_agent.py` — 修改 `_handle_impl` 续接段（第 313-333 行）

续接逻辑基本不变（SessionAgent 仍管 `_paused_workitem` + `additional_input`），但确认续接时 `additional_input` 正确注入 WI 供 scheduler `_run_graph` 的 `Command(resume=...)` 使用。当前代码（第 320-325 行）已设 `continue_wi.additional_input = content`，**无需改动**，仅需确认 scheduler M7 读 `wi.additional_input` 作为 resume_input（已在 `_run_one` 中 `getattr(wi, "additional_input", "")` 读取）。

**唯一需改**：`_handle_impl` 第 366-372 行，WAITING_FOR_INPUT 检测段保持不变（scheduler 仍设 `wi.state = WAITING_FOR_INPUT` + `wi.question`，SessionAgent 据此返回问题）。确认无需改动后，本模块 SessionAgent 部分零代码改动——仅在验收中验证续接端到端可用。

### 模块验收检测

```bash
# 验收 1：scheduler 无旧手写状态转换（transition_to 仅保留 CREATED→PLANNING→EXECUTING 必要转换）
grep -n "transition_to" emily-core/emily_core/workitem/scheduler.py
→ 预期输出：仅 is_resuming 分支的 EXECUTING 转换 + CREATED→PLANNING→EXECUTING 两行 + FAILED/DONE 终态转换，无 node3 手写检查

# 验收 2：scheduler 含 Command(resume=)
grep -n "Command(resume" emily-core/emily_core/workitem/scheduler.py
→ 预期输出：一行匹配

# 验收 3：scheduler 不含 WAITING_FOR_INPUT 手写检查（由 _check_interrupt 处理）
grep -n "WAITING_FOR_INPUT" emily-core/emily_core/workitem/scheduler.py
→ 预期输出：仅 _run_one 末尾的 `if wi.state == WorkItemState.WAITING_FOR_INPUT` 返回 + _check_interrupt 内的状态设置，无 node3 手写 needs_input 检查

# 验收 4：端到端续接（需 docker 起来后跑，见组装验证）
```

**失败处理**：
- 若 `Command` import 失败：langgraph 版本太旧，按 M6 失败处理的 checkpoint 回退方案（不用 interrupt/Command，改用 MemorySaver checkpoint + 条件边 + 重新 ainvoke 注入用户消息）。
- 若 `graph.get_state(config)` 报错：langgraph 版本 API 差异，改用 `await graph.aget_state(config)`。
- 若续接时 interrupt 未触发（用户回复后图从头跑）：确认 `thread_id` 一致（都用 `context.pipeline_run_id`）且 MemorySaver 持久化生效。

---

## M8: 清理旧代码

**依赖**：M7（新路径全通）

**职责**：删除 Skill 体系（executor/definition/parser/validator/param_extractor + 10 Skill YAML + ExecutionPlan/PlanStep）+ WorkItemAgent 旧方法；SkillRegistry 降级为 SOP .md 索引器；tools_consistency.py 删 Skill YAML 检查项。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 删除 Skill 执行体系 | `emily-core/emily_core/skill/executor.py`, `definition.py`, `parser.py`, `validator.py`, `param_extractor.py` |
| 2 | 删 10 份 Skill YAML | `emily-data/skills/*.skill.yaml` |
| 3 | 删 ExecutionPlan/PlanStep | `emily-core/emily_core/workitem/pipeline/interfaces/planning.py` |
| 4 | SkillRegistry 降级 | `emily-core/emily_core/skill/registry.py` |
| 5 | WorkItemAgent 清理 | `emily-core/emily_core/workitem/workitem_agent.py` |
| 6 | tools_consistency 清理 | `emily-core/emily_core/infrastructure/tools_consistency.py` |
| 7 | EmilyCore 清理 | `emily-core/emily_core/__init__.py`（`_init_skill_module` 等） |

### 执行动作

#### 动作 1：删除文件

```bash
# 删除 Skill 执行体系（保留 registry.py 重写）
git rm emily-core/emily_core/skill/executor.py
git rm emily-core/emily_core/skill/definition.py
git rm emily-core/emily_core/skill/parser.py
git rm emily-core/emily_core/skill/validator.py
git rm emily-core/emily_core/skill/param_extractor.py

# 删除 10 份 Skill YAML
git rm emily-data/skills/SOP-000-SYS.skill.yaml
git rm emily-data/skills/SOP-001-REC.skill.yaml
git rm emily-data/skills/SOP-002-REC.skill.yaml
git rm emily-data/skills/SOP-003-REC.skill.yaml
git rm emily-data/skills/SOP-004-FILE.skill.yaml
git rm emily-data/skills/SOP-005-QRY.skill.yaml
git rm emily-data/skills/SOP-007-REC.skill.yaml
git rm emily-data/skills/SOP-008-SYS.skill.yaml
git rm emily-data/skills/SOP-011-SYS.skill.yaml
git rm emily-data/skills/SOP-999-SYS.skill.yaml

# 删除 ExecutionPlan/PlanStep（planning.py 整文件删，StepResult 在 execution.py 保留）
git rm emily-core/emily_core/workitem/pipeline/interfaces/planning.py
```

#### 动作 2：`skill/registry.py` 重写为 SOP .md 索引器

整文件替换为扫描 `emily-data/sops/*.md` 的索引器，保留 `dump_as_text()` / `get_by_sop_id()` / `has_skill()` / `list_sop_ids()` 接口（SessionAgent 依赖）：

```python
# emily-core/emily_core/skill/registry.py
"""SkillRegistry —— 降级为 SOP .md 索引器（L3 agent loop 迁移后）。

扫描 emily-data/sops/*.md，按 sop_id 索引。保留 dump_as_text/get_by_sop_id 等接口
供 SessionAgent 意图识别消费。不再解析 Skill YAML（已删除）。
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("emily.skill.registry")


@dataclass
class SopDoc:
    """SOP .md 索引项。"""
    sop_id: str
    display_name: str
    file_path: str
    instructions: str = ""  # 首行摘要


@dataclass
class SkillRegistryStatus:
    total_files: int = 0
    successfully_parsed: int = 0
    failed_parsed: int = 0
    is_ready: bool = False
    last_reload_at: str = ""


def _extract_sop_type(sop_id: str) -> str:
    parts = sop_id.split("-")
    for p in parts[1:]:
        if p in ("REC", "FILE", "QRY", "FLOW", "SYS"):
            return p
    return "UNKNOWN"


class SkillRegistry:
    """SOP .md 索引器（原 Skill YAML 注册表降级）。"""

    def __init__(self, skill_directory: str):
        # skill_directory 仍指向 skills/ 目录（兼容），但实际扫描 sops/
        self.skill_directory = Path(skill_directory)
        self._sop_dir: Path | None = None
        self._lock = threading.RLock()
        self._registry: dict[str, SopDoc] = {}
        self._is_ready = False

    def _resolve_sop_dir(self) -> Path:
        if self._sop_dir is not None:
            return self._sop_dir
        # sops/ 与 skills/ 同级
        candidates = [
            self.skill_directory.parent / "sops",
            self.skill_directory.parent.parent / "emily-data" / "sops",
            Path("/app/sops"),
        ]
        for c in candidates:
            if c.exists():
                self._sop_dir = c
                return c
        self._sop_dir = self.skill_directory.parent / "sops"
        return self._sop_dir

    def load(self) -> SkillRegistryStatus:
        with self._lock:
            return self._scan()

    def reload(self) -> SkillRegistryStatus:
        return self.load()

    def _scan(self) -> SkillRegistryStatus:
        now = datetime.now(timezone.utc).isoformat()
        sop_dir = self._resolve_sop_dir()
        new_reg: dict[str, SopDoc] = {}
        if not sop_dir.exists():
            logger.warning("SOP dir not found: %s", sop_dir)
            self._registry = new_reg
            self._is_ready = False
            return SkillRegistryStatus(last_reload_at=now)
        ok = 0
        for p in sorted(sop_dir.glob("SOP-*.md")):
            try:
                sop_id = p.stem  # 如 SOP-002-REC-event_record
                text = p.read_text(encoding="utf-8")
                display_name = self._extract_display_name(text, sop_id)
                first_line = self._extract_first_instruction(text)
                new_reg[sop_id] = SopDoc(
                    sop_id=sop_id, display_name=display_name,
                    file_path=str(p), instructions=first_line,
                )
                ok += 1
            except Exception as e:
                logger.error("SOP parse failed: %s — %s", p.name, e)
        self._registry = new_reg
        self._is_ready = ok > 0
        logger.info("SkillRegistry(SOP indexer) loaded: %d docs from %s", ok, sop_dir)
        return SkillRegistryStatus(total_files=ok, successfully_parsed=ok,
                                   is_ready=ok > 0, last_reload_at=now)

    @staticmethod
    def _extract_display_name(text: str, sop_id: str) -> str:
        # 从首行 # 标题提取
        for line in text.splitlines()[:3]:
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("# ").strip() or sop_id
        return sop_id

    @staticmethod
    def _extract_first_instruction(text: str) -> str:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(">") and not line.startswith("|"):
                return line[:100]
        return ""

    # ── 查询接口（SessionAgent 依赖，签名不变）──

    def get_by_sop_id(self, sop_id: str) -> SopDoc | None:
        with self._lock:
            return self._registry.get(sop_id)

    def has_skill(self, sop_id: str) -> bool:
        with self._lock:
            return sop_id in self._registry

    def list_sop_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._registry.keys())

    def list_skills(self) -> list[SopDoc]:
        with self._lock:
            return list(self._registry.values())

    def dump_as_text(self) -> str:
        """以类型树格式导出（供 SessionAgent 意图识别消费）。保留原输出结构。"""
        with self._lock:
            docs = list(self._registry.values())
        if not docs:
            return "（暂无已加载的业务流程/SOP）"
        grouped: dict[str, list[SopDoc]] = {}
        for d in docs:
            grouped.setdefault(_extract_sop_type(d.sop_id), []).append(d)
        TYPE_DESC = {
            "REC": "记录与录入（事件/任务/会议等）",
            "FILE": "文件管理（归档/查询/分享）",
            "QRY": "数据查询（项目/进度/人员等）",
            "FLOW": "深度调查（跨维度分析/审计）",
            "SYS": "系统管理（确认/取消/设置等）",
        }
        lines = ["## 一、业务类型树（先看这里，确定消息属于哪个类型）", ""]
        for sop_type, type_docs in grouped.items():
            names = "、".join(d.display_name for d in type_docs)
            ids = "、".join(d.sop_id for d in type_docs)
            lines.append(f"**{sop_type}** — {TYPE_DESC.get(sop_type, sop_type)}")
            lines.append(f"  包含流程: {names}")
            lines.append(f"  编号: {ids}")
            lines.append("")
        lines += ["---", "", "## 二、各类型流程清单（锁定类型后精匹配）", ""]
        for sop_type, type_docs in grouped.items():
            lines.append(f"### {sop_type} — {TYPE_DESC.get(sop_type, sop_type)}")
            lines.append("")
            for d in type_docs:
                lines.append(f"**[{d.sop_id}] {d.display_name}**")
                if d.instructions:
                    lines.append(f"  说明: {d.instructions}")
                lines.append("")
            lines += ["---", ""]
        return "\n".join(lines)
```

#### 动作 3：`workitem/workitem_agent.py` 清理

删除以下方法（M6 已迁移或不再需要）：
- `node1_intent` / `node2_plan` / `node3_execute` / `node4_summary`（M6 新图不再调用）
- `_skill_to_execution_plan` / `_grade_skill_risk` / `_execute_skill`
- `_llm_plan` / `_map_to_execution_plan` / `_real_execute`
- `_extract_structured_result`（已迁移到 nodes.py）
- `_llm_synthesize_reply` / `_build_tools_text` / `_build_params_summary`
- `authorize` / `grade_risk`（reserved 无调用者）
- `node_handlers`（旧映射）
- `_fallback_steps`（不再用）

删除 `__init__` 中对 `skill_registry` / `skill_executor` / `injector` / `_guardian` 的依赖字段。若 WorkItemAgent 删空，整文件删除并把 `workitem/__init__.py` 中的 `WorkItemAgent` 导出去掉。

**判定**：若删完方法后 WorkItemAgent 仅剩空壳，直接 `git rm emily-core/emily_core/workitem/workitem_agent.py`，并修改 `workitem/__init__.py` 移除其导出。EmilyCore._build_pipeline_bus（M6 已改）不再构造 WorkItemAgent。

#### 动作 4：`workitem/injector.py` 清理

KnowledgeInjector 的 planner 角色已无用（SOP 全文由 nodes.py `_load_sop_text` 加载）。整文件 `git rm emily-core/emily_core/workitem/injector.py`，并修改 `workitem/__init__.py` 移除 `KnowledgeInjector` 导出。EmilyCore._build_pipeline_bus（M6 已改）不再构造 injector。

#### 动作 5：`infrastructure/tools_consistency.py` 清理

删除 V10/V11/V12 验证项（Skill YAML 检查，YAML 已删）。在文件顶部验证项注释中移除 V10/V11/V12 行。`REGISTERED_TOOLS` / `TOOL_META_MAP` / `TOOL_SCHEMA_MAP` 保留（仍用于 DB 一致性检查）。具体：搜索 `V10` / `V11` / `V12` / `Skill YAML` 关键词，删除相关检查函数与调用。

#### 动作 6：`emily_core/__init__.py` 清理

- `_init_skill_module`：把 `SkillExecutor` 构造删除（`self._skill_executor = None`），只保留 `SkillRegistry`（降级后的 SOP 索引器）。删除 `from .skill.executor import SkillExecutor`。
- `reload_skills`：保留（调用 `SkillRegistry.reload`，接口不变）。
- `_collect_injected_services`：`skill_registry` 注入保留（Hook 可能用）。
- 类属性声明：删除 `self._skill_executor = None`（第 116 行）。

### 模块验收检测

```bash
# 验收 1：Skill YAML 已删
ls emily-data/skills/ 2>/dev/null | grep -c "\.skill\.yaml$"
→ 预期输出：0

# 验收 2：SkillExecutor/Definition/ParamExtractor 已删
ls emily-core/emily_core/skill/ 2>/dev/null
→ 预期输出：仅 __init__.py 和 registry.py

# 验收 3：ExecutionPlan/PlanStep 已删
grep -rn "class PlanStep\|class ExecutionPlan" emily-core/emily_core/
→ 预期输出：无匹配

# 验收 4：_real_execute 已删
grep -rn "_real_execute" emily-core/emily_core/
→ 预期输出：无匹配

# 验收 5：SkillRegistry 降级为 SOP 索引器，dump_as_text 可用
uv run python -c "
from emily_core.skill.registry import SkillRegistry
r=SkillRegistry(skill_directory='emily-data/skills')
r.load()
ids=r.list_sop_ids()
assert len(ids)>=10, f'expected >=10 SOPs, got {len(ids)}'
text=r.dump_as_text()
assert 'SOP-002' in text
assert '事件' in text
print('M8 SkillRegistry OK, SOPs=',len(ids))
"
→ 预期输出：M8 SkillRegistry OK, SOPs= 10

# 验收 6：EmilyCore 可初始化（无 SkillExecutor 依赖）
uv run python -c "
from emily_core.config import Config
from emily_core import EmilyCore
c=Config(llm_api_key='dummy')
core=EmilyCore(config=c)
# 不触发 _ensure_initialized（需 DB），仅验证 import 与构造无报错
assert core._skill_executor is None or not hasattr(core,'_skill_executor')
print('M8 EmilyCore construct OK')
"
→ 预期输出：M8 EmilyCore construct OK

# 验收 7：tools_consistency 无 V10/V11/V12
grep -n "V10\|V11\|V12" emily-core/emily_core/infrastructure/tools_consistency.py
→ 预期输出：无匹配（或仅注释说明已删除）
```

**失败处理**：
- 若删除后 import 报错（某处仍引用 `SkillExecutor`/`ExecutionPlan`/`_real_execute`）：用 `grep -rn` 全局搜索残留引用，逐一改为新路径或删除。
- 若 `SkillRegistry.load()` 找不到 sops 目录：检查 `_resolve_sop_dir` 的候选路径，确认 `emily-data/sops/` 存在（已确认 10 份 .md）。
- 若 `workitem/__init__.py` 导出 `WorkItemAgent`/`KnowledgeInjector` 报错：同步修改 `__init__.py` 移除导出。

---

## M9: SOP .md 指导审查

**依赖**：M8

**职责**：审查 10 份 SOP .md，验证作为 agent 指导是否充分（含工具表 + 字段分级 + 调用场景）。必要时补"工具选择/参数约束"段落。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 10 份 .md 审查 + 增强 | `emily-data/sops/SOP-*.md` |

### 执行动作

对每份 SOP .md 检查以下要素（SOP-002 已确认含全部要素，作为基准）：

1. **§3.2 工具表**：含工具名 + 功能 + 调用场景
2. **§4 字段分级**：必有/应有/可有 + 类型约束
3. **agent 指导段**（必要时新增）：在 §3.3 处理流程后追加"Agent 调用指引"小节，说明：
   - UUID 字段（如 project_id）必须先调 `resolve_project`
   - 工具失败时看 tool_result 调整重试
   - 信息不足时调 `ask_user`

对缺失要素的 .md 补充段落。SOP-002 已含工具表 + 字段分级，预期仅微调（补 agent 调用指引）。

**审查清单**（逐份确认）：
- SOP-000-SYS-standard.md
- SOP-001-REC-meeting_summary.md
- SOP-002-REC-event_record.md（基准，已含）
- SOP-003-REC-task_manage.md
- SOP-004-FILE-file_archive.md
- SOP-005-QRY-data_query.md
- SOP-007-REC-user_memory.md
- SOP-008-SYS-pending_issue.md
- SOP-011-SYS-node_manage.md
- SOP-999-SYS-fallback.md

### 模块验收检测

```bash
# 验收 1：10 份 .md 含工具表 + 字段分级
grep -l "工具名\|工具表\|字段分级\|必有" emily-data/sops/*.md | wc -l
→ 预期输出：10（或 >=9，SOP-999 兜底可豁免）

# 验收 2：含 agent 调用指引（resolve_project 提及）
grep -l "resolve_project\|resolve\|UUID" emily-data/sops/*.md | wc -l
→ 预期输出：>=7（含 project_id 的 REC/FILE 类 SOP 应含）

# 验收 3：SOP-002 作为基准完整
grep -c "record_event\|resolve_project\|字段分级" emily-data/sops/SOP-002-REC-event_record.md
→ 预期输出：>=3
```

**失败处理**：若某份 .md 缺要素，参照 SOP-002 结构补充。若 SOP-999（兜底）无工具表，可豁免（其性质是自由推理）。

---

## 组装验证

所有模块完成后，运行端到端组装验证（需 docker 容器运行）：

```powershell
# ── 0. 重启容器（代码变更后）──
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# ── 1. BUG-01 回归：agent loop 应自然查 UUID 再录入（核心验证）──
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 10;"
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --message "帮我记一下样板段放线完成，翠湖庭院项目" --sender "王建国"
# 预期：agent loop 调 resolve_project → record_event，返回录入成功 + 事件编号，无 FK 报错

# ── 2. 条件分支验证（L3 独有能力）──
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --message "查一下翠湖庭院最近的延期事件，如果有的话帮我建个跟踪任务" --sender "王建国"
# 预期：LLM 调 query 看结果 → 基于观测决定是否 create_task

# ── 3. WAITING_FOR_INPUT 回归：多轮续接从断点恢复 ──
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --message "记录事件：材料进场" --sender "王建国"
# 预期：系统追问缺失信息（如项目/日期），回复后从 interrupt 断点恢复

# ── 4. 歧义项目名：resolver 返回候选，LLM 追问 ──
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --message "记录事件：材料进场，翠湖项目" --sender "王建国"
# 预期：resolve_project 返回多个候选，LLM 追问选择

# ── 5. LLM 流量验证：function calling 序列 ──
docker exec mitmproxy grep "tool_call\|tool_use" /app/logs/llm_trace.jsonl | tail -10
# 预期：trace 含 resolve_project + record_event 的 tool_call 序列

docker exec mitmproxy grep "事件记录" /app/logs/llm_trace.jsonl | head -3
# 预期：system prompt 含 SOP-002 .md 内容

# ── 6. 启动一致性检查 ──
uv run python scripts/check_tools_consistency.py
# 预期：V1/V5/V13a/V13b/V14 通过，V10/V11/V12 已移除不报
```

**组装验证失败处理**：
- 若 BUG-01 仍报 FK 错误：检查 M1 schema hint 是否正确注入（`docker exec mitmproxy grep "resolver" /app/logs/llm_trace.jsonl` 看 LLM 是否看到 hint）；检查 M2 resolver 是否被 LLM 调用（trace 含 `resolve_project`）。
- 若续接不工作：检查 M7 `Command(resume=...)` 的 `thread_id` 一致性 + MemorySaver checkpoint 是否持久化。
- 若工具调用未触发：检查 M3 `build_tool_specs` 的 `session_api_ids` 是否非空（`tool_registry` 表是否填充）。

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化（如 interrupt 不可用改 checkpoint 回退）→ **停止**，报告给用户，等用户决定是否重新生成计划

**关键停损点**：
- M6 若 `langgraph.types.interrupt` 不可用 → 触发 checkpoint 回退方案，属架构方向变化，**停止报告**
- M8 若删除后大量 import 报错（>4 文件残留引用）→ **停止报告**
- 组装验证若 BUG-01 未根治（FK 错误仍现）→ **停止报告**，检查 M1/M2/M4

---

## v1.1 修订记录

（执行过程中按反思指令追加）

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。基于 WorkItem_LangGraph全迁移_PRD_V1.md + 真实代码探查（2026-07-29）+ 用户三项关键决策（保留 SessionAgent 合成 / 仅回复级 Guardian / 大爆炸切换）。*

*执行完成后，本计划应保存至 `待执行计划/WorkItem_LangGraph全迁移_计划_V1.md` 供其他 AI 会话执行（本会话不实施）。*
