# Agent 协同待办模块 — 需求规格说明书 v1.0

> 原始需求：[待办公告模块需求.md](待办公告模块需求.md) | 本文档为架构师完善版 | 最后更新：2026-06-26

---

## 1. 模块定位

### 1.1 一句话定义

**Agent 协同待办**（Agent Issue）是 Session-Agent 在执行任务过程中发现需要人工协同介入的事项时，自动创建并追踪的一条结构化待办记录。它使 Agent 的"口头转达"升级为"闭环追踪"。

### 1.2 与计划任务的边界

Emily 已有 `plan_task` 模块（模板/实例，7 态机）。两个模块的区分：

| 维度 | 计划任务 (plan_task) | Agent 协同待办 (agent_issue) |
|------|---------------------|------------------------------|
| **触发方** | 管理员/模板周期预定义 | Session-Agent 对话中动态判断 |
| **来源** | SOP-009/010：用户主动创建 | Agent 判定"需外部协同"时自动发布 |
| **周期** | 支持循环（日报/周报/月报） | 纯一次性 |
| **验收** | 结构化验收标准 JSON | 简单描述，轻量确认 |
| **发布门槛** | 任何有权限的用户 | 仅 Session-Agent 可发起 |
| **对标场景** | 预排的常规工作（"每周五提交周报"） | 突发协同请求（"请管理员审批权限""系统检测到异常需人工核查"） |

### 1.3 核心价值

```
用户对话 → Session-Agent 判定需外部协同
    ↓
自动创建 Agent Issue（数据库落地，可追踪）
    ↓
受理人下次对话时自动提示
    ↓
受理人确认/拒绝/解决 → 闭环
```

---

## 2. 状态机设计

### 2.1 状态枚举

```
OPEN → ACCEPTED → RESOLVED   （终态）
  ↘ DECLINED                 （终态）
  ↘ EXPIRED                  （终态，超时未处理）
```

| 状态 | 含义 | 谁触发 |
|------|------|--------|
| `OPEN` | 已发布，等待受理人处理 | Session-Agent 创建 |
| `ACCEPTED` | 受理人接受 | 受理人用户 |
| `RESOLVED` | 已解决（终态） | 受理人用户 |
| `DECLINED` | 受理人拒绝（终态，必须附拒绝理由） | 受理人用户 |
| `EXPIRED` | 超时自动过期（终态） | 后台调度器 |

### 2.2 转移矩阵

```python
TRANSITIONS = {
    OPEN:      [ACCEPTED, DECLINED, EXPIRED],
    ACCEPTED:  [RESOLVED],
    DECLINED:  [],   # 终态
    RESOLVED:  [],   # 终态
    EXPIRED:   [],   # 终态
}
TERMINAL_STATES = {RESOLVED, DECLINED, EXPIRED}
```

### 2.3 设计约束

- `DECLINED` 必须附带 `decline_reason`（拒绝理由，≥10 字）
- `RESOLVED` 必须附带 `resolution_note`（解决说明）
- 从 `DECLINED` 不可重新打开——如需重提，由 Agent 创建新的 Issue
- 从 `EXPIRED` 不可恢复——超时意味着原上下文可能已过时

---

## 3. 数据模型

### 3.1 主表 `agent_issues`

| 字段 | 类型 | 可空 | 默认值 | 约束 | 说明 |
|------|------|------|--------|------|------|
| `id` | String | PK | `_new_uuid()` | — | UUID 主键 |
| `issue_no` | String(50) | **否** | — | **UNIQUE** | AIS-YYYYMMDD-NNNN |
| `title` | String(500) | **否** | — | — | 事项一句话描述 |
| `description` | String | ✅ | `""` | — | 事项详情（Agent 自动生成） |
| `status` | String(20) | ✅ | `"OPEN"` | **索引** | OPEN/ACCEPTED/RESOLVED/DECLINED/EXPIRED |
| `priority` | String(20) | ✅ | `"NORMAL"` | — | LOW/NORMAL/HIGH/URGENT |
| `category` | String(50) | ✅ | `"GENERAL"` | **索引** | PERMISSION_REQUEST / ANOMALY_ESCALATION / REVIEW_REQUEST / GENERAL |
| `issuer_type` | String(20) | ✅ | `"AGENT"` | — | 发起人类型：AGENT（系统Agent）/ USER（用户手动） |
| `issuer_user_id` | String | ✅ | — | FK→users.id | Agent 代行的用户（对话发起者） |
| `assignee_id` | String | **否** | — | FK→users.id **索引** | 受理人 |
| `source_conversation_id` | String | ✅ | — | 逻辑 FK→conversations | 来源对话 |
| `source_workitem_id` | String | ✅ | — | 逻辑 FK→work_items | 来源 WorkItem |
| `source_message_id` | String | ✅ | — | 逻辑 FK→messages | 来源消息 |
| `decline_reason` | String(500) | ✅ | `""` | — | 拒绝理由（DECLINED 时必填） |
| `resolution_note` | String(1000) | ✅ | `""` | — | 解决说明（RESOLVED 时必填） |
| `related_file_ids` | String | ✅ | `"[]"` | JSON | 关联文件 FK→files.id |
| `deadline` | String | ✅ | — | — | 建议完成时间 |
| `notified_at` | String | ✅ | — | — | 首次通知时间 |
| `resolved_at` | String | ✅ | — | — | 解决时间 |
| `expires_at` | String | ✅ | — | **索引** | 过期时间（默认创建后 7 天） |
| `metadata_json` | Text | ✅ | `"{}"` | JSON | 扩展元数据（Agent 自由使用） |
| `created_at` | String | ✅ | `_utc_now()` | — | — |
| `updated_at` | String | ✅ | `_utc_now()` | onupdate | — |

**索引**：
- `idx_ais_assignee_status(assignee_id, status)` — 受理人待办快速查询
- `idx_ais_status(status)` — 状态过滤
- `idx_ais_category(category)` — 分类过滤
- `idx_ais_expires(expires_at) WHERE status = 'OPEN'` — 过期扫描

### 3.2 审计日志表 `agent_issue_logs`

| 字段 | 类型 | 可空 | 默认值 | 约束 | 说明 |
|------|------|------|--------|------|------|
| `id` | String | PK | `_new_uuid()` | — | — |
| `issue_id` | String | **否** | — | FK→agent_issues.id **索引** | — |
| `from_status` | String(20) | ✅ | — | — | — |
| `to_status` | String(20) | **否** | — | — | — |
| `operator_id` | String | ✅ | — | FK→users.id | 操作人 |
| `operator_type` | String(20) | ✅ | — | — | AGENT / USER |
| `reason` | String(1000) | ✅ | `""` | — | — |
| `snapshot` | Text | ✅ | `"{}"` | JSON | Issue 完整快照 |
| `created_at` | String | ✅ | `_utc_now()` | — | — |

**索引**：`idx_ail_issue_time(issue_id, created_at)`

---

## 4. 集成设计

### 4.1 Session-Agent 集成

Session-Agent 获得一项新的**固有技能**（不通过 ToolRegistry 暴露给 LLM）：

```
SessionAgent._issue_agent_task(category, title, description, assignee, ...)
    → AgentIssueService.create(issue_command)
    → 返回 issue_no
    → 回复中提及"已将此事标记为待办 AIS-20260626-0001，已通知 @张三"
```

**触发场景**：

| Category | 典型触发条件 | 示例 |
|----------|-------------|------|
| `PERMISSION_REQUEST` | 用户请求超出当前权限范围的操作 | "我需要查看景观验收资料但没有权限" |
| `ANOMALY_ESCALATION` | 全局状态机判定上传信息与当前项目阶段矛盾 | "上传电梯完工记录时，状态机判定当前未进入外墙工程阶段" |
| `REVIEW_REQUEST` | 需要人工审核的敏感操作 | "举报事件涉及安全质量，建议主管复核" |
| `GENERAL` | 其他需要外部协同的事项 | "请项目经理确认下周停工计划" |

### 4.2 受理人通知机制（分期实施）

#### Phase 1：上线触发（当前实施）

受理人下次向 Emily 发消息时，SessionPool 创建新 Session 后、处理消息前，自动检查是否有 `OPEN` 状态的 Issue 分配给该用户并发起会话提示：

```
SessionAgent.handle() 
  → _check_pending_issues()  // 新增
  → 如有未处理待办，回复前缀：
    "⚠ 您有 3 项待处理事项：
     1. [权限申请] AIS-20260626-0001 — 申请人：李工
     2. [异常升级] AIS-20260626-0002 — 19#楼外窗安装异常
     3. [复核请求] AIS-20260626-0003 — 安全质量事件复核
     回复'处理AIS-20260626-0001'开始处理。"
```

#### Phase 2：主动推送（后续迭代）

利用现有的 `OutboundEventBus` + SSE 通道，新增 `issue_notification` 事件类型。当受理人在线且有活跃 Session 时，实时推送通知消息。

### 4.3 与全局状态机联动

`ANOMALY_ESCALATION` 类型 Issue 创建时，携带全局状态机上下文：

```python
issue.metadata_json = {
    "sm_node_id": "5.3.2",      # 当前项目所处节点
    "sm_stage_id": 5,            # 当前阶段
    "conflicting_data": {...},   # 冲突数据详情
}
```

受理人可通过 `query_sm_status` 工具查看关联节点状态，辅助决策。

### 4.4 SOP 集成

新增 `SOP-012-SYS-agent_issue.md`（Agent 协同待办管理），包含子意图：

| 子意图 | 触发方式 | 说明 |
|--------|----------|------|
| `list_my_issues` | 用户说"查看我的待办" | 返回当前用户所有 OPEN 事项 |
| `describe_issue` | 用户说"处理AIS-xxx" | 展开指定 Issue 详情 |
| `accept_issue` | 用户说"接受AIS-xxx" | OPEN → ACCEPTED |
| `resolve_issue` | 用户说"完成AIS-xxx" | ACCEPTED → RESOLVED，必须附解决说明 |
| `decline_issue` | 用户说"拒绝AIS-xxx，因为…" | OPEN → DECLINED，必须附拒绝理由 |
| `query_issues_for` | 管理员说"查看张三的待办" | 按受理人/分类/状态过滤 |

---

## 5. API 设计

### 5.1 REST 端点

路径前缀：`/api/v1/agent-issues`

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/v1/agent-issues` | 待办列表（?assignee_id=&status=&category=） |
| `GET` | `/api/v1/agent-issues/{issue_id}` | 待办详情（含审计日志） |
| `POST` | `/api/v1/agent-issues` | 创建待办（仅 Agent 调用） |
| `PUT` | `/api/v1/agent-issues/{issue_id}/accept` | 接受待办 |
| `PUT` | `/api/v1/agent-issues/{issue_id}/resolve` | 解决待办（需 resolution_note） |
| `PUT` | `/api/v1/agent-issues/{issue_id}/decline` | 拒绝待办（需 decline_reason） |
| `GET` | `/api/v1/agent-issues/{issue_id}/logs` | 审计日志 |
| `GET` | `/api/v1/agent-issues/stats` | 统计数据（按状态/分类/受理人） |

### 5.2 创建请求示例

```json
POST /api/v1/agent-issues
{
  "title": "19#楼4层外窗安装完工记录异常",
  "description": "用户 张工 上传19#楼4层外窗安装完工工作量。状态机查询显示：当前项目阶段为'设计成果交付'(阶段2)，外墙施工尚未开始。该工作量申报与项目实际阶段矛盾。",
  "category": "ANOMALY_ESCALATION",
  "priority": "HIGH",
  "assignee_id": "uuid-of-project-manager",
  "source_conversation_id": "group_888",
  "source_workitem_id": "WI-abc123",
  "metadata_json": {
    "sm_node_id": "5.3.2",
    "sm_stage_id": 5,
    "conflicting_event_no": "EVT-20260626-0005"
  }
}
```

---

## 6. 权限与防滥用

### 6.1 创建权限

| 发起方 | 权限约束 |
|--------|----------|
| Session-Agent | 只能对当前 conversation 的参与者发 Issue（群聊=群成员，私聊=对话双方）。给非参与者的 Issue 需走 PERMISSION_REQUEST 流程。 |
| 管理员用户 | 可手动创建 ISSUE，通过 REST API 直接调用。 |

### 6.2 受理人拒绝保护

同一 issuer + assignee + category 组合在 24 小时内不可重复创建（防 Agent 循环制造待办）：

```sql
UNIQUE INDEX uq_ais_dedup(issuer_user_id, assignee_id, category, title)
    WHERE status = 'OPEN' AND created_at > NOW() - INTERVAL '24 hours'
```

### 6.3 反委派检测

复用 `plan_task` 模块的反委派逻辑：受理人的 `permission_level` 不得低于发起用户的 `permission_level`（同级或上级），否则记录为 `LOW_LEVEL_ASSIGN` 异常。

---

## 7. 过期清理

### 7.1 自动过期

后台调度器（`PlanTaskScheduler` 同进程或独立 tick）每天扫描：

```sql
UPDATE agent_issues SET status = 'EXPIRED'
WHERE status = 'OPEN' AND expires_at < NOW()
```

### 7.2 过期策略

| Priority | expires_at（创建后） |
|----------|---------------------|
| URGENT | 24 hours |
| HIGH | 3 days |
| NORMAL | 7 days |
| LOW | 30 days |

---

## 8. 实现分期

### Phase 1：核心闭环（本期）

| 产出 | 说明 |
|------|------|
| `agent_issues` + `agent_issue_logs` 2 张新表 | 数据库扩至 38 表 |
| `AgentIssue` 状态枚举 + 转移矩阵 | `issue_state.py` |
| `AgentIssueRepository` | sync 数据访问 |
| `AgentIssueService` | 创建 / 接受 / 解决 / 拒绝 / 过期 |
| `IssueNotificationService` | Phase 1：上线触发通知 |
| `GET/POST/PUT /api/v1/agent-issues/*` | 8 个 REST 端点 |
| SessionAgent 集成 | `_check_pending_issues()` + 提示前缀 |

### Phase 2：主动推送（后续）

| 产出 | 说明 |
|------|------|
| `OutboundEventBus` `issue_notification` 事件 | SSE 实时推送 |
| SessionPool 跨 session 路由 | 查找受理人活跃 session 并推送 |

### Phase 3：智能协同（远期）

| 产出 | 说明 |
|------|------|
| 自动分配受理人 | LLM 根据项目组织结构自动推断受理人 |
| Issue 依赖链 | AIS-A 解决后自动触发 AIS-B |
| 统计分析 | 按单位/部门的 Issue 解决率、平均响应时间 |

---

## 9. 与现有模块的关系总图

```
Session-Agent 判定需协同
    ↓
AgentIssueService.create()
    ├── 写入 agent_issues 表 + agent_issue_logs
    ├── 关联 source_conversation_id / source_workitem_id
    ├── [可选] 联动全局状态机（ANOMALY_ESCALATION）
    └── [可选] 关联文件（related_file_ids → files 表）
    ↓
回复中告知用户：已创建 AIS-xxx
    ↓
受理人下次对话
    ↓
SessionAgent._check_pending_issues()
    → 查询 agent_issues WHERE assignee_id=user.id AND status='OPEN'
    → 提示前缀
    ↓
受理人通过 SOP-012 子意图处理
    → accept / resolve / decline
    → AgentIssueService 写状态变更 + 审计日志
    ↓
闭环
```

---

## 10. 设计决策记录

| 编号 | 决策 | 理由 |
|------|------|------|
| D01 | 独立表 `agent_issues` 而非复用 `plan_task_instances` | plan_task 有模板/周期/验收标准等重字段，Issue 是轻量一次性的，复用会导致表语义混乱 |
| D02 | 5 态简化状态机，不设 RETURNED | 协同请求不需要"打回重做"——那是审核流的概念，不属于协同待办 |
| D03 | 通知分 Phase 1/2 | 主动推送涉及跨 Session 路由和安全边界，不宜与核心 CRUD 一期交付 |
| D04 | 不暴露为 LLM function-calling 工具 | 与 M14 原则一致——Agent Issue 是 Agent 固有技能，LLM 不直接调用创建 Issue |
| D05 | 过期而非自动关闭 | 7 天后未处理的 Issue 自动过期而非标记 RESOLVED——需要人工判断是否真已解决 |
