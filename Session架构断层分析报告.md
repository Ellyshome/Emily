# Session 架构断层分析报告

> 整理日期：2026-07-03
> 来源：代码全量追踪 + 架构讨论
> 状态：描述现状，非实施计划

---

## 一、涉及模块

| 模块 | 文件 | 定位 |
|------|------|------|
| `SessionContext` | `emily-core/emily_core/session/session_context.py` | Session 聚合根数据类（蓝图 §4.3.1） |
| `SessionAgent` | `emily-core/emily_core/session/session_agent.py` | 会话调度主脑（蓝图 §4.3） |
| `SessionFactory` | `emily-core/emily_core/adapters/session/session_factory.py` | Session 创建 + 最小化知识灌注（蓝图 §3.4） |
| `SessionPoolManager` | `emily-core/emily_core/adapters/session/session_pool.py` | Session 池，负责路由和 TTL 管理 |
| `collect_session_data.py` | `scripts/collect_session_data.py` | Session 聚合根数据收集脚本（独立工具） |
| `BusContext` | `emily-core/emily_core/workitem/pipeline/context.py` | Pipeline BUS 节点间共享状态 |

---

## 二、SessionContext 的三个角色

`SessionContext` 不止是 LLM prompt 的拼装容器，它承担三个独立职责：

### 角色一：LLM 上下文拼装

`message_history` 被注入到每次 LLM 调用：
- `SessionAgent._recognize_intent()` — 意图识别时拼入
- `WorkItemAgent._llm_plan()` — 执行规划时拼入
- `WorkItemAgent._llm_synthesize_reply()` — 回复合成时拼入

### 角色二：运行时权限执行（非 prompt）

`SessionContext` → `BusContext._session_context` → 供 Pipeline BUS 节点和 Hook 做**代码级鉴权**：

```
SessionScheduler._run_one()
  → BusContext(_session_context=...)
    → WorkItemAgent.authorize() 调用 context.get_permissions()
    → AuthHook 调用 context.has_sop_permission()
    → Hook 调用 context.has_db_permission()
    → Hook 调用 context.meets_level_requirement()
```

这些是 if/else 判断，与 LLM prompt 无关。

### 角色三：归档持久化

Session 过期/终止时，`SessionAgent.archive()` 把关键字段写入 `session_archives` 表：

```python
context_snapshot = json.dumps({
    "user_name": self.context.user_name,
    "sop_catalog_summary": self.context.sop_catalog_summary,
    "permission_level": self.context.permissions.permission_level,
    "company_name": self.context.permissions.company_name,
}, ...)
```

---

## 三、Session 创建流程

**没有独立的"拉起脚本"**。Session 是惰性创建的，触发链如下：

```
用户发消息（QQ/微信）
  → NapCat / AstrBot 平台适配
  → 薄插件 emily_agent/main.py
  → EmilyApiClient.send_message()      HTTP POST /api/v1/message/send
  → api/routes/message.py              handle_message()
  → EmilyCore.handle_message()
      ① takeover 判断
      ② 用户绑定（sender_id → user_id）
  → SessionPoolManager.route()
      ├── conversation_id 命中 → 复用已有 SessionAgent.handle()
      └── 未命中 → SessionFactory.create(message, user_id)
                       → _build_context() 最小化灌注
                          ├── user_name         ← message.sender_name
                          ├── sop_catalog_summary ← SOP 注册表
                          ├── permissions       ← PermissionService
                          └── history_summary   ← UserMemoryService
                       → SessionAgent(conversation_id, context, bus)
```

---

## 四、核心断层：`collect_session_data.py` 与 `SessionFactory._build_context()` 互不连通

### 4.1 问题

`collect_session_data.py` 收集了丰富的数据，但**从未被 Session 创建链路调用**。`SessionFactory._build_context()` 走了自己的最小化灌注路径，两者做的是同一件事的不同版本。

### 4.2 数据覆盖对比

| 数据项 | `collect_session_data.py` 采集了 | `SessionFactory._build_context()` 注入了 | 状态 |
|--------|:---:|:---:|:---:|
| 用户名 | DB `users.username` | `message.sender_name` | 来源不同 |
| 用户职务 | DB `users.position` | 未注入 | **缺失** |
| 权限快照 | `PermissionService` | `PermissionService` | 一致 |
| 项目上下文（name/type/status） | DB `projects` 表 | 未注入 | **缺失** |
| 长期记忆（long_term_memory） | DB `users.long_term_memory` | 未直接注入 | **缺失** |
| 对话摘要（conversation_summary） | DB `users.conversation_summary` | 未注入（归档时才整合） | **缺失** |
| 最近对话（recent 20 条） | DB `messages` 表 | 未注入（`message_history` 从空开始） | **缺失** |
| 工具目录摘要 | 未采集 | 字段存在但不填充 | 两端都空 |

### 4.3 `SessionContext` 僵尸字段

以下字段在 dataclass 中定义了，但从未被填充或消费：

| 字段 | 被填充？ | 被消费？ |
|------|:---:|:---:|
| `user_preferences` | 从未 | 从未 |
| `tool_catalog_summary` | 从未 | 从未 |
| `schema_summary` | 从未 | 从未 |
| `system_prompt` | 从未 | 从未 |

### 4.4 `prompt_variables` 映射未接入

`collect_session_data.py` 输出了完整的模板变量映射（`{project_name}`、`{user_position}` 等），但实际的 LLM prompt 组装（`session.md` / `workitem.md`）并没有使用这个机制——prompt 模板中的变量是通过各自独立的 `.format()` 调用手工注入的，而非通过 `collect_session_data.py` 的统一变量映射。

---

## 五、引用关系图谱

```
collect_session_data.py ──(从未调用)──→ SessionFactory._build_context()
       │                                        │
       │ 收集了丰富的数据                          │ 做了最小化灌注
       │ (项目/职务/记忆/对话)                     │ (SOP目录/权限/记忆摘要)
       │                                        │
       ▼                                        ▼
      无消费者                              SessionContext
                                               │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                         prompt拼装        权限执行          归档持久化
                       (SessionAgent    (BusContext        (session_archives
                        +WorkItemAgent)  →Hook鉴权)        表写入)
```

`self_check.py` 标注了"参照源：scripts/collect_session_data.py"，但仅是代码风格的参照，不是调用关系。`cold_start.py` 有注释引用但同样未实际调用。

---

## 六、总结

| 维度 | 现状 |
|------|------|
| 聚合根设计 | `SessionContext` dataclass 字段齐全，预留了 `user_preferences`、`tool_catalog_summary` 等 |
| 数据采集 | `collect_session_data.py` 已实现完整的收集逻辑（DB/Services） |
| 工厂灌注 | `SessionFactory._build_context()` 仅做了最小化灌注，未调用 `collect_session_data.py` |
| prompt 变量 | `collect_session_data.py` 输出 `prompt_variables` 映射，但下游 prompt 模板未使用 |
| 整体评估 | **采集层就绪、容器设计就绪、但连接层未接通** |

`SessionContext` 的架构位置是正确的——它同时承载 LLM 上下文、权限执行、归档持久化三个职责。断层在于：设计上 `collect_session_data.py` 应该是它的"数据采集层"，但工厂方法绕过了它，导致 `SessionContext` 中一半字段始终为空。
