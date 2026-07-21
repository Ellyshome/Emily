# Agent 追踪写入失败 Bug 报告

**日期**: 2026-07-22  
**发现场景**: 刘大勇 (sender_id=123456009) 测试会话的全链路回溯查询  
**严重程度**: 中 —— 不阻塞主流程，但导致 Agent 执行追踪数据全部丢失

---

## 问题概述

`agent_reasoning_logs` 和 `llm_interaction_logs` 表的 INSERT 操作全部因外键约束违反（`ForeignKeyViolation`）而失败，仅有 `tool_call_logs` 能成功写入。导致 Agent 执行过程无法通过 `get_complete_agent_trace(message_id)` 做全链路回溯。

---

## 复现路径

1. 用户（刘大勇，sender_id=123456009）发送私聊消息
2. Emily 接管消息，Session 创建（conv=123456009）
3. Pipeline 4 节点执行 WorkItem `WI-3ffd5301`
4. Pipeline 完成后，TraceHook 尝试写入追踪数据
5. INSERT 失败，日志输出 `ForeignKeyViolation`

---

## 根因分析

### 直接原因

`agent_reasoning_logs.message_id` 和 `llm_interaction_logs.message_id` 在 INSERT 时均为**空字符串**，而这两个字段有外键约束指向 `messages.id`：

```sql
message_id = Column(String, ForeignKey("messages.id"), nullable=False)
```

空字符串在 `messages` 表中不存在任何匹配记录，因此 PostgreSQL 拒绝 INSERT。

### 上游原因

Pipeline 上下文中的 `context.db_message_id` 未被正确赋值或传递到 TraceHook。TraceHook 在 `trace.reasoning_start` 阶段调用 `create_reasoning_log(message_id=context.db_message_id)`，但此时 `db_message_id` 为空。

**证据**（来自 `emily_20260722.log` 第 500-510 行）：

```
ForeignKeyViolation: insert or update on table "agent_reasoning_logs" 
  violates foreign key constraint "agent_reasoning_logs_message_id_fkey"
DETAIL: Key (message_id)=() is not present in table "messages".

-- 写入参数中 message_id 为空字符串:
'message_id': ''
```

### 为什么 tool_call_logs 成功写入？

`tool_call_logs` 表的外键字段 `reasoning_log_id` 和 `llm_interaction_id` 定义为 `nullable=True`，允许 NULL 值，因此不受上游写入失败的影响。

---

## 影响范围

| 表 | 影响 | 说明 |
|---|---|---|
| `agent_reasoning_logs` | **全部丢失** | 推理日志（路由决策、迭代次数、步骤明细、执行结果）无法写入 |
| `llm_interaction_logs` | **全部丢失** | LLM 调用日志（token 消耗、延迟、响应类型）无法写入 |
| `tool_call_logs` | 部分写入 | 能写入，但 `reasoning_log_id` 和 `llm_interaction_id` 为 NULL，无法关联到推理链路 |

**后果**：`get_complete_agent_trace(message_id)` 查询返回 `{"found": False}`，全链路回溯能力完全失效。

---

## 排查过程记录

### 数据库查询

```
messages 总记录数: 26
agent_reasoning_logs 总记录数: 0
llm_interaction_logs 总记录数: 0
tool_call_logs 总记录数: 1
```

唯一的 `tool_call_log` 记录：
```
tool_name: query_my_nodes
tool_arguments: {"_user_id": "5382454e-...", "_conversation_id": "123456009"}
tool_result_summary: {'success': True, 'reply': '找到 0 个节点', 'data': []}
reasoning_log_id: None
llm_interaction_id: None
```

### 日志关键行

| 行号 | 时间 | 事件 |
|------|------|------|
| 420 | 16:59:32 | 刘大勇消息进入，takeover=true |
| 474 | 16:59:35 | Session 创建 conv=123456009 |
| 489 | 16:59:37 | 意图识别：sop=None, fallback=True |
| 490 | 16:59:37 | Pipeline 启动 WorkItem WI-3ffd5301 |
| 500 | 16:59:41 | **agent_reasoning_logs INSERT 失败 (FK violation)** |
| 506 | 16:59:41 | **llm_interaction_logs INSERT 失败 (FK violation)** |
| 512 | 16:59:41 | WorkItem 完成 |

---

## 修复建议

1. **确保 `db_message_id` 在 Pipeline 启动前已有值**：在 Message 入库后、Pipeline 启动前，将 `message.id` 写入 `PipelineContext.db_message_id`
2. **在 TraceHook 中增加防御性检查**：若 `message_id` 为空，跳过本次追踪写入并记录 warning，而非尝试 INSERT 空值
3. **考虑将 `message_id` 外键设为可空**（降低约束强度），避免追踪失败阻塞 Pipeline（但这会弱化数据完整性）
4. **增加集成测试**：覆盖 "从消息入站到追踪落库" 的完整链路

---

## 当前状态

- [ ] 问题待修复
- [ ] 修复后需重新触发 Agent 执行以验证追踪数据正常落库
- [ ] 验证 `get_complete_agent_trace(message_id)` 可正常返回全链路数据
