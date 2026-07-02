# Session 拼组与拉起脚本 — 需求规格

> **版本**: v1.0
> **日期**: 2026-07-02
> **关联需求**: [Session调度机脚本需求.md](Session调度机脚本需求.md) · [Session聚合根类需求.md](Session聚合根类需求.md)
> **当前对应文件**: `emily-core/emily_core/adapters/session/session_factory.py`

---

## 1. 定位

拼组与拉起脚本是 Session 的"出生证明"。当调度机判定需要创建新 Session 时，由本脚本执行多步骤的数据采集，组装 Session 聚合根，注入 SessionAgent，激活后交还给调度机。

**一句话职责**：根据 conversation_id + user_id，并行/串行拉取各数据源，组装一个就绪的 SessionAgent。

---

## 2. 执行流程

```
调度机调用
    │
    ▼
SessionFactory.create(message, user_id)
    │
    ├── 步骤0: 基础标识获取
    │    conversation_id ← message.conversation_id
    │    user_name       ← message.sender_name
    │    created_at      ← datetime.now(UTC)
    │
    ├── 步骤1: 权限快照获取
    │    ┌─ 条件: user_id 非空 + PermissionService 可用
    │    └─ perm_snapshot ← PermissionService.build_permission_snapshot(user_id)
    │         ├── permission_level  (1-6)
    │         ├── company_id, company_type, company_name
    │         ├── department, position
    │         ├── project_ids, partner_ids, scopes
    │         ├── sop_allow, db_perms
    │         ├── granted_codes, denied_codes
    │         ├── authorized_node_ids
    │         ├── info_level, supervisor_id, org_group
    │         └── 失败 → 默认 PermissionSnapshot() (L1 访客)
    │
    ├── 步骤2: 用户项目上下文获取
    │    ┌─ 条件: user_id 非空 + ProjectService 可用
    │    └─ project ← ProjectService.get_by_user(user_id)
    │         ├── project_name
    │         ├── project_type
    │         ├── project_status
    │         └─ 失败 → 空默认值
    │
    ├── 步骤3: 用户长期记忆获取
    │    ┌─ 条件: user_name 非空 + UserMemoryService 可用
    │    └─ user_memory ← UserMemoryService.load_memory_context(user_name)
    │         └─ 失败 → ""（静默）
    │
    ├── 步骤4: 历史对话摘要获取
    │    ┌─ 条件: conversation_id 非空 + ChatArchiveService 可用
    │    └─ conversation_summary ← ChatArchiveService.get_summary(conversation_id)
    │         └─ 失败 → ""（静默）
    │
    ├── 步骤5: 组装 Session 聚合根
    │    snapshot ← SessionSnapshot(上述全部数据)
    │    session  ← Session(snapshot)
    │    └── state = CREATED
    │
    ├── 步骤6: 获取 LLM 依赖
    │    llm_client           ← core._llm_client
    │    sop_intent_registry  ← core._sop_intent_registry
    │
    ├── 步骤7: 拼组 SessionAgent
    │    agent ← SessionAgent(
    │        session              = session,           ← 聚合根
    │        bus                  = self._bus,         ← 全局 Pipeline BUS
    │        llm_client           = llm_client,
    │        sop_intent_registry  = sop_intent_registry,
    │    )
    │    └── agent 内部: session.activate() → ACTIVE
    │
    └── 返回 agent → 调度机
```

---

## 3. 子脚本依赖清单

每个步骤称为一个"子脚本"——一个独立的数据获取调用。工厂负责编排它们的执行顺序。

| 子脚本 | 服务 | 方法 | 输入 | 输出 | 失败策略 |
|--------|------|------|------|------|----------|
| ① 权限 | `PermissionService` | `build_permission_snapshot(user_id)` | user_id | `PermissionSnapshot` | 降级 L1 访客 |
| ② 项目 | `ProjectService` | `get_by_user(user_id)` | user_id | `{name, type, status}` | 空默认值 |
| ③ 记忆 | `UserMemoryService` | `load_memory_context(user_name)` | user_name | `str` | 空字符串 |
| ④ 摘要 | `ChatArchiveService` | `get_summary(conversation_id)` | conversation_id | `str` | 空字符串 |
| ⑤ 注册 | — | 直接从 `core` 取属性 | — | `llm_client`, `sop_intent_registry` | None（允许为空） |

### 3.1 子脚本间依赖

```
步骤0 (标识)      无依赖 ─────────────────────┐
步骤1 (权限)      依赖 user_id                ├── 都依赖步骤0的输出
步骤2 (项目)      依赖 user_id                │
步骤3 (记忆)      依赖 user_name              │  但互相之间无依赖
步骤4 (摘要)      依赖 conversation_id ───────┘
步骤5 (组装)      依赖 0+1+2+3+4 全部
步骤6 (LLM依赖)   依赖 core 存在
步骤7 (拼组Agent) 依赖 5+6
```

步骤 1-4 **互相独立**，可以并行执行。当前实现是串行的——这是优化空间，但不是阻塞项。

---

## 4. 数据流转图

```
                 ┌─────────────┐
                 │ 调度机       │
                 │ SessionPool │
                 └──────┬──────┘
                        │ message, user_id
                        ▼
              SessionFactory.create()
                        │
        ┌───────┬───────┼───────┬───────┐
        ▼       ▼       ▼       ▼       ▼
    [标识]  [权限①]  [项目②]  [记忆③]  [摘要④]
        │       │       │       │       │
        └───────┴───────┴───────┴───────┘
                        │
                        ▼
              SessionSnapshot 组装
                        │
                        ▼
                 Session(snapshot)
                   state = CREATED
                        │
                        ▼
            ┌───────────────────────┐
            │ SessionAgent(session) │
            │  session.activate()   │
            │  state → ACTIVE       │
            └───────────┬───────────┘
                        │
                        ▼
                  返回给调度机
```

---

## 5. 与 SessionAgent 的交接

拼组脚本不负责 prompt 拼接。它的职责在 `SessionAgent.__init__()` 完成时结束。

**SessionAgent 收到的东西**：

```
SessionAgent 构造参数:
├── session: Session              ← 聚合根（snapshot + state + runtime）
│   ├── snapshot 全部字段已填充
│   ├── state = CREATED
│   └── runtime 为空初始状态
├── bus: PipelineBUS              ← 全局单例引用
├── llm_client                    ← DeepSeek LLM 客户端（可为 None = Mock 模式）
└── sop_intent_registry           ← SOP 意图注册表（可为 None = 回退）

SessionAgent.__init__() 内部:
  self.session = session
  self.scheduler = SessionScheduler(session, bus)   ← Scheduler 也持有 Session
  session.activate()                                 ← CREATED → ACTIVE
```

**prompt 拼接在 SessionAgent.handle() 内部**——拼组脚本不管。

---

## 6. 异常处理

| 场景 | 处理 |
|------|------|
| 子脚本①权限加载失败 | 降级为 `PermissionSnapshot()` 默认值（L1 访客），**不阻断创建** |
| 子脚本②项目加载失败 | `project_name` 等字段为 ""，**不阻断创建** |
| 子脚本③记忆加载失败 | `user_memory = ""`，**不阻断创建** |
| 子脚本④摘要加载失败 | `conversation_summary = ""`，**不阻断创建** |
| core 为 None（无 LLM 依赖） | `llm_client = None`, `sop_intent_registry = None`，SessionAgent 走 Mock/回退模式 |
| Session 组装中途异常 | 整体失败，不返回到调度机；上层 catch + log |

> 核心原则：**fail-open**。任何子脚本失败不阻塞 Session 创建——没有权限就按访客处理，没有记忆就空着，保证用户总能收到回复。

---

## 7. 改造要点（相对当前代码）

| 当前 (`session_factory.py`) | 改造后 |
|------------------------------|--------|
| `_build_context()` 返回 `SessionContext` | 返回 `Session`（聚合根） |
| `create()` 构造 `SessionAgent(conv_id, context, bus, llm, registry)` | 构造 `SessionAgent(session, bus, llm, registry)` |
| `context.sop_catalog_summary` 写了但不用 | 删除——SOP 目录由 Registry 在 prompt 拼接时直取 |
| `context.history_summary` 写了但不用 | 改为 `snapshot.user_memory`，被 `to_prompt_vars()` 消费 |
| 缺项目上下文 | 新增子脚本②：`ProjectService.get_by_user()` |
| 缺对话摘要 | 新增子脚本④：`ChatArchiveService.get_summary()` |
| 缺职务字段 | `PermissionSnapshot` 扩展 `position` 字段 |
| 缺职责字段 | `snapshot.user_responsibilities`：从 role/岗位推导 |
