# SessionContext 操作台重构 — 实施计划

## Context

Session 模块三个核心问题：
1. **数据采集两套互不通气**：`SessionFactory._build_context()` 只灌 4 项，`collect_session_data.py` 采了 7 类却未被生产路径调用
2. **SessionContext 字段脱节**：僵尸字段无数据源，采集了的数据无对应字段
3. **行为散落**：消息记录/LLM 拼装/归档持久化/压缩分布在 SessionAgent 各处

**目标**：SessionContext 升级为操作台（聚合根），统一承载数据获取、消息记录、LLM 上下文拼装、归档持久化。SessionAgent 只保留决策职责。**PermissionSnapshot 对象移除**，其全部字段扁平化为 SessionContext 直接字段。

## 决策汇总

| # | 决策 | 选择 |
|---|------|------|
| D1 | 新建 ProjectRepository？ | **否**，保留直查 Project 模型 |
| D2 | 两套记忆源？ | **只保留 DB 记忆**（`users.long_term_memory`），移除文件记忆加载 |
| D3 | history_summary 改 @property？ | **是**，合并 `long_term_memory` + `conversation_summary` |
| D4 | PermissionSnapshot？ | **移除**，所有字段扁平化到 SessionContext |
| D5 | 两阶段 format？ | **可接受** |
| D6 | compress_overflow 触发？ | **record_turn 内部自动检测** |
| D7 | SessionDataFetcher 依赖注入？ | **接收 core 对象**，与 SessionContext.create() 一致 |
| D8 | recent_turns 范围？ | **跨会话查**（用户维度） |
| D9 | level_label() helper？ | **在 permission/level.py 新增公共函数** |
| D10 | Skill 预留？ | **最小**：`available_skills: list[str]` + `{available_skills}` 占位符 |
| D11 | 热更新？ | **同意分类方案 + `--hot-update` 模式** |

***

## Phase 1：数据采集层

### 1.1 新建 `session_data_fetcher.py`

**文件**：`emily-core/emily_core/session/session_data_fetcher.py`

`SessionDataFetcher.fetch(user_id, conversation_id, core)` 静态方法，接收 `core` 对象获取依赖（`getattr(core, "_xxx", None)` + fail-open），一次性全量采集并返回：

```python
{
    "session_snapshot": { "user_name", "user_position", "permissions": dict, "project_name", ... },
    "session_runtime":  { "recent_turns": list[dict] },
    "prompt_variables": { "{user_name}", "{project_name}", ... },
    "errors": list[str],
}
```

**子采集改造要点**：
- 合并 5 次 DB 查询为 1 次 `UserRepository.get_by_id()` → 提取 username/position/project_id/long_term_memory/conversation_summary
- `_sub_fetch_permissions` → 调 `PermissionService.build_permission_dict(user_id)` 返回 **dict**（不再返回 PermissionSnapshot 对象）
- `_sub_fetch_project` → `user.project_id` 直查 `Project` 模型（无 ProjectRepository）
- `_sub_fetch_recent_turns` → `MessageRepository.get_recent_by_user_id()` 新方法

**迁移的工具函数**（从 `collect_session_data.py` 搬入，不改逻辑）：
- `_parse_position_json()`
- `_format_recent_turns()`
- `_format_node_ids()`
- `_format_permission_level()` → 重命名调用 `level_label()`
- `_translate_project_status()`

### 1.2 MessageRepository 扩展

**文件**：`emily-core/emily_core/repositories/message_repo.py`

新增：
```python
@staticmethod
def get_recent_by_user_id(user_id: str, limit: int = 20) -> list[dict]:
    """获取用户最近入站消息（跨会话，OpenAI 格式）。"""
```

### 1.3 `permission/level.py` 新增 `level_label()`

**文件**：`emily-core/emily_core/permission/level.py`

```python
def level_label(level: int) -> str:
    """权限层级可读标签，如 '管理员(L5)'。"""
    return f"{LEVEL_NAME.get(level, '未知')}(L{level})"
```

### 1.4 PermissionService 返回类型改造

**文件**：`emily-core/emily_core/services/permission_service.py`

- `build_permission_snapshot()` → 重命名为 `build_permission_dict()`，返回 `dict[str, Any]` 而非 `PermissionSnapshot`
- `_do_build_snapshot()` 内部构造 dict 替代 `PermissionSnapshot(...)`
- fail-open 路径返回 `{"permission_level": 1}` 替代 `PermissionSnapshot(permission_level=1)`
- `check()` / `grant()` / `query_user_permissions()` / `_grantor_has_permission()` 中所有 `snapshot.xxx` 改为 `snapshot["xxx"]`

### 1.5 collect_session_data.py 改薄壳

**文件**：`scripts/collect_session_data.py`

简化为：
```python
def collect_session_data(user_id, conversation_id=""):
    _init_db_if_needed()
    return SessionDataFetcher.fetch(user_id, conversation_id, core=None)
```

保留 `_init_db_if_needed()` 和 `__main__` 输出格式不变。

### 1.6 session/__init__.py 更新

新增导出 `SessionDataFetcher`。

***

## Phase 2：SessionContext 重构

### 2.1 删除 PermissionSnapshot 类 + 字段扁平化

**文件**：`emily-core/emily_core/session/session_context.py`

**删除** `PermissionSnapshot` 类（L22-78）。

**SessionContext 字段变更**：

| 操作 | 字段 | 类型 | 默认值 | 热更新 |
|------|------|------|--------|--------|
| **新增** | `permission_level` | `int` | `1` | 🔥 |
| **新增** | `company_id` | `str` | `""` | 🔥 |
| **新增** | `company_type` | `str` | `""` | 🔥 |
| **新增** | `company_name` | `str` | `""` | 🔥 |
| **新增** | `department` | `str` | `""` | 🔥 |
| **新增** | `project_ids` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `partner_ids` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `scopes` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `sop_allow` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `db_perms` | `dict[str,str]` | `field(dict)` | 🔥 |
| **新增** | `info_level` | `str` | `"public"` | 🔥 |
| **新增** | `supervisor_id` | `str` | `""` | 🔥 |
| **新增** | `org_group` | `str` | `""` | 🔥 |
| **新增** | `granted_codes` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `denied_codes` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `authorized_node_ids` | `list[str]` | `field(list)` | 🔥 |
| **新增** | `permission_version` | `int` | `0` | 🔥 |
| **新增** | `permissions_loaded_at` | `str` | `""` | 🔥 |
| **新增** | `user_position` | `str` | `""` | 🔒 |
| **新增** | `project_name` | `str` | `""` | 🔄 |
| **新增** | `project_type` | `str` | `""` | 🔄 |
| **新增** | `project_status` | `str` | `""` | 🔄 |
| **新增** | `long_term_memory` | `str` | `""` | 📝 |
| **新增** | `conversation_summary` | `str` | `""` | 📝 |
| **新增** | `created_at` | `str` | `""` | 🔒 |
| **新增** | `available_skills` | `list[str]` | `field(list)` | 🔥 |
| **删除** | `permissions` | — | — | — |
| **删除** | `user_preferences` | — | — | — |
| **删除** | `tool_catalog_summary` | — | — | — |
| **删除** | `schema_summary` | — | — | — |
| **删除** | `system_prompt` | — | — | — |
| **删除** | `perm_list` | — | — | — |
| **删除** | `extra` | — | — | — |
| **重组** | `history_summary` | `@property` | — | 📝 |

`history_summary` 变为计算属性：
```python
@property
def history_summary(self) -> str:
    parts = [p for p in (self.long_term_memory, self.conversation_summary) if p]
    return "\n".join(parts)
```

**热更新完整分类**：

| 分类 | 字段 |
|------|------|
| 🔒 冻结 | `conversation_id`, `user_id`, `user_name`, `user_position`, `created_at` |
| 🔥 可热更新 | `permission_level`, `company_id`, `company_type`, `company_name`, `department`, `project_ids`, `partner_ids`, `scopes`, `sop_allow`, `db_perms`, `info_level`, `supervisor_id`, `org_group`, `granted_codes`, `denied_codes`, `authorized_node_ids`, `permission_version`, `permissions_loaded_at`, `sop_catalog_summary`, `available_skills` |
| 🔄 可热更新(谨慎) | `project_name`, `project_type`, `project_status` |
| 📝 运行时自维护 | `message_history`, `long_term_memory`, `conversation_summary` |

**SessionContext 权限方法更新**（字段从 `self.permissions.xxx` 改为 `self.xxx`）：

```python
def has_sop_permission(self, sop_id: str) -> bool:
    return sop_id in self.sop_allow or "all" in self.sop_allow

def has_db_permission(self, table: str, operation: str = "read") -> bool:
    perm = self.db_perms.get(table)
    if perm is None: return False
    if operation == "read": return perm in ["read", "read_write"]
    if operation == "write": return perm == "read_write"
    return False

def meets_level_requirement(self, required_level: int) -> bool:
    from ..permission.level import can_access
    return can_access(self.permission_level, required_level)
```

**删除** `get_permission_snapshot()` 方法。

### 2.2 下游适配（PermissionSnapshot 移除联动）

**文件：`emily-core/emily_core/permission/auth_engine.py`**

所有方法签名 `perms: "PermissionSnapshot"` → `perms: dict`：
- `check_sop_access(perms: dict, ...)`
- `_check_deny_codes(perms: dict, ...)`
- `_check_granted_codes(perms: dict, ...)`
- `_check_sop_matrix(perms: dict, ...)`
- `_log_access_denied(perms: dict, ...)`
- `check_access(perms: dict, ...)`

所有 `perms.xxx` → `perms.get("xxx", default)`：
- `perms.denied_codes` → `perms.get("denied_codes", [])`
- `perms.granted_codes` → `perms.get("granted_codes", [])`
- `perms.permission_level` → `perms.get("permission_level", 1)`
- `perms.info_level` → `perms.get("info_level", "public")`
- `perms.company_type` → `perms.get("company_type", "")`
- `perms.department` → `perms.get("department", "")`
- `perms.authorized_node_ids` → `perms.get("authorized_node_ids", [])`
- `perms.supervisor_id` → `perms.get("supervisor_id", "")`
- `perms.extra_perms.get("user_id")` → `perms.get("extra_perms", {}).get("user_id", "")`

删除 `TYPE_CHECKING` 中 `from ..session.session_context import PermissionSnapshot`。

**文件：`emily-core/emily_core/workitem/pipeline/context.py`**

- 删除 `TYPE_CHECKING` 中 `PermissionSnapshot` import
- 删除 `get_permissions()` 方法
- `has_sop_permission()` / `has_db_permission()` / `meets_grouping_requirement()` 仍保留，委托 `self._session_context.has_sop_permission()` 等（SessionContext 方法已适配）

**文件：`emily-core/emily_core/workitem/pipeline/hook.py` (AuthHook)**

- L122: `perms = context.get_permissions()` → `session_ctx = context.get_session_context()`
- L131: `_is_admin(perms.permission_level)` → `_is_admin(session_ctx.permission_level)`
- L142: `perms = context.get_permissions()` → `session_ctx = context.get_session_context()`
- L145: `sop_id not in perms.sop_allow` → `sop_id not in session_ctx.sop_allow`
- L151: `perms.supervisor_id` → `session_ctx.supervisor_id`
- None 检查：`perms is not None` → `session_ctx is not None`

**文件：`emily-core/emily_core/session/session_agent.py` (_persist_archive)**

- L527: `self.context.permissions.permission_level` → `self.context.permission_level`
- L528: `self.context.permissions.company_name` → `self.context.company_name`

**文件：`emily-core/emily_core/services/permission_service.py`**

- `check()` L264: `snapshot = ...` → `perm_dict = ...`；传给 AuthEngine 时已是 dict
- `grant()` L319: 同理
- `query_user_permissions()` L427: `snapshot.xxx` → `perm_dict["xxx"]`
- `_grantor_has_permission()` 参数类型 `PermissionSnapshot` → `dict`，内部 `.granted_codes` → `.get("granted_codes", [])`

**文件：`scripts/collect_session_data.py`**

`_sub_fetch_permissions` 中 `snapshot = service.build_permission_snapshot(user_id)` 已在 Phase 1 改为 `build_permission_dict()` 返回 dict，`snapshot.xxx` 已改为 `snapshot["xxx"]`。

### 2.3 SessionContext 操作台方法

**文件：`emily-core/emily_core/session/session_context.py`**

#### `create()` 工厂方法

```python
@classmethod
def create(cls, user_id: str, conversation_id: str,
           sender_name: str, core) -> "SessionContext":
    """一次性全量灌注创建。调 SessionDataFetcher.fetch() 获取数据。"""
```

#### `record_turn()`

```python
def record_turn(self, user_content: str, assistant_content: str,
                sender_name: str = "") -> None:
    """记录一轮对话。含溢出检查（>40 条时异步触发 compress_overflow）。"""
```

D6 选择 A：`record_turn` 内部检测并触发 `asyncio.ensure_future(self.compress_overflow(...))`。

#### `build_llm_messages()`

```python
def build_llm_messages(self, system_prompt_template: str,
                       current_user_msg: str = "",
                       sender_name: str = "",
                       pending_context: str = "") -> list[dict]:
    """统一拼装 LLM messages 列表。内部调 get_prompt_variables() + format_map。"""
```

两阶段 format（D5 确认可接受）：调用方先 `.format()` WorkItem 级变量，再传给此方法做 `format_map()` 替换 Session 级变量。

#### `get_prompt_variables()`

```python
def get_prompt_variables(self) -> dict[str, str]:
    """返回 prompt 模板变量映射。"""
    from ..permission.level import level_label
    return {
        "{project_name}": self.project_name,
        "{project_type}": self.project_type,
        "{project_status}": self.project_status,
        "{user_name}": self.user_name,
        "{user_position}": self.user_position,
        "{user_company}": self.company_name,
        "{user_company_type}": self.company_type,
        "{user_department}": self.department,
        "{user_permission_level}": level_label(self.permission_level),
        "{current_node_ids}": "、".join(self.authorized_node_ids),
        "{conversation_summary}": self.conversation_summary,
        "{user_memory}": self.long_term_memory,
        "{sop_catalog}": self.sop_catalog_summary,
        "{current_datetime}": self.current_datetime,
        "{available_skills}": ", ".join(self.available_skills) or "（无）",
        "{recent_turns}": "",  # 由 runtime 填充
    }
```

#### `persist_and_consolidate()`

从 `SessionAgent._persist_archive()` + `_consolidate_conversation_summary()` 迁入。

#### `compress_overflow()`

从 `SessionAgent._compress_overflow()` 迁入。需接收 `llm_client` 参数（`record_turn` 内部触发时需传入）。

#### `refresh()`

```python
def refresh(self, data: dict) -> list[str]:
    """从 SessionDataFetcher.fetch() 结果刷新可热更新字段。只覆盖🔥和🔄类。"""
```

#### Skill 预留

```python
available_skills: list[str]  # 初始化自 sop_allow，可 register/unregister

def register_skill(self, skill_id: str) -> None: ...
def unregister_skill(self, skill_id: str) -> None: ...
def has_skill(self, skill_id: str) -> bool: ...
```

***

## Phase 3：SessionFactory 简化

**文件**：`emily-core/emily_core/adapters/session/session_factory.py`

`_build_context()` 重写为委托 `SessionContext.create()`：

```python
def _build_context(self, message, user_id):
    return SessionContext.create(
        user_id=user_id,
        conversation_id=message.conversation_id,
        sender_name=message.sender_name or "",
        core=self._core,
    )
```

**SessionContext.create() 内部逻辑**（对应旧 `_build_context` 完整流程）：
1. 构造基础 SessionContext（conversation_id, user_id, sender_name, current_datetime, created_at）
2. 调 `SessionDataFetcher.fetch(user_id, conversation_id, core=core)`
3. 从 snapshot dict 灌注所有字段（user_name, user_position, 权限 dict → 逐字段展开, project_*, long_term_memory, conversation_summary）
4. 从 runtime dict 灌注 recent_turns → message_history
5. SOP 目录摘要
6. available_skills 初始化自 sop_allow
7. **移除文件记忆加载**（D2：不调 UserMemoryService）

***

## Phase 4：Prompt 模板增强

**文件：`emily-data/prompts/session.md`**

在 `## 当前时间` 前插入：
```markdown
## 当前用户
{user_name}，{user_position}，{user_company}（{user_company_type}）
权限等级：{user_permission_level}

## 当前项目
{project_name}（{project_type}，{project_status}）
```

**文件：`emily-data/prompts/workitem.md`** / **`planner.md`**

插入简化版用户/项目上下文。

**文件：`emily-core/emily_core/infrastructure/llm/prompt_loader.py`**

`_DEFAULTS` dict 中硬编码回退文本同步添加对应占位符。

**SessionAgent 临时适配**：`_recognize_intent()` 的 `_SESSION_SYSTEM_PROMPT.format()` 调用中新增变量注入（Phase 6 会迁移到 build_llm_messages）。

### 【验证1】Phase 1-4

1. `uv run python scripts/collect_session_data.py <user_id>` — 输出与旧版一致
2. `uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"` — Session 创建正常，新字段有值
3. LLM 回复体现用户职务/项目名上下文
4. `uv run python scripts/smoke_test.py` — 全链路不断

***

## Phase 5：SessionAgent 瘦身

**文件**：`emily-core/emily_core/session/session_agent.py`

### 5.1 消息记录 → `context.record_turn()`

```python
def _record_turn(self, message, reply_content):
    self.context.record_turn(
        user_content=(message.content or "")[:2000],
        assistant_content=(reply_content or "")[:2000],
        sender_name=getattr(message, "sender_name", "") or "",
    )
    # 溢出压缩已由 record_turn 内部触发
```

删除 `_MAX_HISTORY_MESSAGES` / `_COMPRESS_BATCH_SIZE` 常量，删除 `_compress_overflow()` 方法。

### 5.2 LLM 上下文拼装 → `context.build_llm_messages()`

```python
pending_text = self._build_pending_context()
full_messages = self.context.build_llm_messages(
    system_prompt_template=_SESSION_SYSTEM_PROMPT,
    current_user_msg=content,
    sender_name=sender,
    pending_context=pending_text,
)
```

新增 `_build_pending_context()` 辅助方法提取 pending 注入逻辑。

### 5.3 归档 → `context.persist_and_consolidate()`

```python
await self.context.persist_and_consolidate(llm_client=self._llm)
```

删除 `_persist_archive()` 和 `_consolidate_conversation_summary()` 方法。

### 5.4 热更新

```python
if len(self.context.message_history) % 10 == 0:
    await self._maybe_refresh_context()

async def _maybe_refresh_context(self):
    data = SessionDataFetcher.fetch(
        user_id=self.context.user_id,
        conversation_id=self.conversation_id,
        core=self._core_ref,  # 从 SessionFactory 注入
    )
    updated = self.context.refresh(data)
    if updated:
        logger.info("Session[%s] auto-refreshed: %s", self.conversation_id, updated)
```

***

## Phase 6：WorkItemAgent 调整

**文件**：`emily-core/emily_core/workitem/workitem_agent.py`

### 6.1 `_llm_plan()` 改造

```python
session_ctx = context.get_session_context() if context else None
if session_ctx:
    full_messages = session_ctx.build_llm_messages(
        system_prompt_template=system_prompt,  # 已 format WorkItem 级变量
        current_user_msg=f"Plan for: {wi.user_input[:200]}",
    )
else:
    full_messages = [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": f"Plan for: {wi.user_input[:200]}"}]
```

### 6.2 `_llm_synthesize_reply()` 同理

### 【验证2】Phase 5-6

1. emy-test 完整流程（创建事件 → 确认 → 等归档）— 归档和摘要回写正常
2. 检查日志：`_record_turn` / `archive` / `_compress_overflow` 走 SessionContext 方法
3. 热更新验证：`context.refresh()` 日志输出
4. `uv run python scripts/smoke_test.py` — 全链路不断

***

## Phase 7：CLI 脚本

### 7.1 collect_session_data — `--hot-update` 模式

```bash
uv run python scripts/collect_session_data.py <user_id>              # 全量（与旧版一致）
uv run python scripts/collect_session_data.py <user_id> --hot-update  # 标注热更新分类
```

`--hot-update` 输出：每个字段后追加 🔒/🔥/🔄/📝 分类标注。

### 7.2 新建 `scripts/build_llm_prompt.py`

```bash
uv run python scripts/build_llm_prompt.py <user_id> --prompt session
uv run python scripts/build_llm_prompt.py <user_id> --prompt workitem
uv run python scripts/build_llm_prompt.py <user_id> --prompt planner --msg "帮我创建事件"
```

核心流程：`SessionDataFetcher.fetch()` → 拼装 SessionContext → `load_prompt()` → `ctx.build_llm_messages()` → 输出。

### 【最终验证】

1. `uv run python scripts/smoke_test.py` — 全链路不断
2. emy-test 完整业务流程
3. 检查 Session 归档：`SELECT conversation_id, turn_count, archive_reason FROM session_archives ORDER BY archived_at DESC LIMIT 5;`
4. 检查 conversation_summary 回写：`SELECT username, LEFT(conversation_summary, 100) FROM users WHERE conversation_summary != '' LIMIT 5;`
5. 运维脚本：`collect_session_data.py` 全量 + `--hot-update` + `build_llm_prompt.py`
6. Skill 预留：`available_skills` 初始值来自 `sop_allow`，`has_skill()` / `register_skill()` 正常
7. **PermissionSnapshot 完全移除**：`grep -r "PermissionSnapshot" emily-core/` 无结果

***

## 文件清单

| 文件 | Phase | 改动 |
|------|-------|------|
| `emily-core/emily_core/session/session_data_fetcher.py` | 1 | **新建** |
| `emily-core/emily_core/repositories/message_repo.py` | 1 | 修改（新增 `get_recent_by_user_id`） |
| `emily-core/emily_core/permission/level.py` | 1 | 修改（新增 `level_label`） |
| `emily-core/emily_core/services/permission_service.py` | 1 | 修改（`build_permission_snapshot` → `build_permission_dict`，返回 dict；内部属性访问改下标） |
| `emily-core/emily_core/session/session_context.py` | 2 | **重写**（删除 PermissionSnapshot + 扁平化字段 + 删僵尸 + @property + 操作台方法） |
| `emily-core/emily_core/permission/auth_engine.py` | 2 | 修改（参数 PermissionSnapshot → dict，属性访问改 `.get()`） |
| `emily-core/emily_core/workitem/pipeline/context.py` | 2 | 修改（删 `get_permissions()`，删 PermissionSnapshot import） |
| `emily-core/emily_core/workitem/pipeline/hook.py` | 2 | 修改（AuthHook: `get_permissions()` → `get_session_context()`，属性直接访问） |
| `emily-core/emily_core/session/session_agent.py` | 2/5 | 修改（`ctx.permissions.xxx` → `ctx.xxx`；剥离 4 方法） |
| `emily-core/emily_core/adapters/session/session_factory.py` | 3 | 简化（→ `SessionContext.create()`） |
| `emily-data/prompts/session.md` | 4 | 修改（加用户/项目占位符） |
| `emily-data/prompts/workitem.md` | 4 | 修改 |
| `emily-data/prompts/planner.md` | 4 | 修改 |
| `emily-core/emily_core/infrastructure/llm/prompt_loader.py` | 4 | 修改（硬编码回退同步） |
| `emily-core/emily_core/workitem/workitem_agent.py` | 6 | 修改（改用 `build_llm_messages`） |
| `scripts/collect_session_data.py` | 1/7 | 简化（薄壳 + `--hot-update`） |
| `scripts/build_llm_prompt.py` | 7 | **新建** |
| `emily-core/emily_core/session/__init__.py` | 1 | 修改（新增导出） |

## 回退策略

| Phase | 回退方式 |
|-------|---------|
| Phase 1 | 删除 `session_data_fetcher.py`，恢复 `collect_session_data.py` |
| Phase 2 | 恢复 `PermissionSnapshot` 类 + `ctx.permissions` 字段，恢复下游文件 |
| Phase 3 | 恢复旧 `_build_context()` |
| Phase 4 | 恢复原 .md 和 prompt_loader.py |
| Phase 5 | 逐方法恢复 SessionAgent |
| Phase 6 | 恢复 `if session_ctx else ...` 回退路径 |
