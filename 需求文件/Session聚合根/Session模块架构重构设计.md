# Session 模块架构重构设计报告

> **版本**: v1.0
> **日期**: 2026-07-02
> **性质**: 架构需求文档 — 指导本次 Session 模块重构的详细设计
> **关联文档**: [业务模块与运转全景](docs/业务模块与运转全景.md)、[代码文件目录](docs/代码文件目录.md)

---

## 1. 背景与动因

### 1.1 现状问题

当前 Session 模块由三个松散概念拼接而成，历史演进痕迹明显：

| 组件 | 文件 | 设计意图 | 实际效果 |
|------|------|----------|----------|
| `SessionContext` | `session/session_context.py` | Session 创建时的最小化知识灌注 | 19 个字段中 6 个写了但从未被消费；prompt 注入变量绕过它直接从 Registry 取值 |
| `SessionState` | `session/session_state.py` | 会话生命周期枚举 + 转换表 | 裸枚举，不关联任何数据；转换逻辑散落在 `SessionAgent.archive()` 中 |
| *(缺失)* | — | Session 级可变 KV 存储 | 不存在。临时用 `BusContext.baggage`（单 WorkItem 生命周期）凑合 |

**核心矛盾**：需要存储"权限数据、可见文档列表、运行时缓存"等 Session 级信息时，没有合适的位置。既不能放 `SessionContext`（设计为冻结快照），也不能放 `BusContext.baggage`（WI 结束即销毁）。

### 1.2 目标

将分立的 `SessionContext` + `SessionState` + (缺失的 Store) 统一为一个领域实体 `Session`，作为会话数据的唯一权威来源 (Single Source of Truth)，并使 `SessionAgent` 从"数据持有者"退化为"数据使用者/协调者"。

---

## 2. 核心设计

### 2.1 领域实体模型

```
Session (聚合根 / 领域实体)
│
├── snapshot: SessionSnapshot      ← 不可变，创建时冻结
│   ├── conversation_id
│   ├── user_id
│   ├── user_name
│   ├── created_at
│   └── permissions: PermissionSnapshot  (18 字段，v2.0 权限系统)
│
├── state: SessionState            ← 状态枚举，含转换校验
│
├── runtime: SessionRuntime        ← 可变，Agent 运行期累进读写
│   ├── recent_turns: deque        ← 滑动窗口对话 (maxlen=20)
│   ├── doc_visible_set: set[str]  ← 可见文档 ID 集合
│   ├── cached_lookups: dict       ← Agent 查询缓存
│   ├── active_focus: str | None   ← 当前焦点 (原 FocusLock 退化)
│   ├── pending_confirms: deque    ← 待确认队列 (原 ConfirmQueue 退化)
│   └── baggage: dict[str, Any]    ← 通用兜底 KV
│
├── transition_to(target) → None   ← 行为：状态流转 + 前置校验
├── activate() → None
├── wait_for_confirm() → None
├── archive() → dict               ← 行为：归档 + 返回打包数据
├── package() → dict               ← 行为：序列化待持久化数据
└── is_active() → bool             ← 便捷判断
```

### 2.2 SessionAgent 的定位变化

```
重构前：                              重构后：
┌─────────────────────────┐           ┌────────────────┐
│ SessionAgent (数据+逻辑) │           │ SessionAgent   │  ← 协调者，做决策
│  ├ conversation_id      │           │  ├ session ────→ Session (数据)
│  ├ context (Context)    │           │  ├ scheduler ──→ SessionScheduler
│  ├ state (State)         │           │  ├ _llm        → LLM Client
│  ├ focus (FocusLock)    │           │  └ _registry   → SOP Registry
│  ├ confirm_queue        │           └────────────────┘
│  ├ scheduler            │
│  ├ _llm                 │
│  └ _registry            │
└─────────────────────────┘
```

`SessionAgent` 不再自己持有数据字段，改为全部通过 `self.session` 读写。它的职责仍然是对外对话协调——意图识别、WorkItem 拆分、回复组装——但数据的归属权转移给 `Session`。

### 2.3 为什么不用纯状态机

经典状态机只有 `状态 × 事件 → 新状态` 三要素，不承载业务数据。本项目 `Session` 的目标是同时承载状态 + 权限快照 + 运行时缓存 + 对话框等数据，其中状态机逻辑只占约 20%。因此 `Session` 是一个**内嵌状态机的领域实体**，不是纯状态机。

---

## 3. 详细设计

### 3.1 Session 类 (`session/entity.py`)

```python
@dataclass
class SessionSnapshot:
    """创建时冻结的不可变数据。"""
    conversation_id: str
    user_id: str
    user_name: str
    permissions: PermissionSnapshot
    created_at: str


@dataclass
class SessionRuntime:
    """运行期累进读写的可变数据。"""
    recent_turns: deque              # 滑动窗口，maxlen=20
    doc_visible_set: set[str]        # 可见文档 ID
    cached_lookups: dict[str, Any]   # 查询缓存
    active_focus: str | None = None  # 当前焦点 WorkItem ID
    pending_confirms: deque = field(default_factory=deque)  # 待确认项
    baggage: dict[str, Any] = field(default_factory=dict)   # 通用兜底


class Session:
    """IM 会话领域实体。

    拥有状态、数据和生命周期行为。是会话相关数据的唯一权威来源。
    SessionAgent 作为协调者从本对象读取所需数据、调用状态转换方法。
    """

    def __init__(self, snapshot: SessionSnapshot):
        self.snapshot = snapshot
        self.runtime = SessionRuntime()
        self._state = SessionState.CREATED

    # ── 状态属性 (只读暴露) ──
    @property
    def state(self) -> SessionState:
        return self._state

    # ── 状态转换 (带校验) ──
    def transition_to(self, target: SessionState) -> None:
        allowed = TRANSITIONS.get(self._state, [])
        if target not in allowed:
            raise ValueError(
                f"非法状态转换: {self._state.value} → {target.value}"
            )
        self._state = target

    def activate(self) -> None:
        self.transition_to(SessionState.ACTIVE)

    def wait_for_confirm(self) -> None:
        self.transition_to(SessionState.WAITING_CONFIRM)

    # ── 生命周期 ──
    def archive(self) -> dict:
        """归档：状态推进 + 数据打包。返回待持久化的数据。"""
        self.transition_to(SessionState.ARCHIVING)
        data = self.package()
        self._state = SessionState.CLOSED
        return data

    def package(self) -> dict:
        """打包运行时数据供持久化。不改变状态。"""
        return {
            "conversation_id": self.snapshot.conversation_id,
            "user_id": self.snapshot.user_id,
            "state": self._state.value,
            "recent_turns": list(self.runtime.recent_turns),
            "doc_visible_set": list(self.runtime.doc_visible_set),
            "cached_lookups": dict(self.runtime.cached_lookups),
            "pending_confirms": list(self.runtime.pending_confirms),
        }

    # ── 便捷方法 ──
    def is_active(self) -> bool:
        return self._state == SessionState.ACTIVE

    def is_terminal(self) -> bool:
        return self._state == SessionState.CLOSED
```

### 3.2 SessionAgent 改造

改造前后对比：

```python
# 改造前
class SessionAgent:
    def __init__(self, conversation_id, context, bus, llm_client, sop_intent_registry):
        self.conversation_id = conversation_id
        self.context = context
        self.state = SessionState.CREATED
        self.focus = FocusLock()
        self.confirm_queue = ConfirmQueue()
        self.scheduler = SessionScheduler(conversation_id, bus, session_context=context)
        self._llm = llm_client
        self._sop_intent_registry = sop_intent_registry
        self.state = SessionState.ACTIVE

# 改造后
class SessionAgent:
    def __init__(self, session: Session, bus: PipelineBUS, llm_client, sop_intent_registry):
        self.session = session           # ← 唯一数据源
        self.scheduler = SessionScheduler(session, bus)
        self._llm = llm_client
        self._sop_intent_registry = sop_intent_registry
        session.activate()               # ← 由 Session 管理自己的状态
```

数据访问路径简化为：

```python
# 需要用户名
# 前: self.context.user_name
# 后: self.session.snapshot.user_name

# 需要权限
# 前: self.context.permissions (然后绕 BusContext 透传)
# 后: self.session.snapshot.permissions

# 需要焦点
# 前: self.focus.current_focus
# 后: self.session.runtime.active_focus

# 需要缓存
# 前: 没有合适位置，临时拼接
# 后: self.session.runtime.cached_lookups["key"]
```

### 3.3 SessionScheduler / BusContext 数据访问调整

当前 `BusContext` 通过私有字段 `_session_context` 透传 SessionContext。重构后改为引用 `Session` 对象：

```python
class BusContext:
    _session: Session | None = None     # 私有引用，替代 _session_context

    # 删除: get_session_context()
    # 删除: get_permissions()    → 改为 context._session.snapshot.permissions
    # 删除: has_sop_permission() → 改为 context._session.snapshot.permissions 直接判断
    # 保留: get() / set()        → 但标注"仅限节点间临时传参，持久数据请走 session.runtime"
```

Hook 层面的访问同样简化——Hook 读 `context._session.snapshot.permissions` 即可获取权限，不再需要多层 getter。

### 3.4 SessionFactory 改造

```
改造前：
  SessionFactory.create() → 组装 SessionContext → 创建 SessionAgent(持有零散字段)

改造后：
  SessionFactory.create() → 组装 SessionSnapshot → 创建 Session → 创建 SessionAgent(session)
```

---

## 4. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新建** | `session/entity.py` | `Session` + `SessionSnapshot` + `SessionRuntime` 类 |
| **新增** | `session/__init__.py` | 导出 `Session`, `SessionState`, `SessionSnapshot`, `SessionRuntime` |
| **改造** | `session/session_agent.py` | 移除零散字段，改为持有 `session: Session`；所有数据访问改走 `self.session` |
| **保留** | `session/session_state.py` | `SessionState` 枚举 + `TRANSITIONS` 表维持不变 |
| **改造** | `session/session_context.py` | 移除 SessionContext dataclass；`PermissionSnapshot` 保留并移入 `entity.py` |
| **改造** | `adapters/session/session_factory.py` | `_build_context()` → 组装 `SessionSnapshot` + 创建 `Session` |
| **改造** | `adapters/session/session_pool.py` | `_Entry` 持有的 `agent` 不变，但 `archive()` 调用改为通过 `agent.session.archive()` |
| **简化** | `workitem/pipeline/context.py` | `_session_context` → `_session`；删除 5 个权限便捷方法 |
| **改造** | `workitem/scheduler.py` | `SessionScheduler.__init__` 接受 `Session` 替代 `session_id` + `session_context` |
| **改造** | `workitem/workitem_agent.py` | 权限检查改为 `context._session.snapshot.permissions` |
| **可用废弃** | `session/focus_lock.py` | `FocusLock` 逻辑退化到 `SessionRuntime.active_focus` 字符串，类可移除 |

> `session/confirm_queue.py`：`ConfirmQueue` 类似退化，`SessionRuntime.pending_confirms` (deque) 承担。

---

## 5. 与现有架构约束的兼容性

| 约束 | 兼容情况 |
|------|----------|
| 分层不跳 | 仍为 `API → Core → Session → WorkItem → ...`；`Session` 在 Session 层，不越界 |
| 权限架构 v1.2 | `PermissionSnapshot` 在 `SessionSnapshot` 中保持只读冻结，Hook/Agent 只能读不能写 |
| Hook 三态 | Hook 通过 `context._session.snapshot.permissions` 读取权限，路径缩短但逻辑不变 |
| Mock/Real | 无影响。`Session` 是纯数据对象，不依赖 LLM |

---

## 6. 验证

### 6.1 烟雾测试

```powershell
uv run python scripts/smoke_test.py
```
验证 Session 创建 → 状态流转 → WorkItem 执行 → 回复生成的完整路径无断裂。

### 6.2 实战测试

```powershell
uv run python .claude/skills/emy-test/cli.py --managed --llm `
    --message "帮我创建事件：样板段放线完成" --sender "张工"
```
验证 Session 权限注入 + Agent 读取 + Hook 鉴权路径正常。

### 6.3 回归检查项

- [ ] `SessionState` 枚举值和转换表不变
- [ ] `PermissionSnapshot` 18 字段不变
- [ ] `SessionAgent.handle()` 外部行为不变（回复内容一致）
- [ ] 旧 `SessionContext` 引用全部替换，不遗留 import
- [ ] `history_summary` 等死字段不再写入空数据

---

## 7. 附：类图总览

```
┌─────────────────────────────────────────────────────┐
│ Session (entity.py)                                  │
│ ─────────────────────────────────────────────────── │
│ + snapshot: SessionSnapshot   (frozen)               │
│ + state: SessionState         (enum)                 │
│ + runtime: SessionRuntime     (mutable)              │
│ ─────────────────────────────────────────────────── │
│ + activate() / wait_for_confirm() / archive()        │
│ + package() → dict                                   │
│ + is_active() / is_terminal()                        │
└────────────┬────────────────────────────────────────┘
             │ 被持有
             ▼
┌─────────────────────────────────────────────────────┐
│ SessionAgent (session_agent.py)                      │
│ ─────────────────────────────────────────────────── │
│ + session: Session        ← 数据源                   │
│ + scheduler: SessionScheduler                        │
│ - _llm: LLM Client                                  │
│ - _sop_intent_registry: SOPIntentRegistry            │
│ ─────────────────────────────────────────────────── │
│ + handle(message) → ReplyMessage                     │
│ - _recognize_intent(message) → dict                  │
│ - _split_into_workitems(message) → list[WorkItem]    │
└─────────────────────────────────────────────────────┘
```
