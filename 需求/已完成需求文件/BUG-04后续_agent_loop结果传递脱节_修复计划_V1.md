# BUG-04 后续：SessionAgent / WorkItem 职责重构 — AI 执行计划

> **基于测试**：[BUG-04_LangGraph引擎agent_loop双重调用与状态持久化缺陷报告](../BUG-04_LangGraph引擎agent_loop双重调用与状态持久化缺陷报告.md) 修复后的端到端回归测试（2026-07-30）
> **计划版本**：V2（**V1 方向作废**——V1 的"单 WI 直通 result_text"是迁就 agent loop 偏离，本版改为根治偏离）
> **目标**：回归 [workitem.py:75-76](../../emily-core/emily_core/workitem/workitem.py#L75-L76) 的原设计意图——WorkItem 返回成果、SessionAgent 组织回复、checkpoint 反馈明确区分
> **严重程度**：Critical（阻塞所有"LLM 选择先确认/直接回复"的场景，SOP-002 默认确认流程即在此列）

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **agent loop 不得直接回复用户**：agent loop 的 LLM 只做工具决策，完成工作后必须调 `complete_work` 返回成果，由 SessionAgent 组织回复
2. **WorkItem 输入是工作要求而非原文**：SessionAgent 向 WorkItem 下发 `work_spec`（结构化工作要求），`user_input` 仅作上下文附注
3. **成果与 checkpoint 必须区分**：WorkItem 返回 StructuredResult（成果）或 ask_user 反馈（checkpoint），两者走不同路径，SessionAgent 不得误判
4. **不新增规划 LLM 调用**：`work_spec` 由现有意图识别产出（route_decision + output_spec + result_constraints）规则化组装，决策 4B
5. **保留 LangGraph interrupt checkpoint 机制**：ask_user 仍用 interrupt + WAITING_FOR_INPUT 状态机，决策 3A
6. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
7. **改动后同步更新 docs/**：本计划涉及架构前提变更，须同步 [docs/业务模块与运转全景.md](../../docs/业务模块与运转全景.md) 和 [docs/接口协议与调用约定.md](../../docs/接口协议与调用约定.md)

---

## 1. 背景与决策

### 1.1 问题复现

测试消息「翠湖庭院住宅小区3号楼外墙涂料施工完成了，验收通过，帮我记个事件。」触发 SOP-002。agent loop 成功执行 resolve_project 并生成确认单（type=text），但用户收到"系统异常"兜底。LLM 流量日志显示：agent loop（v4-flash）生成确认单后，SessionAgent 又用 v4-pro 独立调 LLM 重新组织回复，因 StructuredResult 误判 failed 而输出"系统异常"。

### 1.2 根因：架构偏离原设计

[workitem.py:75-76](../../emily-core/emily_core/workitem/workitem.py#L75-L76) 注释表明原设计意图：
- `result_text`：仅兜底用，**正常路径由 Session 合成**
- `structured_result`：回传 Session 做语言组织

但 LangGraph agent loop 迁移时偏离：
- [prompt_builder.py:76](../../emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py#L76) 第 5 条指示 agent loop **"直接回复用户"**
- agent loop 的 type=text 生成确认单，抢了 Session 的回复组织职责
- Session 又重新组织，造成重复 + 上下文丢失

### 1.3 已确认的四个设计决策

| 决策 | 选择 | 说明 |
|------|------|------|
| 1. WorkItem 输入模型 | **C**：user_input 保留作上下文 + 新增 `work_spec` 字段 | work_spec 承载 SessionAgent 的结构化工作要求 |
| 2. agent loop 不生成回复的方式 | **A**：新增 `complete_work` 工具，LLM 显式调用返回结构化成果 | 显式工具调用天然区分"成果"/"继续调工具"/"ask_user" |
| 3. checkpoint 反馈路径 | **A**：保留 interrupt + WAITING_FOR_INPUT 状态机 | 强化 prompt 语义，不重写续接机制 |
| 4. SessionAgent 规划能力 | **B**：复用现有意图识别产出，不新增 LLM 调用 | work_spec 规则化组装 |

### 1.4 隐藏问题（本计划顺带修复）

`ask_user` 在 [prompt_builder.py:77](../../emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py#L77) 被提及、[loop.py:185](../../emily-core/emily_core/workitem/langgraph_engine/agent/loop.py#L185) 被处理，但 **tool spec 从未暴露给 LLM**（`build_tool_specs` 只加 business_tools + resolvers）。LLM 只能"幻觉"调用 ask_user。本计划 M1 顺带补上 ask_user spec。

---

## 2. 目标架构

### 2.1 职责划分

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| **SessionAgent** | 对话主权：意图分析、**组装 work_spec**、成果约束校验、**组织回复**、转达 checkpoint 提问 | 用户消息 + 对话上下文 | work_spec → WorkItem；回复 → 用户 |
| **WorkItem** | 纯执行：按 work_spec 调工具获取/写入数据，**不组织回复** | work_spec + 工具表 | StructuredResult（成果）**或** ask_user 反馈（checkpoint） |
| **agent loop** | 工具调用引擎：ReAct 循环只做工具决策，完成工作调 `complete_work` 返回成果 | work_spec + 工具表 | complete_work 成果 / ask_user 挂起 |
| **StructuredResult** | 成果载体（元数据）：SessionAgent 据此组织回复、归档、审核 | — | 由 complete_work 参数构造 |

### 2.2 关键机制设计

#### 2.2.1 complete_work 控制工具

agent loop 完成工作后，LLM 调用 `complete_work` 工具显式返回结构化成果。tool_node 识别该调用，构造 StructuredResult 存入 `wi.structured_result`，路由到 summarizing。

```
agent_node LLM 决策
  ├─ tool_call(业务工具/resolver) → tool_node 执行 → agent_node（循环）
  ├─ tool_call(complete_work)     → tool_node 构造 StructuredResult → summarizing
  └─ tool_call(ask_user)          → tool_node interrupt → WAITING_FOR_INPUT
```

type=text（LLM 返回纯文本不调工具）：兜底处理——转为 complete_work(status=success, summary=[text])，避免 LLM 不遵守 prompt 时卡死。

#### 2.2.2 work_spec 结构

SessionAgent 在 `_split_into_workitems` 时组装，规则化（不调 LLM）：

```python
work_spec = {
    "objective": intent.output_spec.intent,      # 任务目标（如 "record_event"）
    "sop_id": sop_id,                            # 匹配的 SOP
    "user_request": content[:500],               # 用户原始请求（上下文）
    "output_spec": output_spec,                  # 成果规格（intent/detail/format/data_fields）
    "constraints": result_constraints,           # 成果约束（scope/must_include/must_not）
    "required_tools": required_tools,            # 建议工具集（来自 SOP/权限）
}
```

agent loop 的 system prompt 以 work_spec 为执行指令，user_input 降为附注上下文。

#### 2.2.3 checkpoint 与成果的区分

| 路径 | WorkItem 状态 | SessionAgent 处理 |
|------|--------------|-------------------|
| 成果（complete_work） | DONE | `_synthesize_final_reply` 基于 StructuredResult 组织回复 |
| checkpoint（ask_user） | WAITING_FOR_INPUT | 直接转达 `wi.question`（[session_agent.py:372](../../emily-core/emily_core/session/session_agent.py#L372) 现状保留） |

两者由 `wi.state` 明确区分，SessionAgent 不误判。

---

## 3. 模块依赖图

```
M0 (work_spec 字段 + 组装)
  │
  ├─→ M1 (complete_work 控制工具 + agent loop 角色改造)  ← 核心
  │     │
  │     ├─→ M2 (StructuredResult 构造改造)
  │     │
  │     └─→ M3 (SessionAgent 回复合成回归原设计)
  │           │
  │           └─→ M5 (注释 + docs 同步)
  │
  └─→ M4 (ask_user spec + checkpoint 语义强化)  ← 与 M1 协同

M-B (工具 schema 补完，独立支线) ──→ M6

M6 (端到端验收)  ← 依赖全部
```

M0 是基础（work_spec 字段）。M1 是核心（complete_work + prompt 改造）。M2 依赖 M1（complete_work 产出 StructuredResult）。M3 依赖 M2（SessionAgent 基于完整 StructuredResult 组织）。M4 与 M1 协同（控制工具 spec 一起加）。M-B 独立。

---

## 4. 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M0 | `emily_core/workitem/workitem.py` | 修改 | 新增 `work_spec` 字段 |
| M0 | `emily_core/session/session_agent.py` | 修改 | `_split_into_workitems` 组装 work_spec |
| M1 | `emily_core/workitem/langgraph_engine/agent/control_tools.py` | **新增** | complete_work + ask_user 的 tool spec 定义 |
| M1 | `emily_core/workitem/langgraph_engine/agent/tool_adapter.py` | 修改 | `build_tool_specs` 追加控制工具 spec |
| M1 | `emily_core/workitem/langgraph_engine/agent/prompt_builder.py` | 修改 | system prompt 角色改造（不直接回复，调 complete_work） |
| M1 | `emily_core/workitem/langgraph_engine/agent/loop.py` | 修改 | complete_work 分支 + route_after_tool 改造 + type=text 兜底 |
| M1 | `emily_core/workitem/langgraph_engine/graph.py` | 修改 | route_after_tool 边映射加 summarizing |
| M2 | `emily_core/workitem/langgraph_engine/nodes.py` | 修改 | `_extract_structured_result` 从 complete_work 成果构造 |
| M3 | `emily_core/session/session_agent.py` | 修改 | `_synthesize_final_reply` 回归基于 StructuredResult（移除 V1 的直通方案） |
| M4 | `emily_core/workitem/langgraph_engine/agent/prompt_builder.py` | 修改 | ask_user 语义强化（与 M1 同文件，合并改动） |
| M5 | `emily_core/workitem/pipeline/interfaces/execution.py` | 修改 | StructuredResult 注释更新 |
| M5 | `emily_core/workitem/workitem.py` | 修改 | result_text/work_spec 注释更新 |
| M5 | `docs/业务模块与运转全景.md` | 修改 | 同步架构前提 |
| M5 | `docs/接口协议与调用约定.md` | 修改 | 同步结果传递契约 |
| M-B | `emily_core/tools/registry.py` | 修改 | 14 个业务工具补 `params=`（参照已有 schema 计划 V1 的 M1/M2/M4） |
| M-B | `emily_core/tools/node_task_tool.py` | 修改 | 5 个 schema 常量 |

---

## 5. M0: work_spec 输入模型

**依赖**：无（首建模块）

**职责**：WorkItem 新增 `work_spec` 字段；SessionAgent 在创建 WorkItem 时组装 work_spec（规则化，不调 LLM）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | WorkItem 新增 work_spec 字段 | `emily-core/emily_core/workitem/workitem.py` |
| 2 | SessionAgent 组装 work_spec | `emily-core/emily_core/session/session_agent.py` |

### 代码

#### `emily-core/emily_core/workitem/workitem.py` — 在 result_constraints 字段之后（第 57 行后）追加 work_spec 字段

```python
    # ── M0: Session 下发的工作要求（任务化指令）──
    work_spec: dict = field(default_factory=dict)
    """SessionAgent 组装的结构化工作要求，agent loop 据此执行（非用户原文）。

    Structure:
        objective: str       — 任务目标（如 "record_event"）
        sop_id: str          — 匹配的 SOP
        user_request: str    — 用户原始请求（上下文附注，非主指令）
        output_spec: dict    — 成果规格（intent/detail/format/data_fields）
        constraints: dict    — 成果约束（scope/must_include/must_not）
        required_tools: set  — 建议工具集
    """
```

#### `emily-core/emily_core/session/session_agent.py` — 新增 `_build_work_spec` 方法，并在所有 WorkItem 创建处调用

在 `_derive_constraints` 方法附近新增：

```python
    def _build_work_spec(self, intent: dict, sop_id: str, content: str,
                         output_spec: dict, constraints: dict,
                         required_tools: set | None = None) -> dict:
        """M0: 组装 work_spec（规则化，不调 LLM）。

        复用意图识别产出，把 user_input 原文 + 路由结果任务化为执行指令。
        """
        return {
            "objective": (output_spec or {}).get("intent", "") or sop_id or "fallback",
            "sop_id": sop_id or "",
            "user_request": (content or "")[:500],
            "output_spec": output_spec or {},
            "constraints": constraints or {},
            "required_tools": sorted(required_tools or set()),
        }
```

在所有 `WorkItem(...)` 创建后、`wi.output_spec = ...` 旁边追加 `wi.work_spec = ...`。涉及 4 处（第 550-555、562-567、572-577、587-597、601-610 行的 WorkItem 创建段），每处在设置 `wi.output_spec` / `wi.result_constraints` 之后追加：

```python
        wi.output_spec = self._derive_output_spec(intent, None)
        wi.result_constraints = self._derive_constraints(intent)
        wi.work_spec = self._build_work_spec(
            intent, wi.sop_id, content, wi.output_spec, wi.result_constraints,
            getattr(wi, "required_tools", set()))
```

> 复合任务（sub_tasks）的 WorkItem（第 587-597 行）同样在设置 output_spec/result_constraints 后追加 work_spec 组装，`content` 用 `st.get("user_input", content)`。

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/workitem/workitem.py', quiet=1); compileall.compile_dir('emily_core/session/session_agent.py', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：WorkItem 默认 work_spec 为空 dict
uv run python -c "
from emily_core.workitem.workitem import WorkItem
wi = WorkItem()
assert wi.work_spec == {}, f'expected empty dict, got {wi.work_spec}'
print('OK: work_spec field exists with default empty dict')
"

# 验收 3：_build_work_spec 组装正确
uv run python -c "
from emily_core.session.session_agent import SessionAgent
sa = SessionAgent.__new__(SessionAgent)
spec = sa._build_work_spec(
    {'output_spec': {'intent': 'record_event'}}, 'SOP-002-REC', '帮我记个事件',
    {'intent': 'record_event', 'detail': 'standard'}, {'scope': {'project': '翠湖庭院'}},
    {'record_event', 'resolve_project'})
assert spec['objective'] == 'record_event'
assert spec['sop_id'] == 'SOP-002-REC'
assert spec['user_request'] == '帮我记个事件'
assert spec['constraints']['scope']['project'] == '翠湖庭院'
assert spec['required_tools'] == ['record_event', 'resolve_project']
print('OK: work_spec assembled correctly')
"
```

**失败处理**：若验收 3 的 required_tools 顺序不符，检查 `sorted()` 是否应用。

---

## 6. M1: complete_work 控制工具 + agent loop 角色改造（核心）

**依赖**：M0（agent loop prompt 需要 work_spec）

**职责**：新增 complete_work 控制工具（含 ask_user spec）；改造 agent loop prompt 不直接回复用户、完成工作调 complete_work；tool_node 处理 complete_work 构造 StructuredResult 并路由 summarizing；type=text 兜底。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 新建 control_tools.py 定义 complete_work + ask_user spec | `emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py` |
| 2 | build_tool_specs 追加控制工具 spec | `emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py` |
| 3 | system prompt 角色改造 | `emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py` |
| 4 | tool_node 处理 complete_work + route_after_tool 改造 + type=text 兜底 | `emily-core/emily_core/workitem/langgraph_engine/agent/loop.py` |
| 5 | route_after_tool 边映射加 summarizing | `emily-core/emily_core/workitem/langgraph_engine/graph.py` |

### 代码

#### 交付物 1 — `emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py`（新建）

```python
# emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py
"""agent loop 控制工具 spec —— complete_work + ask_user。

这两个工具是 agent loop 的控制信号（非业务工具）：
- complete_work: LLM 完成工作后显式返回结构化成果 → 路由 summarizing
- ask_user: 信息不足时挂起反馈 → interrupt WAITING_FOR_INPUT

不经过 BusinessFlowToolRegistry / 权限过滤，由 build_tool_specs 直接追加给 LLM。
"""
from __future__ import annotations

# complete_work: 成果返回控制工具
COMPLETE_WORK_SPEC = {
    "type": "function",
    "function": {
        "name": "complete_work",
        "description": (
            "完成工作，向系统返回结构化成果。当所有必要工具调用已完成、工作要求已满足时，"
            "必须调用此工具返回成果，由上层组织给用户的回复。禁止用纯文本回复用户。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["success", "partial", "failed"],
                    "description": "工作完成状态",
                },
                "summary": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "关键成果事实（上层据此组织回复，每条≤100字）",
                },
                "data": {
                    "type": "object",
                    "description": "结构化成果数据（如 event_no、project_id 等）",
                },
                "business_object_no": {
                    "type": "string",
                    "description": "业务编号（如 EVT-xxx / TSK-xxx），无则空",
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "执行中遇到的问题（无则空数组）",
                },
                "needs_confirm": {
                    "type": "boolean",
                    "description": "成果是否需要用户确认（如拟录入单待确认）",
                },
            },
            "required": ["status", "summary"],
        },
    },
}

# ask_user: checkpoint 反馈控制工具
ASK_USER_SPEC = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "信息不足无法继续工作时调用此工具向用户提问（由上层转达）。"
            "仅用于缺少必填信息需用户补充的场景，不用于返回成果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题（清晰具体）",
                },
            },
            "required": ["question"],
        },
    },
}

CONTROL_TOOL_SPECS = [COMPLETE_WORK_SPEC, ASK_USER_SPEC]
CONTROL_TOOL_NAMES = {"complete_work", "ask_user"}
```

#### 交付物 2 — `emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py` — build_tool_specs 追加控制工具

在 `build_tool_specs` 函数末尾（return specs 之前，第 61 行 logger.info 之前）追加：

```python
    # 控制工具（complete_work / ask_user）始终可见——是 agent loop 的控制信号，
    # 非业务工具，不经过权限过滤
    from .control_tools import CONTROL_TOOL_SPECS
    specs.extend(CONTROL_TOOL_SPECS)

    resolver_count = len(list(resolvers.list_all()))
    control_count = len(CONTROL_TOOL_SPECS)
    logger.info("build_tool_specs: %d business tools + %d resolvers + %d control = %d specs",
                len(specs) - resolver_count - control_count, resolver_count,
                control_count, len(specs))
    return specs
```

> 删除原第 59-61 行的 `resolver_count = ...` + logger.info（被上方替换）。

#### 交付物 3 — `emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py` — system prompt 角色改造

替换 `build_system_prompt` 的签名（接收 work_spec）和 prompt 主体。**关键变更**：agent loop 不再"直接回复用户"，而是"调 complete_work 返回成果"。

新签名（第 16-23 行）：
```python
def build_system_prompt(
    sop_text: str,
    tool_specs: list[dict],
    session_ctx: Any,
    work_spec: dict | None = None,
    user_input: str = "",
    additional_input: str = "",
) -> str:
```

> `result_constraints` 参数移除（已包含在 work_spec.constraints 里）。调用方（loop.py）相应调整。

新 prompt 主体（替换第 69-96 行）：
```python
    # ── 工作要求（来自 SessionAgent，agent loop 的执行指令）──
    ws = work_spec or {}
    objective = ws.get("objective", "")
    constraints = ws.get("constraints", {})
    output_spec = ws.get("output_spec", {})
    user_request = ws.get("user_request", "") or user_input

    rc_text = ""
    if constraints:
        rc_text = f"\n\n【成果约束】\n{json.dumps(constraints, ensure_ascii=False, indent=2)}"

    cont_text = ""
    if additional_input:
        cont_text = (
            f"\n\n【续接上下文】\n用户上一轮补充：{additional_input}\n"
            f"请基于新信息继续，跳过已收集的字段。"
        )

    data_fields = output_spec.get("data_fields", [])
    data_fields_text = f"\n- 成果数据字段：{data_fields}" if data_fields else ""

    prompt = f"""你是 Emily 的工作执行引擎。你的职责是按工作要求调用工具获取/写入数据，**不要直接回复用户**。

# 你的工作方式（agent loop）
1. 阅读下方工作要求与 SOP 指导，明确要完成的目标
2. 查看可用工具表，选择合适工具
3. 若工具参数需要 UUID（如 project_id）但你只有名称，**必须先调 resolve_project 解析**，再填入业务工具
4. 调用工具后，查看 tool_result：成功则继续下一步；失败则根据错误自行调整重试
5. **完成工作要求后，必须调用 `complete_work` 工具返回结构化成果**（status/summary/data/business_object_no）——禁止用纯文本回复用户，回复由上层组织
6. **信息不足无法继续时，调用 `ask_user` 工具提问**（由上层转达用户）——不要用 complete_work 返回疑问

# 工作要求
- 目标：{objective or '（按 SOP 指导处理）'}
- 用户原始请求：{user_request}
- 成果规格：detail={output_spec.get('detail', 'standard')}, format={output_spec.get('format', 'natural')}{data_fields_text}

# 业务流指导（SOP）
{sop_text or '（未匹配到 SOP，按通用方式处理）'}

# 可用工具
{tools_text}

# 会话上下文
{ctx_text}

# 行为规则
- 工具参数中的 UUID 字段必须先调 resolver 解析，禁止把名称塞进 UUID 字段
- 看到 tool_result 报错时，分析原因并调整参数重试，不要原样重试
- **完成工作必调 complete_work，信息不足必调 ask_user，二者必居其一，不要返回纯文本**
- summary 字段是给上层组织回复的关键事实，应包含业务编号（如 EVT-xxx）和核心结论
{rc_text}{cont_text}
"""
    return prompt
```

#### 交付物 4 — `emily-core/emily_core/workitem/langgraph_engine/agent/loop.py` — complete_work 分支 + route_after_tool + type=text 兜底

**改动 4a**：agent_node 首轮构建 messages 时传 work_spec（替换第 74-81 行的 build_system_prompt 调用）：

```python
        system_prompt = build_system_prompt(
            sop_text=sop_text,
            tool_specs=tool_specs,
            session_ctx=session_ctx,
            work_spec=getattr(wi, "work_spec", {}) or {},
            user_input=wi.user_input,
            additional_input=getattr(wi, "additional_input", "") or "",
        )
```

**改动 4b**：tool_node 新增 complete_work 分支（在 ask_user 分支之后、resolver 分支之前，即第 200 行后插入）：

```python
    # ── complete_work 控制工具 → 构造 StructuredResult，路由 summarizing ──
    if tool_name == "complete_work":
        from ..pipeline.interfaces.execution import StructuredResult
        from ...pipeline.interfaces.routing import NoneType  # noqa: F401  (避免误导入)
        args = arguments or {}
        sr = StructuredResult(
            status=args.get("status", "success"),
            intent=(getattr(wi, "output_spec", {}) or {}).get("intent", wi.sop_id or "fallback"),
            sop_id=wi.sop_id or "",
            risk_level=getattr(wi, "risk_level", "L2") or "L2",
            data=args.get("data", {}) or {},
            summary_facts=[str(s) for s in args.get("summary", []) or []],
            rag_sources=[],
            business_object_no=args.get("business_object_no", "") or "",
            issues=[str(i) for i in args.get("issues", []) or []],
            needs_confirm=bool(args.get("needs_confirm", False)),
            error_category="" if args.get("status", "success") != "failed" else "system",
            suggested_followup="",
        )
        wi.structured_result = sr
        ctx.set("work_completed", True)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "成果已接收，工作完成。",
        })
        logger.info("tool_node complete_work: status=%s, object=%s",
                    sr.status, sr.business_object_no)
        return {"messages": messages, "wi_state": "summarizing", "_pending_tool_call": None}
```

> 注：删除上方 `from ...pipeline.interfaces.routing import NoneType` 那行占位（实际不需要），仅保留 StructuredResult 导入。正确插入如下（清理版）：

```python
    # ── complete_work 控制工具 → 构造 StructuredResult，路由 summarizing ──
    if tool_name == "complete_work":
        from ..pipeline.interfaces.execution import StructuredResult
        args = arguments or {}
        sr = StructuredResult(
            status=args.get("status", "success"),
            intent=(getattr(wi, "output_spec", {}) or {}).get("intent", wi.sop_id or "fallback"),
            sop_id=wi.sop_id or "",
            risk_level=getattr(wi, "risk_level", "L2") or "L2",
            data=args.get("data", {}) or {},
            summary_facts=[str(s) for s in args.get("summary", []) or []],
            rag_sources=[],
            business_object_no=args.get("business_object_no", "") or "",
            issues=[str(i) for i in args.get("issues", []) or []],
            needs_confirm=bool(args.get("needs_confirm", False)),
            error_category="" if args.get("status", "success") != "failed" else "system",
            suggested_followup="",
        )
        wi.structured_result = sr
        ctx.set("work_completed", True)
        messages.append({"role": "tool", "tool_call_id": tool_call_id,
                         "content": "成果已接收，工作完成。"})
        logger.info("tool_node complete_work: status=%s, object=%s",
                    sr.status, sr.business_object_no)
        return {"messages": messages, "wi_state": "summarizing", "_pending_tool_call": None}
```

**改动 4c**：agent_node 的 type=text 分支兜底转 complete_work（替换第 160-167 行）：

```python
    # type == "text" → LLM 未遵守 prompt（应调 complete_work）。兜底转为 StructuredResult
    content = result.get("content", "")
    messages.append({"role": "assistant", "content": content})
    wi.llm_call_count += 1
    logger.warning("agent_node got type=text (expected complete_work), "
                   "fallback-converting to StructuredResult: %s", content[:80])
    from ..pipeline.interfaces.execution import StructuredResult
    wi.structured_result = StructuredResult(
        status="success",
        intent=(getattr(wi, "output_spec", {}) or {}).get("intent", wi.sop_id or "fallback"),
        sop_id=wi.sop_id or "",
        risk_level=getattr(wi, "risk_level", "L2") or "L2",
        data={},
        summary_facts=[content[:200]] if content else [],
        rag_sources=[],
        business_object_no="",
        issues=[],
        needs_confirm=False,
        error_category="",
        suggested_followup="",
    )
    ctx.set("agent_final_reply", content)  # 兜底保留，供归档
    return {"messages": messages, "wi_state": "summarizing",
            "iteration_count": iteration_count + 1}
```

> 设计意图：prompt 强约束 LLM 调 complete_work，但若 LLM 仍返回纯文本，兜底转为 StructuredResult（status=success, summary=[文本]），避免卡死。SessionAgent 据此组织回复。

**改动 4d**：route_after_tool 改造（替换第 319-323 行）：

```python
def route_after_tool(state: dict) -> str:
    """tool_node 之后路由：complete_work → summarizing，否则 → agent_node 继续循环。"""
    state["_pending_tool_call"] = None
    if state.get("wi_state") == "summarizing":
        return "summarizing"
    return "agent_node"
```

#### 交付物 5 — `emily-core/emily_core/workitem/langgraph_engine/graph.py` — route_after_tool 边映射加 summarizing

替换第 77-81 行：
```python
    # tool_node → 条件路由（complete_work→summarizing / 否则→agent_node 循环）
    gs.add_conditional_edges(
        "tool_node",
        route_after_tool,
        {"agent_node": "agent_node", "summarizing": "summarizing"},
    )
```

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/workitem/langgraph_engine', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：控制工具 spec 可导入
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.control_tools import CONTROL_TOOL_SPECS, CONTROL_TOOL_NAMES
names = [s['function']['name'] for s in CONTROL_TOOL_SPECS]
assert 'complete_work' in names and 'ask_user' in names
assert CONTROL_TOOL_NAMES == {'complete_work', 'ask_user'}
# complete_work 必填字段
cw = [s for s in CONTROL_TOOL_SPECS if s['function']['name']=='complete_work'][0]
assert 'status' in cw['function']['parameters']['required']
assert 'summary' in cw['function']['parameters']['required']
print('OK: control tool specs valid')
"

# 验收 3：build_tool_specs 包含控制工具
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.tool_adapter import build_tool_specs
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry
from unittest.mock import MagicMock
reg = BusinessFlowToolRegistry()
specs = build_tool_specs(reg, MagicMock(list_all=lambda: []), set())  # 空 api_ids, 仅控制工具
names = [s['function']['name'] for s in specs]
assert 'complete_work' in names, f'complete_work missing: {names}'
assert 'ask_user' in names, f'ask_user missing: {names}'
print('OK: control tools in specs even with empty business tools')
"

# 验收 4：prompt 不再指示直接回复用户
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.prompt_builder import build_system_prompt
prompt = build_system_prompt('', [], None, {'objective':'record_event','user_request':'测试'}, '测试')
assert 'complete_work' in prompt, 'prompt must mention complete_work'
assert '不要直接回复用户' in prompt or '不直接回复用户' in prompt
# 旧的第 5 条 '直接回复用户' 不应作为指令出现
assert '完成后给出最终文本回复' not in prompt
print('OK: prompt role changed to use complete_work')
"
```

**失败处理**：
- 验收 3 失败：检查 tool_adapter.py 是否删除了原 resolver_count logger 并正确 extend CONTROL_TOOL_SPECS。
- 验收 4 失败：确认 prompt_builder.py 第 69-96 行已整体替换，旧的第 5/6 条指令已移除。

---

## 7. M2: StructuredResult 构造改造

**依赖**：M1（complete_work 已构造 StructuredResult）

**职责**：summarizing 节点的 `_extract_structured_result` 改为优先使用 complete_work 已构造的 StructuredResult，仅在未构造时（兜底）才从 step_results 提取。根治 status 误判。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `_extract_structured_result` 优先用 complete_work 成果 | `emily-core/emily_core/workitem/langgraph_engine/nodes.py` |

### 代码

#### `emily-core/emily_core/workitem/langgraph_engine/nodes.py` — summarizing 节点改用已构造的 StructuredResult

替换 [nodes.py:288](../../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L288) 的 summarizing 节点中 `wi.structured_result = _extract_structured_result(wi, ctx)` 行：

替换前（第 288 行）：
```python
            wi.structured_result = _extract_structured_result(wi, ctx)
```

替换后：
```python
            # M2: 优先使用 complete_work 已构造的 StructuredResult
            # agent loop 完成 work 后由 complete_work 控制工具构造（权威成果）；
            # 若缺失（异常路径），回退到 _extract_structured_result 从 step_results 提取
            if wi.structured_result is None:
                wi.structured_result = _extract_structured_result(wi, ctx)
                logger.info("summarizing: structured_result from fallback extraction, status=%s",
                            wi.structured_result.status if wi.structured_result else 'None')
            else:
                logger.info("summarizing: structured_result from complete_work, status=%s",
                            wi.structured_result.status)
```

> 设计意图：complete_work 构造的 StructuredResult 是权威成果（status 由 LLM 显式给定，不依赖 step_results 推断）。`_extract_structured_result` 保留作兜底（异常路径），但其 `if not step_results: status="failed"` 的误判问题在主路径不再触发。可选：后续清理 `_extract_structured_result` 的 status 判定，但本模块保留以降低风险。

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/workitem/langgraph_engine/nodes.py', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：summarizing 优先用已构造 StructuredResult（mock）
uv run python -c "
from unittest.mock import MagicMock
from emily_core.workitem.langgraph_engine.nodes import make_summarizing
from emily_core.workitem.pipeline.interfaces.execution import StructuredResult

# 模拟 complete_work 已构造 StructuredResult
wi = MagicMock()
wi.structured_result = StructuredResult(status='success', intent='record_event',
                                         summary_facts=['已记录 EVT-001'], business_object_no='EVT-001')
wi.step_results = []
wi.result_text = ''
wi.warnings = []
wi.result_constraints = {}

ctx = MagicMock()
ctx.work_item = wi
ctx.get = lambda k, d='': d
ctx.set = lambda k, v: None
ctx.verified_reply = ''

hook = MagicMock()
hook.fire_before = MagicMock(return_value=True)
hook.fire_after = MagicMock()
hook.fire_error = MagicMock()

summarizing = make_summarizing(hook)
import asyncio
asyncio.run(summarizing({}))
# 验证未覆盖 complete_work 的 StructuredResult
assert wi.structured_result.business_object_no == 'EVT-001', 'should keep complete_work result'
print('OK: summarizing preserves complete_work StructuredResult')
"
```

**失败处理**：若验收 2 覆盖了 StructuredResult，检查 summarizing 节点是否用了 `if wi.structured_result is None` 守卫。

---

## 8. M3: SessionAgent 回复合成回归原设计

**依赖**：M2（StructuredResult 正确构造）

**职责**：`_synthesize_final_reply` 回归"基于 StructuredResult 组织回复"的原设计。**移除 V1 的"单 WI 直通 result_text"方案**（方向错误）。`_render_wi_results` 已有结构化字段足够支撑 LLM 组织回复。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `_synthesize_final_reply` 回归基于 StructuredResult | `emily-core/emily_core/session/session_agent.py` |

### 代码

#### `emily-core/emily_core/session/session_agent.py` — `_synthesize_final_reply` 移除 V1 直通方案

**若 V1 的直通分支已实施**（单 WI + result_text 直通），删除该分支。**若 V1 未实施**（当前状态），本模块主要确保 `_synthesize_final_reply` 基于 StructuredResult 组织回复，并补充 result_constraints 校验。

当前 [session_agent.py:674-731](../../emily-core/emily_core/session/session_agent.py#L674-L731) 的 `_synthesize_final_reply` 已经是基于 StructuredResult 的（这是原设计）。本模块做两处增强：

**增强 3a**：在 `_synthesize_final_reply` 的 LLM 合成之后、返回之前，追加 result_constraints 校验（成果规则约束，决策要求）：

在第 725 行 `return reply` 之前插入校验：

```python
                if reply and len(reply) > 10:
                    # M3: 成果规则约束校验——检查回复是否满足 must_include / must_not
                    reply = self._enforce_result_constraints(reply, done_workitems)
                    # M4: review_reply 上移——审核合适性
                    await self._review_final_reply(reply, done_workitems)
                    return reply
```

**增强 3b**：新增 `_enforce_result_constraints` 方法（在 `_render_wi_results` 附近）：

```python
    def _enforce_result_constraints(self, reply: str, done_workitems: list) -> str:
        """M3: 成果规则约束校验——must_include 必须出现，must_not 不得出现。

        违规时追加提示，不删减回复（fail-open，避免过度干预）。
        """
        for wi in done_workitems:
            rc = getattr(wi, "result_constraints", {}) or {}
            must_include = rc.get("must_include", []) or []
            must_not = rc.get("must_not", []) or []
            for item in must_include:
                clean = item.replace("必须", "").replace("包含", "").strip()
                if clean and clean not in reply:
                    reply += f"\n（提示：{item}）"
            for item in must_not:
                clean = item.replace("不要", "").replace("别", "").strip()
                if clean and clean in reply:
                    reply += f"\n（注意：{item}）"
        return reply
```

> 设计意图：SessionAgent 对 WorkItem 返回的成果做约束校验（用户决策要求"对 workitem 返回的结果进行成果规则约束"）。校验是 fail-open 的（追加提示不删减），避免过度干预。

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/session/session_agent.py', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：_enforce_result_constraints 校验逻辑
uv run python -c "
from emily_core.session.session_agent import SessionAgent
sa = SessionAgent.__new__(SessionAgent)
wi = type('W', (), {'result_constraints': {'must_include': ['事件编号'], 'must_not': ['不要提费用']}})()
# must_include 缺失 → 追加提示
reply = sa._enforce_result_constraints('已记录事件', [wi])
assert '事件编号' in reply, f'should append must_include hint: {reply}'
# must_not 命中 → 追加注意
reply2 = sa._enforce_result_constraints('已记录事件，费用 100', [wi])
assert '不要提费用' in reply2 or '注意' in reply2, f'should warn must_not: {reply2}'
print('OK: result_constraints enforced')
"
```

**失败处理**：若 must_include 未追加提示，检查 `_enforce_result_constraints` 的 clean 逻辑是否过度清洗。

---

## 9. M4: ask_user spec + checkpoint 语义强化

**依赖**：M1（ask_user spec 已在 control_tools.py 定义并加入 build_tool_specs）

**职责**：ask_user 的 tool spec 已在 M1 补上（修复幽灵工具）。本模块确认 checkpoint 路径完整：SessionAgent 检测 WAITING_FOR_INPUT 转达 question（[session_agent.py:368-372](../../emily-core/emily_core/session/session_agent.py#L368-L372) 现状已正确），prompt 已在 M1 强化 ask_user 语义。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | checkpoint 路径验证（无代码改动，确认现状） | `emily-core/emily_core/session/session_agent.py` |

### 代码

无代码改动。M1 的 prompt_builder 已明确 ask_user 语义（"信息不足必调 ask_user，由上层转达"），control_tools.py 已补 ask_user spec。SessionAgent 的 [session_agent.py:368-372](../../emily-core/emily_core/session/session_agent.py#L368-L372) 已是"WorkItem 反馈 → SessionAgent 转达"模式。

### 模块验收检测

```bash
# 验收 1：ask_user spec 在 build_tool_specs 中（与 M1 验收 3 重合，确认未回归）
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.tool_adapter import build_tool_specs
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry
from unittest.mock import MagicMock
reg = BusinessFlowToolRegistry()
specs = build_tool_specs(reg, MagicMock(list_all=lambda: []), set())
names = [s['function']['name'] for s in specs]
assert 'ask_user' in names
print('OK: ask_user spec exposed to LLM')
"

# 验收 2：prompt 含 ask_user 语义强化
uv run python -c "
from emily_core.workitem.langgraph_engine.agent.prompt_builder import build_system_prompt
prompt = build_system_prompt('', [], None, {'objective':'test'}, 'test')
assert 'ask_user' in prompt
assert 'complete_work' in prompt
print('OK: prompt distinguishes complete_work vs ask_user')
"
```

---

## 10. M5: 注释 + docs 同步

**依赖**：M3（新契约确立后同步文档）

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | StructuredResult 注释更新 | `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` |
| 2 | workitem.py result_text/work_spec 注释 | `emily-core/emily_core/workitem/workitem.py` |
| 3 | docs 同步 | `docs/业务模块与运转全景.md` + `docs/接口协议与调用约定.md` |

### 代码

#### `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` — StructuredResult 注释（替换第 122-128 行）

```python
@dataclass
class StructuredResult:
    """WorkItem 回传给 Session 的结构化成果。

    LangGraph agent loop 职责重构后的定位：
    - **权威成果**由 agent loop 的 complete_work 控制工具显式构造（status/summary/data
      由 LLM 返回），存于 wi.structured_result。
    - SessionAgent 据此组织给用户的回复（_synthesize_final_reply），并做成果规则约束校验。
    - 归档（ArchiveHook）、审计（AuditHook）、回复审核（RealGuardian）均消费此对象。
    - checkpoint 请求不走此对象——ask_user 走 WAITING_FOR_INPUT 状态机，SessionAgent 直接转达。
    """
```

#### `emily-core/emily_core/workitem/workitem.py` — result_text 注释更新（第 75 行）

```python
    result_text: str = ""                # 兜底用（type=text 异常路径保留），正常路径成果在 structured_result
```

#### docs 同步要点（人工编写）

在 `docs/业务模块与运转全景.md` 补充"结果传递契约"章节：

> **SessionAgent / WorkItem 职责契约（V2）**：
> - SessionAgent 掌握对话上下文，分析意图并组装 work_spec（工作要求）下发给 WorkItem
> - WorkItem 按 work_spec 执行工具调用，不组织回复；完成工作调 complete_work 返回 StructuredResult
> - SessionAgent 基于 StructuredResult 组织回复 + 成果约束校验
> - checkpoint：WorkItem 调 ask_user → WAITING_FOR_INPUT → SessionAgent 转达 question → 用户续接

在 `docs/接口协议与调用约定.md` 追加约定：

> **约定 N：agent loop 不直接回复用户**：agent loop 的 LLM 只做工具决策，完成工作必须调 complete_work 返回成果，信息不足调 ask_user 挂起。type=text 兜底转为 StructuredResult。回复组织权在 SessionAgent。

### 模块验收检测

```bash
# 验收 1：旧注释已移除
cd d:\app\Emily\emily-core
grep -c "WorkItem 不做任何语言组织" emily_core/workitem/pipeline/interfaces/execution.py
→ 预期输出：0
grep -c "M3 后仅兜底用" emily_core/workitem/workitem.py
→ 预期输出：0
```

---

## 11. M-B: 工具 schema 补完（独立支线）

**依赖**：无（与 M0-M5 并行）

**职责**：修复 14 个业务工具注册时未传 `params=` schema 的问题。

### 执行要点

参照已有 [工具参数schema补完_计划_V1.md](工具参数schema补完_计划_V1.md)，但**删除其 M3**（`_build_params_summary` 已随 workitem_agent.py 删除）。执行其 M1（query_data）+ M2（13 file + 3 CRUD）+ M4（node_task 5 个）。

新架构下 schema 传递路径：`tool 源文件 (_XXX_SCHEMA) → registry.py params= → tool.parameters → build_tool_specs → LLM tools`。不再需要 `_build_params_summary` 摘要。

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/tools', quiet=1); print('OK')"

# 验收 2：启动 emily-core，SchemaGuard WARNING 清零
docker compose -f docker-compose-napcat.yml restart emily-core
sleep 12
docker logs emily-core 2>&1 | grep -c "SchemaGuard: 工具.*未提供 params schema"
→ 预期输出：0
```

---

## 12. M6: 端到端验收

**依赖**：M0 + M1 + M2 + M3 + M4 + M5 + M-B 全部完成

### 验收检测

```bash
# 验收 1：重启 emily-core 让改动生效（清 pycache）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
sleep 15

# 验收 2：确认真实用户 UUID
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status = 'active' AND username = '刘大勇';"
→ 预期：bf91685b-f452-4636-ac09-e36bc3ffb11c | 刘大勇

# 验收 3：重发测试消息
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "翠湖庭院住宅小区3号楼外墙涂料施工完成了，验收通过，帮我记个事件。" \
  --sender "刘大勇"
→ 预期：回复由 SessionAgent 基于 complete_work 成果组织，**不再出现"系统异常"**
→ 预期：确认单场景回复包含"拟录入"或"确认"等字样（LLM 调 complete_work 返回 needs_confirm=true，SessionAgent 据此组织）

# 验收 4：LLM 流量日志验证 agent loop 行为
docker exec mitmproxy tail -8 /app/logs/llm_trace.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        obj = json.loads(line)
        rb = json.loads(obj['request_body']) if isinstance(obj.get('request_body'), str) else obj.get('request_body',{})
        model = rb.get('model','')
        tools = rb.get('tools',[])
        tool_names = [t.get('function',{}).get('name','') for t in tools]
        resp = obj.get('response_body','')
        if isinstance(resp, str):
            try: resp = json.loads(resp)
            except: pass
        msg = resp.get('choices',[{}])[0].get('message',{}) if isinstance(resp, dict) else {}
        tc = msg.get('tool_calls')
        out = f'tool_call={tc[0][\"function\"][\"name\"]}' if tc else f'text={ (msg.get(\"content\",\"\") or \"\")[:60] }'
        print(f'model={model} has_complete_work={\"complete_work\" in tool_names} has_ask_user={\"ask_user\" in tool_names} → {out}')
    except: pass
"
→ 预期验证点：
# - agent loop 的 tool_specs 含 complete_work + ask_user
# - LLM 调 resolve_project → 调 complete_work（而非 type=text 直接回复）
# - 不再出现 v4-pro + msgs=2 的独立合成调用（SessionAgent 基于成果组织，上下文完整）

# 验收 5：emily-core 日志验证
docker logs --tail 60 emily-core 2>&1 | grep -E "complete_work|structured_result from|WI-.*DONE|status="
→ 预期：见 "complete_work: status=success" + "structured_result from complete_work" + WI DONE

# 验收 6：SchemaGuard WARNING 清零（M-B 生效）
docker logs emily-core 2>&1 | grep -c "SchemaGuard: 工具.*未提供 params schema"
→ 预期输出：0
```

### 验收判定标准

| 验收点 | 通过标准 |
|--------|----------|
| 用户回复 | 不含"系统异常"，由 SessionAgent 基于 complete_work 成果组织 |
| agent loop 行为 | LLM 调 complete_work 返回成果（而非 type=text 直接回复） |
| tool_specs | 含 complete_work + ask_user 控制工具 |
| StructuredResult | 来自 complete_work，status 不再误判 failed |
| LLM 调用 | 无 v4-pro 独立合成的"系统异常"调用 |
| SchemaGuard | 0 条 WARNING |

---

## 13. 风险与回滚

### 风险点

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 不遵守 prompt，不调 complete_work 而返回 type=text | 中 | 中 | M1 改动 4c 的 type=text 兜底转为 StructuredResult，避免卡死 |
| complete_work 的 summary 质量差，SessionAgent 组织的回复不如原 agent loop 回复 | 中 | 中 | M3 的 _enforce_result_constraints 校验 + review_reply 审核；prompt 强约束 summary 字段 |
| ask_user spec 暴露后 LLM 过度调用 ask_user | 低 | 中 | prompt 第 6 条"仅用于缺少必填信息"，且 ask_user 触发 interrupt 会挂起，有 _paused_workitem 续接机制兜底 |
| work_spec 组装在 4 处 WorkItem 创建遗漏 | 中 | 低 | M0 验收 3 + M6 端到端会暴露 |
| M2 保留 _extract_structured_result 兜底，其 status 误判仍在异常路径存在 | 低 | 低 | 主路径走 complete_work，异常路径 rare；后续可清理 |

### 回滚

模块独立提交。核心是 M1（complete_work 机制）。若 M6 验收失败：
1. 先检查 agent loop 是否正确调 complete_work（LLM 流量日志）
2. 若 LLM 不调 complete_work：回滚 M1 的 prompt 改造，保留 complete_work 工具但调整 prompt 措辞
3. 若 StructuredResult 仍误判：检查 M2 的 `if wi.structured_result is None` 守卫
4. 最坏情况：回滚 M1，保留 M0/M3/M-B（它们是独立改进）

---

## 14. 涉及文件总览

| 文件 | 变更类型 | 模块 |
|------|----------|------|
| `emily_core/workitem/workitem.py` | 修改 | M0, M5 |
| `emily_core/session/session_agent.py` | 修改 | M0, M3 |
| `emily_core/workitem/langgraph_engine/agent/control_tools.py` | **新增** | M1 |
| `emily_core/workitem/langgraph_engine/agent/tool_adapter.py` | 修改 | M1 |
| `emily_core/workitem/langgraph_engine/agent/prompt_builder.py` | 修改 | M1, M4 |
| `emily_core/workitem/langgraph_engine/agent/loop.py` | 修改 | M1 |
| `emily_core/workitem/langgraph_engine/graph.py` | 修改 | M1 |
| `emily_core/workitem/langgraph_engine/nodes.py` | 修改 | M2 |
| `emily_core/workitem/pipeline/interfaces/execution.py` | 修改 | M5 |
| `docs/业务模块与运转全景.md` | 修改 | M5 |
| `docs/接口协议与调用约定.md` | 修改 | M5 |
| `emily_core/tools/registry.py` | 修改 | M-B |
| `emily_core/tools/node_task_tool.py` | 修改 | M-B |

---

## 15. 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "V2.1 修订记录"，继续
   - 偏差 > 4 个文件或架构方向变化（如 complete_work 机制被否决）→ **停止**，报告给用户

---

*本计划为 AI 可执行操作手册。核心架构决策：回归 workitem.py:75-76 原设计意图——agent loop 不直接回复用户，完成工作调 complete_work 返回成果，SessionAgent 基于 StructuredResult 组织回复 + 成果约束校验，checkpoint 走 ask_user + WAITING_FOR_INPUT 明确区分。V1 的"单 WI 直通 result_text"方向作废。*
