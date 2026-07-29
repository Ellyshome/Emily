# BUG-04: LangGraph 引擎 agent loop 双重调用与状态持久化缺陷报告

> **发现日期**：2026-07-29
> **发现人**：AI 自动化测试
> **严重程度**：Critical（阻塞所有工具调用）
> **影响范围**：M6 LangGraph 执行引擎 — 所有需要调用工具的 WorkItem
> **状态**：已修复

---

## 1. 问题概述

LangGraph 统一生命周期图实施后，端到端测试发现工具调用完全不通。LLM 发起了 `resolve_project` 调用，但解析完成后没有继续调用业务工具（如 `record_event`、`query_data`），Agent 直接返回兜底错误文本。

排查发现 **7 个 bug**，其中 5 个为 Critical，各自独立可导致业务流断裂。

---

## 2. 根因分析

### Bug #1（Critical）：executing 节点双重调用 agent_node

**文件**：`emily_core/workitem/langgraph_engine/nodes.py:244`

**现象**：agent_node 的 LLM 调用报 DeepSeek 400 错误：
```
"An assistant message with 'tool_calls' must be followed by tool messages 
responding to each 'tool_call_id'."
```

**根因**：`executing` 节点内部直接调用了 `agent_node()` 函数（line 244），同时 `graph.py` 有一条图边 `executing → agent_node`：

```python
# nodes.py — executing 节点内部
async def executing(state):
    result = await agent_node(state, ...)  # ← 第一次调用
    return {**result}

# graph.py — 图边
gs.add_edge("executing", "agent_node")     # ← 第二次调用
```

执行流程变成：
```
executing 节点 → 内部调 agent_node → LLM 返回 tool_call → 
  agent_node 把 assistant(tool_calls) 追加到 messages → return
graph 边 executing→agent_node → agent_node 又被调用 →
  messages 现在是 [system, user, assistant(tool_calls)]
  → 没有 tool_result！→ LLM API 报 400
```

**修复**：executing 节点不再内部调用 agent_node，仅做 hook + 阶段标记：

```python
async def executing(state):
    # hooks only — agent_node 由 graph.py 的 executing→agent_node 边驱动
    await hook_adapter.fire_after("executing", ctx)
    return {"wi_state": "executing"}
```

---

### Bug #2（Critical）：`_pending_tool_call` 未被 LangGraph 持久化

**文件**：`emily_core/workitem/langgraph_engine/agent/loop.py:149`

**现象**：`route_after_agent` 永远看不到 tool_call，直接路由到 `summarizing`，工具从未被调用。

**根因**：agent_node 在 state dict 上做了 in-place 修改 `state["_pending_tool_call"] = {...}`，但 return dict 中没有包含此字段。LangGraph 只持久化 return dict 中显式声明的字段，in-place 修改被丢弃。

```python
# Bug: in-place 修改不会被 LangGraph 持久化
state["_pending_tool_call"] = {...}
return {"messages": messages, "wi_state": "executing"}  # ← 缺少 _pending_tool_call!
```

**修复**：显式 return `_pending_tool_call`：
```python
return {"messages": messages, "wi_state": "executing",
        "_pending_tool_call": state.get("_pending_tool_call"), ...}
```

同时 tool_node 的所有 return 也显式清除 `"_pending_tool_call": None`。

---

### Bug #3（Critical）：`_reg_biz()` 位置参数错位

**文件**：`emily_core/tools/registry.py:216-284`

**现象**：OpenAI API 报 400：
```
"Invalid schema for function 'record_event': 'business' is not of types 'boolean', 'object'"
```

**根因**：15 处 `_reg_biz()` 调用使用位置参数，`"business"` 字符串被传入了 `params` 形参：

```python
# 函数签名
def _reg_biz(reg, name, desc, handler, params=None, 
             category="business", permission_flag="write"):

# 调用（错误）
_reg_biz(reg, "record_event", "记录项目事件", handler, "business", "write")
#       ↑ reg     ↑ name         ↑ desc      ↑ handler ↑ params="business"!
#                                                          ↑ category="write"!
```

`params="business"`（一个字符串）被当作 JSON Schema 传给 OpenAI → API 校验失败。

**修复**：
1. 所有 `_reg_biz` 调用改用关键字参数：`category="business", permission_flag="write"`
2. `_reg_biz` 内部加防护：检测 params 为 str 时自动修正为 None

---

### Bug #4（Critical）：相对导入层级错误

**文件**：`emily_core/workitem/langgraph_engine/nodes.py:65,209`

**现象**：`ModuleNotFoundError: No module named 'emily_core.pipeline'`

**根因**：`nodes.py` 包深度为 3（`emily_core.workitem.langgraph_engine`），使用 `...pipeline`（3 个点）解析到 `emily_core.pipeline`。但 `pipeline` 模块在 `emily_core.workitem.pipeline`，应该是 `..pipeline`（2 个点）。

```
emily_core / workitem / langgraph_engine / nodes.py
    0           1            2           ← 包深度 = 3
..  = emily_core.workitem                        ✅
... = emily_core                                  ❌
```

**修复**：`...pipeline` → `..pipeline`

注：`agent/loop.py` 在更深一层（`agent/` 子包），它的 `...pipeline` 是正确的。

---

### Bug #5（Critical）：LLM fallback 误触发死循环

**文件**：`emily_core/infrastructure/llm/client.py:138`

**现象**：每次 LLM 调用失败时，触发 2 次 API 调用（先 tools 模式失败，再无 tools 模式失败），结合 error_analysis 无限重试 → **累计 10,000+ 条重复 API 调用**。

**根因**：
```python
if tools and "tools" in str(e).lower():   # ← "tools" 是 "tool_calls" 的子串！
    del kwargs["tools"]
    response = await self._client.chat.completions.create(**kwargs)  # 仍然 400
```

错误消息 `"An assistant message with 'tool_calls'..."` 包含子串 `"tools"`，误触发"tools 不支持"的 fallback 逻辑。但真正问题是 messages 格式错误，去不掉 tools 也一样报错。

**修复**：
```python
if tools and "tools " in str(e).lower() and "tool_calls" not in str(e).lower():
```

同时在 agent_node 异常处理中加 fail counter：连续 3 次 LLM 失败强制 abort。

---

### Bug #6（Moderate）：`tools_consistency.py` Skill YAML 残留

**文件**：`emily_core/infrastructure/tools_consistency.py`

**现象**：`check_all()` 引用未定义变量 `skills`（line 303），`check_quick()` 调用已删除的函数 `_check_skill_yaml()` / `_check_meta_tools_whitelist()` / `_check_dark_tools()`。

**修复**：删除 `_load_skills()` 函数，移除 `check_all()` 中 `skills` 字段，重写 `check_quick()` 仅保留 V14 schema map 检查。

---

### Bug #7（Minor）：`health()` 引用已删除的 `_bus`

**文件**：`emily_core/__init__.py:1069`

**现象**：`GET /api/v1/health` 返回 500：`'EmilyCore' object has no attribute '_bus'`

**修复**：`health()` 中 `self._bus.hook_count()` 改为 `self._graph is not None`

---

## 3. 影响汇总

| Bug | 影响 | 症状 |
|-----|------|------|
| #1 双重调用 | **工具调用全阻塞** | DeepSeek 400 "insufficient tool messages" |
| #2 state 丢失 | **工具从未执行** | route 直接跳到 summarizing |
| #3 参数错位 | **工具 schema 损坏** | OpenAI 400 "business is not of types" |
| #4 导入错误 | **summarizing 失败** | ModuleNotFoundError |
| #5 死循环 | **API 调用风暴** | 后台 10,000+ 条重复请求 |
| #6 残留引用 | 启动自检报错 | self_check 失败 |
| #7 _bus 引用 | health API 500 | 监控不可用 |

## 4. 验证状态

| 验证项 | 结果 |
|--------|------|
| LangGraph 图编译 | ✅ 通过 |
| agent_node → tool_node → agent_node 循环 | ✅ 通过 |
| resolve_project resolver 调用 | ✅ 通过（返回正确 project_id） |
| 业务工具调用 | ⚠️ LLM 在 resolve 后给出确认文本而非继续调业务工具（prompt 指导问题，非引擎 bug） |
| error_analysis 兜底 | ✅ 3 次失败后正确 abort |
| 简单问候 | ✅ 正常 |

## 5. 遗留问题

LLM 在调用 `resolve_project` 拿到 project_id 后，倾向于向用户确认而非继续调用业务工具。这是 prompt/SOP 指导问题，非引擎 bug。需要在 SOP .md 的 "Agent 调用指引" 中增加：

> "信息齐全时直接调用业务工具，无需向用户确认。仅在确实缺少关键字段时调用 ask_user。"

---

## 6. 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `emily_core/workitem/langgraph_engine/nodes.py` | 修复 #1, #4 — executing 不再内调 agent_node，修正相对导入 |
| `emily_core/workitem/langgraph_engine/agent/loop.py` | 修复 #2, #5 — 显式 return _pending_tool_call，加 fail counter |
| `emily_core/tools/registry.py` | 修复 #3 — _reg_biz 改用关键字参数 + 防护 |
| `emily_core/infrastructure/llm/client.py` | 修复 #5 — fallback 检测排除 "tool_calls" |
| `emily_core/infrastructure/tools_consistency.py` | 修复 #6 — 删除 Skill YAML 残留 |
| `emily_core/__init__.py` | 修复 #7 — health() 移除 _bus 引用 |
| `scripts/check_tools_consistency.py` | 修复 #6 — 移除 --skill-dir 参数 |
| `scripts/self_check.py` | 修复 #6 — check_quick() 不再传 skill_dir |

---

## 7. 教训

1. **LangGraph 的 state 更新规则**：只有 return dict 中的字段会被持久化。In-place 修改 `state["key"] = value` 必须同步出现在 return 中。
2. **不要在图边和节点内部重复调用同一逻辑**：节点内部不应直接调用下游节点函数，应交由图边驱动。
3. **位置参数 + 默认值的组合容易出错**：新增参数时应使用 keyword-only（`*, params=None`）强制调用方显式传参。
4. **fallback 检测逻辑应精确匹配**：子串匹配 `"tools" in "tool_calls"` 会导致误判。
