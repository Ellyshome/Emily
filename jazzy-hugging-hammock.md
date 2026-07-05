# SessionContext 操作台重构 — 完整实施计划

## Context

当前 Session 模块存在三个核心问题：

1. **数据采集两套互不通气**：`SessionFactory._build_context()` 生产路径只灌了 4 项（user\_name/sop\_catalog/permissions/user\_memory），而 `scripts/collect_session_data.py` 采集了 7 大类（含 user\_position/project/long\_term\_memory/conversation\_summary/recent\_turns/prompt\_variables）却从未被生产路径调用。
2. **SessionContext 字段与数据源脱节**：有僵尸字段（user\_preferences/tool\_catalog\_summary/schema\_summary/system\_prompt/perm\_list）无数据源；有采集了却无对应字段的数据（user\_position/project\_name/long\_term\_memory 独立/conversation\_summary 独立）。
3. **行为散落三处**：消息记录在 `SessionAgent._record_turn()`、LLM 拼装在 `SessionAgent._recognize_intent()` 手工拼、归档持久化在 `SessionAgent._persist_archive()` + `_consolidate_conversation_summary()`、压缩在 `SessionAgent._compress_overflow()`。

**目标**：将 `SessionContext` 升级为操作台（聚合根），统一承载数据获取、消息记录、LLM 上下文拼装、归档持久化四项职责。`SessionAgent` 只保留"决策"职责。运维环节保留独立 CLI 薄壳可单跑验证。

**运维约束**：每个环节有独立 CLI 脚本。脚本调操作台方法（共享核心代码），脚本只负责 DB 初始化 + 参数解析 + 输出格式化。

***

## 关键发现（影响实现细节）

| 发现                                                                                        | 影响                                                                                   |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| 无 `ProjectRepository`，`collect_session_data.py` 直连 DB 查 `User.project_id → Project`       | `SessionDataFetcher` 需要决定：走直连 DB 还是新建 ProjectRepository                              |
| `level_label` 函数不存在，只有 `LEVEL_NAME` dict（`permission/level.py:44-51`）                     | `get_prompt_variables()` 应使用 `LEVEL_NAME` dict + helper                              |
| 两套记忆源：文件 `UserMemoryService.load_memory_context(user_name)` vs DB `User.long_term_memory` | 不可混为一谈；SessionContext 应同时保留两者（文件记忆→`history_summary`兼容，DB 记忆→`long_term_memory` 新字段） |
| `UserMemoryService` 按 `user_name` 查，不是 `user_id`                                          | `SessionDataFetcher` 需先查 username 再调 file-based 记忆                                   |
| `core` 所有属性为私有（`_xxx`），无 public getter                                                    | `SessionContext.create()` 需沿用 `getattr(core, "_xxx", None)` + fail-open 模式           |
| `collect_session_data.py` 全部 `get_session()` 直连，不走 Repository 层                           | `SessionDataFetcher` 应改走 Repository/Service 层，减少重复                                   |
| 缺失 Skill 概念：SOP 文档 + Tool（脚本）未封装为自包含单元                                                    | 需在 SessionContext 预留 Skill 接口，为后续 SOP+Tool 封装做准备                                     |
| 权限动态授权：用户可在 Session 生命周期内获得新权限（SOP/节点/文件可见性）                                              | SessionContext 需支持部分字段热更新，`collect_session_data.py` 需支持中途重跑刷新                        |

***

## 待决事项（需统一确认）

### D1：ProjectRepository 是否新建？

当前无 `ProjectRepository`，`collect_session_data.py` 直连 DB 查 `User.project_id → Project`。

- **A**：不新建，`SessionDataFetcher` 保留直查 `Project` 模型（与旧脚本一致，减少改动量）
- **B**：新建 `ProjectRepository`，`SessionDataFetcher` 走 Repository 层（架构更规范，但多一个文件）

> 你的选择：\_\_A\_\_

### D2：两套记忆源如何合并展示？

文件记忆（`UserMemoryService.load_memory_context(user_name)`，Markdown 文件）和 DB 记忆（`User.long_term_memory` 列）是**两个独立数据源**。当前生产路径只灌文件记忆到 `history_summary`，`collect_session_data.py` 只采 DB 记忆。

- **A**：都灌入 `long_term_memory` 字段——文件记忆追加到 DB 记忆前面，`history_summary` 作为计算属性合并两者
- **B**：分开存——文件记忆放 `extra["file_memory"]`，DB 记忆放 `long_term_memory`，`history_summary` 只合并 DB 记忆 + conversation\_summary
- **C**：只保留一个源——选择哪个？放弃另一个？

> 你的选择：\_选C,保留DB记忆，移除markdown记忆文件\_\_

### D3：`history_summary` 从字段改为 `@property` 是破坏性变更

当前代码 `session_factory.py:125` 有 `ctx.history_summary = memory_text` 赋值操作。改为 `@property` 后赋值会报错。需要决定赋值语义的替代方式（D2 的选择会影响这里）。

- **A**：赋值改为 `ctx.long_term_memory = memory_text`（文件记忆语义更匹配）
- **B**：赋值改为 `ctx.long_term_memory += "\n" + memory_text`（追加模式）
- **C**：`history_summary` 保留为可写字段，不改为 `@property`，由操作台方法统一维护

> 你的选择：\_\_根据D2选择处理\_\_

### D4：PermissionService 重复查询是否可接受？

`SessionDataFetcher.fetch()` 内部调 `PermissionService.build_permission_snapshot(user_id)` 查一次 DB，然后 `SessionFactory._build_context()` 又调一次获取 `PermissionSnapshot` 对象。Session 创建时查两次权限表。

- **A**：可接受——Session 创建只调一次，两次查询开销忽略不计
- **B**：优化——让 `SessionDataFetcher.fetch()` 同时返回 `PermissionSnapshot` 对象，避免重复查询

> 你的选择：\_\_\_\_\_\_参照D12的问题处理方法，移除`PermissionSnapshot` 对象，有用的字段作为普通字段获取。

### D5：`build_llm_messages()` 的两阶段 format 是否可接受？

WorkItem 级变量（`sop_text/available_tools/user_input/step_results/warnings`）在调用点先 `.format()` 替换，然后传给 `build_llm_messages()` 做 `format_map()` 替换 Session 级变量（`user_name/project_name/...`）。两阶段 format 不会冲突（已替换的文本不含 `{xxx}`），但需要确保 WorkItem 变量值中不意外包含 `{xxx}` 模式。

- **A**：可接受——正常业务文本不会出现 `{xxx}` 模式
- **B**：不可接受——改用其他模板引擎或统一 format 入口

> 你的选择：\_\_\_A\_\_

### D6：`compress_overflow` 触发时机

- **A**：`record_turn()` 内部自动检测并触发压缩（行为内聚，但 `record_turn` 变成 async 或需要 `ensure_future`）
- **B**：`SessionAgent.handle()` 中显式触发（调用方控制，但操作台不完整自治）

> 你的选择：\_A\_\_

### D7：`SessionDataFetcher.fetch()` 的依赖注入方式

- **A**：接收个体 service 参数（`permission_service=None, sop_registry=None`）——灵活但参数多
- **B**：接收 `core` 对象（`core=None`）——与 `SessionContext.create()` 一致，内部 `getattr(core, "_xxx", None)` 取依赖
- **C**：无参数，内部自动创建 service——与旧 `collect_session_data.py` 一致，但无法复用 `core` 中的单例

> 你的选择：\_\_\_\_\_\_

### D8：`recent_turns` 采集范围

当前 `collect_session_data.py` 按 `sender_user_id` 查最近 20 条（跨所有会话）。是否需要改为按 `conversation_id` 限定？

- **A**：保持跨会话查（用户维度，与旧脚本一致）
- **B**：改为按会话查（会话维度，更精确但需要 message 表有 conversation\_id 关联）

> 你的选择：\_\_**A**\_

### D9：是否在 `permission/level.py` 新增 `level_label()` helper？

`collect_session_data.py` 有私有 `_format_permission_level()`，`get_prompt_variables()` 也需要。是在公共模块统一一个 helper，还是各处内联？

- **A**：在 `permission/level.py` 新增 `level_label(level: int) -> str`，返回如 `"管理员(L5)"`
- **B**：各处内联 `f"{LEVEL_NAME.get(level, '未知')}(L{level})"`

> 你的选择：\_\_A\_\_

### D10：Skill 接口预留形式

后续计划将 SOP 文档 + Tool（脚本）封装为自包含的 Skill 单元，SessionAgent 应能按 skill\_id 查找并执行。本次重构需预留接口。

- **A**：最小预留——仅新增 `available_skills: list[str]` 字段 + `get_prompt_variables()` 中暴露 `{available_skills}`，Skill 注册/发现/执行留后续
- **B**：接口预留——新增 `available_skills` 字段 + `register_skill(skill_id)` / `unregister_skill(skill_id)` 方法 + `has_skill(skill_id)` 查询方法，内部暂用 `set` 存储
- **C**：完整预留——新增 `SkillSlot` 协议类（id + name + sop\_id + tool\_names），`SessionContext.skills: list[SkillSlot]`，`register_skill()` 接受 `SkillSlot` 对象

> 你的选择：\_**A**\_\_

### D11：热更新字段分类与 `refresh()` 方法

用户可在 Session 生命周期内动态获得新权限（授权后可访问新 SOP、新节点、新文件），SessionContext 应支持部分字段热更新。`collect_session_data.py` 支持中途重跑刷新这些字段。

**字段分类方案**：

| 分类                       | 字段                                                                                           | 说明                                     |
| ------------------------ | -------------------------------------------------------------------------------------------- | -------------------------------------- |
| 🔒 冻结（创建时灌注，不可更新）        | `user_id`, `user_name`, `user_position`, `conversation_id`, `created_at`                     | 用户身份与会话标识，整个 Session 生命周期不变            |
| 🔥 可热更新（运行时刷新）           | `permissions`（整块）, `company_name`, `company_type`, `sop_catalog_summary`, `available_skills` | 权限/SOP/技能——授权后需立即可见                    |
| 🔄 可热更新（需谨慎）             | `project_name`, `project_type`, `project_status`                                             | 项目变更不常见但可能发生                           |
| 📝 运行时自维护（不由 refresh 更新） | `message_history`, `conversation_summary`, `long_term_memory`                                | 由 record\_turn / compress / archive 维护 |

**`SessionContext.refresh()`** **方法**：接收 `SessionDataFetcher.fetch()` 的结果，只覆盖🔥和🔄类字段，保留🔒和📝类字段。

**`collect_session_data.py`** **热更新用法**：

```bash
# 初次采集（全量）
uv run python scripts/collect_session_data.py <user_id>

# 中途重跑，只输出可热更新的字段（运维验证用）
uv run python scripts/collect_session_data.py <user_id> --hot-update
```

`--hot-update` 模式：输出不变（session\_snapshot + prompt\_variables + errors），但在输出中标注每个字段是 🔒冻结 / 🔥可热更新 / 🔄可热更新(谨慎) / 📝运行时自维护，运维据此判断哪些字段值变化了。

- **A**：同意上述分类方案和 `--hot-update` 模式
- **B**：字段分类需调整（请说明哪些字段要改）
- **C**：不需要 `--hot-update` 模式，运维直接重跑对比即可

> 你的选择：\_\_A\_\_\_

***

## 实施顺序（7 Phase，含验证点）

```
Phase 1  →  Phase 2a  →  Phase 3  →  Phase 7a  →  Phase 6  → 【验证1】
                                                              ↓
         Phase 2b  →  Phase 4  →  Phase 7b  → 【验证2】
                                                      ↓
                              Phase 5  →  【最终验证】
```

***

## Phase 1：新建 `session_data_fetcher.py`

**目标**：将 `collect_session_data.py` 核心采集逻辑迁入 `emily_core/session/`，改走 Repository/Service 层。旧脚本保留为 CLI 薄壳。

### 1.1 新建文件

**文件**：`emily-core/emily_core/session/session_data_fetcher.py`

```python
"""SessionDataFetcher — Session 聚合根数据采集器。

生产路径 + 运维脚本共享。与旧 scripts/collect_session_data.py 对齐的数据项：
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

### 1.2 类设计

```python
class SessionDataFetcher:
    """Session 聚合根数据采集器。"""

    # 哨兵值（与旧脚本对齐）
    _SENTINEL = "XXXXXXXXXX"

    @staticmethod
    def fetch(user_id: str, conversation_id: str = "",
              permission_service=None, sop_registry=None) -> dict:
        """收集 Session 聚合根所需的全部数据。

        Args:
            user_id: 用户 UUID
            conversation_id: 会话 ID（可选，用于 recent_turns 限定）
            permission_service: PermissionService 实例（可选，无则自动创建）
            sop_registry: SOPIntentRegistry 实例（可选，无则跳过 SOP 目录）

        Returns:
            {
                "session_snapshot": { ... },
                "session_runtime":  { ... },
                "prompt_variables": { ... },
                "errors": [...],
            }
        """
```

### 1.3 子采集方法改造对照

| 旧 `_sub_fetch_*`                                            | 新实现（走 Repository/Service）                                                                    |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `_sub_fetch_user_name()` → `get_session()` 直查               | `UserRepository.get_by_id(user_id).username`                                                 |
| `_sub_fetch_user_position()` → `get_session()` 直查           | `UserRepository.get_by_id(user_id).position` → `_parse_position_json()`                      |
| `_sub_fetch_permissions()` → `PermissionService()`          | 参数注入 `permission_service`，无则 `PermissionService()`                                           |
| `_sub_fetch_project()` → `get_session()` 双跳查询               | `UserRepository.get_by_id()` → `user.project_id` → 直查 `Project` 模型（无 ProjectRepository，保留直查） |
| `_sub_fetch_user_memory_and_summary()` → `get_session()` 直查 | `UserRepository.get_by_id()` 的 `.long_term_memory` / `.conversation_summary`                 |
| `_sub_fetch_recent_turns()` → `get_session()` 直查            | `MessageRepository` 新增 `get_recent_by_user_id()` 或保留直查                                       |

**重要**：`_sub_fetch_user_name` 和 `_sub_fetch_user_position` 各查一次 DB，可合并为一次 `UserRepository.get_by_id()` 获取完整 User 对象后提取多个字段。`_sub_fetch_project` 也依赖 User 对象的 `project_id`，所以优化为：**一次** **`UserRepository.get_by_id()`** **→ 提取 username/position/project\_id/long\_term\_memory/conversation\_summary**。

### 1.4 迁移的工具函数

从 `collect_session_data.py` 直接搬入（不改逻辑）：

- `_parse_position_json()`
- `_format_recent_turns()`
- `_format_node_ids()`
- `_format_permission_level()` → 重命名为 `_level_label()` 以便复用
- `_translate_project_status()`

### 1.5 修改 `scripts/collect_session_data.py` 为薄壳

```python
# scripts/collect_session_data.py（简化后）
"""CLI 薄壳：验证 Session 数据采集。"""
from emily_core.session.session_data_fetcher import SessionDataFetcher

def _init_db_if_needed():
    """仅 CLI 需要初始化 DB 连接。"""
    ...

def collect_session_data(user_id, conversation_id=""):
    _init_db_if_needed()
    return SessionDataFetcher.fetch(user_id, conversation_id)

# __main__ 块保持现有输出格式不变（运维已习惯）
```

### 1.6 更新 `session/__init__.py`

新增导出：

```python
from .session_data_fetcher import SessionDataFetcher

__all__ = [
    ...,
    "SessionDataFetcher",
]
```

### 1.7 MessageRepository 扩展

在 `message_repo.py` 新增方法：

```python
@staticmethod
def get_recent_by_user_id(user_id: str, limit: int = 20) -> list[dict]:
    """获取用户最近的入站消息（OpenAI 格式）。

    Returns:
        [{"role": "user"|"assistant", "content": str,
          "sender_name": str, "time": str}, ...]
    """
```

***

## Phase 2a：SessionContext 加新字段（不加方法）

**目标**：扩展 SessionContext 字段，使其能承载 `SessionDataFetcher` 采集的全部数据。暂不加方法，不影响现有行为。

### 2a.1 字段变更

**新增字段**：

| 字段                     | 类型          | 来源                                       | 默认值  | 热更新         |
| ---------------------- | ----------- | ---------------------------------------- | ---- | ----------- |
| `user_position`        | `str`       | DB `users.position`                      | `""` | 🔒 冻结       |
| `company_name`         | `str`       | PermissionSnapshot                       | `""` | 🔥 可热更新     |
| `company_type`         | `str`       | PermissionSnapshot                       | `""` | 🔥 可热更新     |
| `project_name`         | `str`       | DB `projects.name`                       | `""` | 🔄 可热更新(谨慎) |
| `project_type`         | `str`       | DB `projects.lifecycle_stage`            | `""` | 🔄 可热更新(谨慎) |
| `project_status`       | `str`       | DB `projects.status`                     | `""` | 🔄 可热更新(谨慎) |
| `long_term_memory`     | `str`       | DB `users.long_term_memory`              | `""` | 📝 运行时自维护   |
| `conversation_summary` | `str`       | DB `users.conversation_summary`          | `""` | 📝 运行时自维护   |
| `created_at`           | `str`       | 创建时间戳                                    | `""` | 🔒 冻结       |
| `available_skills`     | `list[str]` | PermissionSnapshot.sop\_allow + Skill 注册 | `[]` | 🔥 可热更新     |

> `available_skills`：Skill 接口预留字段。当前初始值从 `permissions.sop_allow` 推导（有 SOP 权限 → 可用对应 Skill），后续 Skill 体系完善后由 `register_skill()` 动态管理。`get_prompt_variables()` 暴露 `{available_skills}` 占位符供 prompt 使用。

**热更新分类完整对照**：

| 分类          | 字段                                                                                           | 更新方式                                                                     |
| ----------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| 🔒 冻结       | `conversation_id`, `user_id`, `user_name`, `user_position`, `created_at`                     | 仅 `create()` 时灌注                                                         |
| 🔥 可热更新     | `permissions`(整块), `company_name`, `company_type`, `sop_catalog_summary`, `available_skills` | `refresh()` 从 SessionDataFetcher 刷新                                      |
| 🔄 可热更新(谨慎) | `project_name`, `project_type`, `project_status`                                             | `refresh()` 刷新，但变更频率极低                                                   |
| 📝 运行时自维护   | `message_history`, `long_term_memory`, `conversation_summary`                                | `record_turn()` / `compress_overflow()` / `persist_and_consolidate()` 维护 |

**删除僵尸字段**：

| 字段                     | 删除理由                  |
| ---------------------- | --------------------- |
| `user_preferences`     | 从无数据源，`extra` dict 足够 |
| `tool_catalog_summary` | prompt 中动态构建已够用       |
| `schema_summary`       | 从无消费者                 |
| `system_prompt`        | prompt 拼装在各调用点完成      |
| `perm_list`            | 已废弃的兼容字段              |

**重组** **`history_summary`**：从数据字段改为**计算属性**，合并 `long_term_memory` + `conversation_summary`：

```python
@property
def history_summary(self) -> str:
    """合并摘要：长期记忆 + 对话摘要（向后兼容）。"""
    parts = []
    if self.long_term_memory:
        parts.append(self.long_term_memory)
    if self.conversation_summary:
        parts.append(self.conversation_summary)
    return "\n".join(parts)
```

注意：当前 `history_summary` 是 `str` 字段，改为 `@property` 是**破坏性变更**（`ctx.history_summary = "xxx"` 赋值会报错）。需检查所有写入点：

- `session_factory.py:125`：`ctx.history_summary = memory_text` — 需改为 `ctx.long_term_memory = memory_text`（文件记忆语义更匹配）

**影响点排查**：

- `session_factory.py:125` — `ctx.history_summary = memory_text` → 改为 `ctx.long_term_memory = memory_text`
- `session_agent.py:568` — `format_message_history()` 读 `self.context.message_history`，不涉及 `history_summary`
- `collect_session_data.py` — 不操作 `SessionContext`
- 其他文件 grep `history_summary` 确认无其他写入点

***

## Phase 3：SessionFactory 简化

**目标**：`_build_context()` 改为调用 `SessionDataFetcher` + 直接赋新字段，消除手写的零散查询。

### 3.1 `_build_context()` 重写

```python
def _build_context(self, message, user_id):
    ctx = SessionContext(
        conversation_id=message.conversation_id,
        user_id=user_id,
        user_name=message.sender_name or "",
        current_datetime=datetime.now(timezone.utc).isoformat(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    core = self._core
    if core is None:
        return ctx

    # 一次性全量采集（走 Repository/Service 层）
    from ...session.session_data_fetcher import SessionDataFetcher
    perm_service = getattr(core, "_permission_service", None)
    sop_registry = getattr(core, "_sop_intent_registry", None)

    data = SessionDataFetcher.fetch(
        user_id=user_id,
        conversation_id=message.conversation_id,
        permission_service=perm_service,
        sop_registry=sop_registry,
    )
    snapshot = data["session_snapshot"]

    # 覆盖灌注（DB 值优先于 sender_name）
    ctx.user_name = snapshot.get("user_name") or message.sender_name or ""
    ctx.user_position = snapshot.get("user_position", "")
    ctx.project_name = snapshot.get("project_name", "")
    ctx.project_type = snapshot.get("project_type", "")
    ctx.project_status = snapshot.get("project_status", "")
    ctx.long_term_memory = snapshot.get("user_memory", "")
    ctx.conversation_summary = snapshot.get("conversation_summary", "")

    # 权限快照
    perm_data = snapshot.get("permissions", {})
    if perm_data and perm_service is not None:
        # 直接使用 PermissionService 已生成的快照（避免重复查询）
        ctx.permissions = perm_service.build_permission_snapshot(user_id)
        ctx.company_name = ctx.permissions.company_name
        ctx.company_type = ctx.permissions.company_type

    # SOP 目录
    if sop_registry:
        sops = sop_registry.list_loaded_sops()
        if sops:
            ctx.sop_catalog_summary = f"可用业务流程 ({len(sops)}): {', '.join(sops[:15])}"

    # 文件记忆（UserMemoryService，与 DB long_term_memory 不同）
    memory_service = getattr(core, "_user_memory_service", None)
    if memory_service and ctx.user_name:
        try:
            memory_text = memory_service.load_memory_context(ctx.user_name)
            if memory_text:
                # 文件记忆追加到 long_term_memory（与 DB 记忆合并展示）
                if ctx.long_term_memory:
                    ctx.long_term_memory = memory_text + "\n" + ctx.long_term_memory
                else:
                    ctx.long_term_memory = memory_text
        except Exception:
            pass

    # 最近对话注入到 message_history
    runtime = data.get("session_runtime", {})
    recent = runtime.get("recent_turns", [])
    if recent:
        ctx.message_history = [
            {"role": t["role"], "content": t["content"],
             "name": t.get("sender_name") if t["role"] == "user" else None}
            for t in recent
        ]

    if data.get("errors"):
        logger.warning("Session[%s] data fetch errors: %s",
                       message.conversation_id, " | ".join(data["errors"]))

    return ctx
```

### 3.2 权限快照避免重复查询

`SessionDataFetcher.fetch()` 内部的 `_sub_fetch_permissions` 已调用 `PermissionService.build_permission_snapshot()`。但 `SessionFactory._build_context()` 也需要 `PermissionSnapshot` 对象设到 `ctx.permissions`。

**方案**：`SessionDataFetcher.fetch()` 返回的 `session_snapshot.permissions` 已包含完整 dict。`_build_context()` 用 `perm_service.build_permission_snapshot(user_id)` 直接获取 `PermissionSnapshot` 对象（已在 SessionDataFetcher 内查过一次，PermissionService 无缓存时会再查一次 DB——可接受，因为只在 Session 创建时调一次）。

**后续优化**：Phase 2b 中 `SessionContext.create()` 将统一采集路径，消除重复查询。

***

## Phase 7a：`collect_session_data.py` 改薄壳 + 热更新支持

（Phase 7a 提前到 Phase 3 之后，因为这是第一个验证点）

### 7a.1 简化脚本

`scripts/collect_session_data.py` 内部改为调 `SessionDataFetcher.fetch()`，保留 `_init_db_if_needed()` 和 `__main__` 输出格式。

### 7a.2 新增 `--hot-update` 模式

```bash
# 初次采集（全量输出，与旧版一致）
uv run python scripts/collect_session_data.py <user_id>

# 热更新模式：输出中标注每个字段的热更新分类，运维据此判断哪些值变了
uv run python scripts/collect_session_data.py <user_id> --hot-update
```

`--hot-update` 模式的输出变化：

- session\_snapshot 和 prompt\_variables 输出格式不变
- 每个字段后追加热更新分类标注：🔒冻结 / 🔥可热更新 / 🔄可热更新(谨慎) / 📝运行时自维护
- 末尾汇总：可热更新字段的当前值 vs 上次采集值（如有 `.last_snapshot.json` 缓存）

```python
# scripts/collect_session_data.py 新增
HOT_UPDATE_FIELDS = {
    "🔒": ["user_id", "user_name", "user_position", "conversation_id", "created_at"],
    "🔥": ["permissions", "company_name", "company_type", "sop_catalog_summary", "available_skills"],
    "🔄": ["project_name", "project_type", "project_status"],
    "📝": ["long_term_memory", "conversation_summary", "recent_turns"],
}
```

### 7a.3 生产路径热更新触发

在 `SessionAgent.handle()` 中，每 N 条消息后自动调用 `self.context.refresh()`：

```python
# SessionAgent._handle_impl 末尾
if len(self.context.message_history) % 10 == 0:  # 每 10 条消息刷新一次权限
    await self._maybe_refresh_context()
```

```python
async def _maybe_refresh_context(self):
    """定期刷新 SessionContext 的可热更新字段。"""
    try:
        from ..session.session_data_fetcher import SessionDataFetcher
        data = SessionDataFetcher.fetch(
            user_id=self.context.user_id,
            conversation_id=self.conversation_id,
            permission_service=getattr(self, "_perm_service", None),
            sop_registry=self._sop_intent_registry,
        )
        updated = self.context.refresh(data)
        if updated:
            logger.info("Session[%s] auto-refreshed: %s", self.conversation_id, updated)
    except Exception as e:
        logger.debug("Session[%s] refresh skipped: %s", self.conversation_id, e)
```

### 7a.4 验证命令

```bash
$env:PYTHONIOENCODING="utf-8"
uv run python scripts/collect_session_data.py <user_id>
uv run python scripts/collect_session_data.py <user_id> --hot-update
```

**验收**：

1. 不带 `--hot-update`：输出与旧版本一致（session\_snapshot + prompt\_variables + errors）
2. 带 `--hot-update`：字段后标注热更新分类，🔥类字段可清晰识别

***

## Phase 6：Prompt 模板增强

**目标**：在 prompt 模板中增加用户上下文占位符，使 LLM 能感知用户身份和项目信息。

### 6.1 `session.md` 修改

在 `## 当前时间` 之前插入：

```markdown
## 当前用户
{user_name}，{user_position}，{user_company}（{user_company_type}）
权限等级：{user_permission_level}

## 当前项目
{project_name}（{project_type}，{project_status}）
```

### 6.2 `workitem.md` / `planner.md` 修改

在现有内容之前增加：

```markdown
## 当前用户
{user_name}，{user_position}，{user_company}

## 当前项目
{project_name}（{project_status}）
```

### 6.3 `prompt_loader.py` 硬编码回退同步更新

`_DEFAULTS` dict 中 "session" / "workitem" / "planner" 的硬编码回退文本也需同步添加对应占位符，确保文件缺失时仍可用。

### 6.4 SessionAgent 临时适配

在 `_recognize_intent()` 的 `_SESSION_SYSTEM_PROMPT.format()` 调用中，新增变量注入：

```python
# 之前
system_prompt = _SESSION_SYSTEM_PROMPT.format(
    sop_catalog=sop_catalog,
    current_datetime=_beijing_now_str(),
)

# 之后（临时，Phase 4 会迁移到 build_llm_messages）
from ..permission.level import LEVEL_NAME
system_prompt = _SESSION_SYSTEM_PROMPT.format(
    sop_catalog=sop_catalog,
    current_datetime=_beijing_now_str(),
    user_name=self.context.user_name,
    user_position=self.context.user_position,
    user_company=self.context.company_name,
    user_company_type=self.context.company_type,
    user_permission_level=f"{LEVEL_NAME.get(self.context.permissions.permission_level, '未知')}(L{self.context.permissions.permission_level})",
    project_name=self.context.project_name,
    project_type=self.context.project_type,
    project_status=self.context.project_status,
)
```

**验证**：`uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"` 检查 LLM 回复是否体现了用户职务/项目名上下文。

***

## 【验证1】Phase 1 + 2a + 3 + 7a + 6

1. `uv run python scripts/collect_session_data.py <user_id>` — 输出与旧版一致
2. `uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"` — Session 创建正常，新字段有值
3. 检查 LLM 回复是否体现用户职务/项目名上下文
4. `uv run python scripts/smoke_test.py` — 全链路不断

***

## Phase 2b：SessionContext 加操作台方法

**目标**：给 SessionContext 加上操作台方法，使行为内聚到聚合根。

### 2b.1 `SessionContext.create()` 工厂方法

```python
@classmethod
def create(cls, user_id: str, conversation_id: str,
           sender_name: str, core) -> "SessionContext":
    """工厂方法：一次性全量灌注创建 SessionContext。

    调用 SessionDataFetcher.fetch() 获取全部数据。
    core 参数提供 Service/Repository 依赖。
    """
```

实现逻辑与 Phase 3 的 `_build_context()` 重写版本对齐，直接搬入。`SessionFactory._build_context()` 改为一行委托：

```python
def _build_context(self, message, user_id):
    return SessionContext.create(
        user_id=user_id,
        conversation_id=message.conversation_id,
        sender_name=message.sender_name or "",
        core=self._core,
    )
```

### 2b.2 `record_turn()` 方法

```python
def record_turn(self, user_content: str, assistant_content: str,
                sender_name: str = "") -> None:
    """记录一轮对话到 message_history（含溢出检查）。"""
    self.message_history.append({
        "role": "user",
        "content": (user_content or "")[:2000],
        "name": sender_name if sender_name else None,
    })
    self.message_history.append({
        "role": "assistant",
        "content": (assistant_content or "")[:2000],
    })
    # 溢出检查由 compress_overflow 处理
```

### 2b.3 `build_llm_messages()` 方法

```python
def build_llm_messages(
    self,
    system_prompt_template: str,
    current_user_msg: str = "",
    sender_name: str = "",
    pending_context: str = "",
) -> list[dict]:
    """组装 LLM 调用的 messages 列表。

    统一出口：[system_prompt(已format)] + message_history + [pending_context?] + [current_user]
    """
    # 1. 格式化 system prompt（自动填充变量）
    variables = self.get_prompt_variables()
    system_prompt = system_prompt_template.format_map(
        collections.ChainMap(variables, collections.defaultdict(str))
    )

    # 2. 组装
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(self.message_history)

    # 3. pending 上下文
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

### 2b.4 `get_prompt_variables()` 方法

```python
def get_prompt_variables(self) -> dict[str, str]:
    """返回 prompt 模板变量映射。"""
    from ..permission.level import LEVEL_NAME
    level = self.permissions.permission_level
    level_str = f"{LEVEL_NAME.get(level, '未知')}(L{level})"
    return {
        "{project_name}": self.project_name,
        "{project_type}": self.project_type,
        "{project_status}": self.project_status,
        "{user_name}": self.user_name,
        "{user_position}": self.user_position,
        "{user_company}": self.company_name,
        "{user_company_type}": self.company_type,
        "{user_permission_level}": level_str,
        "{conversation_summary}": self.conversation_summary,
        "{user_memory}": self.long_term_memory,
        "{sop_catalog}": self.sop_catalog_summary,
        "{current_datetime}": self.current_datetime,
    }
```

### 2b.5 `persist_and_consolidate()` 方法

从 `SessionAgent._persist_archive()` + `_consolidate_conversation_summary()` 迁入：

```python
async def persist_and_consolidate(self, llm_client=None) -> None:
    """归档：持久化到 session_archives 表 + 整合对话摘要回写 users 表。"""
    # 1. 持久化到 session_archives 表
    await self._persist_archive()
    # 2. 整合 conversation_summary 到 users 表
    if self.user_id and llm_client:
        await self._consolidate_summary(llm_client)

async def _persist_archive(self) -> None:
    """持久化到 session_archives 表。"""
    # 从 SessionAgent._persist_archive() 搬入

async def _consolidate_summary(self, llm_client) -> None:
    """整合 conversation_summary 到 users 表。"""
    # 从 SessionAgent._consolidate_conversation_summary() 搬入
```

### 2b.6 `compress_overflow()` 方法

从 `SessionAgent._compress_overflow()` 迁入，使用 `session_context.py` 中已有的 `build_compress_messages()` 函数：

```python
async def compress_overflow(self, llm_client=None) -> None:
    """message_history 溢出压缩。"""
    _MAX_HISTORY = 40
    _COMPRESS_BATCH = 20

    if len(self.message_history) <= _MAX_HISTORY:
        return

    batch = self.message_history[:_COMPRESS_BATCH]
    self.message_history = self.message_history[_COMPRESS_BATCH:]

    if not llm_client:
        return  # fail-open: 丢弃旧消息

    # 提取已有摘要
    existing_summary = ""
    if (self.message_history
            and self.message_history[0].get("name") == "system"
            and "[对话历史摘要]" in self.message_history[0].get("content", "")):
        existing_summary = self.message_history[0]["content"]
        self.message_history = self.message_history[1:]

    compress_msgs = build_compress_messages(batch, existing_summary)
    try:
        result = await llm_client.chat_messages(compress_msgs)
        summary_content = result.get("content", "") or ""
        if summary_content and len(summary_content) > 20:
            self.message_history.insert(0, {
                "role": "user",
                "content": f"[对话历史摘要] {summary_content.strip()}",
                "name": "system",
            })
    except Exception:
        pass  # fail-open
```

### 2b.7 `refresh()` 方法（热更新）

```python
def refresh(self, data: dict, permission_service=None, sop_registry=None) -> list[str]:
    """从 SessionDataFetcher.fetch() 结果刷新可热更新字段。

    只覆盖 🔥可热更新 和 🔄可热更新(谨慎) 字段。
    保留 🔒冻结 和 📝运行时自维护 字段不变。

    Args:
        data: SessionDataFetcher.fetch() 的返回值
        permission_service: PermissionService 实例（用于重建 PermissionSnapshot）
        sop_registry: SOPIntentRegistry 实例（用于重建 SOP 目录）

    Returns:
        list[str]: 被更新的字段名列表（供日志/调试）
    """
    snapshot = data.get("session_snapshot", {})
    updated = []

    # 🔥 权限快照（整块替换）
    if permission_service:
        try:
            self.permissions = permission_service.build_permission_snapshot(self.user_id)
            self.company_name = self.permissions.company_name
            self.company_type = self.permissions.company_type
            updated.extend(["permissions", "company_name", "company_type"])
        except Exception:
            pass

    # 🔥 SOP 目录
    if sop_registry:
        sops = sop_registry.list_loaded_sops()
        if sops:
            new_summary = f"可用业务流程 ({len(sops)}): {', '.join(sops[:15])}"
            if new_summary != self.sop_catalog_summary:
                self.sop_catalog_summary = new_summary
                updated.append("sop_catalog_summary")

    # 🔥 available_skills（从权限 sop_allow 推导）
    new_skills = list(self.permissions.sop_allow)
    if new_skills != self.available_skills:
        self.available_skills = new_skills
        updated.append("available_skills")

    # 🔄 项目上下文（谨慎更新）
    for key, snap_key in [
        ("project_name", "project_name"),
        ("project_type", "project_type"),
        ("project_status", "project_status"),
    ]:
        new_val = snapshot.get(snap_key, "")
        old_val = getattr(self, key)
        if new_val != old_val:
            setattr(self, key, new_val)
            updated.append(key)

    if updated:
        logger.info("SessionContext[%s] refreshed: %s", self.conversation_id, updated)

    return updated
```

### 2b.8 Skill 接口预留方法

```python
# ── Skill 接口预留（D10 决定具体形式，此处为方案 B 的骨架）──

def register_skill(self, skill_id: str) -> None:
    """注册一个可用 Skill（运行时动态添加）。

    当前实现：向 available_skills 追加。
    后续 Skill 体系完善后：接受 SkillSlot 对象，管理完整生命周期。
    """
    if skill_id not in self.available_skills:
        self.available_skills.append(skill_id)
        logger.debug("SessionContext[%s] skill registered: %s",
                     self.conversation_id, skill_id)

def unregister_skill(self, skill_id: str) -> None:
    """注销一个 Skill。"""
    if skill_id in self.available_skills:
        self.available_skills.remove(skill_id)
        logger.debug("SessionContext[%s] skill unregistered: %s",
                     self.conversation_id, skill_id)

def has_skill(self, skill_id: str) -> bool:
    """检查是否拥有指定 Skill。"""
    return skill_id in self.available_skills
```

**`get_prompt_variables()`** **同步更新**：

```python
def get_prompt_variables(self) -> dict[str, str]:
    """返回 prompt 模板变量映射。"""
    from ..permission.level import LEVEL_NAME
    level = self.permissions.permission_level
    level_str = f"{LEVEL_NAME.get(level, '未知')}(L{level})"
    return {
        ...
        "{available_skills}": ", ".join(self.available_skills) if self.available_skills else "（无）",
    }
```

***

## Phase 4：SessionAgent 瘦身

**目标**：将 SessionAgent 中的"管账"操作全部转调 SessionContext 操作台方法。

### 4.1 剥离消息记录

```python
# SessionAgent._record_turn — 改后
def _record_turn(self, message, reply_content):
    self.context.record_turn(
        user_content=(message.content or "")[:2000],
        assistant_content=(reply_content or "")[:2000],
        sender_name=getattr(message, "sender_name", "") or "",
    )
    # 溢出压缩由 record_turn 内部或 handle() 后触发
    if len(self.context.message_history) > 40:
        asyncio.ensure_future(self.context.compress_overflow(llm_client=self._llm))
```

### 4.2 剥离 LLM 上下文拼装

```python
# SessionAgent._recognize_intent — 改后
# 之前的 7 行手工拼装 → 改为：
pending_text = self._build_pending_context()
full_messages = self.context.build_llm_messages(
    system_prompt_template=_SESSION_SYSTEM_PROMPT,
    current_user_msg=content,
    sender_name=sender,
    pending_context=pending_text,
)
```

需新增 `_build_pending_context()` 辅助方法，提取 `_get_pending_event()` 的 pending 注入逻辑。

### 4.3 剥离归档

```python
# SessionAgent.archive — 改后
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

删除 `SessionAgent._persist_archive()` 和 `_consolidate_conversation_summary()` 两个方法。

### 4.4 删除 SessionAgent 中的常量

`_MAX_HISTORY_MESSAGES` 和 `_COMPRESS_BATCH_SIZE` 已迁入 `SessionContext`，从 `session_agent.py` 删除。

***

## Phase 7b：新建 `scripts/build_llm_prompt.py`

**目标**：运维 CLI 薄壳，验证 prompt 拼装结果。输入 user\_id + prompt 名，输出完整 messages 列表。

### 7b.1 脚本设计

```python
# scripts/build_llm_prompt.py
"""CLI 薄壳：验证 LLM Prompt 拼装。

用法：
    uv run python scripts/build_llm_prompt.py <user_id> --prompt session
    uv run python scripts/build_llm_prompt.py <user_id> --prompt workitem
    uv run python scripts/build_llm_prompt.py <user_id> --prompt planner --msg "帮我创建事件"

输出：
    - prompt_variables: 所有模板变量的值
    - messages: 完整 messages 列表（role + content 前 200 字）
    - token 估算（基于字符数粗估）
"""
```

核心流程：

1. `_init_db_if_needed()` 初始化 DB
2. `SessionDataFetcher.fetch(user_id)` 采集数据
3. 手工拼装 `SessionContext`（从 snapshot 填充字段）
4. `load_prompt(prompt_name)` 加载模板
5. `ctx.build_llm_messages(system_prompt_template, current_user_msg=...)` 拼装
6. 格式化输出 messages 列表

### 7b.2 验证命令

```bash
uv run python scripts/build_llm_prompt.py <user_id> --prompt session --msg "帮我创建事件"
```

**验收**：

- prompt\_variables 中新字段有值
- messages 列表包含 system prompt（变量已替换）+ message\_history + user message
- 无未替换的 `{xxx}` 占位符残留

***

## 【验证2】Phase 2b + 4 + 7b

1. `uv run python scripts/collect_session_data.py <user_id>` — 仍一致
2. `uv run python scripts/collect_session_data.py <user_id> --hot-update` — 字段分类标注正确
3. `uv run python scripts/build_llm_prompt.py <user_id> --prompt session` — prompt 拼装正确，`{available_skills}` 占位符已替换
4. emy-test 完整流程（创建事件 → 确认 → 等待归档）— 归档和摘要回写正常
5. 检查日志：`_record_turn` / `archive` / `_compress_overflow` 是否走 SessionContext 方法
6. 热更新验证：授权新 SOP 后，检查 `context.refresh()` 日志输出是否刷新了 `available_skills`

***

## Phase 5：WorkItemAgent 调整

**目标**：`_llm_plan()` 和 `_llm_synthesize_reply()` 改用 `context.build_llm_messages()`。

### 5.1 `_llm_plan()` 改造

```python
# workitem_agent.py _llm_plan — 改后
async def _llm_plan(self, wi, context):
    ...
    planner_prompt = _load_planner_prompt()

    # WorkItem 级变量先 format
    system_prompt = planner_prompt.format(
        sop_text=sop_text[:4000] if sop_text else f"SOP: {wi.sop_id or '未知'}（全文未加载）",
        user_input=wi.user_input,
        available_tools=tools_text,
    )

    # 改用 SessionContext.build_llm_messages() 拼装整体
    session_ctx = context.get_session_context() if context else None
    if session_ctx:
        full_messages = session_ctx.build_llm_messages(
            system_prompt_template=system_prompt,  # 已 format 过 WorkItem 变量
            current_user_msg=f"Plan for: {wi.user_input[:200]}",
        )
    else:
        # 回退：无 SessionContext 时手工拼装
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.append({"role": "user", "content": f"Plan for: {wi.user_input[:200]}"})

    ...
```

**注意**：`build_llm_messages()` 内部会调用 `get_prompt_variables()` 对模板做 `format_map`。但 `_llm_plan` 已经手动 `.format()` 过 WorkItem 级变量了。两者不冲突——`format_map` 对已替换的文本不再替换（`{sop_text}` 已变成实际内容），对未替换的 Session 级变量（如 `{user_name}`）自动填充。需确认：planner prompt 模板中 `{user_name}` 等 Session 级占位符确实存在（Phase 6 已添加），且 `format_map(defaultdict(str))` 对不存在于 `prompt_variables` 中的 key 不报错。

### 5.2 `_llm_synthesize_reply()` 同理

```python
# workitem_agent.py _llm_synthesize_reply — 改后
system_prompt = _load_workitem_prompt().format(
    available_tools=self._build_tools_text(),
    sop_text=self.injector.get_context_text()[:3000] if self.injector else f"SOP: {wi.sop_id or '未知'}",
    user_input=(getattr(wi, "user_input", "") or "")[:1000],
    step_results=steps_text[:2000],
    warnings=warnings_text,
)

session_ctx = ...  # 从 message_history 来源获取
if session_ctx:
    full_messages = session_ctx.build_llm_messages(
        system_prompt_template=system_prompt,
        current_user_msg=f"合成回复: {getattr(wi, 'user_input', '?')[:100]}",
    )
else:
    full_messages = [{"role": "system", "content": system_prompt}]
    full_messages.append({"role": "user", "content": f"合成回复: {getattr(wi, 'user_input', '?')[:100]}"})
```

***

## 【最终验证】

1. `uv run python scripts/smoke_test.py` — 全链路不断
2. emy-test 完整业务流程（创建事件 → 确认 → 创建任务 → 查询 → 等归档）
3. 检查 Session 归档：`docker exec emily-postgres psql -U emily -d emily -c "SELECT conversation_id, turn_count, archive_reason FROM session_archives ORDER BY archived_at DESC LIMIT 5;"`
4. 检查 conversation\_summary 回写：`docker exec emily-postgres psql -U emily -d emily -c "SELECT username, LEFT(conversation_summary, 100) FROM users WHERE conversation_summary != '' LIMIT 5;"`
5. 运维脚本：`uv run python scripts/collect_session_data.py <user_id>` 和 `uv run python scripts/build_llm_prompt.py <user_id> --prompt session`
6. Skill 预留验证：检查 `SessionContext.available_skills` 初始值来自 `permissions.sop_allow`，`has_skill()` / `register_skill()` 可正常调用
7. 热更新验证：`uv run python scripts/collect_session_data.py <user_id> --hot-update` 标注正确，生产路径 `refresh()` 在每 10 条消息后自动触发

***

## 文件清单总览

| 文件                                                          | Phase | 改动类型                                                 |
| ----------------------------------------------------------- | ----- | ---------------------------------------------------- |
| `emily-core/emily_core/session/session_data_fetcher.py`     | 1     | **新建**                                               |
| `emily-core/emily_core/session/session_context.py`          | 2a/2b | **重写**（加字段 + 加方法 + 删僵尸字段 + Skill 预留 + 热更新）           |
| `emily-core/emily_core/session/__init__.py`                 | 1/2b  | **修改**（新增导出）                                         |
| `emily-core/emily_core/adapters/session/session_factory.py` | 3/2b  | **简化**（→ SessionContext.create()）                    |
| `emily-core/emily_core/session/session_agent.py`            | 6/4   | **瘦身**（剥离 4 个方法）                                     |
| `emily-core/emily_core/workitem/workitem_agent.py`          | 5     | **调整**（改用 build\_llm\_messages）                      |
| `emily-core/emily_core/repositories/message_repo.py`        | 1     | **修改**（新增 get\_recent\_by\_user\_id）                 |
| `scripts/collect_session_data.py`                           | 7a    | **简化**（→ 薄壳调 SessionDataFetcher + `--hot-update` 模式） |
| `scripts/build_llm_prompt.py`                               | 7b    | **新建**                                               |
| `emily-data/prompts/session.md`                             | 6     | **修改**（加用户上下文占位符）                                    |
| `emily-data/prompts/workitem.md`                            | 6     | **修改**（加用户上下文占位符）                                    |
| `emily-core/emily_core/infrastructure/llm/prompt_loader.py` | 6     | **修改**（硬编码回退同步）                                      |

***

## 回退策略

| Phase    | 回退方式                                                        |
| -------- | ----------------------------------------------------------- |
| Phase 1  | 直接删除 `session_data_fetcher.py`，恢复 `collect_session_data.py` |
| Phase 2a | 恢复旧字段，删除新字段                                                 |
| Phase 3  | 恢复旧 `_build_context()`                                      |
| Phase 6  | 恢复原 .md 文件和 prompt\_loader.py                               |
| Phase 2b | 逐方法回退，每个方法独立迁移                                              |
| Phase 4  | 逐方法恢复 SessionAgent                                          |
| Phase 5  | 恢复 `if session_ctx else ...` 回退路径                           |

