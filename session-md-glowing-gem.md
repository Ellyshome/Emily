# SessionContext 操作台重构计划

## Context

当前 `SessionContext` 是一个贫血 dataclass（纯字段、无行为），所有"管账"操作散落在 `SessionAgent`（归档、摘要回写、消息记录、LLM 上下文拼装）和 `SessionFactory._build_context()`（数据采集）中。`collect_session_data.py` 作为并行开发的采集脚本，逻辑完整但从未被生产路径调用。

**目标**：将 `SessionContext` 从被动数据容器升级为**主动操作台**，统一承载 Session 生命周期的数据获取、消息记录、LLM 上下文拼装、归档持久化四项职责。`SessionAgent` 只保留"决策"职责（路由、意图识别、WorkItem 编排）。

---

## 架构决策（已对齐）

| # | 决策 | 选择 |
|----|------|------|
| Q1 | LLM 上下文拼装是否属于 SessionContext | **是** — `build_llm_messages()` 归入操作台 |
| Q2 | 数据采集时机 | **方案 A：创建时全量灌注** |
| Q3 | 归档职责拆分 | **两步**：SessionAgent 管调度清理，SessionContext 管数据持久化 + 摘要回写 |
| Q4 | collect_session_data.py 定位 | **操作台的数据采集层** — SessionContext 创建时调用其逻辑填充字段 |

---

## 改动总览

### 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `emily-core/emily_core/session/session_context.py` | **重写** | 从 dataclass 升级为操作台（保留字段 + 新增方法） |
| `emily-core/emily_core/adapters/session/session_factory.py` | **简化** | `_build_context()` 改为调用 `SessionContext.create()` |
| `emily-core/emily_core/session/session_agent.py` | **瘦身** | 剥离消息记录/归档/LLM拼装/压缩，转调 context |
| `emily-core/emily_core/workitem/workitem_agent.py` | **调整** | `_llm_plan` / `_llm_synthesize_reply` 改用 `context.build_llm_messages()` |
| `scripts/collect_session_data.py` | **迁移** | 核心采集逻辑迁入 `emily_core/session/session_data_fetcher.py`，脚本保留为 CLI 薄壳 |
| `emily-core/emily_core/session/session_data_fetcher.py` | **新建** | 从 collect_session_data.py 提取，改走 Repository/Service 层 |
| `emily-data/prompts/session.md` | **修改** | 增加 `{user_context}` 占位符 |
| `emily-data/prompts/workitem.md` | **修改** | 增加 `{user_context}` 占位符 |
| `docs/业务模块与运转全景.md` | **更新** | 同步架构变更 |

---

## Phase 1：数据采集层迁入 emily_core

### 1.1 新建 `session_data_fetcher.py`

从 `collect_session_data.py` 提取核心采集逻辑，但改为走 Repository/Service 层：

```python
# emily-core/emily_core/session/session_data_fetcher.py

def fetch_session_data(user_id: str, conversation_id: str = "") -> dict:
    """收集 Session 聚合根所需的全部数据（生产路径）。
    
    与 scripts/collect_session_data.py 对齐的数据项：
    - 用户名（DB users.username）
    - 用户职务（DB users.position）
    - 权限快照（PermissionService）
    - 项目上下文（users.project_id → projects 表）
    - 长期记忆（DB users.long_term_memory）
    - 对话摘要（DB users.conversation_summary）
    - 最近对话（messages 表，最近 20 条）
    
    数据源：全部通过 Repository / Service 获取，不直接 get_session()。
    """
```

**关键差异**（相对 collect_session_data.py）：
- `_sub_fetch_user_name()` → `UserRepository.get_by_id(user_id).username`
- `_sub_fetch_user_position()` → `UserRepository.get_by_id(user_id).position`
- `_sub_fetch_permissions()` → `PermissionService.build_permission_snapshot(user_id)`（不变）
- `_sub_fetch_project()` → `UserRepository` + `ProjectRepository`
- `_sub_fetch_user_memory_and_summary()` → `UserRepository.get_by_id(user_id)` 的 `long_term_memory` / `conversation_summary`
- `_sub_fetch_recent_turns()` → `MessageRepository` 或直接查 messages 表（通过现有 Repository 模式）
- 不再需要 `_init_db_if_needed()` — 生产环境 DB 已初始化
- 返回结构保持与 collect_session_data.py 对齐，方便验证

### 1.2 collect_session_data.py 保留为 CLI 薄壳

```python
# scripts/collect_session_data.py（简化后）
from emily_core.session.session_data_fetcher import fetch_session_data

def collect_session_data(user_id, conversation_id=""):
    """兼容入口：初始化 DB 后调用 fetch_session_data。"""
    _init_db_if_needed()  # 仅 CLI 需要初始化
    return fetch_session_data(user_id, conversation_id)
```

---

## Phase 2：SessionContext 升级为操作台

### 2.1 新字段设计

**新增字段**（对应 collect_session_data.py 已采集但 _build_context 未注入的数据）：

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `user_position` | `str` | DB `users.position` | 用户职务 |
| `company_name` | `str` | PermissionSnapshot 冗余 | 公司名（便捷访问） |
| `company_type` | `str` | PermissionSnapshot 冗余 | 公司类型 |
| `project_name` | `str` | DB `projects.name` | 当前项目名 |
| `project_type` | `str` | DB `projects.lifecycle_stage` | 项目类型 |
| `project_status` | `str` | DB `projects.status` | 项目状态 |
| `long_term_memory` | `str` | DB `users.long_term_memory` | 长期记忆（独立字段） |
| `conversation_summary` | `str` | DB `users.conversation_summary` | 对话摘要（独立字段） |
| `created_at` | `str` | 创建时间 | Session 创建时间戳 |

**删除僵尸字段**：

| 字段 | 删除理由 |
|------|----------|
| `user_preferences` | 从无数据源，`extra` dict 足够 |
| `tool_catalog_summary` | prompt 中动态构建已够用 |
| `schema_summary` | 从无消费者 |
| `system_prompt` | prompt 拼装在各调用点完成 |
| `perm_list` | 已废弃的兼容字段 |

**重组 `history_summary`**：当前混淆了"长期记忆"和"对话摘要"。拆分为独立字段后，`history_summary` 保留为**计算属性**，合并 `long_term_memory` + `conversation_summary`，供需要整体摘要的场景使用。

### 2.2 操作台方法设计

```python
@dataclass
class SessionContext:
    """Session 操作台 —— 聚合根，管理 Session 生命周期。"""

    # ── 标识 ──
    conversation_id: str = ""
    user_id: str = ""
    user_name: str = ""
    created_at: str = ""

    # ── 用户画像（创建时全量灌注）──
    user_position: str = ""
    company_name: str = ""
    company_type: str = ""
    project_name: str = ""
    project_type: str = ""
    project_status: str = ""

    # ── 多轮对话记忆 ──
    message_history: list[dict] = field(default_factory=list)

    # ── 压缩摘要 ──
    long_term_memory: str = ""
    conversation_summary: str = ""

    # ── SOP 目录 ──
    sop_catalog_summary: str = ""

    # ── 当前日期时间 ──
    current_datetime: str = ""

    # ── 权限快照 ──
    permissions: PermissionSnapshot = field(default_factory=PermissionSnapshot)

    # ── 扩展 ──
    extra: dict[str, Any] = field(default_factory=dict)

    # ═══════════════════════════════════════════════════
    # 操作台方法
    # ═══════════════════════════════════════════════════

    @classmethod
    def create(cls, user_id: str, conversation_id: str,
               sender_name: str, core) -> "SessionContext":
        """工厂方法：一次性全量灌注创建 SessionContext。
        
        调用 session_data_fetcher.fetch_session_data() 获取全部数据，
        填充所有字段。core 参数提供 Service/Repository 依赖。
        """

    def record_turn(self, user_content: str, assistant_content: str,
                    sender_name: str = "") -> None:
        """记录一轮对话到 message_history（含溢出检查）。"""

    def build_llm_messages(
        self,
        system_prompt_template: str,
        current_user_msg: str = "",
        sender_name: str = "",
        pending_context: str = "",
    ) -> list[dict]:
        """组装 LLM 调用的 messages 列表。
        
        统一出口：[system_prompt(已format)] + message_history + [pending_context?] + [current_user]
        system_prompt_template 中的占位符通过 get_prompt_variables() 自动填充。
        """

    def get_prompt_variables(self) -> dict[str, str]:
        """返回 prompt 模板变量映射（与 collect_session_data.prompt_variables 对齐）。
        
        下游 prompt 模板统一通过此接口获取变量值，不再各自手工拼装。
        """

    async def persist_and_consolidate(self, llm_client=None) -> None:
        """归档：持久化到 session_archives 表 + 整合对话摘要回写 users 表。"""

    async def compress_overflow(self, llm_client=None) -> None:
        """message_history 溢出压缩（从 SessionAgent._compress_overflow 迁入）。"""

    # ── 计算属性 ──

    @property
    def history_summary(self) -> str:
        """合并摘要：长期记忆 + 对话摘要（向后兼容）。"""
        parts = []
        if self.long_term_memory:
            parts.append(self.long_term_memory)
        if self.conversation_summary:
            parts.append(self.conversation_summary)
        return "\n".join(parts)

    # ── 权限访问方法（保留，供 WorkItemAgent 通过 BusContext 调用）──
    # get_permission_snapshot / has_sop_permission / has_db_permission / meets_level_requirement
    # 不变
```

### 2.3 关键方法详细设计

#### `SessionContext.create()`

```python
@classmethod
def create(cls, user_id: str, conversation_id: str,
           sender_name: str, core) -> "SessionContext":
    ctx = cls(
        conversation_id=conversation_id,
        user_id=user_id,
        user_name=sender_name,  # 初始值，下面用 DB 值覆盖
        current_datetime=datetime.now(timezone.utc).isoformat(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # 一次性全量采集
    from .session_data_fetcher import fetch_session_data
    data = fetch_session_data(user_id, conversation_id)
    snapshot = data["session_snapshot"]

    # 覆盖灌注（DB 值优先于 sender_name）
    ctx.user_name = snapshot.get("user_name") or sender_name or ""
    ctx.user_position = snapshot.get("user_position", "")
    ctx.project_name = snapshot.get("project_name", "")
    ctx.project_type = snapshot.get("project_type", "")
    ctx.project_status = snapshot.get("project_status", "")
    ctx.long_term_memory = snapshot.get("user_memory", "")
    ctx.conversation_summary = snapshot.get("conversation_summary", "")

    # 权限快照
    perm_data = snapshot.get("permissions", {})
    ctx.permissions = _build_permission_snapshot(perm_data)
    ctx.company_name = ctx.permissions.company_name
    ctx.company_type = ctx.permissions.company_type

    # SOP 目录
    sop_registry = getattr(core, "_sop_intent_registry", None)
    if sop_registry:
        sops = sop_registry.list_loaded_sops()
        if sops:
            ctx.sop_catalog_summary = f"可用业务流程 ({len(sops)}): {', '.join(sops[:15])}"

    # 最近对话（注入到 message_history）
    runtime = data.get("session_runtime", {})
    recent = runtime.get("recent_turns", [])
    if recent:
        ctx.message_history = [
            {"role": t["role"], "content": t["content"],
             "name": t.get("sender_name") if t["role"] == "user" else None}
            for t in recent
        ]

    return ctx
```

#### `build_llm_messages()`

```python
def build_llm_messages(
    self,
    system_prompt_template: str,
    current_user_msg: str = "",
    sender_name: str = "",
    pending_context: str = "",
) -> list[dict]:
    # 1. 格式化 system prompt（自动填充变量）
    variables = self.get_prompt_variables()
    system_prompt = system_prompt_template.format_map(
        # format_map 不抛 KeyError，缺失变量保留原样
        collections.ChainMap(variables, defaultdict(str))
    )

    # 2. 组装
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(self.message_history)

    # 3. pending 上下文（如待确认事件）
    if pending_context:
        messages.append({"role": "system", "content": pending_context})

    # 4. 当前用户消息
    if current_user_msg:
        messages.append({
            "role": "user", "content": current_user_msg,
            "name": sender_name if sender_name else None,
        })

    return messages
```

#### `get_prompt_variables()`

```python
def get_prompt_variables(self) -> dict[str, str]:
    """与 collect_session_data.py 的 prompt_variables 对齐。"""
    from ..permission.level import level_label
    return {
        "{project_name}": self.project_name,
        "{project_type}": self.project_type,
        "{project_status}": self.project_status,
        "{user_name}": self.user_name,
        "{user_position}": self.user_position,
        "{user_company}": self.company_name,
        "{user_company_type}": self.company_type,
        "{user_permission_level}": level_label(self.permissions.permission_level),
        "{conversation_summary}": self.conversation_summary,
        "{user_memory}": self.long_term_memory,
        "{sop_catalog}": self.sop_catalog_summary,
        "{current_datetime}": self.current_datetime,
    }
```

---

## Phase 3：SessionFactory 简化

`_build_context()` 改为一行委托：

```python
def _build_context(self, message, user_id):
    return SessionContext.create(
        user_id=user_id,
        conversation_id=message.conversation_id,
        sender_name=message.sender_name or "",
        core=self._core,
    )
```

`SessionFactory` 的其余代码不变（`create()` 方法仍组装 `SessionAgent`）。

---

## Phase 4：SessionAgent 瘦身

### 4.1 剥离消息记录

```python
# 之前（SessionAgent._record_turn）
def _record_turn(self, message, reply_content):
    sender = getattr(message, "sender_name", "") or ""
    self.context.message_history.append({"role": "user", ...})
    self.context.message_history.append({"role": "assistant", ...})
    if len(self.context.message_history) > _MAX_HISTORY_MESSAGES:
        asyncio.ensure_future(self._compress_overflow())

# 之后（SessionAgent._record_turn）
def _record_turn(self, message, reply_content):
    self.context.record_turn(
        user_content=(message.content or "")[:2000],
        assistant_content=(reply_content or "")[:2000],
        sender_name=getattr(message, "sender_name", "") or "",
    )
```

### 4.2 剥离 LLM 上下文拼装

```python
# 之前（SessionAgent._recognize_intent）
system_prompt = _SESSION_SYSTEM_PROMPT.format(sop_catalog=..., current_datetime=...)
full_messages = [{"role": "system", "content": system_prompt}]
full_messages.extend(self.context.message_history)
# ... 手工追加 pending_context 和 user message

# 之后
pending_text = self._build_pending_context()  # 提取 pending 逻辑
full_messages = self.context.build_llm_messages(
    system_prompt_template=_SESSION_SYSTEM_PROMPT,
    current_user_msg=content,
    sender_name=sender,
    pending_context=pending_text,
)
```

### 4.3 剥离归档（两步拆分）

```python
# 之前（SessionAgent.archive — 做 4 件事）
async def archive(self):
    # 1. 清空待确认队列
    # 2. 标记活跃 WorkItem 为失败
    # 3. 持久化归档到 session_archives 表
    # 4. 整合 conversation_summary

# 之后（SessionAgent.archive — 只做调度清理）
async def archive(self):
    if self.state in (SessionState.CLOSED, SessionState.ARCHIVING):
        return
    self.state = SessionState.ARCHIVING
    try:
        self.confirm_queue.clear()
        for wi in list(self.scheduler._active.values()):
            if not wi.is_terminal:
                try: wi.transition_to(WorkItemState.FAILED)
                except ValueError: pass
        # 操作台接管归档持久化 + 摘要回写
        await self.context.persist_and_consolidate(llm_client=self._llm)
    except Exception as e:
        logger.warning("Session[%s] archive warning: %s", self.conversation_id, e)
    finally:
        self.state = SessionState.CLOSED
```

### 4.4 剥离压缩

`_compress_overflow()` → `self.context.compress_overflow(llm_client=self._llm)`，由 `record_turn()` 内部自动触发。

---

## Phase 5：WorkItemAgent 调整

### 5.1 `_llm_plan()` 改用 `build_llm_messages()`

```python
# 之前
full_messages = [{"role": "system", "content": system_prompt}]
full_messages.extend(message_history)
full_messages.append({"role": "user", "content": f"Plan for: {wi.user_input[:200]}"})

# 之后
session_ctx = context.get_session_context() if context else None
if session_ctx:
    full_messages = session_ctx.build_llm_messages(
        system_prompt_template=planner_prompt,
        current_user_msg=f"Plan for: {wi.user_input[:200]}",
    )
else:
    # 回退：无 SessionContext 时手工拼装
    full_messages = [{"role": "system", "content": system_prompt}]
    ...
```

### 5.2 `_llm_synthesize_reply()` 同理

```python
# 之后
session_ctx = context.get_session_context() if context else None
if session_ctx:
    full_messages = session_ctx.build_llm_messages(
        system_prompt_template=_load_workitem_prompt(),
        current_user_msg=f"合成回复: {getattr(wi, 'user_input', '?')[:100]}",
    )
```

注意：WorkItemAgent 的 `build_llm_messages()` 需要额外传入 `available_tools` / `sop_text` / `step_results` / `warnings` 等 WorkItem 级别的变量。这些通过 `get_prompt_variables()` 的 `extra` 参数或直接在 template format 前注入。

**设计决策**：`get_prompt_variables()` 返回 Session 级变量（用户/项目/权限/SOP 目录）。WorkItem 级变量（SOP 全文/工具列表/步骤结果/警告）仍由各调用点手工 `.format()` 后再传给 `build_llm_messages()`。这样避免操作台耦合 WorkItem 的瞬态数据。

---

## Phase 6：Prompt 模板增强

### 6.1 session.md 增加用户上下文

```markdown
## 当前用户
{user_name}，{user_position}，{user_company}（{user_company_type}）
权限等级：{user_permission_level}

## 当前项目
{project_name}（{project_type}，{project_status}）

## 当前时间
{current_datetime}

## 可用业务流程目录
{sop_catalog}
```

### 6.2 workitem.md / planner.md 同理

在现有内容之前增加：

```markdown
## 当前用户
{user_name}，{user_position}，{user_company}

## 当前项目
{project_name}（{project_status}）
```

---

## 实施顺序与风险控制

| 阶段 | 内容 | 风险 | 回退策略 |
|------|------|------|----------|
| **Phase 1** | 新建 session_data_fetcher.py | 低（纯新增，不影响现有代码） | 直接删除 |
| **Phase 2** | SessionContext 重写 | **高**（核心数据结构变更） | Phase 2 拆为 2a（加字段）+ 2b（加方法），2a 先行 |
| **Phase 3** | SessionFactory 简化 | 中（创建链路变更） | 恢复旧 `_build_context()` |
| **Phase 4** | SessionAgent 瘦身 | 中（3 个方法迁移） | 每个方法独立迁移，逐一验证 |
| **Phase 5** | WorkItemAgent 调整 | 中（LLM 调用链路变更） | 保留 `if session_ctx else ...` 回退 |
| **Phase 6** | Prompt 模板增强 | 低（纯文本修改） | 恢复原 .md 文件 |

**建议顺序**：Phase 1 → Phase 2a（只加字段，不加方法）→ Phase 3 → Phase 6 → 验证 → Phase 2b（加方法）→ Phase 4 → Phase 5 → 验证

---

## 验证方案

1. **Phase 1 完成后**：`uv run python scripts/collect_session_data.py <user_id>` 验证 CLI 薄壳仍可用
2. **Phase 2a + 3 完成后**：`emy-test --managed --llm --message "你好" --sender "真实用户名"` 验证 Session 创建正常，新字段有值
3. **Phase 6 完成后**：检查 LLM 回复是否体现了用户职务/项目名上下文
4. **Phase 4 完成后**：完整 emy-test 流程（创建事件 → 确认 → 归档），验证归档和摘要回写正常
5. **Phase 5 完成后**：`uv run python scripts/smoke_test.py` 验证全链路
