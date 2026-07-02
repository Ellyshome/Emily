# Session 调度机脚本 — 需求规格

> **版本**: v1.0
> **日期**: 2026-07-02
> **关联需求**: [Session聚合根类需求.md](Session聚合根类需求.md) · [Session拼组与拉起脚本需求.md](Session拼组与拉起脚本需求.md)
> **当前对应文件**: `emily-core/emily_core/adapters/session/session_pool.py`

---

## 1. 定位

Session 调度机是 Adapter 层的 Session 池管理器。它是消息进入系统后的第一道关卡——决定一条入站消息应该路由到哪个 Session。

**一句话职责**：维护 conversation_id → SessionAgent 的哈希表，命中则复用，未命中则委托拼组脚本创建。

---

## 2. 核心行为

### 2.1 消息路由（create-or-route）

```
StandardMessage 到达
        │
        ▼
SessionPoolManager.route(message, user_id)
        │
        ├── ① 从消息提取 conversation_id，lookup 池
        │
        ├── 命中 → 获取已有 SessionAgent
        │      ├── 更新 last_active 时间戳
        │      ├── 获取 conversation_id 对应的 asyncio.Lock
        │      └── 在锁内执行 agent.handle(message)
        │
        └── 未命中 → 委托拼组脚本创建
               ├── 1. 检查并发上限 → 超限先 sweep_expired()
               ├── 2. 调用 SessionFactory.create(message, user_id)
               │      └── 返回已激活的 SessionAgent（内含 Session 聚合根）
               ├── 3. 包装为 _Entry(agent, last_active, lock) 存入池
               ├── 4. 更新 last_active 时间戳
               └── 5. 在锁内执行 agent.handle(message)
```

### 2.2 创建决策表

| 条件 | 行为 |
|------|------|
| conversation_id 在池中存在 | 复用已有 SessionAgent |
| conversation_id 不存在 + 并发 < max | 调用拼组脚本创建新 Session |
| conversation_id 不存在 + 并发 ≥ max | 先 sweep_expired()，仍有空位则创建；否则拒绝 |

### 2.3 并发控制

- **同 conversation_id**：asyncio.Lock 串行。同一群/同一私聊的消息按到达顺序处理，后到的等待前一条完成。
- **不同 conversation_id**：完全并行，各自独立 Lock。
- **池大小上限**：`SessionConfig.max_concurrent`（默认 100），超出时先清理过期再尝试，仍超限则返回错误。

### 2.4 TTL 过期清理

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ttl_seconds` | 600 (10分钟) | 无新消息多久后 Session 过期 |
| `sweep_interval_seconds` | 60 | 后台扫描间隔 |

- 后台 sweeper 定时扫描：`now - last_active > ttl` 的 Session 自动清理
- 清理前**不**触发 `Session.archive()`（静默过期 ≠ 主动归档，两者不同）
- 主动终止（`/session/terminate` API）才会触发 `Session.archive() → package()`

### 2.5 终止

```
POST /api/v1/session/terminate { conversation_id }
        │
        ▼
SessionPoolManager.terminate(conversation_id)
        ├── 从池中查找 SessionAgent
        ├── 调用 agent.session.archive() → 状态推进 + 数据打包
        ├── 持久化 package() 结果（如有需要）
        └── 从池中移除
```

---

## 3. 数据交互

### 3.1 输入

| 数据 | 来源 | 类型 | 说明 |
|------|------|------|------|
| `message` | EmilyCore → API 路由 | `StandardMessage` | 含 conversation_id, content, sender_name, attachments |
| `user_id` | Adapter 层绑定后传入 | `str` | 用户 UUID，不是 conversation_id |
| Pipeline BUS | 全局单例，构造时注入 | `PipelineBUS` | 所有 Session 共享同一条 BUS |

### 3.2 持有的状态

| 字段 | 类型 | 说明 |
|------|------|------|
| `_sessions` | `dict[str, _Entry]` | conversation_id → {agent, last_active, lock} |
| `_config` | `SessionConfig` | TTL / 并发上限配置 |
| `_factory` | `SessionFactory` | 拼组脚本引用 |
| `_sweeper_task` | `asyncio.Task` | 后台 TTL 扫描任务 |

### 3.3 输出

| 输出 | 类型 | 说明 |
|------|------|------|
| `route()` 返回值 | `ReplyMessage \| None` | 直接透传 SessionAgent.handle() 的返回值给上游 API 层 |

---

## 4. 与 Session 聚合根的关系

调度机**不直接接触** Session 聚合根。它的持有对象是 `SessionAgent`，通过 `agent.session` 访问聚合根：

```
SessionPoolManager
  └── _sessions: dict[str, _Entry]
        └── _Entry
              ├── agent: SessionAgent
              │     └── session: Session    ← 聚合根在这里
              ├── last_active: float
              └── lock: asyncio.Lock
```

调度机只关心"哪个 conversation_id 对应哪个 agent"和"多久没活跃了"，不关心 Session 里面存了什么数据。

---

## 5. 异常处理

| 场景 | 处理 |
|------|------|
| 拼组脚本创建失败 | 不存入池，返回错误给上游 |
| handle() 内部抛异常 | 在 route() 中 catch，不污染池状态；记录日志 |
| sweeper 扫描异常 | catch + log，不中断后台循环 |
| terminate() 的 session 不存在 | 返回 False，不抛异常 |

---

## 6. 改造要点（相对当前代码）

| 项目 | 当前 | 改造后 |
|------|------|--------|
| 持有对象 | `_Entry.agent: SessionAgent` | **不变**，仍持 SessionAgent |
| 创建调用 | `self._factory.create(message, user_id)` | **不变**，但返回值内部的 SessionAgent 已改为持有 Session |
| 终止时归档 | `agent.archive()` | 改为 `agent.session.archive()`，再加 `package()` 持久化 |
| 静默过期 | `sweep_expired()` 直接 pop | 可选：过期前尝试 `package()` 保存关键数据 |
| 未见改动 | — | SessionConfig 不变，TTL 逻辑不变 |
