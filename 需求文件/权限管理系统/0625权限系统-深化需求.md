# Emily 权限（鉴权）系统设计

> 创建日期：2026-06-25
> 状态：设计已确认，待实施
> 版本：v1.5-auth-3d-model
>
> ## 版本历史
> | 版本 | 日期 | 变更内容 |
> |------|------|----------|
> | v1.0 | 2026-06-25 | 初始版本 - 基础权限系统设计 |
> | v1.2 | 2026-06-25 | 权限架构调整 - 权限快照不再直接灌注到 Session-Agent |
> | v1.3 | 2026-06-25 | 新增 Project State Machine 项目状态机子系统 |
> | v1.4 | 2026-06-25 | 架构师审核优化 - 性能/安全/可观测性全面加固 |
> | v1.5 | 2026-06-25 | 三维鉴权模型落地 - grouping重命名为permission_level / authorize()三维重写 / CompanyInfo扩展 / 新增索引-审计-缓存策略 |
>
> ## 重要修改记录
>
> ### 2026-06-25 v1.5 三维鉴权模型落地（架构审核修正）
>
> **修改背景**：架构师审核发现以下三类关键风险——
> 1. `grouping` 字段在代码中的语义（0=临时组/1=访客组/2=工程组/3=供货商/4=管理组）与文档定义（0=访客→4=管理员）存在冲突，直接迁移会造成权限越权
> 2. `authorize()` 仅基于旧的 `allow_roles` 角色字符串，与设计的三维模型（企业+部门+层级）完全不兼容
> 3. `User.company` JSON→单FK 方案、Company 表与现有 CompanyInfo 表重复等问题需明确
>
> **v1.5 修正内容**：
> 1. `grouping` 重命名为 `permission_level`，完全废弃旧字段语义，新增数据迁移脚本
> 2. `authorize()` 重写为三维鉴权引擎（permission_level × company_type × department）
> 3. `User.company` 改为单 FK 关联 `CompanyInfo`（暂不支持一人多司，后续需求到达时再改）
> 4. 不新建 `Company` 表，改为扩展已有 `CompanyInfo` 表
> 5. SOP 文件 `allow_roles` → `SOPBusinessFlow` 数据迁移路径明确
> 6. 新增索引设计、权限审计日志表、应用级权限矩阵缓存、错误处理策略、State Machine 并发安全设计
>
> ### 2026-06-25 v1.2 权限存放架构调整
>
> **修改背景**：原设计中，鉴权信息是灌注到 Session-Agent 中的。但随着鉴权系统的实际实现，数据库访问权限限定通过权限可见范围白名单机制实现，这个白名单列表并不适合直接灌给 Agent（可能导致上下文污染和安全边界不清晰）。
>
> **架构调整内容**：
> 1. 权限信息不再直接灌注到 Session-Agent
> 2. 改为在 Session 状态机（SessionContext）内增加对应的专门字段来存放权限快照
> 3. WorkItemAgent 对 Session 状态机保持只读权限，用于获取会话状态信息与权限列表进行鉴权检查
> 4. SOP 鉴权分为两层架构：
>    - 第一层：数据库表存放 SOP 业务流文件特征信息（名称、一句话功能描述、权限组归属）
>    - 第二层：权限组归属至少包含两层分类——企业 + 部门
>
> **企业分组设计**：
> - 一般企业只做简单的管理组与业务组划分即可
> - 建设单位需细分部门以方便权限细分（如：设计部、工程部、成本部、采购部等）

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

| permission_level | 角色 | 说明 |
|------------------|------|------|
| 0 | 访客 | 默认值，仅访问公开信息 + 公共 SOP |
| 1 | 参建单位人员 | 可访问公开信息 + 本公司承包范围数据 |
| 2 | 主管 | 参建权限 + 分管范围全部数据 |
| 3 | 一般管理员 | 主管权限 + 内部信息，不可系统配置 |
| 4 | 系统管理员 | 全部权限 |

> **v1.5 字段重命名说明**：旧版中 `grouping` 在数据库中的实际语义为组织类型标签（0=临时组/1=访客组/2=工程组/3=供货商/4=管理组），与权限层级定义存在语义冲突（如"供货商=3"会误映射为"一般管理员=3"造成越权）。
> 因此 v1.5 起废弃旧 `grouping` 字段，启用新 `permission_level` 字段，权限检查完全基于新字段执行。旧 `grouping` 列保留只读（改名为 `org_category`），不再参与鉴权逻辑。

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

权限检查逻辑：`user_permission_level >= required_permission_level` 放行。

---

## 三、数据表设计

### 3.1 扩展 CompanyInfo 表（不新建 Company 表）

> **v1.5 变更说明**：原设计提出新建 `Company` 表，但代码中已存在 `CompanyInfo` 表（by [models.py:307](emily-core/emily_core/infrastructure/database/models.py#L307)）。为避免表名混乱和重复维护——不再新建，改为扩展已有 `CompanyInfo` 表。

在现有 `CompanyInfo` 表基础上增加以下字段：

```python
# 现有字段（保持不变）：
#   id, company_name, unified_code, business_desc, project_leader_id, creator_id, created_at, updated_at, is_deleted

# v1.5 新增字段：
CompanyInfo
  type         enum           # 建设单位/设计单位/总包/分包/监理/供应商
  status       enum           # 投标中/履约中/已退场
  scope        JSON           # 承包范围 ["景观", "1标段", ...]
  partners     JSON           # 对接公司ID列表 [company_id, ...]
  parent_id    FK|null        # 上级公司（分包→总包层级）
  department   JSON           # 部门列表 ["设计部", "工程部", "成本部", ...]
```

### 3.2 改造 User 表

```python
# v1.5 字段重命名
User.permission_level = Column(Integer, default=0)    # 新增：权限层级 0=访客 1=参建 2=主管 3=一般管理员 4=系统管理员
User.org_category      = Column(Integer, default=0)   # 原 grouping 字段改名，仅保留为组织类型标签（0=临时组 1=访客组 2=工程组 3=供货商 4=管理组），不再参与鉴权逻辑
User.company           = Column(String, ForeignKey("company_info.id"), nullable=True)  # 改为单 FK → CompanyInfo
User.perm_list         = Column(String, default="[]") # 保持 JSON 数组，存储细分权限白名单（关联 PermissionDef）
```

**字段变更对照**：

| 旧字段名 | 新字段名/行为 | 变更类型 |
|----------|-------------|----------|
| `grouping` (鉴权用) | `permission_level` | 新增字段，参与鉴权 |
| `grouping` (旧语义) | `org_category` | 原地改名，仅作组织标签，不参与鉴权 |
| `company` (JSON "[]") | `company` (FK → CompanyInfo) | 类型变更，单公司归属 |
| `perm_list` | 不变 | 保持 JSON，关联 PermissionDef |

**数据迁移脚本要点**：

| 旧 grouping 值 | 旧含义 | 迁移后 permission_level (默认) | 迁移后 org_category | 备注 |
|---|---|---|---|---|
| 0 | 临时组 | 0 (访客) | 0 | 临时人员最低权限 |
| 1 | 访客组 | 0 (访客) | 1 | 语义一致 |
| 2 | 工程组 | 1 (参建) | 2 | 工程人员=参建方 |
| 3 | 供货商 | 1 (参建) | 3 | 供应商=参建方 |
| 4 | 管理组 | 3 (一般管理员) | 4 | 需人工确认是否升级到4 |

迁移完成后，管理员通过 SOP-000-SYS 对旧 `grouping=4` 的用户逐个确认是否应设置为 `permission_level=4`（系统管理员）。

### 3.3 新建 PermissionDef 表

```python
PermissionDef
  id           UUID
  perm_code    str            # 权限码，如 "db:event:write"
  description  str            # 说明
```

### 3.4 新建 public_field_registry 表

公开信息白名单（方案 B）。**v1.5 增加项目级作用域**：

```python
public_field_registry
  id           UUID
  project_id   FK|null        # 关联项目（null 表示全局公开，跨项目生效）
  model_name   str            # "Project"
  field_name   str            # "name" / "area" / "greening_rate"
  description  str            # 用途备注
```

不需要给每一行数据打标记——"模型-字段级"白名单足够覆盖场景。真正公开的信息占比小：

- 项目基本信息：名称、位置、占地面积、容积率、绿化率、总户数
- 证件公示：五证信息、竣工验收备案号
- 公共通知：停水停电公告、施工进度公示

### 3.5 新建 permission_audit_log 表（v1.5 新增）

> **设计背景**：权限拒绝和变更需要结构化审计日志，用于异常检测和合规追溯。

```python
PermissionAuditLog
  id             UUID
  log_type       enum           # ACCESS_DENIED / PERMISSION_CHANGE / SESSION_STALE
  user_id        FK|null        # 被审计的用户
  operator_id    FK|null        # 操作人（权限变更时）
  resource_type  str            # "sop" / "db_table" / "project_state" / "field"
  resource_id    str            # SOP编号 / 表名 / phase_id / 模型.字段
  action         str            # "read" / "write" / "execute" / "grant" / "revoke"
  required_level int|null       # 所需权限层级
  actual_level   int|null       # 实际权限层级
  decision       str            # "ALLOW" / "DENY" / "WARN"
  reason         str            # 拒绝原因或变更说明
  ip_address     str|null       # 请求来源 IP
  session_id     str|null       # 关联 Session
  created_at     str
```

**异常检测规则**：同一 user_id + 同一 resource_id 在 5 分钟内 ACCESS_DENIED ≥ 10 次 → 触发告警。

### 3.6 ProjectStateMachine 接口（Mock 占位）

```python
class ProjectStateMachine(ABC):
    async def is_company_active(self, company_id: str) -> bool: ...
    async def is_worker_active(self, user_id: str) -> bool: ...
    async def validate_scope(self, company_id: str, record_type: str, record_data: dict) -> ScopeCheckResult: ...
```

Phase 1 使用 Mock 实现，后续替换为真实状态机。

---

## 三-B、架构决策记录（已决事项）

以下关键缺口已在设计讨论中达成共识：

| 缺口 | 决策 | 说明 |
|------|------|------|
| 缺口1 · 权限快照结构 | ✅ 采用建议方案 | `perm_snapshot` 字典，包含 permission_level / company / scopes / sop_allow / db_perms / supervisor 等字段，在 `SessionFactory._build_context()` 中一次性全量灌注 |
| 缺口2 · 数据访问拦截器 | ✅ SQLAlchemy 事件钩子 | 使用 `Session.before_execute` 事件统一拦截所有 SQL，自动注入公司过滤条件，对所有 Repository 透明 |
| 缺口3 · SOP 可见性 | ✅ 纯可见性控制 | SOP 白名单决定可见性——看不到即不可用。无需区分"可见不可执行"，避免用户困惑 |
| 缺口4 · 权限变更生效 | ✅ 下次会话生效 | 符合"灌注一次"原则。用户可主动发送"刷新"等指令触发 session 重置以立即生效 |
| 缺口5 · grouping 冲突 | ✅ permission_level 新字段 | 旧 grouping 重命名为 org_category，新 permission_level 独立管理权限层级。旧值 0-4 到新值 0-4 按业务映射表迁移，grouping=4(旧管理组) → permission_level=3(一般管理员) 需人工确认升级 |
| 缺口6 · Company vs CompanyInfo | ✅ 扩展 CompanyInfo | 不新建 Company 表，扩展现有 CompanyInfo 表增加 type/scope/partners/parent_id/department 字段 |
| 缺口7 · User.company 多公司 | ✅ 暂不支持 | 改为单 FK → CompanyInfo。后续如有多公司需求，通过中间表 user_company_bindings 实现 |

---

## 四、权限快照结构（灌注到 SessionContext）

`SessionContext.perm_list` 在当前代码中为空列表 `[]`。改造后在 `SessionFactory._build_context()` 中全量加载：

```python
perm_snapshot = {
    "permission_level": 2,          # 主管（累进继承的层级）
    "company_id": "gl_001",         # 所属公司
    "company_type": "subcontractor",
    "project_ids": ["proj_01"],     # 参与的项目
    "partner_ids": ["gc_001"],      # 对接公司（可见对方部分数据）
    "scopes": ["景观", "绿化"],      # 承包范围
    "sop_allow": ["SOP-001", "SOP-002", ...],  # 可用的 SOP 白名单
    "db_perms": {                   # 数据库表级权限
        "event": "read_write",
        "task": "read_write",
        "project": "read",
        "financial": None,          # 不可访问
    },
    "info_level": "internal",       # 可访问的信息级别
    "supervisor_id": "user_05",     # 直接上级（异常审核人）
    "permissions_loaded_at": "2026-06-25T10:30:00Z",  # v1.5: 权限快照加载时间戳
    "permission_version": 1,        # v1.5: 权限版本号（轻量变更检测）
}
```

---

## 五、鉴权执行点

### 5.1 Session 创建时（灌注）

- 位置：`SessionFactory._build_context()`（[session_factory.py:77](emily-core/emily_core/adapters/session/session_factory.py#L77)）
- 动作：查询 User + Company 表 → 组装权限快照 → 注入 `SessionContext.perm_list`
- 后续不再查 DB

### 5.2 SOP 路由鉴权（三维匹配引擎）

- 位置：`WorkItemAgent.authorize()`（[workitem_agent.py:482](emily-core/emily_core/workitem/workitem_agent.py#L482)）
- 输入：`BusContext`（通过 `get_permissions()` 获取 `PermissionSnapshot`）+ `RouteDecision`
- 输出：`AuthResult(decision, reason, matched_details, _source)`
- **v1.5 完全重写**：废弃旧的 `allow_roles` 字符串匹配，改为三维鉴权引擎

#### 5.2.1 鉴权流程

```
authorize(context, route_decision)
    │
    ├─ 1. 获取 SOP ID
    │     sop_id = route_decision.sop_id
    │     无 SOP ID → ALLOW（纯聊天等无需鉴权）
    │
    ├─ 2. 查询 SOP 权限要求
    │     sop_flow = SOPBusinessFlowRepo.get_by_sop_id(sop_id)
    │     未注册 → DENY（白名单制度，未注册即不可用）
    │     is_public = True → ALLOW
    │
    ├─ 3. 获取用户权限快照
    │     perms = context.get_permissions()
    │     无权限快照 → DENY
    │
    ├─ 4. 三维匹配（逐一检查，任一不满足 → DENY）
    │   │
    │   ├─ 维度1: 权限层级（累进继承）
    │   │     perms.permission_level >= sop_flow.min_permission_level ?
    │   │
    │   ├─ 维度2: 企业类型匹配（如 SOP 要求）
    │   │     perms.company_type IN (sop_flow.allowed_company_types ∪
    │   │       SOPPermissionBinding→PermissionGroup.company_type) ?
    │   │
    │   └─ 维度3: 部门匹配（如 SOP 要求）
    │         perms.department IN (sop_flow.allowed_departments ∪
    │           SOPPermissionBinding→PermissionGroup.department) ?
    │
    └─ 5. 返回结果
          ALLOW → 包含匹配详情（哪个权限组、哪个维度满足）
          DENY  → 包含拒绝原因（哪个维度不满足、需要什么权限）
```

#### 5.2.2 鉴权实现骨架

```python
async def authorize(self, context: BusContext, route_decision) -> AuthResult:
    """三维鉴权引擎 —— permission_level × company_type × department。
    
    v1.5 重写：不再依赖 SOPIntentSpec.allow_roles 字符串，
    改为从 SOPBusinessFlow 表查询权限要求，与 PermissionSnapshot 做三维匹配。
    
    旧 SOP 文件的 allow_roles 通过数据迁移脚本映射到 SOPBusinessFlow 表的
    min_permission_level / allowed_company_types / allowed_departments 字段。
    """
    auth_mode = self._resolve_mode("auth")
    if auth_mode != "real":
        return AuthResult(decision=AuthDecision.ALLOW, _source="mock_auth")
    
    sop_id = getattr(route_decision, "sop_id", None)
    if not sop_id:
        # 无 SOP ID 的请求（如纯聊天、问候）无需鉴权
        return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")

    # ── 第一层：查询 SOP 的权限要求 ──
    sop_flow = await self._sop_flow_repo.get_by_sop_id(sop_id)
    if sop_flow is None:
        # SOP 未在权限系统中注册 → 默认拒绝（白名单制度）
        await self._audit_log("ACCESS_DENIED", context, sop_id,
            reason=f"SOP {sop_id} 未在权限系统中注册")
        return AuthResult(decision=AuthDecision.DENY,
                         reason=f"SOP {sop_id} 未注册，不可用",
                         _source="real_auth")

    if sop_flow.is_public:
        return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")

    # ── 第二层：获取用户权限快照 ──
    perms = context.get_permissions()
    if perms is None:
        await self._audit_log("ACCESS_DENIED", context, sop_id,
            reason="无法获取用户权限快照")
        return AuthResult(decision=AuthDecision.DENY,
                         reason="无法获取用户权限信息，请重新登录",
                         _source="real_auth")

    # ── 第三层：三维匹配 ──

    # 维度1: 权限层级（累进继承）
    if perms.permission_level < sop_flow.min_permission_level:
        await self._audit_log("ACCESS_DENIED", context, sop_id,
            required_level=sop_flow.min_permission_level,
            actual_level=perms.permission_level,
            reason=f"权限层级不足")
        return AuthResult(decision=AuthDecision.DENY,
                         reason=(f"当前权限层级为 {perms.permission_level}（{_level_name(perms.permission_level)}），"
                                f"SOP [{sop_id}] 要求最低 {sop_flow.min_permission_level}（{_level_name(sop_flow.min_permission_level)}）"),
                         _source="real_auth")

    # 维度2: 企业类型匹配
    if sop_flow.require_company_match:
        bindings = await self._binding_repo.get_active_bindings_by_sop(sop_flow.id)
        # 合并 SOP 自身 allowed_company_types + 所有绑定权限组的 company_type
        allowed_company_types = set(sop_flow.allowed_company_types or [])
        for b in bindings:
            if b.permission_group and b.permission_group.company_type:
                allowed_company_types.add(b.permission_group.company_type)
        
        if allowed_company_types and perms.company_type not in allowed_company_types:
            await self._audit_log("ACCESS_DENIED", context, sop_id,
                reason=f"企业类型不匹配: {perms.company_type}")
            return AuthResult(decision=AuthDecision.DENY,
                             reason=f"当前企业类型（{perms.company_type}）无权使用 SOP [{sop_id}]",
                             _source="real_auth")

    # 维度3: 部门匹配
    if sop_flow.require_department_match:
        bindings = bindings if 'bindings' in dir() else await self._binding_repo.get_active_bindings_by_sop(sop_flow.id)
        allowed_depts = set(sop_flow.allowed_departments or [])
        for b in bindings:
            if b.permission_group and b.permission_group.department:
                allowed_depts.add(b.permission_group.department)
        
        if allowed_depts and perms.department not in allowed_depts:
            await self._audit_log("ACCESS_DENIED", context, sop_id,
                reason=f"部门不匹配: {perms.department}")
            return AuthResult(decision=AuthDecision.DENY,
                             reason=f"当前部门（{perms.department}）无权使用 SOP [{sop_id}]",
                             _source="real_auth")

    # ── 通过鉴权 ──
    logger.info("Auth ALLOW: user=%s sop=%s level=%d company=%s dept=%s",
                context.user_id, sop_id, perms.permission_level,
                perms.company_type, perms.department)
    return AuthResult(decision=AuthDecision.ALLOW,
                     matched_details={
                         "permission_level": perms.permission_level,
                         "company_type": perms.company_type,
                         "department": perms.department,
                         "matched_via": "permission_group" if bindings else "sop_config",
                     },
                     _source="real_auth")
```

#### 5.2.3 DENY 结果处理

`authorize()` 返回 DENY 后，系统行为：
1. 记录 `PermissionAuditLog`（log_type=ACCESS_DENY）
2. 返回用户友好提示（含拒绝原因和所需权限层级）
3. 不展示 SOP 在目录中（已通过 `sop_allow` 白名单在 Session 创建时过滤）
4. 异常检测：同一用户 + 同一 SOP 5 分钟内 ACCESS_DENY ≥ 10 次 → 触发告警

#### 5.2.4 旧 allow_roles 数据迁移

SOP 文件中 §1 的旧格式需要迁移到 `SOPBusinessFlow` 表。迁移映射：

| 旧 allow_roles 值 | 新 min_permission_level | 新 require_company_match | 新 require_department_match |
|---|---|---|---|
| `all` | 0 | false | false |
| `admin` | 4 | false | false |
| `supervisor` | 2 | true | false |
| `engineer` | 1 | true | false |
| `designer` | 1 | true | true |
| `owner_design` | 1 | true（限定"建设单位"） | true（限定"设计部"） |
| `owner_engineering` | 1 | true（限定"建设单位"） | true（限定"工程部"） |

此迁移在 Phase I 数据表创建时通过 SQL 脚本执行，并在 Phase III 鉴权上线前完成所有 SOP 文件的权限字段升级。

### 5.3 数据访问层拦截（行级安全）

- 位置：SQLAlchemy `Session.before_execute` 事件钩子（推荐方案）
- 动作：拦截所有 SELECT/INSERT/UPDATE/DELETE，自动注入公司过滤条件
- 范围：`WHERE company_id IN (self_id, partner_ids) OR is_public = true`
- 优势：对所有 Repository 透明，不依赖开发者纪律

#### 5.3.1 拦截器实现注意事项

1. **防止重复注入**：如果 Repository 层已显式添加了公司过滤条件，`before_execute` 不应再追加重复条件。解决方案：使用 Thread-local 标记位（`_skip_auth_injection = threading.local()`），已显式过滤的查询设置 `_skip_auth_injection.flag = True`，拦截器检测后跳过。禁止的写操作应被拦截。支持应用层再追加额外的条件。

2. **JOIN 多表查询**：多表 JOIN 时需确定"主表"（数据归属表）以注入过滤条件。策略：从 `FROM` 子句的 leftmost table 识别主表，若主表不在可过滤的表清单中，遍历 JOIN 表查找第一个匹配的。

3. **UNION / 子查询**：复杂查询需递归遍历所有 `SELECT` AST 节点（含子查询、CTE、UNION 分支），确保每个都注入过滤条件。对无法安全注入的查询（如复杂聚合），记录 WARNING 日志并以 ALLOW 模式放行（fail-open + 告警）。

```python
# 拦截器核心实现参考
from sqlalchemy import event
from threading import local

_injection_ctx = local()

@event.listens_for(Session, "before_execute")
def inject_company_filter(conn, clause, multiparams, params, context):
    if getattr(_injection_ctx, 'skip', False):
        return clause, multiparams, params
    
    perms = get_current_permission_snapshot()  # 从 Thread-local 获取
    if perms is None:
        return clause, multiparams, params
    
    allowed_ids = [perms.company_id] + perms.partner_ids
    return _inject_where_clause(clause, allowed_ids), multiparams, params
```

### 5.4 异常标记（履约检查）

- 位置：`node4_summary` 的 Guardian 守护阶段
- 动作：调用状态机验证用户单位履约状态、承包范围匹配
- 异常结果：标记 + 告知用户 + 用户可坚持录入
- 被标记为异常的信息，需数据库记录的直接上级给出处理意见

---

## 六、系统配置

### 6.1 初始管理员

- 项目启动时在 `.env` 配置 `EMILY_INITIAL_ADMIN_IM_ID`
- `init_db` 时自动创建管理员 User 记录，`is_admin=True`，`permission_level=4`，`org_category=4`
- 后续通过 IM 对话（SOP-000-SYS）管理权限分配

### 6.2 权限变更与 Session 失效

- 权限变更在用户**下次会话生效**（符合"灌注一次"原则）
- 管理员操作后提示："权限变更将在用户下次对话时生效"
- 如用户需要立即生效，可引导用户主动触发 session 刷新（如发送"刷新"或"重置会话"等指令）
- **v1.5 新增——紧急权限撤销**：管理员可通过 `SOP-000-SYS` 主动将指定用户/公司的活跃 Session 标记为 `permissions_stale`。被标记的 Session 在下次 WorkItem 处理前自动重新加载权限快照，无需用户手动刷新

### 6.3 Session 生命周期（v1.5 新增）

- Session 创建后，`PermissionSnapshot.permissions_loaded_at` 记录权限加载时间
- 默认最大 Session 存活时间：24 小时（可配置 `EMILY_SESSION_MAX_TTL_HOURS`）
- 超过 TTL 后，下次请求自动重新创建 Session 并刷新权限快照
- `PermissionSnapshot.permission_version` 用于轻量变更检测：每次权限变更时递增全局版本号，Hook 处理前对比版本号，不一致则触发 Session 权限重载

### 6.4 错误处理策略（v1.5 新增）

当权限查询 DB 失败时（如数据库宕机、网络超时），系统采用 **fail-open + 告警** 策略：

| 失败场景 | 行为 | 说明 |
|---------|------|------|
| Session 创建时权限查询失败 | `permission_level=0`（访客）降级 + 告警 | 用户可进行公开操作，核心功能不受影响 |
| 鉴权引擎查询 DB 失败 | ALLOW + 告警 | 不阻断业务，但记录审计日志和告警 |
| 行级安全拦截器失败 | ALLOW + 告警 | 允许 SQL 执行但记录 WARNING |
| 权限审计日志写入失败 | 静默忽略 + 本地日志 | 不抛异常，不影响主流程 |

告警通过日志输出，后续可接入 IM 通知（如向管理员发送异常消息）。

---
## 六-B、权限矩阵缓存策略（v1.5 新增）

### 6B.1 背景

每次 Session 创建都需联表查询 3 张权限表（`permission_groups` + `sop_business_flows` + `sop_permission_bindings`）来构建用户的 SOP 白名单。为减少 DB 压力和延迟，引入应用级缓存。

### 6B.2 两级缓存设计

| 缓存层级 | 内容 | TTL | 命中率预期 |
|---------|------|-----|-----------|
| L1 - 权限矩阵 | PermissionGroup × SOPBusinessFlow × SOPPermissionBinding 的完整结果集 | 5 分钟 | 80%+ |
| L2 - 用户 SOP 白名单 | 单个用户的 `sop_allow` 列表（基于 L1 + 用户属性计算） | Session 生命周期 | 100%（Session 内） |

### 6B.3 实现要点

```python
class PermissionCache:
    """权限矩阵缓存 —— 减少 Session 创建时的联表查询。"""
    
    def __init__(self, ttl: int = 300):
        self._matrix: dict = {}          # {group_id: {sop_id: binding}}
        self._matrix_loaded_at: float = 0
        self._ttl = ttl
        self._lock = threading.RLock()
    
    def get_user_sop_allow(self, perms: PermissionSnapshot) -> list[str]:
        """计算用户的 SOP 白名单（基于缓存的权限矩阵 + 用户属性）。"""
        if self._is_stale():
            self._reload()
        # 遍历矩阵，按 permission_level + company_type + department 过滤
        ...
    
    def invalidate(self) -> None:
        """管理员修改权限组/SOP绑定时调用，强制下次 Session 创建时重载。"""
        with self._lock:
            self._matrix_loaded_at = 0
```

### 6B.4 缓存失效触发

- 管理员通过 `SOP-000-SYS` 修改权限组/SOP 绑定时 → 调用 `invalidate()`
- 任何增删 `PermissionGroup` 或 `SOPPermissionBinding` 的操作 → 自动调用 `invalidate()`
- TTL 到期 → 下次 Session 创建时自动重载
- 缓存加载失败 → 降级为直接查 DB（记录 WARNING）

---
## 六-C、索引设计（v1.5 新增）

以下索引确保权限相关查询在百万级数据量下的性能：

```sql
-- PermissionGroup 查询索引（按企业类型+部门查询权限组）
CREATE INDEX idx_pg_company_dept ON permission_groups(company_type, department, status);

-- SOPBusinessFlow 公开 SOP + 按类型查询索引
CREATE INDEX idx_sbf_public ON sop_business_flows(is_public, is_active, sop_type);

-- SOPPermissionBinding 反向查询索引（按权限组查 SOP）
CREATE INDEX idx_spb_group ON sop_permission_bindings(permission_group_id);
CREATE INDEX idx_spb_flow_group ON sop_permission_bindings(sop_business_flow_id, permission_group_id);

-- PermissionAuditLog 查询索引
CREATE INDEX idx_pal_user_time ON permission_audit_logs(user_id, created_at);
CREATE INDEX idx_pal_type_time ON permission_audit_logs(log_type, created_at);

-- User 权限查询索引
CREATE INDEX idx_users_company ON users(company);
CREATE INDEX idx_users_perm_level ON users(permission_level);
```

---

## 七、当前代码对齐情况

| 现有字段 | 位置 | 状态 | 改造动作 |
|----------|------|------|----------|
| `User.grouping` | [models.py:81](emily-core/emily_core/infrastructure/database/models.py#L81) | 值域 0-4（旧语义） | v1.5：改名为 `org_category`，保留只读不参与鉴权 |
| `User.permission_level` | 新增 | 不存在 | 新增字段，值域 0-4（0=访客→4=管理员），参与鉴权。迁移脚本按映射表填充 |
| `User.perm_list` | [models.py:80](emily-core/emily_core/infrastructure/database/models.py#L80) | JSON `"[]"` | 关联 PermissionDef |
| `User.company` | [models.py:83](emily-core/emily_core/infrastructure/database/models.py#L83) | JSON `"[]"` | 改为单 FK → CompanyInfo（扩展后的表） |
| `CompanyInfo` | [models.py:307](emily-core/emily_core/infrastructure/database/models.py#L307) | 已有基础字段 | 扩展 type/scope/partners/parent_id/department 字段 |
| `SessionContext.perm_list` | [session_context.py:96](emily-core/emily_core/session/session_context.py#L96) | 空列表 | 保留为兼容字段，标记 deprecated（Phase III 后移除） |
| `SessionContext.permissions` | [session_context.py:93](emily-core/emily_core/session/session_context.py#L93) | 已有 PermissionSnapshot | 新增 permissions_loaded_at, permission_version 字段 |
| `WorkItemAgent.authorize()` | [workitem_agent.py:482-507](emily-core/emily_core/workitem/workitem_agent.py#L482) | 永远 ALLOW | **v1.5 完全重写**：改为三维鉴权引擎（§5.2） |
| `SOPIntentSpec.allow_roles` | [intent_registry.py:44](emily-core/emily_core/agent/intent_registry.py#L44) | 已有 | 保留作为降级兜底，Phase III 后标记 deprecated |
| `AuthHook` | [hook.py:92-128](emily-core/emily_core/workitem/pipeline/hook.py#L92) | 已有，部分工作 | 完善 system.execute 检查，接入 PermissionSnapshot |
| `BusContext._session_context` | [context.py:49](emily-core/emily_core/workitem/pipeline/context.py#L49) | 已有 | authorize() 签名改为接收 BusContext 参数 |

### 7.1 兼容字段 Sunset Plan（v1.5 新增）

| 字段 | 当前阶段 | Phase I-III | Phase IV+ |
|------|---------|-------------|-----------|
| `SessionContext.perm_list` | 空列表（兼容） | 保持 | 标记 deprecated，下个版本移除 |
| `SOPIntentSpec.allow_roles` | 鉴权依赖 | 迁移到 SOPBusinessFlow 后标记 deprecated | 仅保留作为 SOP 文件元数据，不参与鉴权 |
| `User.org_category` (原 grouping) | 参与鉴权 | 迁移到 permission_level 后只读 | 保留作为统计维度，不参与鉴权 |

---

## 八、待明确事项

- [ ] 公司"承包范围"是否需关联到具体项目/标段？
- [ ] "履约中"的判断依据：合同有效期内 or 有未完工的施工任务？
- [ ] 异常审核人"直接上级"是否存入 User 表字段 or 从组织架构树推导？
- [ ] 权限管理 SOP 是否需要独立的管理界面 vs 纯 IM 对话？

---
## 九、SOP 鉴权两层架构（v1.2 新增）

### 9.1 架构概述

SOP 鉴权设计为两层结构：
- **第一层：组织架构维度** - PermissionGroup 权限组
- **第二层：业务流维度** - SOPBusinessFlow 业务流特征信息

两层通过 SOPPermissionBinding 实现多对多关联，支持灵活的权限配置。

### 9.2 第一层：PermissionGroup（组织架构维度）

#### 两层归属分类：
| 层级 | 分类维度 | 说明 |
|------|----------|------|
| 第一层 | 企业类型 | 建设单位 / 设计单位 / 总包 / 分包 / 监理 / 供应商 |
| 第二层 | 部门归属 | 设计部 / 工程部 / 成本部 / 采购部 等（建设单位需细分） |

#### 企业分组设计原则：
- **一般企业**：只做简单的管理组与业务组划分
- **建设单位**：需细分部门以便权限细分
  - 设计部：仅限设计相关 SOP
  - 工程部：仅限工程管理相关 SOP
  - 成本部：仅限成本核算相关 SOP
  - 采购部：仅限采购相关 SOP
  - ...

#### 权限组字段：
- `name` / `code`：权限组名称与编码（如"建设单位-设计部" / "OWNER-DESIGN"）
- `company_type`：企业类型
- `department`：部门归属
- `org_level`：组织层级（1=企业 2=部门 3=小组）
- `parent_group_id`：支持层级嵌套
- `min_permission_level`：最低权限层级要求（累进继承）

### 9.3 第二层：SOPBusinessFlow（业务流维度）

#### 业务流特征信息：
| 字段 | 说明 |
|------|------|
| `sop_id` | SOP 编号，关联 SOP 文件 |
| `display_name` | 业务名称 |
| `description` | 一句话功能描述 |
| `sop_type` | SOP 类型（REC/FILE/QRY/FLOW/SYS） |
| `category` | 业务分类（工程记录/项目管理） |

#### 权限控制字段：
- `min_permission_level`：最低权限层级（累进继承）
- `require_company_match`：是否需要企业类型匹配
- `require_department_match`：是否需要部门匹配
- `is_public`：是否公开（所有用户可见）
- `allowed_company_types`：允许的企业类型列表
- `allowed_departments`：允许的部门列表

### 9.4 鉴权逻辑流程

```
用户访问 SOP 目录请求
    ↓
检查是否公开 SOP?
    ├─ 是 → 直接可见
    └─ 否 → 检查权限组绑定
              ↓
        检查企业类型匹配?
        ├─ 否 → 不可见
        └─ 是 → 检查部门匹配（如需要）
                  ├─ 否 → 不可见
                  └─ 是 → 检查权限层级
                            ├─ 不满足 → 不可见
                            └─ 满足 → ✅ 可见可用
```

### 9.5 数据库表结构

三张新增表（v1.2）：
1. **permission_groups** - 权限组表（第一层）
2. **sop_business_flows** - SOP 业务流特征信息表（第二层）
3. **sop_permission_bindings** - SOP 权限组绑定表（多对多）

---
## 十、实施建议

分四个 Phase 推进：

| Phase | 内容 | 依赖 | 预计工作量 |
|-------|------|------|----------|
| **Phase I** | 数据表改造 + 数据迁移 | 无 | 2 周 |
| | - 扩展 `CompanyInfo` 表（新增 type/scope/partners/parent_id/department） | | |
| | - 新建 `PermissionDef` 表 | | |
| | - 新建 `public_field_registry` 表（含 project_id） | | |
| | - 新建 `PermissionGroup` / `SOPBusinessFlow` / `SOPPermissionBinding` 表 | | |
| | - 新建 `PermissionAuditLog` 表 | | |
| | - User 表改造：新增 `permission_level`，`grouping`→`org_category`，`company`→FK | | |
| | - 数据迁移脚本：grouping→permission_level 映射 + company JSON→FK 映射 | | |
| | - 创建索引（§6-C） | | |
| **Phase II** | SessionContext 权限快照灌注 + SOPBusinessFlow 数据初始化 | Phase I | 1.5 周 |
| | - `PermissionSnapshot` 扩展（permissions_loaded_at / permission_version） | | |
| | - `SessionFactory._build_context()` 中全量加载权限快照 | | |
| | - SOP 文件 allow_roles → SOPBusinessFlow 数据迁移脚本 | | |
| | - `PermissionCache` 权限矩阵缓存实现 | | |
| | - Session TTL 和权限版本号变更检测 | | |
| **Phase III** | 鉴权执行点全面实现 | Phase II | 2 周 |
| | - `authorize()` 三维鉴权引擎重写（§5.2） | | |
| | - `BusContext` 鉴权只读方法集成 | | |
| | - SQLAlchemy `before_execute` 行级安全拦截器（§5.3.1） | | |
| | - `AuthHook` 接入 PermissionSnapshot | | |
| | - 权限审计日志写入 + 异常检测 | | |
| | - 紧急权限撤销（Session 标记 stale） | | |
| **Phase IV** | Project State Machine | Phase III | 6 周 |
| | - 见 §12.8 实施路线图 | |

---
## 十一、版本变更总结

### v1.5 变更说明（2026-06-25）

| 变更项 | 变更前 | 变更后 |
|---------|--------|--------|
| 权限层级字段 | `grouping`（同名多义，旧语义=组织标签） | `permission_level`（独立新字段），旧字段改名 `org_category` |
| 用户公司归属 | JSON `"[]"`（支持多公司但未使用） | 单 FK → `CompanyInfo` |
| 公司表 | 新建 `Company` 表 | 扩展现有 `CompanyInfo` 表 |
| SOP 鉴权引擎 | `allow_roles` 字符串匹配（`authorize()` 永远 ALLOW） | 三维鉴权引擎（permission_level × company_type × department） |
| SOP 权限数据 | 仅 SOP 文件 §1 中的 `allow_roles` | 数据库 `SOPBusinessFlow` 表 + `SOPPermissionBinding` 多对多 |
| 权限审计 | 无 | `PermissionAuditLog` 表 + 异常检测 |
| 行级安全 | 方案概述 | 详细实现：重复注入防护 + JOIN/UNION 处理 + fail-open |
| 索引 | 未定义 | 完整索引方案（§6-C） |
| 权限缓存 | 无 | 两层缓存：权限矩阵(L1) + 用户白名单(L2) |
| Session 生命周期 | 无限制 | 24h TTL + 权限版本号检测 + 管理员主动失效 |
| 错误处理 | 未明确 | fail-open + 告警策略（§6.4） |
| `predecessor_ids` | JSON 数组 | `stage_dependencies` 关联表 + 外键约束 |
| 阶段状态变更 | 无并发保护 | 乐观锁 `version` 字段 + 审批完成重校验 |
| 工具阶段校验 | 硬编码 `tool_name in [...]` | 工具元数据声明 `required_phase_status` |

### v1.2 核心变更点：

| 变更项 | 变更前 | 变更后 |
|---------|--------|--------|
| 权限存放位置 | 直接灌注到 Session-Agent | SessionContext 内专门字段存放（PermissionSnapshot） |
| WorkItemAgent 权限访问 | 直接持有权限数据 | 通过 BusContext 只读方法访问（无状态，不持有数据） |
| SOP 鉴权层级 | 单层（仅 allow_roles 简单角色列表） | 两层架构（企业+部门）+ 权限组绑定 |
| 部门权限粒度 | 粗粒度 | 细粒度（建设单位可细分部门） |

### 安全边界：

1. **上下文污染隔离**：权限数据不直接注入 Agent 上下文，避免 LLM 意外访问或泄露
2. **只读访问保证**：WorkItemAgent 仅能通过公开方法读取权限，无法修改
3. **权限校验集中化**：所有鉴权逻辑集中在 SessionContext/BusContext 层，便于审计追踪

### 兼容策略：

- 保留 `SessionContext.perm_list` 作为过渡字段，后续逐步移除
- `WorkItemAgent.authorize()` 方法签名变更为接收 `BusContext` 参数而非 `user_id`
- SOPIntentRegistry 的 `allow_roles` 保留作为兜底逻辑

---
## 十二、Project State Machine - 项目状态机子系统（v1.3 规划）

### 12.1 设计背景与目标

**业务场景示例**：
景观施工承包单位业务人员上报："完成编号为 S4 地块的铺装"。
查询 Project 状态机发现：
1. ✅ '大区景观工程施工'阶段模块已启动
2. ❌ 细分的子状态机显示 S4 地块当前记录处于**土方回填阶段**
3. ❌ 其后续阶段节点"铺装施工"尚未启动激活

**系统行为**：判定信息异常 → 询问用户录入是否有误 → 用户坚持录入 → 通知有此阶段节点启停权限的负责人（建设方景观工程主管）给予启动确认或驳回丢弃。

**设计目标**：
- 提供**两层状态机架构**，支持项目级大阶段与模块级子状态机的分层管理
- 基于**权限表控制字段可见性**：公共/内部两层字段访问控制
- Session-Agent / WorkItem-Agent 通过 Session 权限表控制读取范围
- 提供**阶段状态校验**：任务数据上报前校验所属阶段运转状态
- 异常流程：用户确认 → 负责人审批 → 启动/驳回

---
### 12.2 两层状态机架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Project State Machine                     │
├─────────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  外层：Phase 项目大阶段（ProjectPhase）            │   │
│  │  - 立项阶段 │ 规划设计 │ 工程施工 │ 交付结算  │   │
│  └───────────────────────────────────────────────────────┘   │
│                           ↓                                │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  内层：Stage 模块/地块子状态机（ProjectStage）    │   │
│  │  - 土方回填 → 管线铺设 → 铺装施工 → 绿化种植  │   │
│  │  - 每个大阶段可包含多个并行子状态机实例     │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐   │
│  │  权限控制层（Permission Gate）                   │   │
│  │  - public_fields: 所有用户可读                  │   │
│  │  - internal_fields: 仅限授权用户可读            │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---
### 12.3 数据模型设计

#### 12.3.1 大阶段表（project_phases）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| project_id | FK | 关联项目 |
| phase_code | String | 阶段编码（如 LANDSCAPE_CONSTRUCTION） |
| phase_name | String | 阶段名称（如"大区景观工程施工"） |
| description | String | 阶段描述 |
| status | Enum | 状态：NOT_STARTED / RUNNING / PAUSED / COMPLETED / CANCELLED |
| parent_phase_id | FK | 父阶段ID（支持阶段嵌套） |
| sequence | Integer | 阶段顺序号 |
| started_at | DateTime | 启动时间 |
| completed_at | DateTime | 完成时间 |
| expected_duration | Integer | 预计工期（天） |
| owner_id | FK | 阶段负责人（启停权限） |
| is_public | Boolean | 是否公开阶段 |
| created_at | DateTime | |
| updated_at | DateTime | |

#### 12.3.2 子阶段/地块表（project_stages）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| phase_id | FK | 所属大阶段 |
| project_id | FK | 关联项目 |
| stage_code | String | 子阶段编码（如 EARTH_BACKFILL_S4） |
| stage_name | String | 子阶段名称（如"S4 地块-土方回填"） |
| block_id | String | 地块/模块标识（如"S4"） |
| description | String | 子阶段描述 |
| status | Enum | 状态：NOT_STARTED / RUNNING / PAUSED / COMPLETED / CANCELLED |
| sequence | Integer | 子阶段顺序号 |
| version | Integer | **v1.5 新增**：乐观锁版本号，每次状态变更 +1 |
| started_at | DateTime | 启动时间 |
| completed_at | DateTime | 完成时间 |
| owner_id | FK | 子阶段负责人（启停权限） |
| metadata | JSON | 扩展元数据 |
| is_public | Boolean | 是否公开子阶段 |
| created_at | DateTime | |
| updated_at | DateTime | |

> **v1.5 变更**：`predecessor_ids`（JSON 数组）改为关联表 `stage_dependencies`，确保引用完整性。

#### 12.3.2b 阶段依赖关系表（stage_dependencies）—— v1.5 新增

替换 `project_stages.predecessor_ids` JSON 字段，使用独立关联表确保外键约束：

```python
StageDependency
  id                  UUID           # 主键
  predecessor_stage_id FK            # 前置子阶段 ID → project_stages.id
  successor_stage_id   FK            # 后置子阶段 ID → project_stages.id
  dependency_type      str           # "finish_to_start" / "start_to_start" / "finish_to_finish"
  lag_days             int           # 滞后天数（默认 0，正数=滞后，负数=提前）
  created_at           str

  __table_args__ = (
      UniqueConstraint("predecessor_stage_id", "successor_stage_id", name="uq_stage_dep"),
  )
```

校验时查询：`SELECT predecessor_stage_id FROM stage_dependencies WHERE successor_stage_id = ? AND dependency_type = 'finish_to_start'`，确保所有前置阶段的 status = COMPLETED。

#### 12.3.3 状态机权限表（project_state_permissions）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| phase_id | FK | 关联阶段（为空则为全局） |
| stage_id | FK | 关联子阶段（为空则为阶段级） |
| permission_type | Enum | 权限类型：READ_PUBLIC / READ_INTERNAL / START_STOP / MODIFY_METADATA |
| permission_level_required | Integer | 所需权限层级（累进继承） |
| allowed_company_types | JSON | 允许的企业类型列表 |
| allowed_departments | JSON | 允许的部门列表 |
| allowed_roles | JSON | 允许的角色列表 |
| created_at | DateTime | |

#### 12.3.4 状态机审计日志表（project_state_audit_logs）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| project_id | FK | 关联项目 |
| phase_id | FK | 关联阶段 |
| stage_id | FK | 关联子阶段 |
| action_type | Enum | 操作类型：START / STOP / PAUSE / COMPLETE / MODIFY |
| previous_status | Enum | 变更前状态 |
| new_status | Enum | 变更后状态 |
| operator_id | FK | 操作人 |
| reason | String | 操作原因 |
| related_task_id | FK | 关联任务ID（如有） |
| created_at | DateTime | |

---
### 12.4 公共/内部两层字段访问控制

#### 12.4.1 字段分级定义

| 层级 | 字段范围 | 说明 |
|------|----------|------|
| **public_fields** | phase_name / status / started_at / completed_at / is_public | 所有用户可读，无需特殊权限 |
| **internal_fields** | owner_id / expected_duration / metadata / predecessor_ids / audit_logs | 仅授权用户可读，需满足权限校验 |

#### 12.4.2 访问控制机制

```python
# PermissionSnapshot 扩展字段（v1.3）
permissions: {
    # ... 新增 Project State Machine 专用权限
    "project_state_access": {
        "can_read_public": true,           # 可读公共字段（默认 true）
        "can_read_internal": false,        # 可读内部字段（需授权）
        "can_start_stop_phase": false,    # 可启停大阶段
        "can_start_stop_stage": false,   # 可启停子阶段
        "can_modify_metadata": false     # 可修改元数据
    },
    "authorized_phase_ids": ["..."],       # 有权限的阶段ID
    "authorized_stage_ids": ["..."]       # 有权限的子阶段ID
}
```

#### 12.4.3 Agent 读取流程

```
Session-Agent / WorkItem-Agent 读取请求
         ↓
  通过 BusContext 获取 PermissionSnapshot
         ↓
  访问 ProjectStateService（统一入口）
         ↓
┌─────────────────────────────────────┐
│  权限校验                          │
│  - 检查 can_read_public / internal │
│  - 检查 phase/stage 授权列表    │
└─────────────────────────────────────┘
         ↓
  字段过滤（只返回有权限的字段）
         ↓
  返回结果给 Agent
```

---
### 12.5 阶段状态校验与异常处理流程

#### 12.5.1 状态枚举定义

**PhaseStatus（大阶段状态）**：
- `NOT_STARTED` - 未启动
- `RUNNING` - 运行中
- `PAUSED` - 已暂停
- `COMPLETED` - 已完成
- `CANCELLED` - 已取消

**StageStatus（子阶段状态）**：
- `NOT_STARTED` - 未启动
- `RUNNING` - 运行中
- `PAUSED` - 已暂停
- `COMPLETED` - 已完成
- `CANCELLED` - 已取消

**ValidationResultCode（校验结果码）**：
- `VALID` - ✅ 校验通过
- `PHASE_NOT_RUNNING` - ❌ 所属大阶段未运行
- `STAGE_NOT_RUNNING` - ❌ 所属子阶段未运行
- `PREDECESSOR_NOT_COMPLETED` - ❌ 前置依赖未完成
- `BLOCK_MISMATCH` - ❌ 地块/模块不匹配
- `NO_PERMISSION` - ❌ 无操作权限

#### 12.5.2 任务数据上报校验流程

```
用户上报任务数据
       ↓
  WorkItemAgent 接收
       ↓
  解析任务关联的 phase_code + stage_code + block_id
       ↓
  调用 ProjectStateService.validate()
       ↓
  ┌──────────────────────────────────────────────┐
  │  校验逻辑：                            │
  │  1. phase 是否为 RUNNING?            │
  │  2. stage 是否为 RUNNING?            │
  │  3. 前置 predecessor 是否 COMPLETED?    │
  │     (查询 stage_dependencies 表)           │
  │  4. block_id 与 stage.block_id 匹配?   │
  │  5. v1.5: 乐观锁 version 检查（并发安全） │
  └──────────────────────────────────────────────┘
       ↓
  ┌─────────────┐      ┌─────────────┐
  │  VALID?    │ Yes → │  正常录入  │
  └─────────────┘      └─────────────┘
       │ No
       ↓
  ┌──────────────────────────────────────────────┐
  │  生成异常提示：                          │
  │  "检测到阶段状态异常：                  │
  │  当前阶段：[阶段名称] - [当前状态]      │
  │  目标阶段：[目标阶段名称] - [未启动]    │
  │  请问录入是否有误？                   │
  └──────────────────────────────────────────────┘
       ↓
  弹出 SOP 确认节点（SOP-00X-异常确认）
       ↓
  ┌─────────────────────┐      ┌─────────────────┐
  │  用户纠正?   │ No → │  丢弃录入   │
  └─────────────────────┘      └─────────────────┘
       │ Yes
       ↓
  ┌──────────────────────────────────────────────┐
  │  生成阶段启动审批请求                    │
  │  - 关联：phase_id / stage_id / operator_id  │
  │  - 发送给：阶段负责人（owner_id）       │
  │  - 附带：用户确认记录 + 任务上下文       │
  └──────────────────────────────────────────────┘
       ↓
  SOP 审批流程（SOP-00Y-阶段启动审批）
       ↓
  ┌──────────────────────────────────────────────┐
  │  v1.5: 审批通过后重新执行 validate()  │
  │  确保状态未被并发操作改变              │
  └──────────────────────────────────────────────┘
       ↓
  ┌────────────────────┐      ┌───────────────────┐
  │  负责人批准?     │ Yes → │  启动目标阶段 → │
  │  (start_stage)  │      │  继续任务录入    │
  └────────────────────┘      └───────────────────┘
       │ No
       ↓
  ┌──────────────────────────────────────────────┐
  │  驳回，通知用户：                      │
  │  "负责人[姓名]已驳回阶段启动请求    │
  │  原因：[驳回原因]                      │
  └──────────────────────────────────────────────┘
```

#### 12.5.3 并发安全设计（v1.5 新增）

状态变更操作（START/STOP/PAUSE/COMPLETE）必须处理并发竞态：

```python
async def start_stage(self, stage_id: str, operator_id: str) -> StageResult:
    """启动子阶段 —— 乐观锁版本号检测并发冲突。"""
    stage = await self._stage_repo.get_by_id(stage_id)
    if stage is None:
        return StageResult(success=False, code="NOT_FOUND")
    
    # 乐观锁更新：WHERE id = ? AND version = ?，如 affected_rows = 0 则冲突
    affected = await self._stage_repo.update_status_with_version(
        stage_id=stage_id,
        new_status="RUNNING",
        expected_version=stage.version,
        operator_id=operator_id,
    )
    if affected == 0:
        # 版本号不匹配 → 并发冲突 → 重新读取最新状态后重试
        fresh_stage = await self._stage_repo.get_by_id(stage_id)
        return StageResult(
            success=False,
            code="CONCURRENT_MODIFICATION",
            message=f"阶段状态已被其他操作修改（当前: {fresh_stage.status}），请刷新后重试",
        )
    
    # 记录审计日志
    await self._audit_log(stage_id, "START", "NOT_STARTED", "RUNNING", operator_id)
    return StageResult(success=True, code="OK")
```

---
### 12.6 权限集成点与现有系统对接

#### 12.6.1 SessionContext 扩展（v1.3 扩展）

在 `PermissionSnapshot` 中新增：

```python
@dataclass
class PermissionSnapshot:
    # ... 现有字段 ...

    # ── Project State Machine 访问权限
    project_state_access: dict = field(default_factory=lambda: {
        "can_read_public": True,
        "can_read_internal": False,
        "can_start_stop_phase": False,
        "can_start_stop_stage": False,
        "can_modify_metadata": False,
    })
    authorized_phase_ids: list = field(default_factory=list)
    authorized_stage_ids: list = field(default_factory=list)
```

#### 12.6.2 BusContext 扩展只读方法

```python
class BusContext:
    # ... 现有方法 ...

    def can_read_project_state(self, phase_id: str = None, stage_id: str = None) -> bool:
        """检查是否有权限读取项目状态（公共字段）"""
        # 检查 project_state_access.can_read_public
        # 检查 phase_id / stage_id 是否在授权列表
        pass

    def can_read_project_state_internal(self, phase_id: str = None, stage_id: str = None) -> bool:
        """检查是否有权限读取项目内部字段"""
        pass

    def can_start_stop_phase(self, phase_id: str) -> bool:
        """检查是否有权限启停指定阶段"""
        pass

    def can_start_stop_stage(self, stage_id: str) -> bool:
        """检查是否有权限启停指定子阶段"""
        pass
```

#### 12.6.3 WorkItemAgent 集成点

在 `node3_execute` 执行工具调用前插入校验：

```python
async def node3_execute(self, context: BusContext) -> None:
    # ... 现有逻辑 ...

    # v1.5: Project State Machine 阶段校验 Hook
    # 不再硬编码 tool_name 列表——通过工具注册时的元数据判定是否需要阶段校验
    if self._tool_registry.requires_phase_validation(tool_name):
        validation_result = await self._validate_project_stage(context, tool_params)
        if not validation_result.is_valid:
            # 触发异常确认流程
            context.set("stage_validation_result", validation_result)
            # 进入 SOP 确认节点
            # ...
```

**v1.5 改进**：工具注册时声明 `required_phase_status: RUNNING` 元数据，校验逻辑从工具元数据读取，而非硬编码 `tool_name in ["record_event", "record_task"]`。新增工具只需声明元数据即可自动获得阶段校验保护。

```python
# 工具注册示例
@register_tool(
    name="record_event",
    handler=record_event_handler,
    metadata={
        "required_phase_status": "RUNNING",  # 需要所属阶段处于运转状态
        "phase_field_mapping": {"phase_code": "phase_code"},  # 参数→阶段字段映射
    }
)
class RecordEventTool:
    ...
```

---
### 12.7 典型场景完整示例

**场景**：景观施工承包单位上报 S4 地块铺装完成

**步骤 1：数据准备**
```python
# 用户输入
user_input = "完成编号为 S4 地块的铺装"

# 系统解析
task_params = {
    "project_id": "proj-001",
    "phase_code": "LANDSCAPE_CONSTRUCTION",
    "stage_code": "PAVING_CONSTRUCTION",
    "block_id": "S4"
}
```

**步骤 2：调用 ProjectStateService.validate()**
```python
validation_result = ProjectStateService.validate(
    project_id="proj-001",
    phase_code="LANDSCAPE_CONSTRUCTION",
    stage_code="PAVING_CONSTRUCTION",
    block_id="S4",
    user_permissions=context.get_permissions()
)

# 返回结果
{
    "is_valid": False,
    "code": "STAGE_NOT_RUNNING",
    "message": "S4 地块当前处于'土方回填'阶段，'铺装施工'阶段尚未启动",
    "current_phase": {
        "id": "phase-003",
        "name": "大区景观工程施工",
        "status": "RUNNING"
    },
    "current_stage": {
        "id": "stage-015",
        "name": "S4 地块-土方回填",
        "status": "RUNNING",
        "block_id": "S4"
    },
    "target_stage": {
        "id": "stage-016",
        "name": "S4 地块-铺装施工",
        "status": "NOT_STARTED",
        "block_id": "S4"
    },
    "stage_owner": {
        "user_id": "user-owner-001",
        "name": "张三",
        "department": "建设方-景观工程部"
    }
}
```

**步骤 3：异常确认与审批**
```
系统提示用户：
  "检测到阶段状态异常：
   当前 S4 地块处于'土方回填'阶段（运行中），
   你上报的'铺装施工'阶段尚未启动。
   请问录入是否有误？"

用户选择："确认无误，继续录入"

→ 系统自动向 stage_owner（张三）发送审批通知
→ 张三登录系统查看审批请求
→ 张三批准：启动"铺装施工"阶段
→ 系统自动更新 Stage 状态为 RUNNING
→ 任务数据正常录入
```

---
### 12.8 实施路线图

| Phase | 内容 | 依赖 | 预计工作量 |
|-------|------|------|----------|
| **Phase IV-1 | 数据表设计与基础服务实现 | Phase III 完成 | 2 周 |
| | - project_phases / project_stages 表 | | |
| | - project_state_permissions 表 | | |
| | - ProjectStateService 基础 CRUD | | |
| **Phase IV-2** | 权限控制与字段过滤 | Phase IV-1 | 1 周 |
| | - PermissionSnapshot 扩展 | | |
| | - BusContext 只读方法 | | |
| | - 字段过滤中间件 | | |
| **Phase IV-3** | 状态校验与异常流程 | Phase IV-2 | 2 周 |
| | - validate() 方法实现 | | |
| | - 前置依赖检查逻辑 | | |
| | - 用户确认 SOP | | |
| | - 负责人审批 SOP | | |
| **Phase IV-4** | 审计日志与监控 | Phase IV-3 | 1 周 |
| | - 状态变更审计日志 | | |
| | - 阶段状态看板 | | |
| | - 异常告警通知 | | |

---
### 12.9 风险与注意事项

1. **状态一致性**：
   - 阶段启动/停止操作需要原子性
   - 避免并发操作导致状态不一致
   - 使用数据库乐观锁（`project_stages.version` 字段），实现见 §12.5.3
   - 审批通过后重新执行 `validate()` 再写入，防止审批期间的并发变更

2. **权限粒度控制**：
   - 负责人权限分配需谨慎
   - 建议支持多级审批
   - 权限变更需记录审计

3. **性能考虑**：
   - 频繁的状态查询需缓存
   - 避免每次校验时重复查询数据库
   - 状态变更实时推送到 Session
   - `stage_dependencies` 依赖关系可缓存在应用内存中（依赖关系变更频率极低）

4. **回退机制**：
   - 阶段误启动后的回退流程
   - 数据已录入后的阶段回滚处理
   - 回退操作同样需要乐观锁保护

---
## 十三、架构师审核优化方案（v1.4 架构升级建议）

> 审核结论：v1.3 设计方向正确，但需要补充性能、安全、可观测性三个维度的架构细节。建议按 M1-M4 路线图分阶段实施。

---
### 13.1 核心架构优化

#### 13.1.1 权限校验：快照 + 实时混合模式

**问题**：Session 创建时一次性灌注权限，后续管理员吊销权限后旧 Session 仍持有旧权限。

**优化方案**：

```
┌──────────────────────────────────────────────────────────┐
│  权限校验策略（分级处理）                              │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  🔹 高频查询（SOP 可见性 / 字段读取）                    │
│     → 使用 Session 本地快照（1 秒内完成）                │
│     → TTL：30 分钟，后台异步刷新                        │
│                                                          │
│  🔹 关键操作（数据写入 / 阶段启停 / 审批）               │
│     → 强制实时 DB 校验（10-50ms）                       │
│     → 记录完整审计日志                                   │
│                                                          │
│  🔹 权限变更广播                                        │
│     → Redis Pub/Sub 推送权限变更事件                     │
│     → 相关 Session 收到后强制刷新快照                   │
└──────────────────────────────────────────────────────────┘
```

**实现要点**：
```python
# PermissionSnapshot 新增字段
{
    "last_refresh_at": "2026-06-25T10:30:00",  # 上次刷新时间
    "refresh_interval_seconds": 1800,           # 刷新间隔（30分钟）
    "critical_operations": [                    # 需要实时校验的操作
        "record_event", "record_task", "start_phase", "approve"
    ]
}
```

---
#### 13.1.2 字段访问：三层分级 + 数据脱敏

**问题**：只有 public / internal 两层，粒度不足，敏感信息保护不够。

**优化方案**：

| 层级 | 字段范围 | 访问控制 | 脱敏规则 |
|------|----------|----------|----------|
| **L1 - 公开** | phase_name / status / started_at / completed_at / is_public | 所有人可读，无需登录 | 无 |
| **L2 - 内部** | owner_name / department / expected_duration / basic_metadata | 项目成员可读 | 手机号中间 4 位隐藏，邮箱显示前缀 |
| **L3 - 敏感** | owner_contact / cost_budget / audit_details / full_metadata | 仅负责人 / 管理员可读 | 完整显示，操作留痕 |

**字段分级配置表（新增表）**：

| 字段名 | 默认层级 | 可动态调整 | 说明 |
|--------|----------|------------|------|
| phase_name | L1 | ❌ | 阶段名称 |
| status | L1 | ❌ | 阶段状态 |
| owner_name | L2 | ✅ | 负责人姓名 |
| owner_contact | L3 | ❌ | 负责人联系方式 |
| cost_budget | L3 | ✅ | 预算金额 |
| expected_duration | L2 | ✅ | 预计工期 |

**脱敏中间件伪代码**：
```python
def apply_field_masking(data: dict, user_permission: PermissionSnapshot) -> dict:
    """根据用户权限等级对敏感字段自动脱敏"""
    result = {}
    for field, value in data.items():
        field_level = get_field_security_level(field)
        user_level = get_user_security_level(user_permission)

        if user_level >= field_level:
            result[field] = value  # 完整显示
        elif user_level >= field_level - 1:
            result[field] = mask_sensitive_data(value)  # 脱敏显示
        else:
            result[field] = None  # 隐藏字段

    return result
```

---
#### 13.1.3 阶段启停权限：去单点化 + 代理机制

**问题**：只有 owner_id 一人有权限，负责人休假时流程卡住。

**优化方案**：

```python
# 权限表扩展字段
{
    "owner_id": "user-001",              # 主负责人（最终决定权）
    "delegate_ids": ["user-002", "user-003"],  # 代理人（可授权多人）
    "temp_auth": [                       # 临时授权
        {
            "user_id": "user-004",
            "granted_by": "user-001",
            "expires_at": "2026-07-15T23:59:59",
            "reason": "张三休假期间代理审批"
        }
    ],
    "backup_owner_id": "user-005"        # 备份负责人（自动升级）
}
```

**权限优先级**：
```
主负责人(owner_id) > 备份负责人 > 代理人(delegate_ids) > 临时授权(temp_auth)
```

---
### 13.2 性能优化架构

#### 13.2.1 三级缓存架构

```
┌──────────────────────────────────────────────────────────┐
│  三级缓存架构                                          │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  L1 - Session 本地缓存（进程内）                         │
│  └─ 当前用户可见的项目状态                              │
│     └─ TTL：1 分钟                                    │
│                                                          │
│  L2 - Redis 全局缓存（分布式）                           │
│  ├─ project:{id}:phases → 大阶段列表（TTL 5 分钟）     │
│  ├─ project:{id}:stages → 子阶段列表（TTL 5 分钟）      │
│  └─ project:{id}:stage:{stage_id} → 子阶段详情（TTL 2m）│
│                                                          │
│  L3 - 数据库（一致性源）                                 │
│  └─ 状态变更时主动失效缓存                              │
│                                                          │
│  ✅ 状态变更后的原子操作序列：                           │
│     1. 开启数据库事务                                   │
│     2. 更新阶段状态                                     │
│     3. 记录审计日志                                     │
│     4. 提交事务                                         │
│     5. DEL 相关 Redis Key                              │
│     6. PUBLISH 事件通知所有 Session 刷新              │
└──────────────────────────────────────────────────────────┘
```

**缓存一致性保证**：
- 写操作：先更新 DB，再删除缓存（不是更新缓存，而是删除）
- 读操作：先读缓存，miss 读 DB，然后写缓存
- 分布式锁：防止缓存击穿

---
#### 13.2.2 批量校验接口

**新增批量校验 API**，避免 N+1 查询问题：

```python
async def validate_batch(
    project_id: str,
    tasks: List[TaskValidationRequest],
    user_permissions: PermissionSnapshot
) -> List[ValidationResult]:
    """
    批量校验多个任务的阶段状态
    一次 DB 查询 vs N 次查询，性能提升 ~10x
    """
    # 1. 一次查询获取所有相关阶段状态
    stage_ids = [task.stage_id for task in tasks]
    stages = await db.query(
        "SELECT id, status, block_id FROM stages WHERE id = ANY(%s)",
        [stage_ids]
    )

    # 2. 构建阶段状态 Map
    stage_map = {s.id: s for s in stages}

    # 3. 批量校验每个任务
    results = []
    for task in tasks:
        stage = stage_map.get(task.stage_id)
        results.append(validate_single_task(task, stage, user_permissions))

    return results
```

---
### 13.3 数据模型增强

#### 13.3.1 阶段依赖：DAG（有向无环图）化

**问题**：`predecessor_ids` 简单数组不支持复杂依赖关系。

**新增表：stage_dependencies**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| project_id | FK | 关联项目 |
| from_stage_id | FK | 前置阶段 ID |
| to_stage_id | FK | 后续阶段 ID |
| dependency_type | Enum | 依赖类型 |
| lag_days | Integer | 滞后天数（可选） |
| is_mandatory | Boolean | 是否强制依赖 |
| created_at | DateTime | |

**支持的依赖类型**：
| 类型 | 代码 | 说明 |
|------|------|------|
| 完成-开始 | `FINISH_TO_START` | A 完成后 B 才能开始（默认） |
| 开始-开始 | `START_TO_START` | A 开始后 B 才能开始 |
| 完成-完成 | `FINISH_TO_FINISH` | A B 必须同时完成 |
| 开始-完成 | `START_TO_FINISH` | A 开始后 B 才能完成 |

**DAG 循环检测**：
```python
def detect_cycle(stages: List[Stage], dependencies: List[Dependency]) -> bool:
    """检测阶段依赖图是否存在循环"""
    # 使用拓扑排序
    # 如果排序结果节点数 < 总节点数，说明有环
```

---
#### 13.3.2 状态变更原因标准化

**新增枚举：StateChangeReason**

```python
class StateChangeReason(Enum):
    """状态变更原因标准化枚举"""
    NORMAL_PROGRESS = "normal_progress"      # 正常推进
    USER_REQUEST = "user_request"             # 用户申请启动
    ADMIN_OVERRIDE = "admin_override"         # 管理员强制变更
    ROLLBACK = "rollback"                     # 回退（误操作修正）
    EXCEPTION = "exception"                   # 异常情况处理
    BATCH_IMPORT = "batch_import"             # 批量数据导入
    SYSTEM_MAINTENANCE = "system_maintenance" # 系统维护
```

**audit_logs 表新增字段**：
- `reason_code`: Enum 枚举值
- `reason_detail`: String 详细说明

---
### 13.4 安全边界增强

#### 13.4.1 越权操作检测与阻断

```
用户 A 尝试启动不属于其权限范围的阶段
         ↓
WorkItemAgent 调用 validate_permission()
         ↓
三维度权限校验：
1. permission_level >= required_permission_level?
2. user_company == stage_owner_company?
3. department in allowed_departments?
         ↓
┌──────────────────────────────────────────────────────────┐
│  ✅ 通过 → 正常执行                                      │
│  ❌ 不通过 →                                          │
│     1. 记录 SECURITY_ALERT 审计日志                   │
│        (包含：用户ID / 操作类型 / 时间 / IP / UA)     │
│     2. 通知安全管理员（IM / 邮件）                     │
│     3. 返回友好提示："您暂无权限执行此操作，如有需要"  │
│        "请联系项目负责人申请权限"                      │
└──────────────────────────────────────────────────────────┘
```

---
#### 13.4.2 幂等性保护

**所有状态变更 API 必须携带 request_id**：

```
前端生成 UUID (request_id)
         ↓
请求携带 request_id 调用 API
         ↓
DB 唯一约束 (project_id + request_id)
         ↓
┌─────────────┐      ┌───────────────────────┐
│  首次请求?  │ Yes → │ 正常执行，记录 request_id │
└─────────────┘      └───────────────────────┘
         │ No
         ↓
返回上次执行结果（不重复执行）
```

**优势**：
- 前端失败重试时不会重复操作
- 网络抖动导致的重复请求安全
- 支持操作结果查询（通过 request_id）

---
### 13.5 异常流程增强

#### 13.5.1 "预启动"状态设计

**状态机新增 PRE_STARTING 状态**：

```
NOT_STARTED
     ↓
PRE_STARTING（用户申请，待审批）
     ↓
┌─────────────────────┐
│  审批中（有超时倒计时） │
└─────────────────────┘
     ↓
     ├─ ✅ 批准 → RUNNING
     ├─ ❌ 驳回 → REJECTED
     └─ ⏰ 超时 → ESCALATED（自动升级通知）
```

**状态流转说明**：
| 状态 | 可执行操作 | 下一状态 |
|------|------------|----------|
| NOT_STARTED | 申请启动 | PRE_STARTING |
| PRE_STARTING | 批准 / 驳回 / 超时 | RUNNING / REJECTED / ESCALATED |
| ESCALATED | 上级批准 / 驳回 | RUNNING / REJECTED |

**好处**：
- 用户可以看到"审批中"状态及进度
- 避免审批期间其他人重复申请
- 支持审批进度追踪和超时提醒

---
#### 13.5.2 审批超时自动升级机制

```
审批请求发出
     ↓
24 小时未处理
     ↓
自动升级通知 → 负责人的直接上级
     ↓
再 24 小时未处理
     ↓
再次升级 → 项目总负责人 / PMO
```

**升级通知配置表**：

| 超时时间 | 通知对象 | 通知方式 |
|----------|----------|----------|
| 0h | 阶段负责人 | IM + 邮件 |
| 24h | 直接上级 | IM + 邮件 + 待办 |
| 48h | 项目总负责人 | IM + 邮件 + 短信 |
| 72h | PMO 总监 | 邮件 + 电话 |

---
### 13.6 可观测性与度量指标

#### 13.6.1 关键度量指标（Prometheus 格式）

| 指标名 | 类型 | 说明 | 告警阈值 |
|--------|------|------|----------|
| `project_state_phase_startup_seconds` | Histogram | 阶段从申请到启动的耗时 | P95 > 48h → 告警 |
| `project_state_stage_rejection_rate` | Gauge | 阶段申请驳回率 | > 30% → 流程可能有问题 |
| `project_state_permission_cache_hit_rate` | Gauge | 权限缓存命中率 | < 90% → 调优缓存策略 |
| `project_state_validation_error_rate` | Gauge | 状态校验失败率 | > 20% → 用户培训或阶段设计 |
| `project_state_dependency_checks_total` | Counter | 依赖检查总次数 | - |
| `project_state_security_alerts_total` | Counter | 安全告警总次数 | > 0 立即通知 |

---
#### 13.6.2 监控大盘设计

**项目状态机运营看板**：
```
┌─────────────────────────────────────────────────────────────────┐
│                     项目状态机运营监控                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  【核心指标】                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │  缓存命中率│ │ 校验通过率│ │ 平均审批耗时│ │ 安全告警数 │       │
│  │   98.5%  │ │   85.2%  │ │   2.3 小时 │ │     0    │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                                 │
│  【阶段状态分布】                                                │
│  NOT_STARTED | ████████████ 42%                               │
│  PRE_STARTING | ████ 12%                                      │
│  RUNNING      | █████████ 28%                                 │
│  COMPLETED    | ████████ 18%                                  │
│                                                                 │
│  【审批超时预警】                                                │
│  ⚠️ S4 地块铺装施工 - 已等待 36 小时（即将升级）              │
│                                                                 │
│  【最近异常】                                                    │
│  [10:30] 检测到越权操作尝试 - 用户 user-089                  │
│  [09:15] 阶段依赖循环检测到潜在风险 - 景观工程                │
└─────────────────────────────────────────────────────────────────┘
```

---
### 13.7 工程实现优化

#### 13.7.1 状态机框架化（DSL）

**避免硬编码，使用装饰器定义状态机**：

```python
# 领域特定语言 (DSL)
@state_machine(project_type="landscape")
class LandscapeConstructionStateMachine:
    """景观工程施工状态机"""

    # 状态定义
    states = [
        State("earth_backfill", "土方回填"),
        State("pipe_laying", "管线铺设"),
        State("paving", "铺装施工"),
        State("planting", "绿化种植"),
    ]

    # 状态转换定义
    transitions = [
        Transition(
            from_="earth_backfill",
            to="pipe_laying",
            condition=check_completion_rate(95),  # 完成率 >= 95%
        ),
        Transition(
            from_="pipe_laying",
            to="paving",
            condition=check_inspection_passed(),  # 验收通过
        ),
    ]

    @permission_check  # 自动应用权限校验装饰器
    async def start_stage(self, stage_id: str, user: User) -> bool:
        """启动阶段"""
        pass
```

**优势**：
- 业务逻辑与状态流转分离
- 新增状态机时只需定义 states 和 transitions
- 统一的权限校验、审计日志、异常处理

---
#### 13.7.2 Feature Flag 渐进式发布

**按项目灰度发布状态机校验**：

```python
# Feature Flag 配置（可动态调整）
feature_flags = {
    "PROJECT_STATE_VALIDATION": {
        "enabled_projects": ["proj-001", "proj-002"],  # 试点项目
        "enabled_companies": ["company-001"],          # 试点公司
        "percentage": 10,                               # 流量百分比
    }
}

# 使用示例
if FeatureFlag.is_enabled("PROJECT_STATE_VALIDATION", project_id):
    # 启用状态机校验
    validation_result = await ProjectStateService.validate(...)
    if not validation_result.is_valid:
        # 走异常确认流程
else:
    # 旧逻辑，跳过校验（不影响现有项目）
    pass
```

**发布策略**：
1. **第 1 周**：只在测试项目启用（0% 生产流量）
2. **第 2 周**：10% 项目试点
3. **第 3 周**：50% 项目
4. **第 4 周**：全量发布（观察期 2 周）

---
### 13.8 实施优先级重排（v1.4 路线图）

| 优先级 | 模块 | 原计划 | 优化后 | 预计工时 | 理由 |
|--------|------|--------|--------|----------|------|
| **P0 - 必须** | 基础 CRUD + 简单校验 | Phase IV-1 | M1 第 1 周 | 5 天 | 核心功能基础 |
| **P0 - 必须** | 三级缓存 + 缓存失效机制 | 无 | M1 第 1 周 | 3 天 | 性能基准，避免上线后卡顿 |
| **P1 - 重要** | 三层字段分级 + 数据脱敏 | 无 | M1 第 2 周 | 4 天 | 安全性基准合规要求 |
| **P1 - 重要** | 状态校验核心逻辑 | Phase IV-3 | M2 第 3 周 | 5 天 | 主要业务价值 |
| **P2 - 应该** | DAG 依赖图 | 无 | M3 第 4 周 | 4 天 | 可迭代增强 |
| **P2 - 应该** | 权限代理 + 审批流程 | Phase IV-3 | M3 第 5-6 周 | 6 天 | 按实际需要上线 |
| **P3 - 可以** | 度量指标 + 监控看板 | Phase IV-4 | M4 后续迭代 | 5 天 | 不阻塞核心功能 |
| **P3 - 可以** | 预启动状态 + 超时升级 | 无 | M4 后续迭代 | 4 天 | 运营优化，可后做 |

---
### 13.9 必须解决的架构债务

#### 13.9.1 乐观锁冲突处理策略

**问题**：并发状态变更可能导致冲突。

**解决方案**：
```python
try:
    # 带版本号的更新
    result = await db.execute(
        "UPDATE stages SET status = $1, version = version + 1 "
        "WHERE id = $2 AND version = $3",
        [new_status, stage_id, current_version]
    )

    if result.rowcount == 0:
        # 冲突，自动重试最多 3 次
        for retry in range(3):
            await asyncio.sleep(0.1 * (2 ** retry))  # 指数退避
            # 重新读取最新版本并重试
            # ...

except Exception as e:
    # 重试 3 次仍失败，转人工处理
    log_conflict_warning(stage_id, user_id, e)
    raise StageUpdateConflictError("请稍后重试或联系管理员")
```

---
#### 13.9.2 分布式事务与最终一致性

**问题**：阶段启动 + 任务录入需要原子性。

**解决方案：Saga 模式**

```
T1: 启动阶段 (start_stage)
    ↓ 成功
T2: 创建任务 (create_task)
    ↓ 成功
✅ 完成

T1: 启动阶段
    ↓ 成功
T2: 创建任务
    ↓ 失败
T1-rollback: 回滚阶段状态 ← 执行补偿事务
```

**补偿机制**：
- 每个正向操作都有对应的补偿操作
- 补偿操作必须是幂等的
- 补偿失败触发人工介入告警

---
#### 13.9.3 存量项目数据迁移方案

**问题**：已有项目如何平滑接入新状态机？

**方案**：

```
步骤 1: 提供 import_from_excel() 批量导入工具
        - 项目管理员导出阶段定义模板
        - 按模板填写阶段依赖关系
        - 批量导入生成状态机

步骤 2: 向导式引导
        - 首次访问新项目时弹出配置向导
        - 逐步引导设置阶段、负责人、依赖关系

步骤 3: 向后兼容模式
        - 未配置状态机的项目
        - 默认跳过所有校验（不影响使用）
        - 后台任务提醒管理员尽快配置
```

---
### 13.10 总结与版本升级建议

```
v1.2  ────────────────────────────────────────┐
    │ 权限快照存放于 SessionContext           │
    │ WorkItemAgent 只读访问权限              │
    └──────────────────────────────────────────┘
         ↓
v1.3  ────────────────────────────────────────┐
    │ Project State Machine 两层状态机        │
    │ 公共/内部字段访问控制                   │
    │ 阶段校验与异常审批流程                  │
    └──────────────────────────────────────────┘
         ↓
v1.4 (本次优化建议)  ─────────────────────────┐
    │ 混合权限校验模式（快照 + 实时）         │
    │ 三层字段分级 + 数据脱敏                 │
    │ 三级缓存架构 + 批量校验接口             │
    │ DAG 阶段依赖图                          │
    │ 去单点权限代理 + 预启动状态             │
    │ 安全检测 + 幂等性保护                   │
    │ 可观测性度量指标                        │
    │ Feature Flag 灰度发布                   │
    └──────────────────────────────────────────┘
```

**架构师最终建议**：
> 当前 v1.3 的基础架构是合理的，但生产环境还需要 v1.4 的性能、安全、可观测性加固。
> 建议按 M1-M4 分阶段实施，每阶段结束后进行架构评审再进入下一阶段。
> 特别注意：**缓存一致性** 和 **分布式事务** 是两个必须在上线前验证清楚的核心技术点。
