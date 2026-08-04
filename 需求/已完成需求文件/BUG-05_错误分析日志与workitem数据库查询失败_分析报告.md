# BUG-05: 错误分析日志记录为空 + WorkItem 数据库查询失败 — 分析报告

> **发现日期**：2026-07-30
> **测试人**：李景利
> **测试日志**：`emily-data/session_archives/2026-07-30_李景利_12345600.md`
> **测试轮次**：第 2 轮（会议纪要查询）、第 3 轮（全景节点查询）
> **严重程度**：Critical（两轮查询均返回空结果，工具调用完全失败）

---

## 一、问题概述

手动测试中发现两个独立但关联的 bug：

1. **错误分析节点日志记录为空**：归档文件中 `### 🔧 错误分析` 段显示 `错误类型: unknown`、`根因: 无`、`Prompt: ? (（未追踪）)`——没有任何有效信息
2. **WorkItem 数据库查询完全失败**：用户请求"查会议纪要"和"查全景节点"，Emily 回复"没有查到相关记录"，但数据库已配置测试环境有此数据，实际上是 LLM **从未调用查询工具**，凭空编造了"无记录"的结论

---

## 二、错误分析日志问题（Bug A / B / C）

### Bug A：`_write_error_analysis_to_wi` 静默失败

**位置**：[nodes.py#L324-L328](emily-core/emily_core/workitem/langgraph_engine/nodes.py#L324-L328)

```python
def _write_error_analysis_to_wi(ctx, result: dict) -> None:
    wi = getattr(ctx, "work_item", None)
    if wi is not None:
        try:
            wi.error_analysis = result
        except Exception:
            pass  # 静默吞掉所有异常 ← BUG
```

**根因**：
- WorkItem 是 `@dataclass`，**未声明 `error_analysis` 字段**
- 虽然大多数 dataclass 支持运行时动态属性赋值，但 `try/except: pass` 把任何可能的失败都吞掉了
- 如果赋值失败，`wi.error_analysis` 就是 `None`，ArchiveHook 读到空值 → `error_type=unknown`、`root_cause=无`

**后果**：归档文件中的错误分析段永远是 `unknown / 无`，无法诊断生产问题。

---

### Bug B：Agent Loop 的 LLM 日志 call_category 不匹配，全部被丢弃

**位置 1**：[loop.py#L107](emily-core/emily_core/workitem/langgraph_engine/agent/loop.py#L107) — LLM 调用日志标记

```python
LLMInteractionLogger.set_context(
    call_category="agent_loop",  # ← 使用此类别
)
```

**位置 2**：[hook.py#L382-L387](emily-core/emily_core/workitem/pipeline/hook.py#L382-L387) — ArchiveHook 类别过滤器

```python
category_map = {
    "executing": {"planning", "execution", "guardian"},  # ← 不含 "agent_loop"
    "error_analysis": {"execution"},                      # ← 不含 "agent_loop"
}
```

**根因**：agent loop 使用 `call_category="agent_loop"` 写 LLM 日志，但 ArchiveHook 的类别白名单只有 `planning / execution / guardian`。`"agent_loop"` 不在任何集合中 → **所有 agent loop 的 LLM 交互日志全部被丢弃**。

**后果**：归档文件的 `### 🔧 Agent 执行循环` 和 `### 🔧 错误分析` 段下面一片空白，看不到任何 LLM 调用记录，看不到工具调用了什么、有没有调用工具、错误是什么。

---

### Bug C：`prompt_info` 可能未到达 ArchiveHook

**位置**：[nodes.py#L388-L392](emily-core/emily_core/workitem/langgraph_engine/nodes.py#L388-L392)

```python
ctx.set("prompt_info_error_analysis", {
    "error_type": result.get("error_type", "unknown"),
    "root_cause": (result.get("root_cause", "") or "")[:200],
    "should_abort": result.get("should_abort", False),
})
```

**位置**：[hook.py#L387](emily-core/emily_core/workitem/pipeline/hook.py#L387)

```python
prompt_info = context.baggage.get(f"prompt_info_{stage}", None)
```

**根因**：节点函数通过 `ctx.set()` 写入，ArchiveHook 通过 `context.baggage.get()` 读取。需要确认两者是否是同一存储层。如果 `ctx.set()` 写到对象属性而非 `baggage` 字典，ArchiveHook 就无法读到 → 显示 `Prompt: ? (（未追踪）)`。

---

## 三、WorkItem 数据库查询失败问题（Bug D）

### 完整根因链

```
用户请求 "查翠湖庭院会议纪要"
  ↓
SessionAgent 意图识别正确 → sop=SOP-005-QRY, output_spec.intent=query_meeting_summary
  ↓
进入 LangGraph agent loop → build_tool_specs()
  ↓
session_api_ids 为空（tool_registry 表中该用户无可用的业务工具记录）
  ↓
tool_adapter.py#L36-L38：session_api_ids 为空 → fail-closed
  ↓
仅暴露 resolver（resolve_project）+ 控制工具（complete_work, ask_user）
query_data 工具 NOT 暴露给 LLM
  ↓
LLM 没有可用的查询工具 → 只能用纯文本回复 "好的，我来查..."
  ↓
text fallback 机制拦截 3 次 → 升级到 error_analysis
  ↓
ErrorAnalyzer._find_failed_step()：wi.step_results 为空（从未成功调用任何工具）
  ↓
找不到失败步骤 → 返回 transient_failure 兜底
  ↓
error_analysis 不 abort → 回到 agent_node 继续循环
  ↓
最终 LLM "编造"回复："目前系统中没有查到相关的会议纪要记录"
```

### 核心 Bug D：`session_api_ids` 为空导致 fail-closed

**位置**：[tool_adapter.py#L36-L38](emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py#L36-L38)

```python
if not session_api_ids:
    logger.warning("session_api_ids 为空，tool_registry 表可能未填充，fail-closed")
    # fail-closed：仅暴露 resolver，不暴露任何业务工具
```

**根因**：`session_api_ids` 来自 `SessionContext.available_tools`，而这个集合来源于 `tool_registry` 数据库表中该用户的工具授权记录。如果表未迁移、数据未初始化、或用户没有对应权限，`session_api_ids` 为空 → 所有业务工具（`query_data`、`record_event` 等）全部不暴露 → LLM 无法调用任何业务工具 → 所有业务查询/写入全部失败。

**旁证**：日志中 `- 关键变量: authorized_node_ids=（无）` 也暗示权限数据可能未正确初始化。

---

## 四、关联文件清单

| 文件 | Bug | 说明 |
|------|-----|------|
| [nodes.py#L324-L328](emily-core/emily_core/workitem/langgraph_engine/nodes.py#L324-L328) | A | `_write_error_analysis_to_wi` 静默吞异常 |
| [nodes.py#L388-L392](emily-core/emily_core/workitem/langgraph_engine/nodes.py#L388-L392) | C | `prompt_info` 写入路径 |
| [loop.py#L107](emily-core/emily_core/workitem/langgraph_engine/agent/loop.py#L107) | B | `call_category="agent_loop"` |
| [hook.py#L382-L387](emily-core/emily_core/workitem/pipeline/hook.py#L382-L387) | B | `category_map` 缺少 `"agent_loop"` |
| [hook.py#L387](emily-core/emily_core/workitem/pipeline/hook.py#L387) | C | ArchiveHook 读 `prompt_info` |
| [tool_adapter.py#L36-L38](emily-core/emily_core/workitem/langgraph_engine/agent/tool_adapter.py#L36-L38) | D | `session_api_ids` 为空 → fail-closed |
| [workitem.py#L24-L107](emily-core/emily_core/workitem/workitem.py#L24-L107) | A | WorkItem 未声明 `error_analysis` 字段 |
| [error_analysis.py#L100](emily-core/emily_core/workitem/langgraph_engine/error_analysis.py#L100) | D | `_find_failed_step` 找不到失败步骤 |
| [session_archive_writer.py#L687](emily-core/emily_core/services/session_archive_writer.py#L687) | A | `render_node_section` 读 `wi.error_analysis` |

---

## 五、修复建议

| 优先级 | Bug | 修复方案 |
|--------|-----|----------|
| **P0** | D — `session_api_ids` 为空 | 检查 `tool_registry` 表是否已运行 `scripts/register_api.py` 脚本初始化；确认 `SessionContext.available_tools` 是否正确填充；考虑在日志中加 WARNING 级提示 |
| **P1** | A — `_write_error_analysis_to_wi` | (1) 在 WorkItem dataclass 中显式声明 `error_analysis: dict = field(default_factory=dict)`；(2) 去除 `try/except: pass`，让错误能被上层捕获 |
| **P1** | B — Agent loop 日志不可见 | 在 ArchiveHook 的 `category_map` 中增加 `"agent_loop"` 到 `executing` 和 `error_analysis` 的类别集合 |
| **P2** | C — prompt_info 未传递 | 确认 `BusContext.set()` 与 `BusContext.baggage` 使用同一存储；如不同，统一路径 |

---

## 六、测试环境复现条件

- 数据库：已配置测试数据（翠湖庭院项目有会议纪要和全景节点）
- 用户：李景利
- `tool_registry` 表：可能为空或未初始化（`session_api_ids` 为空）
- 启用 LangGraph 引擎（agent loop 模式）
