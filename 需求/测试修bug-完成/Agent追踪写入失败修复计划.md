# Agent 追踪写入失败 Bug 修复计划

**关联文档**：[Agent追踪写入失败Bug报告.md](Agent追踪写入失败Bug报告.md)
**制定日期**：2026-07-22
**验证状态**：Bug 已通过 emy-test 实战复现并确认（见报告内核对齐说明）

---

## 0. 验证结论速览（修复前必读）

报告描述的现象、影响范围、DB 现状**全部属实**，但根因分析有一处关键误判，修复方案需据此调整：

| 报告主张 | 核实 | 说明 |
|---------|------|------|
| `agent_reasoning_logs` / `llm_interaction_logs` INSERT 因 FK 违规失败 | ✅ 属实 | 日志有 `ForeignKeyViolation` + `'message_id': ''` |
| `tool_call_logs` 能写入（FK nullable） | ✅ 属实 | |
| `get_complete_agent_trace()` 返回 `{"found": False}` | ✅ 属实 | 容器内直调确认 |
| FK 违规来自 **TraceHook** 调 `create_reasoning_log(message_id=context.db_message_id)` | ❌ **误判** | TraceHook 是**完全死代码**，从未触发（见下） |

### TraceHook 为何是死代码（三重失效）

1. **服务从未注入**：[__init__.py:815-835](../emily-core/emily_core/__init__.py#L815-L835) `_collect_injected_services()` 不产出 `agent_trace_service` → TraceHook 在 [hook.py:251](../emily-core/emily_core/workitem/pipeline/hook.py#L251) `if self.agent_trace_service is None: return allow()` 提前返回
2. **Hook 名不匹配**：[hook_config.json:24,28](../emily-data/config/hook_config.json#L24) 注册 `trace.execution_start/end`，而 [hook.py:255,267](../emily-core/emily_core/workitem/pipeline/hook.py#L255) 检查 `trace.reasoning_start/end`——永不匹配
3. **日志无对应告警**：全程搜不到 `Failed to create reasoning log`（TraceHook 路径告警）

### 真正触发 FK 违规的代码路径

[legacy_log_bridge.py](../emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py) 的 `write_legacy_logs()`，由 [bus.py:210](../emily-core/emily_core/workitem/pipeline/bus.py#L210) 在 Pipeline 完成后调用：
- [L73,92](../emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py#L73)：`db_msg_id = context.db_message_id` → `""` → 传给 `AgentReasoningLog` → FK 违规
- [L138](../emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py#L138)：同样传 `""` 给 `LLMInteractionLog` → FK 违规
- `tool_call_logs` 不传 `message_id`（[L117-128](../emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py#L117-L128)）→ 成功

### 报告漏掉的最上游根因

**入站消息根本没被持久化**——这才是 `db_message_id` 无值的根本原因：
- `MessageService.record_message`（[message_service.py:26](../emily-core/emily_core/services/message_service.py#L26)）**无任何调用方**，`MessageService` 从未被实例化
- `ChatArchiveService` 同样从未被实例化（`core._chat_archive_service` 从未赋值）
- emy-test 实测：发一条消息执行完整 Pipeline 并返回回复，但 `messages` 表计数**不变**（仍 26）→ 入站消息压根没落库
- `BusContext` 在 [scheduler.py:103-109](../emily-core/emily_core/workitem/scheduler.py#L103-L109) 构造时也从不设置 `db_message_id`（默认 `""`，[context.py:68](../emily-core/emily_core/workitem/pipeline/context.py#L68)）

**结论**：即便修好 `db_message_id` 传递，若不先恢复入站消息持久化，`db_message_id` 也无值可填。

---

## 1. 修复策略总览

Bug 呈四层结构，按"从上游到下游"顺序修复，每层独立验收：

```
层 4（旁支）: TraceHook 死代码 ──────────────────→ Module 4 清理
层 3（直接）: legacy_log_bridge 传 message_id="" ─→ Module 3 防御加固
层 2（中游）: BusContext.db_message_id 未赋值 ──→ Module 2 打通传递
层 1（上游）: 入站消息未持久化 ────────────────→ Module 1 恢复落库
```

| Module | 目标 | 优先级 | 风险 | 依赖 |
|--------|------|--------|------|------|
| M1 | 恢复入站消息持久化 | P0（根因） | 低 | 无 |
| M2 | 打通 db_message_id 传递链路 | P0（根因） | 低 | M1 |
| M3 | legacy_log_bridge 防御性加固 | P1（防回归） | 极低 | M1+M2 后自然消解，加防御即可 |
| M4 | TraceHook 死代码清理 | P2（消歧义） | 低 | M1+M2 验证通过后 |
| M5 | 验收测试 | — | — | M1-M4 |

**核心判断**：M1+M2 是"治本"，M3 是"兜底"，M4 是"打扫战场"。M1+M2 完成后，FK 违规自然消失，trace 数据正常落库。

---

## 2. Module 1：恢复入站消息持久化

### 2.1 问题

`MessageService.record_message` 无调用方，入站消息从不落库 → `messages` 表无新记录 → `db_message_id` 无值可填。

### 2.2 修复方案

在 [EmilyCore.handle_message()](../emily-core/emily_core/__init__.py#L841) 中，**takeover 决策后、用户解析后、SessionPool 路由前**，持久化入站消息并捕获 `message.id`。

选择此位置的依据（Emily 分层约束 #2）：
- `handle_message` 已持有 `decision`（RouteDecision）和 `event_id`，正好满足 `MessageService.record_message(event_id, msg, decision)` 签名
- 持久化发生在 EmilyCore 层 → Service → Repository → DB，不跳层
- 在路由前完成，保证 Pipeline 执行期间 `db_message_id` 已可用

### 2.3 代码骨架

**文件**：`emily-core/emily_core/__init__.py`（`handle_message` 方法，约 L886 前）

```python
# ── 入站消息持久化（M1 修复：恢复落库，供 trace 关联）──
db_message_id = ""
try:
    from .services.message_service import MessageService
    _msg_service = MessageService()
    db_msg = await asyncio.to_thread(
        _msg_service.record_message, event_id, message, decision
    )
    db_message_id = db_msg.id
    # 回填 sender_user_id（用户绑定已在上面完成）
    if user_id:
        await asyncio.to_thread(_msg_service.bind_sender, db_message_id, user_id)
    logger.info(
        "Inbound message persisted: id=%s event_id=%s conv=%s",
        db_message_id, event_id, message.conversation_id,
    )
except Exception as e:
    # 非阻断：持久化失败不阻塞 Pipeline，仅 trace 会缺失
    logger.warning("Inbound message persist failed (non-blocking): %s", e)

# SessionPool 路由（携带 db_message_id —— 见 M2）
reply = await self._session_pool.route(
    message, user_id=user_id, db_message_id=db_message_id
)
```

### 2.4 约束对照

| 约束 | 遵循情况 |
|------|---------|
| 分层不可跳（#2） | EmilyCore → MessageService → MessageRepository → DB ✓ |
| Sync repo + asyncio.to_thread（#6） | `asyncio.to_thread` 包裹 sync repo ✓ |
| 非阻断 | try/except + warning，失败放行 Pipeline ✓ |
| event_id 幂等 | `record_message` 内部已 `get_by_event_id` 去重 ✓ |
| FK 列语义（踩坑#FK陷阱） | `create_from_standard` 已正确解析 conv_id → UUID ✓ |

### 2.5 注意事项

- **event_id 为空**：`messages.event_id` 有 `unique=True, nullable=False` 约束。生产环境插件会生成 event_id；但 emy-test 或异常调用可能传空串。建议在 `record_message` 入口加防御：`event_id = event_id or f"fallback_{_new_uuid_short()}"`，避免两条空 event_id 消息冲突
- **出站消息持久化**（可选扩展，非本 Bug 必需）：`handle_message` 拿到 reply 后也可调 `create_outbound` 落库回复。当前 Bug 仅关联入站 message_id，出站持久化建议另立任务

### 2.6 验收标准

- [ ] 发送一条消息后，`messages` 表新增一条 `direction='inbound'` 记录
- [ ] 新记录 `sender_user_id` 已回填为真实用户 UUID
- [ ] 新记录 `conversation_id` 为 conversations 表 UUID（非业务 conv_id 字符串）
- [ ] 重复发送同一 `event_id`，不产生重复记录（幂等）
- [ ] 持久化异常时不阻塞 Pipeline（消息仍能正常回复）

---

## 3. Module 2：打通 db_message_id 传递链路

### 3.1 问题

`BusContext` 在 [scheduler.py:103-109](../emily-core/emily_core/workitem/scheduler.py#L103-L109) 构造时不设 `db_message_id`，默认 `""`。

### 3.2 修复方案

将 `db_message_id` 从 `handle_message` 沿调用链传到 `BusContext`。调用链经核实**每方法仅 1 个调用方**（见下表），签名变更影响面极小：

| 方法 | 位置 | 调用方数 |
|------|------|---------|
| `SessionPoolManager.route()` | session_pool.py:92 | 1（handle_message） |
| `SessionAgent.handle()` | session_agent.py:103 | 1（session_pool.route） |
| `SessionAgent._handle_impl()` | session_agent.py:116 | 1（handle） |
| `SessionScheduler.run_all_with_message()` | scheduler.py:76 | 1（_handle_impl） |
| `SessionScheduler._run_one()` | scheduler.py:94 | 3（run_next/run_all/run_all_with_message），后两者无 db_message_id，传默认 `""` 即可 |

### 3.3 代码骨架

**① session_pool.py** — `route()` 增加 `db_message_id` 参数并透传：

```python
async def route(
    self, message: "StandardMessage", user_id: str = "", db_message_id: str = ""
) -> "ReplyMessage | None":
    ...
    async with entry.lock:
        entry.last_active = time.time()
        return await entry.agent.handle(message, db_message_id=db_message_id)
```

**② session_agent.py** — `handle()` / `_handle_impl()` 透传：

```python
async def handle(self, message, db_message_id: str = "") -> ReplyMessage | None:
    reply = await self._handle_impl(message, db_message_id=db_message_id)
    ...

async def _handle_impl(self, message, db_message_id: str = "") -> ReplyMessage | None:
    ...
    # ③ 经 Pipeline BUS 执行
    for wi in work_items:
        self.scheduler.enqueue(wi)
    done = await self.scheduler.run_all_with_message(message, db_message_id=db_message_id)
```

**③ scheduler.py** — `run_all_with_message()` / `_run_one()` 透传到 BusContext：

```python
async def run_all_with_message(self, message, db_message_id: str = "") -> list[WorkItem]:
    results = []
    while self._queue:
        wi = self._queue.pop(0)
        results.append(await self._run_one(wi, message=message, db_message_id=db_message_id))
    return results

async def _run_one(self, wi, message=None, db_message_id: str = "") -> WorkItem:
    ...
    context = BusContext(
        work_item=wi,
        message=message,
        user_id=wi.user_id,
        is_admin=wi.is_admin,
        db_message_id=db_message_id,  # ✅ 新增
        _session_context=self._session_context,
    )
```

### 3.4 顺带受益

M2 完成后，以下已有读取点自动获得真实值（无需额外改动）：
- [workitem_agent.py:163](../emily-core/emily_core/workitem/workitem_agent.py#L163)：`message_id=context.db_message_id` → SkillExecutor 收到真实 ID
- [workitem_agent.py:394](../emily-core/emily_core/workitem/workitem_agent.py#L394)：`tool_params["_message_id"]` → 工具 handler 收到真实 ID
- [workitem_agent.py:423](../emily-core/emily_core/workitem/workitem_agent.py#L423)：`handler_kwargs["message_id"]` → 同上

### 3.5 验收标准

- [ ] Pipeline 执行期间 `context.db_message_id` 为真实 UUID（日志可加 debug 打印）
- [ ] `tool_params["_message_id"]` 在工具 handler 中有真实值
- [ ] `run_next()` / `run_all()`（非主流程）调用不报错（默认 `""` 兼容）

---

## 4. Module 3：legacy_log_bridge 防御性加固

### 4.1 问题

即使 M1+M2 修好后 `db_message_id` 有值，仍需防御空值场景（如 M1 持久化失败时），避免 FK 违规告警刷屏。

### 4.2 修复方案

在 [legacy_log_bridge.py](../emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py) 中：
- **AgentReasoningLog**：`message_id` 是 `nullable=False`，空值时**跳过写入**并记 warning
- **LLMInteractionLog**：`message_id` 是 `nullable=True`，空值时传 `None`

### 4.3 代码骨架

**文件**：`emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py`

```python
# ── 2. agent_reasoning_logs ──（约 L70-108）
db_msg_id = context.db_message_id if hasattr(context, "db_message_id") else ""
db_msg_id = db_msg_id or ""  # 归一化 None → ""

# ... 计算 elapsed_ms / steps_json / reply_preview / error_msg ...

if not db_msg_id:
    # message_id 是 nullable=False，空值会触发 FK 违规 → 跳过
    logger.warning(
        "Legacy agent_reasoning_logs skipped: db_message_id empty (wi=%s)",
        wi.id,
    )
else:
    try:
        await EvolutionLogWriter.write(
            AgentReasoningLog,
            message_id=db_msg_id,
            # ... 其余字段不变 ...
        )
    except Exception as e:
        logger.warning("Legacy agent_reasoning_logs write failed: %s", e)

# ── 3. llm_interaction_logs ──（约 L138）
# message_id 改为传 None（nullable=True），而非空串
message_id=db_msg_id or None,
```

### 4.4 验收标准

- [ ] `db_message_id` 有值时，`agent_reasoning_logs` + `llm_interaction_logs` 正常写入
- [ ] `db_message_id` 为空时，日志输出 `skipped: db_message_id empty`，**无** `ForeignKeyViolation`
- [ ] `tool_call_logs` 写入不受影响
- [ ] `sop_routing_logs` 不受影响（其 `message_id` 本就传 `None`）

---

## 5. Module 4：TraceHook 死代码清理

### 5.1 问题

TraceHook 三重失效（服务未注入 + hook 名不匹配 + 与 legacy_log_bridge 冗余），保留会误导后续维护者，且若有人将来"修复"注入会导致**双写同一批表**。

### 5.2 推荐方案：删除 TraceHook

**理由**：
- `legacy_log_bridge` 已完整覆盖 4 表写入（从 `wi.step_results` 提取 tool_calls、LLM 交互估算、elapsed_ms 等，数据更丰富）
- TraceHook 即便修好也只写 `reasoning_log`，不写 `llm_interaction` / `tool_call`，功能不全
- 保留两套写入路径 = 维护负担 + 双写风险

### 5.3 代码改动清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | [hook.py:241-283](../emily-core/emily_core/workitem/pipeline/hook.py#L241-L283) | 删除 `TraceHook` 类 |
| 2 | [hook.py:350](../emily-core/emily_core/workitem/pipeline/hook.py#L350) | `HOOK_TYPE_MAP` 删除 `"trace": TraceHook` |
| 3 | [hook_config.json:23-29](../emily-data/config/hook_config.json#L23-L29) | 删除两个 `trace` hook 挂载（before/after wi_node3 的 trace 条目） |
| 4 | [bus.py:108-111](../emily-core/emily_core/workitem/pipeline/bus.py#L108-L111) | 删除 `trace` 类型的 `agent_trace_service` 注入分支 |
| 5 | [agent_trace_service.py](../emily-core/emily_core/services/agent_trace_service.py) | **保留**（`get_complete_agent_trace` 查询仍需要）；`create_reasoning_log` / `finalize_reasoning_log` 等写入方法标记 `# DEPRECATED: 由 legacy_log_bridge 统一写入` 注释，暂不删（避免破坏 import） |

### 5.4 备选方案（不推荐）：修复 TraceHook

若团队认为 TraceHook 是"正规 M11 迁移入口"必须保留，则需：
1. `_collect_injected_services` 注入 `AgentTraceService`（需先实例化 `AgentTraceService()` 并存为 `self._agent_trace_service`）
2. 统一 hook 名：`hook_config.json` 改为 `trace.reasoning_start/end`，或 `hook.py` 改判断为 `trace.execution_start/end`
3. 删除 `legacy_log_bridge`（避免双写）
4. 补全 TraceHook：当前只写 reasoning_log，需补 llm_interaction_logs / tool_call_logs 写入逻辑（工作量大）

**不推荐**：工作量大、风险高、收益低（legacy_log_bridge 已能用）。

### 5.5 验收标准

- [ ] 容器重启后，Hook 注册日志中**无** `trace.execution_start` / `trace.execution_end`
- [ ] Pipeline 执行无双写告警
- [ ] 4 表数据由 `legacy_log_bridge` 单一路径写入
- [ ] `get_complete_agent_trace` 查询功能不受影响

---

## 6. Module 5：验收测试

### 6.1 测试准备

```powershell
# 1. 清除 __pycache__（bind-mount 不自动刷新，踩坑#1）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +

# 2. 重启 emily-core
docker compose -f docker-compose-napcat.yml restart emily-core

# 3. 确认 users 表真实用户（刘大勇 QQ 123456009）
docker exec emily-postgres psql -U emily -d emily -c `
  "SELECT id, username, level FROM users WHERE status='active' ORDER BY level DESC LIMIT 10;"
```

### 6.2 测试执行

```powershell
# 记录测试前计数
docker exec emily-postgres psql -U emily -d emily -c `
  "SELECT 'msg',COUNT(*) FROM messages UNION ALL SELECT 'arl',COUNT(*) FROM agent_reasoning_logs UNION ALL SELECT 'lil',COUNT(*) FROM llm_interaction_logs UNION ALL SELECT 'tcl',COUNT(*) FROM tool_call_logs;"

# 发送测试消息（刘大勇，触发事件创建 SOP）
$env:PYTHONIOENCODING="utf-8"
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --qq "123456009" --message "帮我创建事件：样板段放线完成"

# 回复"确认"完成事件创建（触发工具调用，验证 tool_call_logs）
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --qq "123456009" --message "确认"
```

### 6.3 验收清单

**DB 验证**：
- [ ] `messages` 表新增 ≥1 条 inbound 记录，`sender_user_id` 指向刘大勇 UUID
- [ ] `agent_reasoning_logs` 新增 ≥1 条，`message_id` 指向新 messages 记录（非空）
- [ ] `llm_interaction_logs` 新增 ≥1 条，`message_id` 有值
- [ ] `tool_call_logs` 按实际工具调用数新增（确认后应有 create_event 工具调用）
- [ ] 取最新 `messages.id`，调 `get_complete_agent_trace(id)` 返回 `{"found": True, ...}` 含三层链路

**日志验证**：
- [ ] emily-core 日志**无** `ForeignKeyViolation`
- [ ] 无 `Legacy agent_reasoning_logs skipped: db_message_id empty`（M1+M2 成功的话）
- [ ] 无 `TraceHook` 相关注册日志（M4 完成）

**功能验证**：
- [ ] 事件创建流程正常（SOP-002 → 确认 → EVT-20260722-xxxx 落库）
- [ ] 快回/意图识别/Pipeline 4 节点执行不受影响

### 6.4 回归检查

- [ ] 出站回复仍正常推送（SSE / outbound_bus）
- [ ] Session 池路由、TTL 清理不受影响
- [ ] 调度器（SchedulerEngine）不受影响
- [ ] 其他 SOP（任务/会议/文件）流程不受影响

---

## 7. Emily 项目特性约束对照

本计划严格遵循 [CLAUDE.md](../CLAUDE.md) 约束：

| 约束 | 本计划遵循情况 |
|------|---------------|
| #1 业务内核独立 | 所有改动在 `emily_core` 内，不 import astrbot ✓ |
| #2 分层不可跳 | M1：EmilyCore → MessageService → MessageRepository → DB ✓ |
| #6 Sync repo + asyncio.to_thread | M1 用 `asyncio.to_thread` 包裹 sync `record_message` ✓ |
| #7 Hook 三态 deny-wins | M4 删除 TraceHook 不影响 AuthHook 阻断语义 ✓ |
| 非阻断写入原则 | M1 持久化失败放行 Pipeline；M3 空值跳过仅 warning ✓ |
| FK 列语义陷阱（踩坑） | M1 依赖 `create_from_standard` 已有的 conv_id 解析 ✓ |
| `__pycache__` 不自动刷新（踩坑） | M5 测试步骤显式清除 ✓ |
| emy-test 真实用户（踩坑） | M5 用真实 QQ 123456009，不用假 sender-id ✓ |

---

## 8. 实施顺序与风险控制

### 8.1 推荐实施顺序

```
Step 1: M1 + M2 一起做（根因修复，强耦合）
  → 验收：发消息后 messages 表有新记录 + agent_reasoning_logs 有记录
Step 2: M3（防御加固）
  → 验收：无 FK 违规告警
Step 3: M4（死代码清理）
  → 验收：无 TraceHook 注册日志
Step 4: M5（完整验收）
  → 验收：get_complete_agent_trace 返回完整链路
```

### 8.2 风险与回滚

| 风险 | 等级 | 缓解/回滚 |
|------|------|----------|
| M1 持久化失败影响主流程 | 低 | try/except 非阻断，失败仅 warning；最坏回退为移除新增代码块 |
| M2 签名变更漏改调用方 | 极低 | 每方法仅 1 调用方，且 `db_message_id=""` 默认值兼容旧调用 |
| M3 误跳过正常写入 | 极低 | 仅 `db_message_id` 为空时跳过，M1+M2 成功则有值 |
| M4 删除 TraceHook 影响查询 | 低 | `get_complete_agent_trace` 在 `agent_trace_service.py`，不依赖 TraceHook |
| event_id 空串冲突 | 中 | M1 加 `event_id or fallback_{uuid}` 防御 |

### 8.3 回滚策略

每个 Module 独立提交，若验收失败可单独 revert：
- M1、M2 强耦合，建议同一 commit
- M3、M4 各自独立 commit
- 所有改动不涉及 DB schema 变更（无需迁移），回滚零数据风险

---

## 9. 后续建议（非本 Bug 范围）

1. **出站消息持久化**：`handle_message` 拿到 reply 后调 `create_outbound` 落库回复，实现双向全量归档（原 `ChatArchiveService` 设计意图）
2. **tool_call_logs 关联补全**：当前 `legacy_log_bridge` 写 `tool_call_logs` 时 `reasoning_log_id=None` / `llm_interaction_id=None`（[L119-120](../emily-core/emily_core/infrastructure/logging/legacy_log_bridge.py#L119-L120)），无法关联推理链路。可在 `write_legacy_logs` 内先写 reasoning_log 拿到 ID，再回填到 tool_call / llm_interaction
3. **集成测试**：增加"消息入站 → 持久化 → trace 落库 → 查询"端到端测试，防回归
4. **`ChatArchiveService` / `MessageService` 死代码盘点**：确认是否还有其他从未实例化的服务，避免类似"设计了但没接线"的问题
