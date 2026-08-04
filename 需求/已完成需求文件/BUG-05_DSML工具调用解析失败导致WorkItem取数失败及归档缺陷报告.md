# BUG-05 DSML 工具调用解析失败导致 WorkItem 取数失败 + 错误分析归档缺陷报告

> **报告日期**：2026-07-30
> **触发场景**：手动两轮实战测试（翠湖庭院项目「会议纪要」「全景节点」查询）
> **归档日志**：`emily-data/session_archives/2026-07-30_李景利_12345600.md`（第 2/3 轮）
> **LLM 流量**：`emily-data/logs/llm_trace.jsonl`（13:59:50 那条）
> **严重级别**：P0（主路径查询全部失败）
> **状态**：已定位根因，未修复

---

## 0. 一句话结论

DeepSeek-V4-flash 返回了**新版 DSML 工具调用格式**（`<｜｜DSML｜｜tool_calls>`，双竖线 + "DSML" 标识），而 [client.py](../emily-core/emily_core/infrastructure/llm/client.py#L26-L36) 的降级解析正则只认**旧格式**（`<｜tool_calls｜>`，单竖线、无 DSML 标识）——三个正则全部失配 → 工具调用被当纯文本 → agent loop 3 次 text fallback 后 escalate → error_analysis 找不到 failed_step → 循环至硬上限强制 abort → **查询工具从未真正执行** → 回复"没有查到"。错误分析模块的"日志记录无有效信息"则是另外三层归档缺陷叠加。

两个问题同源连环：**DSML 解析失败（问题二主因）** 触发 error_analysis，而 **归档机制漏掉 agent_loop / error_analysis 两类 LLM 日志 + executing 段渲染时机错误 + wi.error_analysis 未生效（问题一）** 让失败现场在归档里几乎不可见。

---

## 1. 测试现象

### 1.1 第 2 轮（13:56:58）— 查翠湖庭院会议纪要

```
### 🔧 Agent 执行循环              ← 只有标题，无任何 LLM/工具调用记录
### 🔧 错误分析
- 错误类型: unknown
- 根因: 无
- 结果: 重试执行
- Prompt: ? (（未追踪）)
### 🤖 Emily
好的，我来查一下翠湖庭院住宅小区的会议纪要。目前系统中没有查到相关的会议纪要记录……
```

### 1.2 第 3 轮（13:59:31）— 查翠湖庭院全景节点

同上，"Agent 执行循环"段空标题 → "错误分析"段 `unknown / 无 / 重试执行` → 回复"没有查到相关的全景节点记录"。

### 1.3 用户反馈

- 错误分析模块日志记录还是没记录到有效信息
- 数据库已配置测试环境且有相关数据，WorkItem 没成功取得

---

## 2. 问题二（主因）：WorkItem 没有成功取得数据

### 2.1 根因：DSML 工具调用格式正则不匹配

#### 2.1.1 模型实际返回（LLM trace 原始内容，13:59:50）

模型 `deepseek-v4-flash`，`finish_reason=stop`，**无标准 `tool_calls` 字段**（`choice.message.tool_calls` 为空），`content` 为：

```
<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="query_data">
<｜｜DSML｜｜parameter name="query_type" string="true">nodes</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="project_id" string="true">9def49ba-f027-405a-946d-4b21367a47b9</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>
```

**Unicode 码点已确认**：每个 `｜` 均为 `U+FF5C`（FULLWIDTH VERTICAL LINE），与正则里用的是**同一个字符**——不是字符差异，是**格式结构差异**：
- 模型输出：`<` + `｜｜` + `DSML` + `｜｜` + `tool_calls` + `>`（双竖线，带 `DSML` 标识，参数标签带 `string="true"` 属性）
- 旧格式（注释所写）：`<` + `｜` + `tool_calls` + `｜` + `>`（单竖线，无 `DSML` 标识）

#### 2.1.2 正则期望（[client.py:26-36](../emily-core/emily_core/infrastructure/llm/client.py#L26-L36)）

```python
_DSML_TOOL_CALLS_RE = re.compile(r'<\｜tool_calls\｜?\s*>')                    # 单竖线，无 DSML
_DSML_INVOKE_RE     = re.compile(r'<\｜invoke\s+name\s*=\s*"([^"]+)"\｜?\s*>(.*?)</\｜invoke\｜?\s*>', re.DOTALL)
_DSML_PARAM_RE      = re.compile(r'<\｜parameter\s+name\s*=\s*"([^"]+)"[^>]*\｜?\s*>(.*?)</\｜parameter\｜?\s*>', re.DOTALL)
```

注释（[client.py:17-21](../emily-core/emily_core/infrastructure/llm/client.py#L17-L21)）明确写的是旧格式 `<｜tool_calls｜>`。

#### 2.1.3 失配分析

`_DSML_TOOL_CALLS_RE` = `<\｜tool_calls\｜?\s*>`，要求 `<` 后紧跟**一个** `｜` 再接 `tool_calls`；模型实际返回 `<` + `｜｜DSML｜｜` + `tool_calls`，正则匹配到第二个字符 `｜` 后期望 `t`，实际是第三个 `｜` → **不匹配**。同理 `_DSML_INVOKE_RE`、`_DSML_PARAM_RE` 也全部失配（双竖线 + `DSML` 标识 + `string="true"` 属性三重不匹配）。

### 2.2 后果链

1. [client.py:228-233](../emily-core/emily_core/infrastructure/llm/client.py#L228-L233)：`chat_messages` 走 DSML 检测分支 → `_try_parse_dsml_tool_call` 因正则失配返回 `None` → 返回 `{"type": "text", "content": "<｜｜DSML｜｜..."}`
2. [loop.py:165-205](../emily-core/emily_core/workitem/langgraph_engine/agent/loop.py#L165-L205)：`agent_node` 收到 `type=text` → text fallback 纠正（最多 3 次）→ 第 3 次 escalate 到 `error_analysis`，置 `state["error_analysis"]={"should_abort": True, "error_type": "transient_failure", "root_cause": "LLM 连续 3 次返回文本而非工具调用"}`
3. [error_analysis.py:93-97](../emily-core/emily_core/workitem/langgraph_engine/error_analysis.py#L93-L97)：`ErrorAnalyzer.analyze` 找不到 failed_step（resolver 工具不产生 step_results，text fallback 也不产生）→ 返回 `_build_result(TRANSIENT_FAILURE, should_retry=True)`
4. [nodes.py:364-396](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L364-L396) + [graph.py:106-111](../emily-core/emily_core/workitem/langgraph_engine/graph.py#L106-L111)：`should_abort=False` → `route_after_error` 回 `agent_node` 重试 → 同样 DSML 失败 → 循环至 `_error_analysis_count > 3` 强制 abort
5. **查询工具（query_data 等）从未被真正调用** → summarizing 用空 step_results 兜底 → 回复"没有查到"

### 2.3 docker 日志铁证（13:59:50，对应归档第 3 轮）

```
agent_node LLM result: type=text tool= content_preview=<｜｜DSML｜｜tool_calls>
agent_node got type=text (attempt 1/3), retrying with correction
agent_node LLM result: type=text tool= content_preview=<｜｜DSML｜｜tool_calls>
agent_node got type=text (attempt 2/3), retrying with correction
agent_node LLM result: type=text tool= content_preview=<｜｜DSML｜｜tool_calls>
agent_node: 3 consecutive text responses, escalating to error_analysis
error_analysis: no failed step found, fallback to transient
Scheduler[123456002] WI WI-fda95abb DONE
```

### 2.4 "数据库有数据但没取到"的澄清

数据库、工具、权限均正常。docker 日志 13:25:04 显示同会话早先一轮 `query_data` 成功查到 14 位用户：

```
tool_node business query_data result: {"success": true, "query_type": "user", "total": 14, "reply": "共有 14 位用户..."}
```

13:56 / 13:59 两轮的失败**不是数据库无数据、不是工具未注册、不是权限不足**，而是 DSML 解析失败导致 agent loop 根本没走到工具执行分支。

### 2.5 为什么第 1 轮 resolve_project 能成功

第 1 轮（首轮）模型返回了**标准 OpenAI tool_call**（`resolve_project` 是 resolver 工具），`choice.message.tool_calls` 非空，走 [client.py:185-222](../emily-core/emily_core/infrastructure/llm/client.py#L185-L222) 的标准分支。从第 2 轮起，模型切换到 DSML 文本输出格式，业务工具（query_data 等）全部调不上。

### 2.6 代码注释已标记为已知问题

[client.py:16-22](../emily-core/emily_core/infrastructure/llm/client.py#L16-L22)：

> DSML 工具调用泄漏检测（DeepSeek v4-flash 偶发将 tool_call 输出为文本）
> 优先级：可选防御——先配 agent_loop_model 切到 v4-pro，DSML 解析作为降级兜底

但降级兜底的正则与模型实际输出格式不匹配，兜底失效。`agent_loop_model` 是否已配置 v4-pro 待确认（见 §5）。

---

## 3. 问题一：错误分析模块日志记录无有效信息

**三层缺陷叠加**，不是单一原因。

### 3.1 缺陷 A：agent_loop / error_analysis 两类 LLM 调用被归档过滤掉

| 调用方 | call_category | 位置 |
|--------|--------------|------|
| agent_node | `agent_loop` | [loop.py:109](../emily-core/emily_core/workitem/langgraph_engine/agent/loop.py#L109) |
| ErrorAnalyzer | `error_analysis` | [error_analysis.py:191](../emily-core/emily_core/workitem/langgraph_engine/error_analysis.py#L191) |

ArchiveHook 的 `category_map`（[hook.py:369-375](../emily-core/emily_core/workitem/pipeline/hook.py#L369-L375)）：

```python
category_map = {
    "created": set(),
    "routing": set(),
    "executing": {"planning", "execution", "guardian"},   # 不含 agent_loop
    "summarizing": {"execution", "guardian"},
    "error_analysis": {"execution"},                       # 不含 error_analysis
}
```

`render_node_section` 的 `phase_labels`（[session_archive_writer.py:700-705](../emily-core/emily_core/services/session_archive_writer.py#L700-L705)）也只列 `intent / planning / execution / guardian` 四类。

**结果**：`agent_loop` 和 `error_analysis` 两类 LLM 日志既不被 ArchiveHook 选中、也不被渲染器显示 → "Agent 执行循环"段看不到 agent 的 LLM 调用，"错误分析"段看不到 ErrorAnalyzer 的 LLM 调用。这正是"日志记录没有有效信息"的直接原因之一。

### 3.2 缺陷 B：executing 段天然为空（架构时机问题）

- 图结构 `executing → agent_node`（[graph.py:68](../emily-core/emily_core/workitem/langgraph_engine/graph.py#L68)），executing 节点在 agent_node **之前**执行，本身只做 hook + 阶段标记（[nodes.py:261-275](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L261-L275)）
- ArchiveHook 在 executing 的 `fire_after` 渲染时，`step_results` 还没填充（agent_node 还没跑）
- agent_node / tool_node 不挂 ArchiveHook——[graph.py:122-149](../emily-core/emily_core/workitem/langgraph_engine/graph.py#L122-L149) 的 `_make_agent_loop_entry` / `_make_tool_loop_entry` 直接调 loop 函数，无 hook 包装
- LangGraph 模式下 `execution_plan` 也不再设置（agent loop 模式无独立规划阶段）

**结果**：executing 段落永远是空标题（无风险等级、无执行步骤、无工具调用、无 LLM 调用、无 Guardian）——归档里"Agent 执行循环"只剩一行 `### 🔧 Agent 执行循环`。

### 3.3 缺陷 C：error_analysis 段显示 unknown/无/重试执行（wi.error_analysis 未生效）

#### 现象与代码预期的矛盾

`render_node_section` 的 error_analysis 分支读 `wi.error_analysis`（[session_archive_writer.py:681-691](../emily-core/emily_core/services/session_archive_writer.py#L681-L691)）：

```python
ea = getattr(wi, "error_analysis", None) or {}
lines.append(f"- 错误类型: {ea.get('error_type', 'unknown')}")        # 显示 unknown → ea 无 error_type
lines.append(f"- 根因: {(ea.get('root_cause', '') or '无')[:200]}")   # 显示 无 → ea 无 root_cause
if ea.get("should_abort"):                                            # 显示 重试执行 → should_abort=False
    lines.append("- 结果: 中止执行")
else:
    lines.append("- 结果: 重试执行")
```

归档显示 `unknown / 无 / 重试执行` = **空 dict 的默认值**，即 `wi.error_analysis` 为空。

#### 按代码逻辑本应显示的内容

text fallback 超限路径（[loop.py:197-205](../emily-core/emily_core/workitem/langgraph_engine/agent/loop.py#L197-L205)）置 `state["error_analysis"]={"should_abort": True, "error_type": "transient_failure", "root_cause": "LLM 连续 3 次返回文本而非工具调用", ...}`。

[nodes.py:364-388](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L364-L388) 的 `make_error_analysis`：
- `state_ea = state.get("error_analysis", {})` → 含 `should_abort=True`
- `if state_ea.get("should_abort"): result = {**state_ea}` → result 含 `error_type=transient_failure`
- `_write_error_analysis_to_wi(ctx, result)` → `wi.error_analysis = result`
- `ctx.set("prompt_info_error_analysis", {"error_type": "transient_failure", "root_cause": "LLM 连续 3 次...", "should_abort": True})`

按此逻辑，归档应显示 `transient_failure / LLM 连续 3 次返回文本而非工具调用 / 中止执行`。**但实际显示 `unknown / 无 / 重试执行`**——说明 `wi.error_analysis` 赋值未生效，或 ArchiveHook 渲染时读到的 `wi` 与节点写入的不是同一对象。

#### 另一处矛盾（should_abort 与 WI DONE）

归档显示"重试执行"（`should_abort=False`），但 docker 日志显示 `WI DONE`（终态）。按 [graph.py:106-111](../emily-core/emily_core/workitem/langgraph_engine/graph.py#L106-L111) 的 `route_after_error`，`should_abort=False` 应回 agent_node 重试而非 END。这说明 error_analysis 实际执行了多轮（前几轮 `should_abort=False` 循环重试，最后一轮 `_error_analysis_count > 3` 强制 `should_abort=True` 才 END）。但归档只保留了一段"错误分析"——多轮 fire_after 的段落去向、以及为何读到的 `wi.error_analysis` 为空，需加日志确认。

#### 数据其实存对了，只是渲染器不读

`prompt_info_error_analysis`（[nodes.py:384-388](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L384-L388)）里存了正确的 `error_type/root_cause/should_abort`，但 `render_node_section` 的 error_analysis 分支只读 `wi.error_analysis`，**不读 `prompt_info`**（对比：routing/executing/summarizing 分支都会调 `_render_prompt_info(prompt_info)`，error_analysis 分支虽有调用但 `prompt_info` 里没有 `template/rendered_chars`，只显示 `Prompt: ? (（未追踪）)`，而真正的 error_type/root_cause 被忽略）。

#### WorkItem 无 error_analysis 字段

[workitem.py](../emily-core/emily_core/workitem/workitem.py) 的 `WorkItem` dataclass 全文无 `error_analysis` 字段。`_write_error_analysis_to_wi` 用动态属性赋值（`wi.error_analysis = result`，[nodes.py:321-328](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L321-L328)），dataclass 无 `__slots__`/frozen 理论上能成功，但归档读到空——**需实机调试确认赋值是否生效**（见 §5 验证步骤）。

---

## 4. 修复方向（未动手）

| # | 问题 | 修复点 | 文件 | 优先级 |
|---|------|--------|------|--------|
| 1 | DSML 正则失配（主因） | 三条正则兼容双竖线 + `DSML` 标识：`<｜｜DSML｜｜tool_calls>` / `<｜｜DSML｜｜invoke name="...">` / `<｜｜DSML｜｜parameter name="..." string="true">...</｜｜DSML｜｜parameter>`；parameter 还要兼容 `string="true"` 属性 | [client.py:26-36](../emily-core/emily_core/infrastructure/llm/client.py#L26-L36) | P0 |
| 2 | agent_loop 日志不归档 | `category_map["executing"]` 加 `"agent_loop"`；`phase_labels` 加 `agent_loop` | [hook.py:372](../emily-core/emily_core/workitem/pipeline/hook.py#L372)、[session_archive_writer.py:700-705](../emily-core/emily_core/services/session_archive_writer.py#L700-L705) | P1 |
| 3 | error_analysis 日志不归档 | `category_map["error_analysis"]` 加 `"error_analysis"` | [hook.py:374](../emily-core/emily_core/workitem/pipeline/hook.py#L374) | P1 |
| 4 | executing 段永远空 | agent_node/tool_node 末尾也触发归档（或 summarizing 段补齐 step_results + agent_loop 日志） | [graph.py:122-149](../emily-core/emily_core/workitem/langgraph_engine/graph.py#L122-L149) | P2 |
| 5 | error_analysis 段读到空 | 方案 A：改 `render_node_section` error_analysis 分支读 `prompt_info_error_analysis`（数据已正确）；方案 B：先加日志确认 `wi.error_analysis` 赋值未生效的根因再修；方案 C：给 `WorkItem` 显式加 `error_analysis: dict = field(default_factory=dict)` 字段 | [session_archive_writer.py:681-691](../emily-core/emily_core/services/session_archive_writer.py#L681-L691)、[workitem.py](../emily-core/emily_core/workitem/workitem.py)、[nodes.py:321-328](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L321-L328) | P1 |
| 6 | 兜底模型配置 | 确认 `agent_loop_model` 是否配 v4-pro；若配了仍泄漏 DSML，则正则修复为唯一出路 | [client.py:66](../emily-core/emily_core/infrastructure/llm/client.py#L66)、`emily-data/config/core_config.json` | P0 |

### 4.1 DSML 正则修复示例（最小改动，立刻见效）

```python
# 兼容旧格式 <｜tool_calls｜> 和新格式 <｜｜DSML｜｜tool_calls>
_DSML_PREFIX = r'<\｜+\s*(?:DSML\｜\s*)?'   # <｜ 或 <｜｜DSML｜
_DSML_TOOL_CALLS_RE = re.compile(_DSML_PREFIX + r'tool_calls\｜*\s*>')
_DSML_INVOKE_RE = re.compile(
    _DSML_PREFIX + r'invoke\s+name\s*=\s*"([^"]+)"\｜*\s*>(.*?)</\｜+\s*(?:DSML\｜\s*)?invoke\｜*\s*>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    _DSML_PREFIX + r'parameter\s+name\s*=\s*"([^"]+)"[^>]*\｜*\s*>(.*?)</\｜+\s*(?:DSML\｜\s*)?parameter\｜*\s*>',
    re.DOTALL,
)
```

（示例仅示意思路，需用 trace 真实样本回归验证后再合入。）

---

## 5. 待确认项与验证步骤

### 5.1 agent_loop_model 配置

查 `emily-data/config/core_config.json` 是否配了 `agent_loop_model`（v4-pro）。
- 若未配 → 配上可绕开 DSML（但 v4-pro 成本上升、且 reasoner 不支持 temperature）
- 若配了仍出 DSML → 说明 v4-pro 也泄漏，正则修复是唯一出路

### 5.2 缺陷 C 的 wi.error_analysis 矛盾（需加临时日志）

在 `_write_error_analysis_to_wi`（[nodes.py:321-328](../emily-core/emily_core/workitem/langgraph_engine/nodes.py#L321-L328)）和 ArchiveHook（[hook.py:345-405](../emily-core/emily_core/workitem/pipeline/hook.py#L345-L405)）各加一行日志：

```python
# _write_error_analysis_to_wi 里
logger.warning("EA_WRITE wi_id=%s id(wi)=%x ea=%s", wi.id, id(wi), result)

# ArchiveHook.execute 里（error_analysis 段）
logger.warning("EA_READ stage=error_analysis id(wi)=%x ea=%s",
               id(context.work_item), getattr(context.work_item, "error_analysis", None))
```

复测一次，对比 `id(wi)` 是否一致、`ea` 是否为 `None`/空/正确 dict，即可定位是赋值未生效、对象不同、还是多轮覆盖。

### 5.3 端到端验证

修复 DSML 正则后，用 emy-test 复测：
```powershell
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下翠湖庭院项目有哪些全景节点" --sender "李景利"
```
预期：agent_node 第 2 轮 DSML 被解析为 tool_call → tool_node 执行 query_data → 返回真实节点数据。同步查 docker 日志确认 `tool_node business query_data result: {...total: N...}`，查归档确认"Agent 执行循环"段出现工具调用记录。

---

## 6. 关联文档与记忆

- PRD 背景：`需求/WorkItem_LangGraph全迁移_PRD_V1.md`
- 前序缺陷：`需求/BUG-04_LangGraph引擎agent_loop双重调用与状态持久化缺陷报告.md`
- 记忆：`[[workitem-langgraph-l3-migration]]`、`[[bug-01-error-analysis-gap]]`
- 踩坑：CLAUDE.md §9「LLM 不可用时自然降级」「emy-test 禁用假 sender-id」
