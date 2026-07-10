# 日志系统收束 — 验收 Bug 报告

> 版本：V1 | 日期：2026-07-10 | 状态：待修复
> 关联：[日志系统收束_计划_V1.md](日志系统收束_计划_V1.md) | [系统进化日志统一收束需求_V1.md](系统进化日志统一收束需求_V1.md)

---

## 概述

验收过程中发现 **2 个集成缺陷**，导致核心需求——通过 `pipeline_run_id` 串联 Emily 完整请求链路——未能实现。

---

## Bug #1: LLM 交互日志缺少 pipeline_run_id

| 属性 | 内容 |
|------|------|
| **严重度** | 🔴 高 |
| **影响模块** | M4: LLM 交互日志 |
| **影响范围** | `evolution_llm_interaction_logs` 表中所有记录的 `pipeline_run_id` 字段均为空 |

### 现象

`evolution_llm_interaction_logs` 表已正确写入 9 条 LLM 调用记录（含 `total_tokens`、`latency_ms`、`call_category` 等字段），但 `pipeline_run_id`、`conversation_id`、`user_id` 三列全部为空字符串。

```sql
-- 当前状态（pipeline_run_id 全部为空）
SELECT pipeline_run_id, call_category, total_tokens, latency_ms
FROM evolution_llm_interaction_logs ORDER BY created_at DESC LIMIT 3;

 pipeline_run_id | call_category | total_tokens | latency_ms
-----------------+---------------+--------------+------------
                 | execution     |          256 |       2299
                 | intent        |          361 |        777
                 | intent        |          477 |       1421
```

### 根因

`LLMInteractionLogger.set_context()` 方法已定义但**从未被调用**。

- [llm_logger.py L27](file:///d:/app/Emily/emily-core/emily_core/infrastructure/logging/llm_logger.py#L27) — `set_context()` 方法定义，负责设置 `pipeline_run_id` / `conversation_id` / `user_id` / `call_category`
- [llm_logger.py L53](file:///d:/app/Emily/emily-core/emily_core/infrastructure/logging/llm_logger.py#L53) — `_on_llm_call_end()` 从 `cls._current_context` 读取上下文写入日志
- 全局搜索 `set_context` — **仅定义处出现，无任何调用方**

LLM trace callback 已正确注册（`__init__.py:142`），但 Pipeline 执行前未注入上下文，callback 触发时 `_current_context` 始终为空字典。

### 修复建议

在 Pipeline 执行前调用 `LLMInteractionLogger.set_context(...)`，最合理的插入点是 `PipelineBUS.run()` 方法开始时：

```python
# 在 bus.py 的 run() 方法中，记录 started_at 之后追加
from ...infrastructure.logging.llm_logger import LLMInteractionLogger
LLMInteractionLogger.set_context(
    pipeline_run_id=context.pipeline_run_id,
    conversation_id=context.message.conversation_id if context.message else "",
    user_id=context.user_id or "",
    call_category="",  # 由 callback 根据 call_type 推断
)
```

同时在 `run()` 方法末尾（finally 块或返回前）调用 `LLMInteractionLogger.clear_context()` 清理上下文。

---

## Bug #2: 业务事件日志缺少 pipeline_run_id

| 属性 | 内容 |
|------|------|
| **严重度** | 🔴 高 |
| **影响模块** | M5: 业务事件日志 |
| **影响范围** | `business_event_logs` 表中所有记录的 `pipeline_run_id` 字段均为空 |

### 现象

`business_event_logs` 表已正确写入 2 条业务事件记录（`event_category=event`，`event_action=created`），但 `pipeline_run_id` 为空。

```sql
-- 当前状态（pipeline_run_id 为空）
SELECT pipeline_run_id, event_category, event_action, summary
FROM business_event_logs ORDER BY created_at DESC LIMIT 2;

 pipeline_run_id | event_category | event_action |          summary
-----------------+----------------+--------------+----------------------------
                 | event          | created      | 创建事件：日志系统测试验证
                 | event          | created      | 创建事件：日志系统测试验证
```

### 根因

Application 层在调用 `BusinessEventLogger.log()` 时未传入 `pipeline_run_id`。

涉及文件（均未传 `pipeline_run_id`）：
- [event_app.py](file:///d:/app/Emily/emily-core/emily_core/application/event_app.py)
- [meeting_app.py](file:///d:/app/Emily/emily-core/emily_core/application/meeting_app.py)
- [task_app.py](file:///d:/app/Emily/emily-core/emily_core/application/task_app.py)

当前调用模式为 `await BusinessEventLogger.log(**kwargs)`，kwargs 中不含 `pipeline_run_id`。

### 修复建议

有两种方案：

**方案A（推荐）**：从 `WorkItem` 或 `BusContext` 获取当前 `pipeline_run_id`，传入 `log()` 调用：

```python
# 在各 Application handler 中
pipeline_run_id = get_current_pipeline_run_id()  # 从线程局部或上下文获取
await BusinessEventLogger.log(
    pipeline_run_id=pipeline_run_id or "",
    event_category="event",
    event_action="created",
    ...
)
```

**方案B**：由 `BusinessEventLogger` 内部自动获取当前活跃的 `pipeline_run_id`（类似 `LLMInteractionLogger._current_context` 模式），但这需要额外的上下文管理机制。

---

## 影响评估

### 直接后果

无法执行计划文档中定义的端到端关联查询，`pipeline_run_id` 作为"脊椎"的串联功能缺失：

```sql
-- 目标查询（目前无法得到正确结果）
SELECT
  pel.final_status, pel.elapsed_ms,
  lil.call_category, lil.total_tokens,
  bel.event_category, bel.event_action
FROM pipeline_execution_logs pel
LEFT JOIN evolution_llm_interaction_logs lil ON lil.pipeline_run_id = pel.pipeline_run_id
LEFT JOIN business_event_logs bel ON bel.pipeline_run_id = pel.pipeline_run_id
WHERE pel.user_id = 'user-dev1'
ORDER BY pel.created_at DESC;
```

### 不受影响的部分

- `pipeline_execution_logs` 的 `pipeline_run_id` 正常写入
- `hook_execution_logs` 的增强字段正常写入
- `session_lifecycle_logs` 正常写入
- LLM token/latency 统计数据正常写入（仅关联字段缺失）
- 业务事件摘要正常写入（仅关联字段缺失）

---

## 修复优先级

| 优先级 | Bug | 理由 |
|--------|-----|------|
| **P0** | Bug #1 — LLM 日志缺 pipeline_run_id | pipeline_run_id 的核心串联作用，LLM 日志是成本追踪的关键 |
| **P0** | Bug #2 — 业务事件日志缺 pipeline_run_id | 同上，业务事件需要关联到具体 Pipeline 请求 |

两个 Bug 建议**一起修复**，因为它们共享相同的根因模式（上下文未传递），且都涉及 `pipeline_run_id` 注入。

---
*本报告由验收测试自动生成，待开发者确认并修复。*
