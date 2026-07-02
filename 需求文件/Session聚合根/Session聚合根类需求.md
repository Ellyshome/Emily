# Session 聚合根类 — 需求规格

> **版本**: v1.0
> **日期**: 2026-07-02
> **关联需求**: [Session调度机脚本需求.md](Session调度机脚本需求.md) · [Session拼组与拉起脚本需求.md](Session拼组与拉起脚本需求.md)
> **当前对应文件**: `emily-core/emily_core/session/session_context.py` + `session_state.py`（将被替代）

---

## 1. 定位

Session 聚合根是会话领域实体的唯一权威表达。它统一承载会话的身份标识、状态机、权限数据、运行时缓存和对话历史——是 SessionAgent 获取一切信息的唯一入口，也是销毁时数据打包的唯一出口。

**一句话职责**：我是谁、我知道什么、我当前处于什么状态、我能转换成什么状态。

---

## 2. 数据结构

### 2.1 层次总览

```
Session
├── snapshot: SessionSnapshot      ← 不可变，创建时冻结
├── state: SessionState             ← 状态枚举（内嵌状态机）
├── runtime: SessionRuntime         ← 可变，运行时累进读写
│
├── 状态转换方法
├── prompt 变量映射方法
└── 生命周期方法 (package / archive)
```

### 2.2 SessionSnapshot（不可变快照）

创建时一次性冻结，后续只读。拼组脚本负责逐个字段填充。

| 分类 | 字段 | 类型 | 来源子脚本 | prompt 变量 |
|------|------|------|-----------|-------------|
| **标识** | `conversation_id` | `str` | message 直接取 | — |
| | `user_id` | `str` | Adapter 层传入 | — |
| | `user_name` | `str` | message.sender_name | `{user_name}` |
| | `created_at` | `str` (ISO8601) | 当前时间 | — |
| **项目** | `project_name` | `str` | ProjectService | `{project_name}` |
| | `project_type` | `str` | ProjectService | `{project_type}` |
| | `project_status` | `str` | ProjectService | `{project_status}` |
| **企业** | `company_name` | `str` | PermissionSnapshot | `{user_company}` |
| | `company_type` | `str` | PermissionSnapshot | `{user_company_type}` |
| | `department` | `str` | PermissionSnapshot | `{user_department}` |
| | `position` | `str` | PermissionSnapshot 扩展 | `{user_position}` |
| **权限** | `permission_level` | `int` | PermissionSnapshot | `{user_permission_level}` |
| | `sop_allow` | `list[str]` | PermissionSnapshot | 鉴权用 |
| | `db_perms` | `dict[str,str]` | PermissionSnapshot | 鉴权用 |
| | `granted_codes` | `list[str]` | PermissionSnapshot | 鉴权用 |
| | `denied_codes` | `list[str]` | PermissionSnapshot | 鉴权用 |
| | `authorized_node_ids` | `list[str]` | PermissionSnapshot | `{current_node_ids}` |
| | `info_level` | `str` | PermissionSnapshot | 鉴权用 |
| **职责** | `user_responsibilities` | `str` | role/岗位推导 | `{User_responsibilities}` |
| **记忆** | `user_memory` | `str` | UserMemoryService | `{user_longterm_memory}` |
| | `conversation_summary` | `str` | ChatArchiveService | `{conversation_summary}` |

### 2.3 SessionRuntime（可变运行数据）

运行时 Agent 累进读写。Session 关闭时通过 `package()` 打包，非关键数据可丢弃。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `recent_turns` | `deque[dict]` | `deque(maxlen=20)` | 最近 20 轮对话滑动窗口 |
| `doc_visible_set` | `set[str]` | `set()` | 当前可见文档 ID 集合 |
| `cached_lookups` | `dict[str, Any]` | `{}` | Agent 运行时查询缓存 |
| `active_focus` | `str \| None` | `None` | 当前焦点 WorkItem ID |
| `pending_confirms` | `deque` | `deque()` | 待确认项队列 |
| `baggage` | `dict[str, Any]` | `{}` | 通用兜底 KV（Hook/节点临时传参） |

### 2.4 SessionState（状态枚举，复用现有）

```python
CREATED → ACTIVE → WAITING_CONFIRM → ACTIVE → ARCHIVING → CLOSED
```

> 状态枚举 + 转换表 `TRANSITIONS` **不变**，沿用 `session_state.py`。

---

## 3. 行为方法

### 3.1 状态转换

```python
def transition_to(target: SessionState) → None
    """带校验的状态转换。非法转换 raise ValueError。"""

def activate() → None
    """CREATED → ACTIVE。拼组脚本在 Agent 就绪后调用。"""

def wait_for_confirm() → None
    """ACTIVE → WAITING_CONFIRM。有 WorkItem 产出待确认项时调用。"""

def resume() → None
    """WAITING_CONFIRM → ACTIVE。用户确认/取消后恢复。"""
```

### 3.2 Prompt 变量映射

```python
def to_prompt_vars() → dict[str, str]
    """将聚合根内部字段映射为提示词模板变量。

    返回 dict 可直接 ** 解包给 prompt.format(**session.to_prompt_vars())。
    不包含 {sop_catalog} 和 {current_datetime}——
    这两项由调用方从 Registry 和系统时钟直取。
    """
```

**映射规则**：从 `snapshot` + `runtime` 取字段值，对集合类型做格式化（如 `authorized_node_ids` 用 `、` join，`recent_turns` 格式化为文本行）。

### 3.3 生命周期

```python
def package() → dict
    """打包运行时数据供持久化。不改变状态。
    返回纯 dict，包含 conversation_id、state、recent_turns 摘要等。
    """

def archive() → dict
    """执行归档流程：
    1. state → ARCHIVING
    2. 清空 runtime.recent_turns
    3. 清空 runtime.pending_confirms
    4. 调用 package() 打包
    5. state → CLOSED
    返回 package() 的结果。
    """
```

### 3.4 便捷判断

```python
@property is_active → bool    # state == ACTIVE
@property is_terminal → bool  # state == CLOSED
```

---

## 4. 权限访问方法（从 SessionContext 迁移）

Session 聚合根取代 `SessionContext` 成为权限数据的持有者。以下方法从 SessionContext 迁移至 Session：

```python
def get_permission_snapshot() → PermissionSnapshot

def has_sop_permission(sop_id: str) → bool
    """sop_id 在 sop_allow 白名单中，或白名单含 'all'。"""

def has_db_permission(table: str, operation: str = "read") → bool

def meets_level_requirement(required_level: int) → bool
    """检查 permission_level 是否满足要求的 6 级层级。"""
```

> 这些方法在 Hook 和 WorkItemAgent 中仍有调用点，必须保留接口兼容。

---

## 5. 文件结构

```
emily-core/emily_core/session/
├── entity.py           ← 新建：Session + SessionSnapshot + SessionRuntime
├── session_state.py    ← 保留不变：SessionState 枚举 + TRANSITIONS
├── session_agent.py    ← 改造：持有 Session 替代零散字段
├── focus_lock.py       ← 废弃：逻辑退化到 SessionRuntime.active_focus
├── confirm_queue.py    ← 废弃：逻辑退化到 SessionRuntime.pending_confirms
└── session_context.py  ← 删除：字段并入 SessionSnapshot
```

---

## 6. 不变的约定

| 约定 | 说明 |
|------|------|
| **snapshot 不可变** | 创建后不修改。如需更新权限需创建新 Session |
| **runtime 任意读写** | Agent / Hook / Scheduler 均可写，调用方自担一致性 |
| **fail-open** | 权限/记忆加载失败不阻塞 Session 创建，降级为访客级默认值 |
| **不在 prompt 中放敏感字段** | `db_perms`、`denied_codes` 等仅用于程序鉴权，不暴露给 LLM |
| **session.md 模板** | 提示词模板文件独立维护，不在代码中硬编码 |
