# Emily 权限（鉴权）系统设计

> 创建日期：2026-06-25
> 状态：设计已确认，待实施
> 版本：v1.1-confirmed

---

## 一、设计原则

1. **灌注一次，不重复鉴权**：SessionContext 创建时全量加载用户权限快照，后续所有访问基于快照，不再查 DB。
2. **累进继承**：权限层级为访客 ⊂ 参建 ⊂ 主管 ⊂ 一般管理员 ⊂ 系统管理员，高等级自动拥有低等级全部权限。
3. **白名单制度**：数据库表/SOP 访问权、公开信息均采用白名单机制，默认拒绝。
4. **行级安全**：数据库访问自动注入公司过滤条件，用户只能看到其公司创建的数据 + 对接公司相关数据。
5. **异常不阻塞**：权限异常只做标记与告知，用户有坚持录入的权利，异常信息需审核人处理。

---

## 二、权限分层模型

### 2.1 大分类（累进继承）

| grouping | 角色 | 说明 |
|----------|------|------|
| 0 | 访客 | 默认值，仅访问公开信息 + 公共 SOP |
| 1 | 参建单位人员 | 可访问公开信息 + 本公司承包范围数据 |
| 2 | 主管 | 参建权限 + 分管范围全部数据 |
| 3 | 一般管理员 | 主管权限 + 内部信息，不可系统配置 |
| 4 | 系统管理员 | 全部权限 |

### 2.2 细分权限（资源码前缀 / 白名单制）

格式：`{scope}:{resource}`

- `db:project` — 可读 project 表
- `db:event:write` — 可写 event 表（默认 `db:table` 为只读）
- `sop:SOP-001` — 可用 SOP-001 流程
- `scope:landscape` — 景观承包范围
- `info:internal` — 可访问内部信息

### 2.3 权限累进继承实现

```
访客(0) ⊂ 参建(1) ⊂ 主管(2) ⊂ 一般管理员(3) ⊂ 系统管理员(4)
```

权限检查逻辑：`user_grouping >= required_grouping` 放行。

---

## 三、数据表设计

### 3.1 新建 Company 表

```python
Company
  id           UUID           # 主键
  name         str            # 公司名称
  type         enum           # 建设单位/设计单位/总包/分包/监理/供应商
  status       enum           # 投标中/履约中/已退场
  project_id   FK             # 所属项目
  scope        JSON           # 承包范围 ["景观", "1标段", ...]
  partners     JSON           # 对接公司列表 [company_id, ...]
  parent_id    FK|null        # 上级公司（分包→总包层级）
  created_at   str
  updated_at   str
```

### 3.2 改造 User 表

- `User.company` 字段改为 FK，关联 Company 表
- `User.perm_list` 保持 JSON 数组，存储细分权限白名单
- `User.grouping` 枚举值对齐五层体系（0-4）

### 3.3 新建 PermissionDef 表

```python
PermissionDef
  id           UUID
  perm_code    str            # 权限码，如 "db:event:write"
  description  str            # 说明
```

### 3.4 新建 public_field_registry 表

公开信息白名单（方案 B）：

```python
public_field_registry
  id           UUID
  model_name   str            # "Project"
  field_name   str            # "name" / "area" / "greening_rate"
  description  str            # 用途备注
```

不需要给每一行数据打标记——"模型-字段级"白名单足够覆盖场景。真正公开的信息占比小：

- 项目基本信息：名称、位置、占地面积、容积率、绿化率、总户数
- 证件公示：五证信息、竣工验收备案号
- 公共通知：停水停电公告、施工进度公示

### 3.5 ProjectStateMachine 接口（Mock 占位）

```python
class ProjectStateMachine(ABC):
    async def is_company_active(self, company_id: str) -> bool: ...
    async def is_worker_active(self, user_id: str) -> bool: ...
    async def validate_scope(self, company_id: str, record_type: str, record_data: dict) -> ScopeCheckResult: ...
```

Phase 1 使用 Mock 实现，后续替换为真实状态机。

---

## 三-B、架构决策记录（已决事项）

以下四个关键缺口已在设计讨论中达成共识：

| 缺口 | 决策 | 说明 |
|------|------|------|
| 缺口1 · 权限快照结构 | ✅ 采用建议方案 | `perm_snapshot` 字典，包含 grouping / company / scopes / sop_allow / db_perms / supervisor 等字段，在 `SessionFactory._build_context()` 中一次性全量灌注 |
| 缺口2 · 数据访问拦截器 | ✅ SQLAlchemy 事件钩子 | 使用 `Session.before_execute` 事件统一拦截所有 SQL，自动注入公司过滤条件，对所有 Repository 透明 |
| 缺口3 · SOP 可见性 | ✅ 纯可见性控制 | SOP 白名单决定可见性——看不到即不可用。无需区分"可见不可执行"，避免用户困惑 |
| 缺口4 · 权限变更生效 | ✅ 下次会话生效 | 符合"灌注一次"原则。用户可主动发送"刷新"等指令触发 session 重置以立即生效 |

---

## 四、权限快照结构（灌注到 SessionContext）

`SessionContext.perm_list` 在当前代码中为空列表 `[]`。改造后在 `SessionFactory._build_context()` 中全量加载：

```python
perm_snapshot = {
    "grouping": 2,              # 主管（累进继承的层级）
    "company_id": "gl_001",     # 所属公司
    "company_type": "subcontractor",
    "project_ids": ["proj_01"], # 参与的项目
    "partner_ids": ["gc_001"],  # 对接公司（可见对方部分数据）
    "scopes": ["景观", "绿化"],  # 承包范围
    "sop_allow": ["SOP-001", "SOP-002", ...],  # 可用的 SOP 白名单
    "db_perms": {               # 数据库表级权限
        "event": "read_write",
        "task": "read_write",
        "project": "read",
        "financial": None,      # 不可访问
    },
    "info_level": "internal",   # 可访问的信息级别
    "supervisor_id": "user_05", # 直接上级（异常审核人）
}
```

---

## 五、鉴权执行点

### 5.1 Session 创建时（灌注）

- 位置：`SessionFactory._build_context()`（[session_factory.py:77](emily-core/emily_core/adapters/session/session_factory.py#L77)）
- 动作：查询 User + Company 表 → 组装权限快照 → 注入 `SessionContext.perm_list`
- 后续不再查 DB

### 5.2 SOP 路由鉴权（可见性控制）

- 位置：`WorkItemAgent.authorize()`（[workitem_agent.py:482](emily-core/emily_core/workitem/workitem_agent.py#L482)）
- 动作：比对 `SOPIntentSpec.allow_roles` 与用户 `grouping`
- **SOP 白名单决定可见性**：用户只能在 SOP 目录中看到 `sop_allow` 列表中已授权的流程
  - 不可见的 SOP 不在目录中展示，也无法通过直接输入触发
  - 无需区分"可见不可执行"——看不到即不可用，避免用户困惑

### 5.3 数据访问层拦截（行级安全）

- 位置：SQLAlchemy `Session.before_execute` 事件钩子（推荐方案）
- 动作：拦截所有 SELECT/INSERT/UPDATE/DELETE，自动注入公司过滤条件
- 范围：`WHERE company_id IN (self_id, partner_ids) OR is_public = true`
- 优势：对所有 Repository 透明，不依赖开发者纪律

### 5.4 异常标记（履约检查）

- 位置：`node4_summary` 的 Guardian 守护阶段
- 动作：调用状态机验证用户单位履约状态、承包范围匹配
- 异常结果：标记 + 告知用户 + 用户可坚持录入
- 被标记为异常的信息，需数据库记录的直接上级给出处理意见

---

## 六、系统配置

### 6.1 初始管理员

- 项目启动时在 `.env` 配置 `EMILY_INITIAL_ADMIN_IM_ID`
- `init_db` 时自动创建管理员 User 记录，`is_admin=True`，`grouping=4`
- 后续通过 IM 对话（SOP-000-SYS）管理权限分配

### 6.2 权限变更

- 权限变更在用户**下次会话生效**（符合"灌注一次"原则）
- 管理员操作后提示："权限变更将在用户下次对话时生效"
- 如用户需要立即生效，可引导用户主动触发 session 刷新（如发送"刷新"或"重置会话"等指令）

---

## 七、当前代码对齐情况

| 现有字段 | 位置 | 状态 | 改造动作 |
|----------|------|------|----------|
| `User.grouping` | [models.py:81](emily-core/emily_core/infrastructure/database/models.py#L81) | 已有，值域 0-4 | 对齐五层语义 |
| `User.perm_list` | [models.py:80](emily-core/emily_core/infrastructure/database/models.py#L80) | JSON `"[]"` | 关联 PermissionDef |
| `User.company` | [models.py:83](emily-core/emily_core/infrastructure/database/models.py#L83) | JSON `"[]"` | 改为 FK → Company |
| `SessionContext.perm_list` | [session_context.py:47](emily-core/emily_core/session/session_context.py#L47) | 空列表 | 填充权限快照 |
| `WorkItemAgent.authorize()` | [workitem_agent.py:482-507](emily-core/emily_core/workitem/workitem_agent.py#L482) | 永远 ALLOW | 实现真实鉴权 |
| `SOPIntentSpec.allow_roles` | [intent_registry.py:44](emily-core/emily_core/agent/intent_registry.py#L44) | 已有 | 复用 |
| `AuthHook` | [hook.py:92-128](emily-core/emily_core/workitem/pipeline/hook.py#L92) | 已有，部分工作 | 完善 system.execute 检查 |

---

## 八、待明确事项

- [ ] 公司"承包范围"是否需关联到具体项目/标段？
- [ ] "履约中"的判断依据：合同有效期内 or 有未完工的施工任务？
- [ ] 异常审核人"直接上级"是否存入 User 表字段 or 从组织架构树推导？
- [ ] 权限管理 SOP 是否需要独立的管理界面 vs 纯 IM 对话？

---

## 九、实施建议

分三个 Phase 推进：

| Phase | 内容 | 依赖 |
|-------|------|------|
| Phase I | 数据表（Company / PermissionDef / public_field_registry）+ User 改造 | 无 |
| Phase II | SessionContext 权限快照灌注 | Phase I |
| Phase III | 鉴权执行点（SOP 路由 / DB 拦截器 / 履约检查） | Phase II |
