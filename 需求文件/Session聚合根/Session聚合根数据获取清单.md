# Session 聚合根数据获取完整清单

> **版本**: v1.0
> **日期**: 2026-07-02
> **说明**: 拼组脚本（SessionFactory）创建 Session 聚合根时，需要获取的全部参数清单。每行标明参数名、用途、获取脚本名、数据来源。
> **图例**: ✅ 已实现 ⚠️ 半实现（需扩展） ❌ 未实现

---

## 一、注入 Prompt 的变量（提示词模板 17 项）

| # | 参数名 | 模板变量 | 描述 | 获取脚本 | 实现状态 | 数据来源 |
|---|--------|----------|------|----------|----------|----------|
| 1 | `project_name` | `{project_name}` | 项目名称 | `ProjectService.get_by_user(user_id)` | ❌ 预设未实现 | DB `projects.name` [models.py:168]，需通过 User 关联到 Project |
| 2 | `project_type` | `{project_type}` | 项目行业性质 | `ProjectService.get_by_user(user_id)` | ❌ 预设未实现 | DB 无此字段。`Project` 表无 `type`，需扩展 `projects` 表或从 `lifecycle_stage` 推导 |
| 3 | `project_status` | `{project_status}` | 项目当前状态 | `ProjectService.get_by_user(user_id)` | ❌ 预设未实现 | DB `projects.status` [models.py:172] |
| 4 | `user_name` | `{user_name}` | 用户姓名 | 直接取值 | ✅ 已实现 | `StandardMessage.sender_name`，Adapter 层传入 |
| 5 | `user_company` | `{user_company}` | 用户所属公司名称 | `PermissionService.build_permission_snapshot(user_id)` | ✅ 已实现 | DB `company_info.company_name` [models.py:321]，经 PermissionSnapshot 返回 |
| 6 | `user_company_type` | `{user_company_type}` | 公司类型 | `PermissionService.build_permission_snapshot(user_id)` | ✅ 已实现 | DB `company_info.type` [models.py:327] |
| 7 | `user_department` | `{user_department}` | 用户部门 | `PermissionService.build_permission_snapshot(user_id)` | ✅ 已实现 | DB `company_info.department` [models.py:332]，取 `_primary_department()` 计算结果 |
| 8 | `user_position` | `{user_position}` | 用户职务 | `PermissionService._do_build_snapshot()` 需扩展 | ⚠️ 半实现 | DB `users.position` [models.py:85]，JSON 数组字段。**PermissionSnapshot 当前未映射此字段** |
| 9 | `user_permission_level` | `{user_permission_level}` | 权限层级 1-6 | `PermissionService.build_permission_snapshot(user_id)` | ✅ 已实现 | DB `users.permission_level` [models.py:82] |
| 10 | `current_node_ids` | `{current_node_ids}` | 当前参与的工作节点 | `PermissionService.build_permission_snapshot(user_id)` | ✅ 已实现 | 经 `_derive_authorized_nodes()` 从 `company_info.function_scope` [models.py:333] 解析，注入 `PermissionSnapshot.authorized_node_ids` |
| 11 | `User_responsibilities` | `{User_responsibilities}` | 用户职责描述 | `RoleResponsibilityDeriver.derive(snapshot)` | ❌ 预设未实现 | 未指定来源。需从 `permission_level` + `company_type` + `department` + `position` 综合推导 |
| 12 | `recent_turns` | `{recent_turns}` | 最近 20 轮对话记录 | `ChatArchiveService.get_conversation_history()` | ⚠️ 半实现 | DB `messages` 表 [models.py:106-150]，通过 `ChatArchiveService.get_conversation_history()` [chat_archive_service.py:97] 可查，但当前未接入 SessionFactory。需格式化输出 |
| 13 | `user_longterm_memory` | `{user_longterm_memory}` | 用户长期记忆 | `UserMemoryService.load_memory_context(user_name)` | ✅ 已实现 | 文件系统 `emily-data/user_memory/{user_name}-长期记忆.md` [user_memory_service.py:171] |
| 14 | `conversation_summary` | `{conversation_summary}` | 历史对话摘要 | `ChatArchiveService.get_summary(conversation_id)` | ❌ 预设未实现 | 方法不存在。需实现：取 `get_conversation_history()` 原始消息 → 调 LLM 压缩为摘要文本 |
| 15 | `current_datetime` | `{current_datetime}` | 当前时间 | `_beijing_now_str()` | ✅ 已实现 | 系统时钟，实时计算。不存 Session 聚合根 |
| 16 | `sop_catalog` | `{sop_catalog}` | 可用 SOP 目录 | `SOPIntentRegistry.dump_as_text()` | ✅ 已实现 | SOP 注册表内存数据。不存 Session 聚合根 |

> 注：提示词模板有 18 个 `{变量}`，其中 `{project_status}` 出现两次（第二部分项目背景 + 用户上下文），合并为 1 个参数。`{user_permission_level}` 出现一次在模板中。实际注入变量为 16 个独立参数 + 2 个不存聚合根（datetime、sop_catalog）。

---

## 二、程序鉴权用数据（PermissionSnapshot，不注入 Prompt）

| # | 参数名 | 描述 | 获取脚本 | 实现状态 | 数据来源 |
|---|--------|------|----------|----------|----------|
| 17 | `company_id` | 所属公司 ID | `PermissionService.build_permission_snapshot(user_id)` | ✅ 已实现 | DB `users.company` FK → `company_info.id` [models.py:84] |
| 18 | `partner_ids` | 对接公司 ID 列表 | 同上 | ✅ 已实现 | DB `company_info.partners` [models.py:330]，JSON 数组 |
| 19 | `scopes` | 承包范围 | 同上 | ✅ 已实现 | DB `company_info.scope` [models.py:329]，JSON 数组 |
| 20 | `project_ids` | 参与的项目 | 同上 | ✅ 已实现 | 经 `_derive_project_ids()` 从 user 表 + company 表推导 |
| 21 | `sop_allow` | SOP 白名单 | 同上 | ✅ 已实现 | `sop_business_flows` 表 + `sop_bindings` 表 + `permission_groups` 表，经 `_compute_sop_allow()` 计算 |
| 22 | `db_perms` | 数据库表级权限 | 同上 | ✅ 已实现 | 经 `_derive_db_perms()` 从 `permission_level` 推导 [permission_service.py:208] |
| 23 | `info_level` | 信息密级 | 同上 | ✅ 已实现 | 经 `_derive_info_level()` 从 `permission_level` 推导 [permission_service.py:199] |
| 24 | `granted_codes` | 授权权限编码 | 同上 | ✅ 已实现 | DB `permission_grants` 表 [permission_service.py:101] |
| 25 | `denied_codes` | 拒绝权限编码 | 同上 | ✅ 已实现 | 从 SOP deny 绑定推导 [permission_service.py:104] |
| 26 | `supervisor_id` | 直接上级 ID | 同上 | ✅ 已实现 | DB `users.supervisor_id` [models.py:83] |
| 27 | `permission_version` | 权限版本号 | 同上 | ✅ 已实现 | 缓存版本号 [permission_service.py:107] |
| 28 | `extra_perms` | 扩展权限数据 | 同上 | ✅ 已实现 | 当前仅注入 `user_id` [permission_service.py:127] |

---

## 三、运行时可变数据（SessionRuntime，Agent 运行期读写）

| # | 参数名 | 描述 | 获取脚本 | 实现状态 | 数据来源 |
|---|--------|------|----------|----------|----------|
| 29 | `recent_turns` | 滑动窗口对话队列 | `ChatArchiveService.get_conversation_history(conversation_id, limit=20)` | ⚠️ 半实现 | DB `messages` 表，Service 可查但**拼组脚本未接入**。需格式化 deque |
| 30 | `doc_visible_set` | 可见文档 ID 集合 | `DocVisibilityResolver.resolve(permissions)` | ❌ 预设未实现 | 未指定来源。需从权限范围和项目上下文推导可见文档 |
| 31 | `cached_lookups` | Agent 查询缓存 | 无需获取（初始为空） | ✅ 天生为空 | 无来源。运行时 Agent/Hook 自行写入 |
| 32 | `active_focus` | 当前焦点 WorkItem ID | 无需获取（初始为 None） | ✅ 天生为 None | 无来源。handle() 时 SessionAgent 设置 |
| 33 | `pending_confirms` | 待确认项队列 | 无需获取（初始为空） | ✅ 天生为空 | 无来源。WorkItem 执行完成后入队 |
| 34 | `baggage` | 通用兜底 KV | 无需获取（初始为空） | ✅ 天生为空 | 无来源。Hook/节点间临时传参 |

---

## 四、Session 元数据

| # | 参数名 | 描述 | 获取脚本 | 实现状态 | 数据来源 |
|---|--------|------|----------|----------|----------|
| 35 | `conversation_id` | 会话标识 | 直接取值 | ✅ 已实现 | `StandardMessage.conversation_id` |
| 36 | `user_id` | 用户 UUID | Adapter 层传入 | ✅ 已实现 | 上游 `UserBindingService.get_or_create_user()` 绑定后传入 |
| 37 | `created_at` | Session 创建时间戳 | 直接取值 | ✅ 已实现 | `datetime.now(timezone.utc).isoformat()` |

---

## 五、汇总统计

| 分类 | 总数 | ✅ 已实现 | ⚠️ 半实现 | ❌ 未实现 |
|------|------|-----------|-----------|-----------|
| 注入 Prompt | 16 | 8 | 3 | 5 |
| 程序鉴权 | 12 | 12 | 0 | 0 |
| 运行时 | 6 | 4 | 1 | 1 |
| 元数据 | 3 | 3 | 0 | 0 |
| **合计** | **37** | **27** | **4** | **6** |

---

## 六、未实现项的行动清单

| 优先级 | 参数 | 需要做的事情 |
|--------|------|-------------|
| **P0** | `user_position` (#8) | `PermissionService._do_build_snapshot()` 中读取 `User.position` JSON 数组，取第一个岗位填入 `PermissionSnapshot`（需扩展 snapshot 字段） |
| **P0** | `recent_turns` (#12, #29) | 拼组脚本中调用 `ChatArchiveService.get_conversation_history(conversation_id, limit=20)`，格式化后同时注入 `SessionSnapshot`（供 prompt）和 `SessionRuntime.recent_turns`（运行期追加） |
| **P1** | `project_name` (#1) | 新建 `ProjectService`，封装 `ProjectRepository` 查询。用户→项目映射：通过 `users.company` 关联 `company_info`，再通过项目负责人或参建单位关联 `projects` |
| **P1** | `project_type` (#2) | 两种方案：(a) 在 `projects` 表新增 `project_type` 字段；(b) 从 `lifecycle_stage` 枚举 + 公司类型推导。推荐 (a) |
| **P1** | `project_status` (#3) | 同 `project_name`，读 `projects.status`，需 `ProjectService` |
| **P1** | `conversation_summary` (#14) | 在 `ChatArchiveService` 新增 `get_summary()` 方法：取 `get_conversation_history()` → 调 LLM 压缩为 3-5 句摘要文本 |
| **P2** | `User_responsibilities` (#11) | 新建 `RoleResponsibilityDeriver` 工具类：根据 `permission_level` + `company_type` + `department` + `position` 生成中文职责描述。可做模板映射（如 "建设主管+工程部" → "负责工程进度监督与质量验收"） |
| **P2** | `doc_visible_set` (#30) | 新建 `DocVisibilityResolver`：根据 `PermissionSnapshot.authorized_node_ids` + `project_ids` + `info_level` 推导用户可见的文档范围。**建议 Phase B 后再做**，先留空 |
