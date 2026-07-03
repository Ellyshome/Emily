# 权限管理系统 — 实施方案

> **交付说明**：本文件即实施计划文档本身。经批准后，将其内容保存为
> `需求文件/权限管理系统/权限管理系统-实施计划.md`（UTF-8，与同目录其他权限文档并列）。
>
> **依据**：
> - `需求文件/权限管理系统/权限管理系统需求-完整版.md` v1.0 —— 功能蓝图（6 级树形继承 / 4 级密级 / 3 种授权 / 越权暂存 / 审批工作流 / 脱敏 / 实时生效 / 批量管理 / 定期评审）
> - `需求文件/权限管理系统/Emily_权限系统设计.md` v1.5 —— 技术设计参考（三维鉴权 / 权限快照灌注 / 行级安全 / 缓存策略）
> - `需求文件/待办公告模块/Agent协同待办模块-架构师完善版.md` v1.0 —— 审批流集成目标（接口契约来源）
> - 现有代码探索结论 —— 骨架已建、血肉全无

---

## 一、背景（Context）

### 1.1 为什么做

需求-完整版 v1.0（2026-06-26）定义了一套完整的企业级权限管理体系：**RBAC + ABAC 混合模型**，覆盖 6 级权限分组（树形继承）、4 级信息密级、单位权限范围、3 种授权形式（自动/临时/永久）、权限分层编码、越权异常暂存审批、授权审计日志、审批工作流（集成协同待办）、数据脱敏、权限变更实时生效、批量管理、定期评审。这是 Emily 从"可用"走向"安全合规"的关键一步。

### 1.2 现状（代码探索结论）

**骨架已建**：

| 已有资产 | 位置 | 状态 |
|---------|------|------|
| `User.permission_level`(0-4) / `perm_list`(JSON) / `supervisor_id` / `is_admin` / `grouping` | `models.py:56-89` | 字段已到位，但 `grouping` 未改名、`company` 仍是 JSON 未改 FK |
| `permission_groups` / `sop_business_flows` / `sop_permission_bindings` 三表 | `models.py:664-781` | 字段完整，但用 `min_grouping`/`min_grouping_level`（非 `min_permission_level`），值域 0-4 |
| `PermissionSnapshot` dataclass | `session_context.py:22-60` | 有 grouping/company_type/sop_allow/db_perms/info_level/supervisor_id，缺 v1.5 字段，用 `grouping` 非 `permission_level` |
| `SessionContext.permissions` + 只读方法 | `session_context.py:63-126` | 链路通，但快照永远空 |
| `BusContext.get_permissions()` | `workitem/pipeline/context.py:100-126` | 方法正确，返回空快照 |
| `sm_nodes` / `sm_stages` 节点表 | `models.py:972-1082` | 权限编码引用节点 ID 的基础就绪 |

**血肉全无（核心缺口）**：

| 缺口 | 位置 | 影响 |
|------|------|------|
| **`SessionFactory._build_context()` 从不查 DB** | `session_factory.py:77-114` | 权限快照永远空 —— **整个灌注链路缺失，是最大缺口** |
| `WorkItemAgent.authorize()` 永远 ALLOW | `workitem_agent.py:438-463` | 无任何 DENY 路径，依赖 `allow_roles` 字符串 |
| `AuthHook` 默认放行 | `workitem/pipeline/hook.py:92-128` | 仅 `is_admin` 检查，未接入快照 |
| 6 级树形继承 | — | 现是 5 级线性 0⊂1⊂2⊂3⊂4，无继承链概念 |
| `permission_audit_log` / `permission_def` / `public_field_registry` / `pending_data` / `permission_grants` / `permission_requests` / `data_masking_rules` / `permission_review_tasks` | — | 8 张新表全部未建 |
| CompanyInfo 扩展字段 | `models.py:309-320` | 缺 type/scope/partners/parent_id/department/status |
| 权限编码系统 / 授权形式 / 审批工作流 / 越权暂存 / 脱敏 / 实时生效 / 批量管理 / 定期评审 | — | 全部未实现 |
| 协同待办模块（agent_issues） | — | 仅存需求文档，代码未实现 |

### 1.3 目标

分 4 阶段落地完整权限管理系统，**以需求-完整版 6 级树形为准**，复用设计文档 v1.5 的技术方案（三维鉴权/快照灌注/行级安全/缓存），补齐需求-完整版的额外要求（编码系统/授权形式/越权暂存/审批流/脱敏/实时生效/批量/评审）。

### 1.4 关键决策（已与用户确认）

1. **权限层级**：采用 6 级树形继承 L1-L6，扩展 `permission_level` 为 1-6，重写继承逻辑为树形。需迁移现有 0-4 数据 + 三张权限表字段改名（`min_grouping*` → `min_permission_level`）。
2. **协同待办**：本次不搭建 agent_issues 全栈；权限系统自带轻量 `permission_requests` 表承载审批流，按需求 §9.3.4 契约**预留** `/api/v1/agent-issues` 调用接口；协同待办模块后续独立实施时再切换对接。
3. **保存目录**：`需求文件/权限管理系统/权限管理系统-实施计划.md`。

---

## 二、架构概览

### 2.1 权限决策模型

**RBAC + ABAC 混合**（需求 §1.3）：

```
权限决策 = 主体属性(角色/单位/职能/职级) + 资源属性(密级/项目/节点/类型)
         + 环境属性(时间/网络/设备) + 操作类型(读/写/改/删/授权)
```

### 2.2 6 级权限树形继承模型

| level | 名称 | 继承自 | 业务线 |
|-------|------|--------|--------|
| L1(1) | 访客 | — | 基础（所有接入用户自动获得） |
| L2(2) | 参建执行 | L1 | 参建线 |
| L3(3) | 参建管理 | L2 | 参建线 |
| L4(4) | 建设主管 | L1 | 建设线 |
| L5(5) | 管理员 | L4 | 建设线 |
| L6(6) | 系统管理员 | L5 | 建设线 |

> ⚠ **关键**：L4 继承 L1（非 L3）—— 这是树形而非线性。建设线与参建线在 L1 后分叉。

**继承链（含自身）**：

```python
# emily_core/permission/level.py
class PermissionLevel(Enum):
    GUEST = 1            # L1 访客
    PARTICIPANT_EXEC = 2 # L2 参建执行
    PARTICIPANT_MGR = 3  # L3 参建管理
    OWNER_SUPERVISOR = 4 # L4 建设主管
    ADMIN = 5            # L5 管理员
    SYS_ADMIN = 6        # L6 系统管理员

INHERITANCE_CHAIN: dict[int, set[int]] = {
    1: {1},
    2: {2, 1},          # 参建执行 ⊃ 访客
    3: {3, 2, 1},       # 参建管理 ⊃ 参建执行 ⊃ 访客
    4: {4, 1},          # 建设主管 ⊃ 访客（非参建线）
    5: {5, 4, 1},       # 管理员 ⊃ 建设主管 ⊃ 访客
    6: {6, 5, 4, 1},    # 系统管理员 ⊃ 管理员 ⊃ 建设主管 ⊃ 访客
}

def effective_levels(user_level: int) -> set[int]:
    """用户经继承后实际持有的全部级别集合。"""
    return INHERITANCE_CHAIN.get(user_level, {1})

def can_access(user_level: int, required_level: int) -> bool:
    """树形继承鉴权：所需级别在用户继承链内即放行。"""
    return required_level in effective_levels(user_level)
```

**示例**：SOP 要求 `min_permission_level = L2`（参建执行）
- L3 参建管理：`{3,2,1}` 含 L2 ✅
- L4 建设主管：`{4,1}` 不含 L2 ❌（建设线不做参建线的事）
- L5 管理员：`{5,4,1}` 不含 L2 ❌（需通过单独授权或 SOP 绑定多权限组实现跨线）

> 跨线访问需求通过 `SOPPermissionBinding` 绑定多个权限组，或临时/永久授权形式解决，**不破坏树形继承语义**。

### 2.3 权限优先级（需求 §1.4）

```
拒绝(DENY) > 单独文件授权 > 临时授权(TEMP) > 管理员角色授权(PERMANENT) > 单位归属自动授权(AUTO)
```

鉴权引擎按优先级从高到低短路求值：先查 DENY，再查细粒度授权，最后查角色继承。

### 2.4 与现有系统的关系

| 层级 | 现状 | 本次实施 |
|------|------|----------|
| 权限快照灌注 | `SessionFactory._build_context()` 不查 DB | **新增** `_load_permission_snapshot()` 查 User+Company+PermissionGroup |
| SOP 鉴权 | `authorize()` 永远 ALLOW，依赖 `allow_roles` 字符串 | **重写** 为三维树形引擎（level×company_type×department×密级×节点） |
| 数据访问 | 无行级过滤 | **新增** SQLAlchemy `before_execute` 行级安全拦截器 |
| Hook 鉴权 | `AuthHook` 仅 `is_admin` | 接入 PermissionSnapshot + 三维引擎 |
| 审批流 | 无 | **新增** `permission_requests` 轻量审批 + 协同待办预留接口 |
| 审计 | 无权限审计 | **新增** `permission_audit_log`（仅 INSERT） |
| 通知 | `OutboundEventBus` 4 事件类型 | **新增** `permission_changed` 事件 + Session stale |

### 2.5 代码模式遵循

完全遵循项目现有约定（探索报告确认）：

| 模式 | 来源范本 | 说明 |
|------|---------|------|
| ORM | `models.py` | `Base(DeclarativeBase)` + `Column(String, primary_key=True, default=_new_uuid)` + 时间戳 `_utc_now` String + `is_deleted` 软删除 + `__table_args__` 索引元组 |
| Repository | `sm_node_repo.py:22-90` | 纯 `@staticmethod` + `session: Optional[Session]=None` + `_impl` 闭包（权限管理需跨操作事务，用完整版） |
| Service | `state_machine_service.py:71-172` | `async def` + `asyncio.to_thread()` + `_validate_transition` + 跨操作事务 `with get_session() as session:` 透传 |
| Commands | `plan_task_commands.py` | `@dataclass` 命令 DTO 独立文件 |
| Application | `plan_task_app.py:20-70` | 类名 `...Application` + `async def` + 返回 `{"success":bool,"reply":str,...}` + try/except 永不抛 |
| Route | `api/routes/state_machine.py` | `APIRouter(prefix="/api/v1/xxx")` + Pydantic `BaseModel` + 模块级 `_app`+`set_xxx_app()` setter + `async def` |
| EmilyCore | `__init__.py:86-425` | `_init_xxx_module()` try/except 包裹，失败置 None 不抛 |
| Config | `config.py` | dataclass + 默认值 + docstring + `from_dict` 白名单 |
| SOP | `SOP-011-SYS-state_machine.md` | `SOP-NNN-TYPE-name.md` + 顶部表格含"权限控制"行 + §2 子意图表 |

---

## 三、数据模型

### 3.1 改造现有表

#### 3.1.1 `users` 表（`models.py:56-89`）

| 字段 | 变更 | 说明 |
|------|------|------|
| `permission_level` | **值域改 1-6**（默认 1） | 原 0-4 → 1-6（L1-L6），迁移脚本按映射表填充 |
| `grouping` | **改名 `org_category`**，保留只读 | 旧组织标签，不再参与鉴权 |
| `company` | **JSON → FK** `ForeignKey("company_info.id")` | 单公司归属（暂不支持一人多司） |
| `perm_list` | 保持 JSON，关联 `permission_def` | 存用户授权编码列表 |
| `supervisor_id` | 不变 | 直接上级（异常审核人） |
| `position` | 不变 | 岗位角色 JSON |

**数据迁移映射**（`grouping` 旧值 → `permission_level` 新值 + `org_category`）：

| 旧 grouping | 旧含义 | → permission_level | → org_category |
|---|---|---|---|
| 0 | 临时组 | 1 (L1 访客) | 0 |
| 1 | 访客组 | 1 (L1 访客) | 1 |
| 2 | 工程组 | 2 (L2 参建执行) | 2 |
| 3 | 供货商 | 2 (L2 参建执行) | 3 |
| 4 | 管理组 | 5 (L5 管理员) ⚠ 需人工确认 | 4 |

> ⚠ 旧 `grouping=4` 迁移到 L5（非 L6），管理员通过 SOP-000 逐个确认是否升级 L6。`is_admin=True` 用户直接设 L6。

#### 3.1.2 `company_info` 表（`models.py:309-320`）

新增字段（扩展，不新建表）：

```python
type         = Column(String(50), default="")        # 建设单位/设计单位/总包/分包/监理/供应商
status       = Column(String(50), default="active")   # 投标中/履约中/已退场
scope        = Column(String, default="[]")           # 承包范围 JSON ["景观","1标段"]
partners     = Column(String, default="[]")           # 对接公司ID JSON [company_id]
parent_id    = Column(String, ForeignKey("company_info.id"), nullable=True)  # 上级公司
department   = Column(String, default="[]")           # 部门 JSON ["设计部","工程部"]
function_scope = Column(Text, default="{}")           # 职能-节点映射 JSON（需求 §4.1.1）
```

#### 3.1.3 `permission_groups` 表（`models.py:664-703`）

| 字段 | 变更 |
|------|------|
| `min_grouping_level` | **改名 `min_permission_level`**，值域 1-6 |

#### 3.1.4 `sop_business_flows` 表（`models.py:706-757`）

| 字段 | 变更 |
|------|------|
| `min_grouping` | **改名 `min_permission_level`**，值域 1-6 |
| 新增 `security_level` | `String(20)` 密级 PUBLIC/INTERNAL/PRIVATE/CONFIDENTIAL（需求 §3.1，关联权限编码密级段） |
| 新增 `required_node_ids` | `String` JSON，该 SOP 关联的全景节点 ID（用于节点范围鉴权） |

#### 3.1.5 `PermissionSnapshot` dataclass（`session_context.py:22-60`）

| 字段 | 变更 |
|------|------|
| `grouping` | **改名 `permission_level`**，默认 1，值域 1-6 |
| `info_level` | 保持，但值域明确 public/internal/private/confidential（4 级密级） |
| 新增 `permissions_loaded_at` | `str` 快照加载时间戳（v1.5） |
| 新增 `permission_version` | `int` 权限版本号（轻量变更检测） |
| 新增 `authorized_node_ids` | `list[str]` 用户可访问的全景节点 ID（单位权限范围推导） |
| 新增 `granted_codes` | `list[str]` 用户经临时/永久授权持有的权限编码（编译自 `permission_grants`） |
| 新增 `denied_codes` | `list[str]` 显式拒绝的权限编码（优先级最高） |

`meets_grouping_requirement` → 改名 `meets_level_requirement(required_level)`，调用 `can_access(self.permission_level, required_level)` 树形判断。

### 3.2 新增表（8 张）

所有表遵循 ORM 约定：`Base` 继承、`String` UUID 主键 `_new_uuid`、时间戳 `_utc_now` String、`is_deleted` 软删除。

#### 3.2.1 `permission_def` — 权限码定义表

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `perm_code` | String(256) UNIQUE | 权限编码 `DOC-PUBLIC-PRJ001-NODE001-FILE001` |
| `resource_type` | String(3) | DOC/DB/SOP/MSG/SYS |
| `security_level` | String(12) | PUBLIC/INTERNAL/PRIVATE/CONFIDENTIAL |
| `project_id` | String | 项目标识，`*` 表示全部 |
| `node_id` | String | 全景节点标识，`*` 表示全部 |
| `resource_id` | String | 具体资源标识，`*` 表示全部 |
| `description` | String(500) | 说明 |
| `created_at/updated_at/is_deleted` | 标准列 | |

#### 3.2.2 `permission_grants` — 授权记录表（需求 §5、§6.2）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `grant_no` | String(50) UNIQUE | `PGR-YYYYMMDD-NNNN` |
| `grantee_id` | String | FK→users.id 被授权人 |
| `grantor_id` | String | FK→users.id 授权人 |
| `perm_code` | String(256) | 权限编码（关联 permission_def） |
| `grant_type` | String(12) | AUTO/TEMP/PERMANENT |
| `operations` | String | JSON `["read","write"]` |
| `grant_time` | String | 授权时间 |
| `expire_time` | String NULL | 过期时间（TEMP 必填，PERMANENT/AUTO 为 NULL） |
| `status` | String(20) | ACTIVE/REVOKED/EXPIRED 索引 |
| `revoke_time` | String NULL | 撤销时间 |
| `revoke_reason` | String(500) | 撤销原因 |
| `remark` | String(500) | 授权原因（PERMANENT 必填） |
| `client_ip` | String(64) | 授权人 IP |
| `created_at/updated_at/is_deleted` | 标准列 | |

**索引**：`idx_pg_grantee_status(grantee_id, status)`、`idx_pg_expire(expire_time) WHERE status='ACTIVE'`

#### 3.2.3 `permission_requests` — 权限申请审批表（需求 §9，轻量审批流载体）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `request_no` | String(50) UNIQUE | `PRQ-YYYYMMDD-NNNN` |
| `requester_id` | String | FK→users.id 申请人 |
| `perm_code` | String(256) | 申请的权限编码 |
| `request_type` | String(20) | TEMP_GRANT/UNIT_BIND/LEVEL_UP/ANOMALY_DATA |
| `reason` | String(1000) | 申请理由 |
| `status` | String(20) | PENDING/APPROVED/REJECTED/EXPIRED/ESCALATED 索引 |
| `current_approver_id` | String | FK→users.id 当前审批人 |
| `approval_level` | Integer | 当前审批层级 1/2 |
| `priority` | String(20) | NORMAL/HIGH/URGENT |
| `expire_at` | String | 申请过期时间（URGENT=2h，NORMAL=24h） |
| `approved_at` | String NULL | 审批完成时间 |
| `approver_id` | String NULL | 最终审批人 |
| `approval_remark` | String(500) | 审批意见 |
| `source_data` | Text | JSON 额外上下文（如越权数据 pending_id） |
| `agent_issue_id` | String NULL | 协同待办对接 ID（预留，待 agent_issues 落地后回填） |
| `created_at/updated_at/is_deleted` | 标准列 | |

**索引**：`idx_prq_approver_status(current_approver_id, status)`、`idx_prq_expire(expire_at) WHERE status='PENDING'`

#### 3.2.4 `permission_audit_log` — 授权审计日志表（需求 §8.1）

> ⚠ **仅 INSERT，禁止 UPDATE/DELETE**（需求 §8.2）。通过 DB 触发器或应用层强制。

| 列 | 类型 | 说明 |
|----|------|------|
| `log_id` | BigInteger PK | BIGSERIAL（需求 §8.1） |
| `event_time` | String | DEFAULT `_utc_now` |
| `grantor_id` | String | 授权人/操作人 |
| `grantee_id` | String | 被授权人/被审计人 |
| `perm_code` | String(256) | 权限编码 |
| `grant_type` | String(32) | AUTO/TEMP/PERMANENT |
| `duration` | Integer NULL | 时长秒（PERMANENT 为 NULL） |
| `session_id` | String(128) NULL | 授权 Session |
| `operation_type` | String(32) | GRANT/REVOKE/EXPIRE/ACCESS_DENIED/ACCESS_CHECK |
| `client_ip` | String(64) | |
| `user_agent` | Text | |
| `remark` | String(512) | |

**索引**：`idx_pal_grantee_time(grantee_id, event_time)`、`idx_pal_op_time(operation_type, event_time)`

#### 3.2.5 `public_field_registry` — 公开字段白名单（设计文档 §3.4）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `project_id` | String NULL | 关联项目（NULL=全局公开） |
| `model_name` | String(100) | "Project"/"Event" |
| `field_name` | String(100) | "name"/"area" |
| `description` | String(500) | |
| `created_at/is_deleted` | 标准列 | |

#### 3.2.6 `pending_data` — 越权写入暂存表（需求 §7.2.2）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `pending_no` | String(50) UNIQUE | `PND-YYYYMMDD-NNNN` |
| `user_id` | String | FK→users.id 提交人 |
| `data_type` | String(50) | event/task/file |
| `data_content` | Text | 数据内容快照 JSON |
| `exception_reason` | String(1000) | 异常原因 |
| `target_node_id` | String | 目标全景节点 |
| `approver_id` | String | FK→users.id 待审批主管 |
| `status` | String(20) | PENDING/APPROVED/REJECTED/CLEANED 索引 |
| `expire_time` | String | 默认创建后 7 天 |
| `request_id` | String NULL | 关联 permission_requests.id（审批流） |
| `created_at/updated_at/is_deleted` | 标准列 | |

**索引**：`idx_pnd_approver_status(approver_id, status)`、`idx_pnd_expire(expire_time) WHERE status='PENDING'`

#### 3.2.7 `data_masking_rules` — 脱敏规则表（需求 §10.1）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `rule_code` | String(50) UNIQUE | PHONE/ID_CARD/AMOUNT/CONTACT |
| `field_pattern` | String(200) | 匹配字段名正则 |
| `mask_type` | String(50) | MIDDLE_4/MIDDLE_10/RANGE/NAME |
| `min_level_to_view` | Integer | 可见明文的最低 permission_level |
| `params` | Text | JSON 参数（如金额范围） |
| `created_at/updated_at/is_deleted` | 标准列 | |

#### 3.2.8 `permission_review_tasks` — 定期评审任务表（需求 §12.2）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | String PK | UUID |
| `review_no` | String(50) UNIQUE | `REV-YYYYQQ-NN` |
| `review_period` | String(20) | `2026Q2` |
| `scope_type` | String(20) | ALL/UNIT/LEVEL |
| `scope_value` | String | 范围值 |
| `assignee_id` | String | FK→users.id 评审负责人 |
| `status` | String(20) | PENDING/IN_PROGRESS/COMPLETED |
| `deadline` | String | |
| `result_summary` | Text | JSON 评审结果 |
| `created_at/updated_at/is_deleted` | 标准列 | |

### 3.3 权限编码系统（需求 §6）

**格式**：`[资源类型]-[密级]-[项目ID]-[节点ID]-[资源ID]`

```
DOC-PUBLIC-PRJ001-NODE001-FILE001     # 文档/公开/项目001/节点001/文件001
DB-INTERNAL-PRJ002-*-*                # 数据库/内部/项目002/全部节点/全部资源
SOP-CONFIDENTIAL-*-NODE005-FORM003    # SOP/机密/任意项目/节点005/表单003
```

**通配符**：`*` 匹配任意值；支持前缀匹配 `NODE001*`（节点 001 下所有子节点）。

**编码编译**：`PermissionCodeCompiler` 将编码字符串编译为内存结构（正则/前缀树），注入 `PermissionSnapshot.granted_codes` / `denied_codes`，鉴权时按通配符匹配。

---

## 四、分阶段实施计划

### 阶段一：数据模型 + 权限快照灌注（核心骨架）— 目标 5-7 天

#### 4.1 新增/修改文件清单

| # | 路径 | 操作 |
|---|------|------|
| 1 | `emily-core/emily_core/permission/__init__.py` | 新增：包导出 |
| 2 | `emily-core/emily_core/permission/level.py` | 新增：`PermissionLevel` 枚举 + `INHERITANCE_CHAIN` + `can_access()` |
| 3 | `emily-core/emily_core/permission/code_compiler.py` | 新增：`PermissionCodeCompiler` 编码解析/匹配 |
| 4 | `emily-core/emily_core/infrastructure/database/models.py` | **修改**：User/CompanyInfo/PermissionGroup/SOPBusinessFlow 改造 + 新增 8 张表 |
| 5 | `emily-core/emily_core/session/session_context.py` | **修改**：`PermissionSnapshot` 字段重构 + `meets_level_requirement` 树形 |
| 6 | `emily-core/emily_core/repositories/permission_repo.py` | 新增：权限快照加载 + def/company_info CRUD |
| 7 | `emily-core/emily_core/repositories/permission_grant_repo.py` | 新增：授权记录 CRUD |
| 8 | `emily-core/emily_core/services/permission_service.py` | 新增：`build_permission_snapshot()` 快照组装 |
| 9 | `emily-core/emily_core/adapters/session/session_factory.py` | **修改**：`_build_context()` 调用 `_load_permission_snapshot()` |
| 10 | `scripts/migrate_permission_level.py` | 新增：grouping→permission_level 1-6 迁移脚本 |
| 11 | `emily-core/emily_core/config.py` | **修改**：新增 `permission_*` 配置项 |

#### 4.2 权限快照灌注（核心改造）

**`SessionFactory._build_context()` 改造**（`session_factory.py:77-114`）：

在现有填充 SOP/工具摘要后，新增权限快照加载：

```python
def _build_context(self, message, user_id) -> SessionContext:
    ctx = SessionContext(...)  # 现有逻辑不变
    core = self._core
    if core is None:
        return ctx
    # ... 现有 SOP/工具摘要填充 ...

    # ★ 新增：权限快照灌注
    perm_service = getattr(core, "_permission_service", None)
    if perm_service is not None and user_id:
        try:
            snapshot = await asyncio.to_thread(  # 若 _build_context 保持 sync 则用同步调用
                perm_service.build_permission_snapshot, user_id
            )
            ctx.permissions = snapshot
        except Exception as e:
            logger.warning("load permission snapshot failed user=%s: %s", user_id, e)
            # fail-open：降级为 L1 访客快照 + 告警（设计文档 §6.4）
            ctx.permissions = PermissionSnapshot(permission_level=1)
    return ctx
```

> ⚠ `_build_context` 当前是 sync 方法。由于 `PermissionService` 是 async（`asyncio.to_thread` 包裹 sync repo），需将 `_build_context` 改为 async，或在 sync 内直接调用 repo（repo 本身是 sync `@staticmethod`）。**推荐**：保持 `_build_context` sync，直接调用 sync repo（`PermissionRepo.load_snapshot(user_id)`），避免 async 感染 `SessionFactory.create`。

**`PermissionService.build_permission_snapshot(user_id)` 核心逻辑**：

```python
def build_permission_snapshot(self, user_id: str) -> PermissionSnapshot:
    user = self._user_repo.get_by_id(user_id)
    if user is None:
        return PermissionSnapshot(permission_level=1)  # 降级访客

    company = self._company_repo.get_by_id(user.company) if user.company else None
    # 查询用户经授权形式持有的权限码
    grants = self._grant_repo.get_active_grants(user_id)  # AUTO/TEMP/PERMANENT
    denied = self._grant_repo.get_denied_codes(user_id)
    # 查询用户可访问的 SOP 白名单（基于 permission_level + 权限组绑定）
    sop_allow = self._cache.get_user_sop_allow(user, company)
    # 查询用户单位权限范围关联的全景节点
    authorized_nodes = self._derive_authorized_nodes(user, company)

    return PermissionSnapshot(
        permission_level=user.permission_level,
        company_id=user.company or "",
        company_type=company.type if company else "",
        company_name=company.company_name if company else "",
        department=self._primary_department(company),
        project_ids=self._derive_project_ids(user, company),
        partner_ids=json.loads(company.partners) if company else [],
        scopes=json.loads(company.scope) if company else [],
        sop_allow=sop_allow,
        db_perms=self._derive_db_perms(user, company),
        info_level=self._derive_info_level(user.permission_level),
        supervisor_id=user.supervisor_id or "",
        authorized_node_ids=authorized_nodes,
        granted_codes=[g.perm_code for g in grants if g.grant_type != "AUTO"],
        denied_codes=denied,
        permissions_loaded_at=_utc_now(),
        permission_version=self._cache.get_version(),
    )
```

#### 4.3 6 级树形继承落地

- `permission/level.py` 定义 `PermissionLevel` + `INHERITANCE_CHAIN` + `can_access()`（见 §2.2）
- `PermissionSnapshot.meets_level_requirement(required)` 调用 `can_access(self.permission_level, required)`
- 数据迁移脚本 `migrate_permission_level.py` 按 §3.1.1 映射表迁移

#### 4.4 Config 新增配置

```python
# config.py
# ---- 权限管理 (Permission) ----
permission_enabled: bool = True
"""权限管理模块总开关"""
permission_cache_ttl_seconds: int = 300
"""权限矩阵缓存 TTL（秒）"""
permission_super_admin_level: int = 6
"""系统管理员 permission_level"""
permission_session_max_ttl_hours: int = 24
"""Session 权限快照最大存活时间"""
permission_fail_open: bool = True
"""权限查询失败时降级为访客（True）或拒绝（False）"""
```

#### 4.5 验收用例

1. ✅ `permission/level.py` — `can_access(5, 2)` 返回 False（L5 不含 L2）；`can_access(3, 2)` 返回 True
2. ✅ `migrate_permission_level.py` — 旧 `grouping=4` 用户迁移后 `permission_level=5`、`org_category=4`
3. ✅ `SessionFactory._build_context()` — 创建 Session 后 `ctx.permissions.permission_level` 等于 User 表值（非 0）
4. ✅ `ctx.permissions.sop_allow` 非空（基于权限组绑定计算）
5. ✅ `ctx.permissions.granted_codes` 含临时授权的权限码
6. ✅ 权限查询 DB 失败时降级 L1 访客 + 日志告警（fail-open）
7. ✅ `permission_audit_log` 表禁止 UPDATE/DELETE（DB 触发器验证）

---

### 阶段二：三维鉴权引擎 + 权限校验接口 — 目标 4-5 天

#### 4.6 新增/修改文件清单

| # | 路径 | 操作 |
|---|------|------|
| 1 | `emily-core/emily_core/permission/auth_engine.py` | 新增：`PermissionAuthEngine` 三维树形鉴权 |
| 2 | `emily-core/emily_core/permission/row_security.py` | 新增：SQLAlchemy `before_execute` 行级安全拦截器 |
| 3 | `emily-core/emily_core/permission/cache.py` | 新增：`PermissionCache` 两级缓存（矩阵 L1 + 用户白名单 L2） |
| 4 | `emily-core/emily_core/workitem/workitem_agent.py` | **修改**：`authorize()` 重写为三维引擎（签名改 `context: BusContext`） |
| 5 | `emily-core/emily_core/workitem/pipeline/hook.py` | **修改**：`AuthHook` 接入 PermissionSnapshot |
| 6 | `emily-core/emily_core/services/permission_service.py` | **修改**：新增 `check()` / `grant()` / `revoke()` / `query_user_permissions()` |
| 7 | `emily-core/emily_core/application/permission_app.py` | 新增：编排层 |
| 8 | `emily-core/api/routes/permission.py` | 新增：FastAPI 路由（check/grant/revoke/query/request/approve） |
| 9 | `emily-core/emily_core/__init__.py` | **修改**：新增 `_init_permission_module()` |

#### 4.7 三维鉴权引擎重写

**`WorkItemAgent.authorize()` 重写**（`workitem_agent.py:438-463`）：

签名从 `(self, user_id, route_decision)` 改为 `(self, context: BusContext, route_decision)`，调用 `PermissionAuthEngine`：

```python
async def authorize(self, context: BusContext, route_decision) -> AuthResult:
    auth_mode = self._resolve_mode("auth")
    if auth_mode != "real":
        return AuthResult(decision=AuthDecision.ALLOW, _source="mock_auth")

    perms = context.get_permissions()
    if perms is None:
        return AuthResult(decision=AuthDecision.DENY, reason="无权限快照", _source="real_auth")

    sop_id = getattr(route_decision, "sop_id", None)
    if not sop_id:
        return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")  # 纯聊天无需鉴权

    engine = self._permission_engine  # 由 EmilyCore 注入
    result = await engine.check_sop_access(perms, sop_id, context)
    return result  # AuthResult(decision, reason, matched_details, _source)
```

**`PermissionAuthEngine.check_sop_access()` 流程**（按优先级短路）：

```
1. 查 DENY：sop_id 相关编码 in perms.denied_codes → DENY（优先级最高）
2. 查单独授权：sop_id 相关编码 in perms.granted_codes → ALLOW
3. 查 SOPBusinessFlow 表（min_permission_level / security_level / required_node_ids）
   3.1 is_public=True → ALLOW
   3.2 树形继承：can_access(perms.permission_level, sop_flow.min_permission_level)
       不满足 → DENY "权限层级不足"
   3.3 密级校验：sop_flow.security_level 可见性 ⊆ perms.info_level
       不满足 → DENY "密级不足"
   3.4 企业类型匹配：require_company_match 且 perms.company_type 不在 allowed → DENY
   3.5 部门匹配：require_department_match 且 perms.department 不在 allowed → DENY
   3.6 节点范围：required_node_ids 与 perms.authorized_node_ids 有交集（或含 *）
4. 全部通过 → ALLOW + matched_details
5. DENY 时写 permission_audit_log(operation_type=ACCESS_DENIED)
```

#### 4.8 行级安全拦截器

**`permission/row_security.py`**（设计文档 §5.3）：

```python
@event.listens_for(Session, "before_execute")
def inject_company_filter(conn, clause, multiparams, params, context):
    if getattr(_injection_ctx, 'skip', False):
        return clause, multiparams, params
    perms = get_current_permission_snapshot()  # Thread-local
    if perms is None:
        return clause, multiparams, params
    allowed_ids = [perms.company_id] + perms.partner_ids
    return _inject_where_clause(clause, allowed_ids), multiparams, params
```

**实现要点**（设计文档 §5.3.1）：
- Thread-local 标记位 `_skip_auth_injection` 防重复注入
- JOIN 查询识别 leftmost 主表注入过滤
- UNION/子查询递归遍历 SELECT AST
- 无法安全注入的复杂查询：fail-open + WARNING 日志
- 可过滤表清单：`events/tasks/files/messages` 等（含 company_id 归属列的表）

#### 4.9 权限矩阵缓存

**`PermissionCache`**（设计文档 §六-B）：

| 层级 | 内容 | TTL |
|------|------|-----|
| L1 矩阵 | PermissionGroup × SOPBusinessFlow × SOPPermissionBinding 全量结果集 | 5 分钟（`permission_cache_ttl_seconds`） |
| L2 用户白名单 | 单用户 sop_allow（基于 L1 + 用户属性计算） | Session 生命周期 |

失效触发：管理员修改权限组/SOP 绑定 → `cache.invalidate()`；TTL 到期自动重载；加载失败降级直查 DB。

#### 4.10 API 端点（需求 §14）

`api/routes/permission.py`，前缀 `/api/v1/permission`：

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/check` | 权限校验（需求 §14.1）→ `{allowed, reason, suggestedApprover}` |
| POST | `/grant` | 授权（AUTO/TEMP/PERMANENT） |
| POST | `/revoke` | 撤销授权 |
| GET | `/user/{userId}` | 查询用户权限列表 |
| POST | `/request` | 申请权限（创建 permission_request） |
| POST | `/approve` | 审批权限申请 |

#### 4.11 验收用例

1. ✅ L4 建设主管访问 `min_permission_level=L2` 的 SOP → DENY（树形继承，L4 不含 L2）
2. ✅ L3 参建管理访问 `min_permission_level=L2` 的 SOP → ALLOW
3. ✅ `denied_codes` 含某 SOP 编码 → 任何级别都 DENY（优先级最高）
4. ✅ 临时授权 `granted_codes` 含某 SOP → L1 访客也能访问
5. ✅ 密级 CONFIDENTIAL 的 SOP，L4 用户 `info_level=internal` → DENY
6. ✅ 行级安全：参建单位用户查询 events → SQL 自动注入 `company_id IN (自身, partners)`
7. ✅ `POST /api/v1/permission/check` 返回 `{allowed:false, reason:"权限层级不足", suggestedApprover:"user_xx"}`
8. ✅ DENY 时 `permission_audit_log` 写入 ACCESS_DENIED 记录
9. ✅ 权限矩阵缓存命中率 > 80%（重复 Session 创建）

---

### 阶段三：授权形式 + 审批工作流 — 目标 4-5 天

#### 4.12 新增/修改文件清单

| # | 路径 | 操作 |
|---|------|------|
| 1 | `emily-core/emily_core/services/permission_grant_service.py` | 新增：3 种授权形式 + 撤销机制 |
| 2 | `emily-core/emily_core/services/permission_approval_service.py` | 新增：审批工作流（申请/审批/超时升级/转审） |
| 3 | `emily-core/emily_core/services/permission_anomaly_service.py` | 新增：越权异常处理（pending_data 暂存 + SOP 审批） |
| 4 | `emily-core/emily_core/repositories/permission_request_repo.py` | 新增：申请审批 CRUD |
| 5 | `emily-core/emily_core/repositories/pending_data_repo.py` | 新增：越权暂存 CRUD |
| 6 | `emily-core/emily_core/adapters/agent_issue_client.py` | 新增：协同待办预留接口（HTTP client，按需求 §9.3.4 契约） |
| 7 | `emily-core/api/routes/permission.py` | **修改**：新增 `/request` `/approve` `/callback/issue-result` 端点 |
| 8 | `emily-core/emily_core/workitem/pipeline/hook.py` | **修改**：写入越权检测 Hook |
| 9 | `emily-core/emily_core/services/permission_scheduler.py` | 新增：临时授权过期 + 审批超时升级后台 tick |

#### 4.13 三种授权形式（需求 §5）

**`PermissionGrantService`**：

| 形式 | grant_type | 触发 | 有效期 | 执行权限 |
|------|-----------|------|--------|---------|
| 单位归属自动授权 | AUTO | 用户绑定单位 | 长期（单位归属变更止） | 系统自动 |
| 临时授权 | TEMP | 主动授权/被动申请 | 10 分钟默认，24h 上限 | 授权人须有该资源权限 |
| 永久授权 | PERMANENT | 管理员执行 | 永久（手动撤销） | 仅 L5+，必须记录原因 |

**撤销机制**（需求 §5.2）：
- 主动撤销：授权人 `POST /revoke`
- 自动撤销：临时授权到期 → 后台 tick 扫描 `expire_time < now AND status=ACTIVE` → 置 EXPIRED + 审计
- 强制撤销：L5+ 管理员撤销任何授权
- 级联撤销：用户 permission_level 降级 → 超出新级别的 TEMP/PERMANENT 授权自动撤销

#### 4.14 审批工作流（需求 §9）

**`PermissionApprovalService`** 状态机：

```
PENDING → APPROVED / REJECTED / EXPIRED
   ↓ (超时)
ESCALATED → APPROVED / REJECTED
```

**超时升级规则**（需求 §9.2）：
- 一级审批：24h 未处理 → 升级至审批人上级（`current_approver_id.supervisor_id`），`approval_level=2`
- 二级审批：48h 未处理 → 升级至 L5 管理员
- 紧急申请（priority=URGENT）：超时缩短至 2h

**协同待办预留接口**（需求 §9.3.4，`agent_issue_client.py`）：

```python
class AgentIssueClient:
    """协同待办模块预留接口 —— 当前返回 Mock issue_id，待 agent_issues 模块落地后切换为真实 HTTP 调用。"""

    async def create_permission_issue(self, request: PermissionRequest) -> str | None:
        """创建权限审批待办，返回 agent_issue_id（预留）。"""
        if not self._enabled:  # config: permission_agent_issue_integration_enabled=False
            return None  # 未启用时 permission_requests 自身承载审批流
        # TODO: 待 agent_issues 模块落地，改为真实 HTTP POST /api/v1/agent-issues
        # category=PERMISSION_REQUEST / ANOMALY_ESCALATION / PERMISSION_REVIEW
        return await self._http.post("/api/v1/agent-issues", {...})

    async def query_my_issues(self, user_id, status="OPEN") -> list: ...
    async def handle_issue_action(self, issue_id, action, remark) -> dict: ...
```

**回调端点**（需求 §9.3.5）：`POST /api/v1/permission/callback/issue-result` —— 协同待办处理完成后回调，更新 `permission_requests.status`。

**待办分类编码对齐**（⚠ 需求 §9.3.3 与协同待办架构文档的差异）：

| 需求 §9.3.3 编码 | 协同待办架构文档 category | 本次采用 |
|---|---|---|
| `PERMISSION_REQUEST` | `PERMISSION_REQUEST` | ✅ 一致 |
| `ANOMALY_ESCALATION` | `ANOMALY_ESCALATION` | ✅ 一致 |
| `PERMISSION_REVIEW` | `REVIEW_REQUEST` | ⚠ 采用 `PERMISSION_REVIEW`（需求为准），agent_issues 落地时在分类枚举中补充此值 |

#### 4.15 越权异常处理（需求 §7）

**写入越权流程**（`PermissionAnomalyService`，需求 §7.2.1）：

```
用户写入请求 → 权限校验
  ├ 通过 → 数据暂存(正常) → SOP 审批 → 主管确认 → 正式入库
  └ 拒绝 → 数据暂存(pending_data, 异常) → 记录异常原因
            → 创建 permission_request(type=ANOMALY_DATA, approver=建设主管)
            → 主管审批
              ├ 同意 → 数据正式入库 + 可补全用户权限 + 审计
              └ 拒绝 → 删除暂存数据 + 通知用户 + 审计
```

**pending_data 管理**（需求 §7.2.2）：
- 独立临时表 `pending_data`，默认 7 天保留
- 后台 tick 扫描 `expire_time < now AND status=PENDING` → 标记 CLEANED

#### 4.16 验收用例

1. ✅ 临时授权 10 分钟后 `permission_grants.status` 自动变 EXPIRED + 审计日志
2. ✅ L3 参建管理仅能在本单位内调整 L2↔L3（越权调整 L4 → DENY + 审计）
3. ✅ 用户写入越权数据 → `pending_data` 写入暂存 + `permission_requests` 创建 ANOMALY_DATA 申请
4. ✅ 主管审批同意 → pending_data 数据正式入库（标记 APPROVED）+ 通知用户
5. ✅ 主管审批拒绝 → pending_data 删除 + 通知用户
6. ✅ 审批 24h 未处理 → `approval_level` 升级至 2 + `current_approver_id` 改为上级
7. ✅ URGENT 申请 2h 超时升级
8. ✅ 用户 permission_level 降级 → 超出新级别的 TEMP/PERMANENT 授权级联撤销
9. ✅ `AgentIssueClient` 在未启用时返回 None，permission_requests 自身承载审批
10. ✅ 永久授权未填 `remark` → 拒绝执行

---

### 阶段四：脱敏 + 实时生效 + 批量管理 + 定期评审 — 目标 3-4 天

#### 4.17 新增/修改文件清单

| # | 路径 | 操作 |
|---|------|------|
| 1 | `emily-core/emily_core/permission/masking.py` | 新增：`DataMasker` 脱敏中间件 |
| 2 | `emily-core/emily_core/repositories/masking_rule_repo.py` | 新增：脱敏规则 CRUD |
| 3 | `emily-core/emily_core/repositories/review_task_repo.py` | 新增：评审任务 CRUD |
| 4 | `emily-core/emily_core/services/permission_notify_service.py` | 新增：权限变更实时生效（OutboundEventBus + Session stale） |
| 5 | `emily-core/emily_core/services/permission_batch_service.py` | 新增：批量授权/回收/模板 |
| 6 | `emily-core/emily_core/services/permission_review_service.py` | 新增：定期评审生成 + 提醒 |
| 7 | `emily-core/emily_core/services/permission_scheduler.py` | **修改**：新增评审任务 tick + 临时数据清理 tick |
| 8 | `emily-core/emily_core/outbound_bus.py` | **修改**：事件类型新增 `permission_changed` |
| 9 | `data/plugins/emily_agent/adapters/sse_listener.py` | **修改**：`_handlers` 新增 `permission_changed` |
| 10 | `emily-data/sops/SOP-012-SYS-permission.md` | 新增：权限管理 SOP |
| 11 | `emily-data/sops/SOP-000-SYS-standard.md` | **修改**：权限控制值域补充 `L1-L6` 级别 |

#### 4.18 数据脱敏（需求 §10）

**`DataMasker`** 中间件：在 Application 层返回数据前，按 `data_masking_rules` 对字段脱敏。

| 数据类型 | 规则 | 示例 |
|---------|------|------|
| 手机号 | MIDDLE_4 | `138****1234` |
| 身份证号 | MIDDLE_10 | `110101********1234` |
| 金额数据 | RANGE（低权限显示范围） | `100万-500万` |
| 联系人信息 | NAME | `张*明` |

**脱敏逻辑**：`mask_value(field, value, user_level)` —— 查规则 `min_level_to_view`，用户 level < 要求则脱敏，否则明文。

#### 4.19 权限变更实时生效（需求 §11）

**变更触发场景**：单位归属变更 / 权限分组变更 / 资源密级调整 / 单位权限范围调整

**生效机制**：
1. **实时推送**：`PermissionNotifyService.notify_change(user_id)` → `outbound_bus.publish("permission_changed", {user_id, version})` → SSE → 在线用户客户端
2. **Session stale**：标记该用户活跃 Session 的 `permission_version` 过期，下次 WorkItem 处理前自动重载快照
3. **离线用户**：下次请求时 `permission_version` 不匹配 → 自动刷新快照
4. **强制登出**：高危降级时 `outbound_bus.publish("session_closed", {user_id, reason})`

> ⚠ 需扩展 `SSEListener._handlers`（插件端 `data/plugins/emily_agent/adapters/sse_listener.py:42-47`）新增 `permission_changed` 事件处理。

#### 4.20 批量管理（需求 §12.1）

**`PermissionBatchService`**：
- 批量授权：按部门/项目/节点维度批量 `permission_grants` INSERT
- 批量回收：按维度批量 REVOKE
- 权限模板：预设模板一键应用（模板存 `permission_batch_templates` 或复用 `permission_groups`）
- 组织架构调整级联：单位 `parent_id` 变更时级联调整下属用户权限

#### 4.21 定期评审（需求 §12.2）

**`PermissionReviewService`** + 后台 tick（每季度初触发）：
- 自动生成 `permission_review_tasks`（scope=ALL，assignee=L5 管理员）
- 通过 `AgentIssueClient` 创建 `PERMISSION_REVIEW` 待办通知管理员
- 评审内容：用户权限合理性 + 过期 TEMP 授权清理
- 评审结果写入 `result_summary` JSON

#### 4.22 SOP-012-SYS-permission.md

新增权限管理 SOP（参考 `SOP-011-SYS-state_machine.md` 格式）：

| 子意图 | 触发 | 说明 |
|--------|------|------|
| `query_my_permission` | "查看我的权限" | 返回当前用户权限清单 |
| `query_user_permission` | "查看张三的权限"（L5+） | 按用户查询 |
| `grant_permission` | "给李工授权XX"（L5+） | 临时/永久授权 |
| `revoke_permission` | "撤销李工的XX权限" | 撤销授权 |
| `request_permission` | "我需要XX权限" | 创建申请 |
| `approve_request` | "同意PRQ-xxx"（审批人） | 审批申请 |
| `review_permission` | "权限评审"（L5+） | 定期评审 |
| `check_access` | "我能访问XX吗" | 权限校验 |

权限控制行：`admin`（L5+），部分子意图（query_my/request/check）放宽至 `all`。

#### 4.23 验收用例

1. ✅ L2 用户查看含手机号字段 → 返回 `138****1234`；L5 用户查看同一字段 → 明文
2. ✅ 管理员变更用户 permission_level → `outbound_bus` 发布 `permission_changed` → SSE 推送
3. ✅ 在线用户 Session 的 `permission_version` 不匹配 → 下次请求自动重载快照
4. ✅ 批量授权按部门 → 该部门所有用户 `permission_grants` 新增记录 + 缓存失效
5. ✅ 季度初后台 tick 自动生成 `permission_review_tasks`
6. ✅ 用户说"查看我的权限" → 命中 SOP-012 `query_my_permission` → 返回权限清单
7. ✅ 7 天前的 `pending_data` PENDING 记录被后台 tick 标记 CLEANED
8. ✅ 过期 TEMP 授权被评审任务列出待清理

---

## 五、集成点

| 集成点 | 阶段 | 方式 |
|--------|------|------|
| **EmilyCore 初始化** | 阶段一 | `_init_permission_module()`，延迟加载，try/except 失败置 None 不阻塞 Core |
| **Config** | 阶段一 | 新增 `permission_enabled`/`permission_cache_ttl_seconds`/`permission_fail_open` 等 |
| **SessionFactory** | 阶段一 | `_build_context()` 调用 `PermissionService.build_permission_snapshot()` |
| **WorkItemAgent** | 阶段二 | `authorize()` 重写，注入 `PermissionAuthEngine` |
| **AuthHook** | 阶段二 | 接入 PermissionSnapshot，`system.execute` 检查 `permission_level >= 5` |
| **SQLAlchemy Session** | 阶段二 | `before_execute` 行级安全拦截器（Thread-local 快照） |
| **OutboundEventBus** | 阶段四 | 新增 `permission_changed` 事件类型 |
| **SSEListener** | 阶段四 | 插件端 `_handlers` 新增 `permission_changed` |
| **PlanTaskScheduler** | 阶段三/四 | `PermissionScheduler` 复用 advisory lock 模式，或独立 tick |
| **协同待办（预留）** | 阶段三 | `AgentIssueClient` HTTP client，当前 Mock，待 agent_issues 落地切换 |
| **BusinessFlowTool** | 阶段四 | 暴露 `check_permission` 给 Agent（可选） |
| **sm_nodes** | 阶段一 | `authorized_node_ids` 引用 `sm_nodes.node_id` |

---

## 六、非功能需求（需求 §13）

| 需求 | 实现方式 |
|------|----------|
| 单用户权限校验 < 10ms | PermissionSnapshot 内存快照 + 矩阵缓存（L1 5min）|
| 并发用户 > 1000 | 快照 Session 内缓存，避免每次查 DB；拦截器 Thread-local |
| 权限列表查询 < 50ms | `idx_pg_grantee_status` 索引 + L2 用户白名单缓存 |
| 可用性 ≥ 99.9% | 子系统初始化失败不阻塞 Core；fail-open 降级 |
| 服务端校验 | 所有鉴权在 Service/拦截器层执行，前端仅体验优化 |
| CSRF/重放防护 | 权限令牌 + session_id 绑定（后续迭代） |
| 敏感操作记录 IP/设备 | `permission_audit_log.client_ip` + `user_agent` |
| 可扩展性 | `permission_level` 枚举可扩展；`permission_def` 支持新资源类型；`data_masking_rules` 可动态新增 |

---

## 七、验收标准（全阶段）

1. ✅ 6 级树形继承：`can_access` 严格按 `INHERITANCE_CHAIN` 判断，L4 不含 L2/L3
2. ✅ SessionFactory 创建 Session 后权限快照非空，`permission_level` 等于 User 表值
3. ✅ `authorize()` 有 DENY 路径，三维校验（level×company_type×department×密级×节点）
4. ✅ 权限优先级：DENY > 单独授权 > TEMP > PERMANENT > AUTO 短路求值
5. ✅ 3 种授权形式 + 4 种撤销机制（主动/自动/强制/级联）全部生效
6. ✅ 越权写入 → `pending_data` 暂存 → 审批流 → 入库/删除闭环
7. ✅ 审批超时升级（24h/48h/2h URGENT）
8. ✅ `permission_audit_log` 仅 INSERT，所有权限变更留痕
9. ✅ 数据脱敏按用户 level 动态生效
10. ✅ 权限变更实时推送 + Session stale 自动重载
11. ✅ 批量授权/回收 + 季度定期评审
12. ✅ 协同待办预留接口（Mock 可用，真实对接待 agent_issues 落地）
13. ✅ 性能：单用户校验 < 10ms，列表查询 < 50ms
14. ✅ 子系统初始化失败不阻塞 Core 启动
15. ✅ SOP-012-SYS-permission.md 8 个子意图可用

---

## 八、文件结构总览（全阶段完成后）

```
emily-core/emily_core/
  permission/
    __init__.py                # 包导出
    level.py                   # PermissionLevel 枚举 + INHERITANCE_CHAIN + can_access
    code_compiler.py           # PermissionCodeCompiler 编码解析/匹配
    auth_engine.py             # PermissionAuthEngine 三维树形鉴权
    row_security.py            # SQLAlchemy before_execute 行级安全拦截器
    cache.py                   # PermissionCache 两级缓存
    masking.py                 # DataMasker 脱敏中间件
  services/
    permission_service.py              # 快照组装 + check/query
    permission_grant_service.py        # 3 种授权 + 撤销
    permission_approval_service.py     # 审批工作流
    permission_anomaly_service.py      # 越权异常处理
    permission_notify_service.py       # 变更实时生效
    permission_batch_service.py        # 批量管理
    permission_review_service.py       # 定期评审
    permission_scheduler.py            # 后台 tick（过期/超时/评审/清理）
  repositories/
    permission_repo.py                 # 快照加载 + def/company CRUD
    permission_grant_repo.py           # 授权记录 CRUD
    permission_request_repo.py         # 申请审批 CRUD
    pending_data_repo.py               # 越权暂存 CRUD
    masking_rule_repo.py               # 脱敏规则 CRUD
    review_task_repo.py                # 评审任务 CRUD
  application/
    permission_app.py                  # Application 编排
  adapters/
    session/session_factory.py         # ★ 修改：_build_context 灌注快照
    agent_issue_client.py              # 协同待办预留 HTTP client
  session/
    session_context.py                 # ★ 修改：PermissionSnapshot 重构
  workitem/
    workitem_agent.py                  # ★ 修改：authorize() 重写
    pipeline/hook.py                   # ★ 修改：AuthHook + 越权检测 Hook
  infrastructure/database/
    models.py                          # ★ 修改：4 表改造 + 8 张新表
  outbound_bus.py                      # ★ 修改：新增 permission_changed 事件
  config.py                            # ★ 修改：permission_* 配置项
  __init__.py                          # ★ 修改：_init_permission_module()

emily-core/api/routes/
  permission.py                        # FastAPI 路由（check/grant/revoke/query/request/approve/callback）

scripts/
  migrate_permission_level.py          # grouping→permission_level 1-6 迁移

emily-data/sops/
  SOP-012-SYS-permission.md            # 权限管理 SOP（8 子意图）

data/plugins/emily_agent/adapters/
  sse_listener.py                      # ★ 修改：_handlers 新增 permission_changed
```

**表数量变化**：36 张 → 44 张（新增 8 张：permission_def / permission_grants / permission_requests / permission_audit_log / public_field_registry / pending_data / data_masking_rules / permission_review_tasks）

---

## 九、风险与注意事项

1. **树形继承 vs 业务预期**：L5 管理员按树形不含 L2/L3，若业务要求"管理员能做所有事"，需通过 `SOPPermissionBinding` 绑定多权限组或 `is_admin` 超级标记绕过级别检查。**建议**：保留树形严格语义，跨线需求用授权形式解决，避免破坏继承模型。

2. **`_build_context` 同步/异步**：当前是 sync 方法，直接调用 sync repo（`PermissionRepo.load_snapshot`）避免 async 感染。若快照加载耗时，可考虑预加载 + 异步刷新。

3. **行级安全拦截器复杂度**：JOIN/UNION/子查询的 SQL AST 遍历易出错。**建议**：先覆盖单表 SELECT（80% 场景），复杂查询 fail-open + WARNING，逐步完善。可过滤表清单用白名单显式声明，避免误注入无 company_id 的表。

4. **`permission_audit_log` 不可篡改**：仅 INSERT 需在 DB 层强制（PostgreSQL 触发器禁止 UPDATE/DELETE），应用层约定不足以保证。迁移脚本需创建触发器。

5. **协同待办对接不确定性**：`AgentIssueClient` 当前 Mock，待 agent_issues 模块落地后需对齐分类编码（`PERMISSION_REVIEW` vs `REVIEW_REQUEST`）和回调契约。`permission_requests.agent_issue_id` 字段预留回填。

6. **数据迁移不可逆**：`grouping` → `permission_level` 迁移涉及全量用户。**建议**：迁移前备份 `users` 表；迁移脚本支持 `--dry-run`；旧 `grouping` 值保留到 `org_category` 不丢失。

7. **权限快照过期窗口**：Session 24h TTL 内权限变更通过 `permission_version` 检测，但离线用户下次请求才刷新。紧急撤销可通过 `permissions_stale` 标记强制下次请求重载（设计文档 §6.2）。

8. **`__pycache__` 不刷新**：每次代码变更后需 `docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`（项目既有踩坑）。

9. **文档同步**：代码改动后同步更新 `docs/代码文件目录.md`、`docs/数据库设计.md`（36→44 表）、`docs/业务模块与运转全景.md`、`docs/接口协议与调用约定.md`、`docs/技术踩坑备忘录.md`。

---

## 十、实施优先级与里程碑

| 阶段 | 内容 | 工期 | 里程碑 |
|------|------|------|--------|
| 阶段一 | 数据模型 + 快照灌注 | 5-7 天 | Session 权限快照非空，6 级树形落地 |
| 阶段二 | 三维鉴权 + 校验接口 | 4-5 天 | `authorize()` 有 DENY 路径，行级安全生效 |
| 阶段三 | 授权形式 + 审批工作流 | 4-5 天 | 3 种授权 + 越权暂存审批闭环 |
| 阶段四 | 脱敏 + 实时生效 + 批量 + 评审 | 3-4 天 | 全功能上线，SOP-012 可用 |
| **合计** | | **16-21 天** | |

> 每阶段结束后进行架构评审再进入下一阶段（参考设计文档 v1.4 建议）。阶段一、二为 P0 必须，阶段三为 P1 重要，阶段四为 P2 应该。
