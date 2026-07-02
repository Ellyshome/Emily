# Session 聚合根数据获取 — 可实施性分表

> **版本**: v1.0
> **日期**: 2026-07-02
> **说明**: 将 [Session聚合根数据获取清单.md](Session聚合根数据获取清单.md) 中的 37 个参数，按"现在能不能写成脚本"分成两张表。

---

## 表 A：可直接写脚本获取的参数（27 项）

### A-1 注入 Prompt 的（7 项）

| # | 参数名 | 模板变量 | 获取方式 | 数据来源 |
|---|--------|----------|----------|----------|
| 1 | `user_name` | `{user_name}` | `message.sender_name` 直接取 | `StandardMessage.sender_name` |
| 2 | `user_company` | `{user_company}` | `PermissionService.build_permission_snapshot(user_id)` → `snapshot.company_name` | DB `company_info.company_name` |
| 3 | `user_company_type` | `{user_company_type}` | 同上 → `snapshot.company_type` | DB `company_info.type` |
| 4 | `user_department` | `{user_department}` | 同上 → `snapshot.department` | DB `company_info.department`，经 `_primary_department()` |
| 5 | `user_permission_level` | `{user_permission_level}` | 同上 → `snapshot.permission_level` | DB `users.permission_level` |
| 6 | `current_node_ids` | `{current_node_ids}` | 同上 → `snapshot.authorized_node_ids`，join 为 `"、"` 分隔字符串 | DB `company_info.function_scope`，经 `_derive_authorized_nodes()` |
| 7 | `user_longterm_memory` | `{user_longterm_memory}` | `UserMemoryService.load_memory_context(user_name)` | 文件 `emily-data/user_memory/{user_name}-长期记忆.md` |

### A-2 注入 Prompt 但不存聚合根的（2 项）

| # | 参数名 | 模板变量 | 获取方式 | 数据来源 | 说明 |
|---|--------|----------|----------|----------|------|
| 8 | `current_datetime` | `{current_datetime}` | `_beijing_now_str()` 实时计算 | 系统时钟 | 每次 `handle()` 时动态取值，不存 Session |
| 9 | `sop_catalog` | `{sop_catalog}` | `SOPIntentRegistry.dump_as_text()` | SOP 注册表内存数据 | 同上 |

### A-3 程序鉴权用（12 项）

全部来自 `PermissionService.build_permission_snapshot(user_id)` 一次调用返回的 `PermissionSnapshot` 对象。

| # | 参数名 | 数据来源 |
|---|--------|----------|
| 10 | `company_id` | DB `users.company` FK → `company_info.id` |
| 11 | `partner_ids` | DB `company_info.partners` JSON 数组 |
| 12 | `scopes` | DB `company_info.scope` JSON 数组 |
| 13 | `project_ids` | 经 `_derive_project_ids()` 从 user + company 推导 |
| 14 | `sop_allow` | `sop_business_flows` + `sop_bindings` + `permission_groups`，经 `_compute_sop_allow()` |
| 15 | `db_perms` | 经 `_derive_db_perms()` 从 `permission_level` 推导 |
| 16 | `info_level` | 经 `_derive_info_level()` 从 `permission_level` 推导 |
| 17 | `granted_codes` | DB `permission_grants` 表 |
| 18 | `denied_codes` | 从 SOP deny 绑定推导 |
| 19 | `supervisor_id` | DB `users.supervisor_id` |
| 20 | `permission_version` | 权限缓存版本号 |
| 21 | `extra_perms` | 当前仅注入 `{"user_id": user_id}` |

### A-4 Session 元数据（3 项）

| # | 参数名 | 获取方式 | 数据来源 |
|---|--------|----------|----------|
| 22 | `conversation_id` | `message.conversation_id` 直接取 | `StandardMessage.conversation_id` |
| 23 | `user_id` | Adapter 层传入 | 上游 `UserBindingService.get_or_create_user()` |
| 24 | `created_at` | `datetime.now(timezone.utc).isoformat()` | 系统时钟 |

### A-5 运行时初始值（4 项）— 无需获取，天生为空

| # | 参数名 | 初始值 | 说明 |
|---|--------|--------|------|
| 25 | `cached_lookups` | `{}` | Agent/Hook 运行时自行写入 |
| 26 | `active_focus` | `None` | `handle()` 时 SessionAgent 设置 |
| 27 | `pending_confirms` | `deque()` | WorkItem 执行完成后入队 |
| 28 | `baggage` | `{}` | Hook/节点间临时传参 |

---

## 表 B：当前不能或不能完全获取的参数（9 项）

### B-1 缺数据源（3 项）

| # | 参数名 | 模板变量 | 当前状态 | 卡点 | 需要做的 |
|---|--------|----------|----------|------|----------|
| 1 | `project_name` | `{project_name}` | ❌ 取不到 | `ProjectService` 类不存在。`projects` 表有 `name` 字段，但没有 Service 封装查询，也没有 "用户→项目" 的映射链路 | 新建 `ProjectService`，实现 `get_by_user(user_id)`。映射路径：`User.company` → `CompanyInfo` → 通过项目负责人或参建单位关联 `projects` |
| 2 | `project_status` | `{project_status}` | ❌ 取不到 | 同上，`projects.status` 字段存在但无查询链路 | 同 `project_name`，读 `projects.status` |
| 3 | `project_type` | `{project_type}` | ❌ 取不到 | **数据库根本没有这个字段**。`projects` 表 [models.py:160-183] 无 `type` 列 | 二选一：(a) `projects` 表加 `project_type` 字段 (b) 从 `lifecycle_stage` + `company_type` 推导行业性质。推荐 (a) |

### B-2 数据存在但未接入（3 项）

| # | 参数名 | 模板变量 / 用途 | 当前状态 | 卡点 | 需要做的 |
|---|--------|----------------|----------|------|----------|
| 4 | `user_position` | `{user_position}` | ⚠️ 半通 | DB `users.position` [models.py:85] 是 JSON 数组字段，数据在库里。但 `PermissionService._do_build_snapshot()` [permission_service.py:81-128] 没读它，`PermissionSnapshot` 也没 `position` 字段 | 两步：(a) `PermissionSnapshot` 新增 `position: str` 字段 (b) `_do_build_snapshot()` 中读 `User.position`，取 JSON 数组第一个岗位填入 |
| 5 | `recent_turns` | `{recent_turns}` | ⚠️ 半通 | `ChatArchiveService.get_conversation_history()` [chat_archive_service.py:97] 能返回 `Message` 对象列表，数据在 DB。但：(a) 拼组脚本没调它 (b) 返回的是原始 ORM 对象，需格式化为 prompt 可用的文本 | (a) 拼组脚本中调用 `get_conversation_history(conversation_id, limit=20)` (b) 写格式化函数：`{sender} [{time}]: {content}` 每轮一行 |
| 6 | `recent_turns` (运行时) | `SessionRuntime.recent_turns` | ⚠️ 半通 | 同 #5。拼组时可初始化，但之后每条新消息也要 `append` 到 deque | 拼组时初始填充；`SessionAgent.handle()` 中每条消息处理后追加 |

### B-3 缺方法/缺逻辑（3 项）

| # | 参数名 | 模板变量 / 用途 | 当前状态 | 卡点 | 需要做的 |
|---|--------|----------------|----------|------|----------|
| 7 | `conversation_summary` | `{conversation_summary}` | ❌ 取不到 | `ChatArchiveService` 只有 `get_conversation_history()`（返回原始消息列表），没有 `get_summary()`（返回摘要文本） | 在 `ChatArchiveService` 新增 `get_summary(conversation_id)` 方法：取原始消息 → 调 LLM 压缩为 3-5 句中文摘要 |
| 8 | `User_responsibilities` | `{User_responsibilities}` | ❌ 取不到 | 无现成数据源。DB 无"职责"字段，无推导逻辑 | 新建工具函数 `derive_responsibilities(permission_level, company_type, department, position)`：模板映射生成中文职责描述。例：`建设主管 + 工程部` → "负责工程进度监督与质量验收" |
| 9 | `doc_visible_set` | 运行时权限过滤 | ❌ 取不到 | 无现成服务。需要根据 `authorized_node_ids` + `project_ids` + `info_level` 推导用户可见文档范围 | 新建 `DocVisibilityResolver`。**建议 Phase B 之后实现**，当前置为 `set()` 空集合不阻塞 |

---

## 汇总

| | 表 A（可写脚本） | 表 B（不能/不能完全） |
|------|:--:|:--:|
| 注入 Prompt 的参数 | 7 + 2（不存聚合根） | 6 |
| 程序鉴权用 | 12 | 0 |
| 运行时初始值 | 4 | 2 |
| 元数据 | 3 | 0 |
| 缺失数据源 | — | 3 |
| 数据在但未接入 | — | 3 |
| 缺方法/缺逻辑 | — | 3 |

**结论**：如果只做表 A 的 27 项，现在就可以直接写拼组脚本——`PermissionService.build_permission_snapshot()` 一次调用覆盖了 12 项鉴权 + 5 项 prompt 变量，再加 `UserMemoryService` 和消息字段，Session 聚合根的核心数据已经就绪。表 B 的 9 项可以按优先级分批补。
