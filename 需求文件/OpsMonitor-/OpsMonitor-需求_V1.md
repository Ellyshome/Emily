# OpsMonitor — 运维巡检模块需求规格

> **版本**：V1
> **状态**：待评审
> **最后更新**：2026-07-01
> **来源**：整合 ProjectAgent 设计文档群 + 三轮架构讨论 + 代码现状勘查后编写

---

## 1. 定位

### 1.1 一句话

OpsMonitor 是 **PlanTaskScheduler 的扩展模块**，在现有调度循环中增加对项目全景节点图（`project_nodes`）的主动扫描能力，产出运维告警 + 项目摘要 Digest。

### 1.2 不是什么

- **不是独立 Agent** — 不新建进程、不新建 Tick 循环、不常驻 LLM 推理
- **不是独立部署单元** — 随 Emily Core 启动，共享同一进程和 Advisory Lock
- **不是用户 IM 通道** — 不直接与用户对话，告警通过 OutboundEventBus 推送，查询通过 SessionAgent 代理

### 1.3 命名由来

"Monitor" 而非 "Agent"：Phase 1 全部为确定性规则引擎（纯 SQL + 固定模板），不做自主决策。复杂情况按需拉起 LLM 做一次性判断，LLM 调用结束即释放，不常驻。

---

## 2. 背景与动机

### 2.1 当前缺口

PlanTaskScheduler 的 `_tick()` 循环目前只扫描 `plan_tasks` 表（超时检测、临近提醒、循环补齐、归档、升级），**不扫描项目全景节点图**。导致：

- `project_nodes` 中处于 IN_PROGRESS / BLOCKED / DELAYED 状态的节点卡滞超过 30 天无人知晓
- 里程碑节点临近 deadline 无人预警
- 依赖链断裂、数据完整性等结构性问题不被主动发现
- SessionAgent 对项目的认知依赖一份固定的 `domain_knowledge.md`，无法随项目推进而更新

### 2.2 为什么不是独立 Agent

PlanTaskScheduler 已经有一个成熟的调度循环：PG Advisory Lock 分布式锁、60s Tick 间隔、7 步顺序执行、异常隔离、OutboundEventBus 对接。另起一个 ProjectAgent 的 Tick 循环会导致：

- 两个循环抢同一把锁（或需两把不同的锁，增加复杂度）
- 相同的锁管理代码写两遍
- 运维功能和计划任务功能在同一时刻扫描同一组表时可能产生不一致读

**正确做法**：在 PlanTaskScheduler 的 `_tick()` 中追加 3 个步骤，复用已有基础设施。

---

## 3. 架构

### 3.1 在 EmilyCore 中的位置

```
EmilyCore
  ├── PlanTaskScheduler (时间驱动调度循环, 60s tick)
  │     ├── _tick() 现有 7 步 (plan_tasks 维度的操作)
  │     └── _tick() 新增 3 步 (project_nodes 维度的操作)  ← OpsMonitor
  ├── OpsMonitor (一次性启动动作)
  │     └── startup_report() — 冷启动环境感知 + 项目状态简报 + 邮件通知
  ├── OutboundEventBus → SSE → IM (告警推送)
  └── SessionAgent (消费 Digest)
```

### 3.2 数据流

```
PlanTaskScheduler._tick()
  │
  ├── [现有 1-7] plan_tasks 维度的处理
  │
  ├── [新增 8] 卡滞检测
  │     project_nodes WHERE status IN (IN_PROGRESS, BLOCKED, DELAYED)
  │     AND updated_at < now - threshold_days
  │     → OutboundEventBus 推送告警 (含冷却)
  │
  ├── [新增 9] 里程碑预警
  │     project_nodes WHERE deadline < now + warn_days
  │     AND status NOT IN (COMPLETED, CONDITIONS_NOT_MET)
  │     → OutboundEventBus 推送预警 (含冷却)
  │
  ├── [新增 10] Digest 刷新
  │     统计 project_nodes 分布 + 近期 events 摘要
  │     → 写入 project_digest 表 / 更新 prompt 缓存
  │     → SessionAgent 下次加载 prompt 时注入
  │
  └── [新增 11] 按需 LLM 分析
        触发条件: 5+ 节点同时卡滞 / 健康指数骤降 / 依赖链断裂
        → 临时拉起 LLM 做一次性分析
        → 生成分析报告 → 推送给管理员
        → LLM 调用结束即释放，不常驻
```

### 3.3 与现有模块的关系

| 模块 | 关系 |
|------|------|
| `PlanTaskScheduler` | **宿主** — OpsMonitor 的 Tick 步骤在此执行 |
| `EmilyCore._ensure_initialized()` | **宿主** — OpsMonitor 的冷启动报告在此触发 |
| `ProjectNodeRepo` | **依赖** — 读取节点数据；需新增 `find_stale()` / `find_milestones_near_deadline()` / `count_all()` |
| `OutboundEventBus` | **依赖** — 推送告警/预警/分析报告 |
| `EmailService` | **依赖** — 冷启动报告邮件发送（复用已有的 SMTP 通道） |
| `SessionAgent` | **下游消费者** — 读取 Digest 注入 system prompt |

---

## 4. 核心功能

### 4.0 冷启动报告

**定位**：OpsMonitor 初始化完成后（即在 `EmilyCore._ensure_initialized()` 末尾、`_initialized = True` 之前）触发一次，感知当前系统环境和项目状态，生成简报保存并发送给管理员。

**触发**：仅一次 — OpsMonitor 初始化时。不是 Tick 循环的一部分。

**信息采集**：

| 类别 | 信息项 | 采集方式 |
|------|--------|----------|
| **环境** | 启动时间、容器 hostname、Python 版本 | `platform` / `socket` / `os` 标准库 |
| **环境** | DB 连接状态 | 执行 `SELECT 1` 探测 |
| **项目** | 是否有活跃项目（project_nodes 表非空） | `ProjectNodeRepo.count_all()` |
| **项目** | 项目名称（如有 projects 表匹配） | `ProjectNodeRepo` 查 project_id → 关联 projects 表 |
| **项目** | 当前有正在执行中的节点 | `ProjectNodeRepo.count_by_status("IN_PROGRESS")` |
| **状态概述** | 节点总数、各状态分布、阻塞/延期数量 | `ProjectNodeRepo` 按 status 计数汇总 |

**报告格式**（Markdown 邮件正文）：

```
Emily 冷启动报告
═══════════════

启动时间：2026-07-01 14:30:00 UTC+8
实例 ID：emily-core-a1b2c3d4
Python：3.12.x
数据库：可达 ✅

项目：锦绣花园
  节点总数：42
  进行中：5
  阻塞：3
  已完成：28
  当前无执行中节点（所有进行中节点均处于非 ACTIVE 状态）

当前状态概述：
  项目整体进度 66.7%，有 3 个阻塞节点需要关注。
  最近一条事件记录于 2026-06-30。
```

**输出**：
1. **写入 `ops_startup_report` 表** — 持久化审计记录
2. **通过 `EmailService.send()` 发送邮件给管理员** — 邮件凭证从全局 Config 读取（`ops_monitor_admin_email`、邮件 SMTP 凭证复用已有的 `EMILY_EMAIL_*` 环境变量）
3. **邮件发送失败不阻塞启动** — 仅 logger.warning，Core 继续正常运行

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_startup_report_enabled` | bool | true | 冷启动报告开关 |
| `ops_monitor_admin_email` | str | "" | 管理员邮箱地址（为空时不发邮件，仅写 DB） |

**执行位置**：`EmilyCore._ensure_initialized()` 中，在 `_build_session_pool()` 之前（所有 Repo/Service 已就绪）：

```python
# emily_core/__init__.py — _ensure_initialized() 尾部
self._init_ops_monitor()     # 创建 OpsMonitor
await self._ops_monitor.startup_report()  # 冷启动报告（仅一次）
self._build_pipeline_bus()
self._build_session_pool()
self._initialized = True
```

> 注：冷启动报告的依赖是 `ProjectNodeRepo` + `EmailService`，两者在 `_ensure_initialized()` 中都比此执行点更早初始化，不存在循环依赖。

### 4.1 卡滞检测

**触发**：每次 Tick（60s），但告警有冷却

**查询逻辑**：
```sql
SELECT * FROM project_nodes
WHERE status IN ('IN_PROGRESS', 'BLOCKED', 'DELAYED')
  AND updated_at < :cutoff_iso
  AND is_discarded = FALSE
ORDER BY updated_at ASC
```

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_enabled` | bool | true | 总开关 |
| `ops_monitor_stale_threshold_days` | int | 14 | 卡滞判定天数 |
| `ops_monitor_alert_cooldown_hours` | int | 24 | 同节点同问题冷却 |

**告警冷却**：内存字典 `{(node_id, issue_type) → last_alerted_at}`。进程重启后冷却重置——存量卡滞节点会在重启后首次 Tick 重新推送一次。这是已知局限，可接受（运维人员重启容器时期望看到系统状态汇总）。

**告警模板**：
```
【节点卡滞预警】
节点「{node_name}」（{node_id}）处于「{status}」状态已 {days} 天未更新。
负责人：{owner_dept_id}
阶段：Stage {stage_id}
最后更新：{updated_at}
请确认进度或更新状态。
```

### 4.2 里程碑预警

**触发**：每次 Tick，有冷却

**查询逻辑**：
```sql
SELECT * FROM project_nodes
WHERE deadline < :warn_cutoff_iso
  AND deadline > :now_iso
  AND status NOT IN ('COMPLETED', 'CONDITIONS_NOT_MET')
  AND is_discarded = FALSE
ORDER BY deadline ASC
```

> 注：`project_nodes.deadline` 是 VARCHAR 列（存储日期字符串格式如 `2026-07-15`）。查询时需转换为可比格式。若字符串格式不统一，需在 Repo 层做预处理。

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_deadline_warn_days` | int | 7 | 预警提前天数 |

**预警模板**：
```
【里程碑预警】
节点「{node_name}」（{node_id}）的 deadline 为 {deadline}，距今 {days} 天。
当前状态：{status}
负责人：{owner_dept_id}
请关注进度。
```

### 4.3 项目摘要 Digest

**目的**：给 SessionAgent 提供动态、随项目推进而自动刷新的项目认知，替代固定的 `domain_knowledge.md`。

**触发**：每 N 次 Tick 刷新一次（默认 N=10，即每 10 分钟），避免无变化时的无效计算。

**内容**：
```
当前项目状态（自动生成于 {generated_at}）：
- 节点总数 {total}，已完成 {completed}（{pct}%），进行中 {in_progress}
- 阻塞 {blocked} 个，延期 {delayed} 个
- 各阶段概况：Stage 1: {s1_done}/{s1_total} ...
- 本周到期里程碑：{list}
- 最近 7 天新增事件 {event_count} 条
```

**存储方式**：写入 `project_digest` 表（单行 upsert）。SessionAgent 在加载 system prompt 时通过 `prompt_loader` 注入 `{PROJECT_DIGEST}` 模板变量。

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_digest_refresh_ticks` | int | 10 | 每 N 个 Tick 刷新一次 Digest |

### 4.4 按需 LLM 分析

**定位**：大多数时候 OpsMonitor 是沉默的规则引擎。当出现需要判断的复杂情况时，临时拉起 LLM 做一次推理，结果通过 OutboundEventBus 推送后 LLM 立即释放。

**触发条件（可配置）**：

| 条件 | 说明 |
|------|------|
| 单次 Tick 发现 ≥5 个节点同时卡滞 | 可能存在系统性阻塞，需要综合分析 |
| 项目完成率周环比下降 >20% | 进度异常 |
| 检测到循环依赖 | 仅 SQL 能发现、但 LLM 能解释影响范围 |
| 管理员通过 SessionAgent 主动询问 | "分析一下项目目前的瓶颈" |

**LLM 调用方式**：复用 `PlanTaskScheduler._llm.chat_json()` 的既有模式。prompt 中包含当前卡滞节点列表 + 里程碑列表 + 近期事件摘要，要求 LLM 输出结构化分析：

```json
{
  "severity": "INFO|WARNING|CRITICAL",
  "summary": "一句话摘要",
  "bottlenecks": ["瓶颈1", "瓶颈2"],
  "suggestions": ["建议1", "建议2"],
  "requires_escalation": false
}
```

**成本控制**：
- 每次触发仅一次 LLM 调用，结果缓存至下一个 Tick
- 同一触发条件不重复调用（冷却 1 小时）
- 预估：正常项目每天 0~2 次 LLM 调用；异常日 ≤24 次

---

## 5. 与 SessionAgent 的集成

### 5.1 Digest 注入路径

```
PlanTaskScheduler._tick()
  └── _refresh_digest()
        └── ProjectNodeRepo 查询 + 统计
        └── upsert project_digest 表

SessionAgent._recognize_intent() / _handle_shortcut()
  └── 读取 project_digest 表最新一条
  └── 将 digest_text 注入 system prompt 的 {PROJECT_DIGEST} 占位符
  └── 调用 LLM
```

> **注**：`_load_session_prompt()` 在 import 时执行一次（模块级变量），不会随着 Digest 刷新而自动更新。因此 Digest 不能在 prompt 文件加载时注入，必须在 **每次构建 system prompt 时**（即 `_recognize_intent()` 调用前的 prompt 组装阶段）实时从 `project_digest` 表读取并注入。这是实现层面的关键约束，不同于 `{current_datetime}` 或 `{sop_catalog}` 等模板变量。

### 5.2 SessionAgent 中 usage

用户在 IM 中问 SessionAgent "项目现在什么状态？" → SessionAgent 的 system prompt 已包含 Digest → LLM 能基于实时数据回答，而非过期领域知识。

### 5.3 与 domain_knowledge.md 的关系

`domain_knowledge.md` 提供**静态领域骨架**（地产工程术语、组织架构等不变知识），保留。Digest 提供**动态项目快照**（节点进度、里程碑、近期事件等变化数据），作为新增模板变量注入。两者互补，不是替代。

### 5.4 Digest 注入时机

**关键约束**：`_load_session_prompt()` 是 import 时执行的模块级变量（如下），在整个进程生命周期中只加载一次，不会随着 Digest 刷新而自动更新。

```python
# session_agent.py (当前实现)
_SESSION_SYSTEM_PROMPT = _load_session_prompt()  # ← import 时执行，仅一次
```

因此 `{PROJECT_DIGEST}` **不能在 prompt 文件加载时注入**，必须在 **每次构建 system prompt 时注入**。注入点位于 `_recognize_intent()` 或 prompt 组装方法中：

```python
# 改造后（示意）
def _build_system_prompt(self) -> str:
    base = _SESSION_SYSTEM_PROMPT  # 文件加载的模板（含 {PROJECT_DIGEST} 占位符）
    digest_text = self._read_latest_digest() or ""  # 实时读 project_digest 表
    return base.replace("{PROJECT_DIGEST}", digest_text)
```

`{current_datetime}` 和 `{sop_catalog}` 等模板变量继续按现有方式注入（每次调用时 replace），不参与文件缓存刷新。

---

## 6. 数据库需求

### 6.1 新增表

| 表名 | 用途 | 关键列 | 说明 |
|------|------|--------|------|
| `ops_startup_report` | 冷启动报告持久化——每次系统启动写入一条 | startup_time, instance_id, project_id, project_name, nodes_total, nodes_in_progress, nodes_blocked, status_summary, db_reachable | 审计追溯：知道每次重启时系统处于什么状态 |
| `ops_alert_log` | **纯审计表**——记录所有已发送的告警/预警，不做冷却依赖 | node_id, alert_type, alerted_at, project_id | 冷却使用内存字典（Phase 1），不查此表做冷却判断。此表仅用于审计追溯 |
| `project_digest` | 项目摘要缓存（单行 upsert，按 project_id 唯一） | project_id (PK), digest_text, generated_at | project_id 预留多项目扩展；当前默认值 `"default"` |

> **冷却与审计的职责区分**：
> - **冷却**：内存字典 `{(node_id, issue_type) → last_alerted_at}`。进程重启后冷却重置，存量卡滞节点首次 Tick 重新推送。
> - **审计**：`ops_alert_log` 表持久化记录每条已发送的告警，不做冷却查询。两张表职责完全分离。
>
> 进程重启后若需要从审计记录恢复冷却状态（避免重复告警），可从 `ops_alert_log` 表查询最近 `alerted_at`，但这是可选优化，非 Phase 1 必须。

### 6.2 ProjectNodeRepo 新增方法

当前 `node_repo.py` 缺少 OpsMonitor 需要的查询方法，需新增：

| 方法 | SQL 逻辑 | 返回值 |
|------|----------|--------|
| `find_stale(statuses, older_than_iso)` | `WHERE status IN (...) AND updated_at < :cutoff AND is_discarded=false` | `list[ProjectNode]` |
| `find_milestones_near_deadline(now_iso, warn_before_days)` | `WHERE deadline < :warn_cutoff AND deadline > :now AND status NOT IN (...)` | `list[ProjectNode]` |
| `count_all(project_id)` | `SELECT COUNT(*) WHERE project_id=:pid AND is_discarded=false` | `int` |
| `count_by_status(project_id, status)` | `SELECT COUNT(*) WHERE status=:s AND is_discarded=false` | `int` |

> 注：`deadline` 列为 VARCHAR，日期比较需要字符串格式统一（建议在 Repo 方法内做格式转换，或在查询前预处理）。此约束需写入实施计划。

---

## 7. 配置项汇总

所有配置通过 `emily_core/config.py` → env 映射注入，不创建独立配置文件。

| 配置键 | 类型 | 默认值 | 环境变量 |
|--------|------|--------|----------|
| `ops_monitor_enabled` | bool | true | `EMILY_OPS_MONITOR_ENABLED` |
| `ops_monitor_stale_threshold_days` | int | 14 | `EMILY_OPS_MONITOR_STALE_THRESHOLD_DAYS` |
| `ops_monitor_deadline_warn_days` | int | 7 | `EMILY_OPS_MONITOR_DEADLINE_WARN_DAYS` |
| `ops_monitor_alert_cooldown_hours` | int | 24 | `EMILY_OPS_MONITOR_ALERT_COOLDOWN_HOURS` |
| `ops_monitor_digest_refresh_ticks` | int | 10 | `EMILY_OPS_MONITOR_DIGEST_REFRESH_TICKS` |
| `ops_monitor_llm_analysis_cooldown_minutes` | int | 60 | `EMILY_OPS_MONITOR_LLM_ANALYSIS_COOLDOWN_MINUTES` |
| `ops_monitor_startup_report_enabled` | bool | true | `EMILY_OPS_MONITOR_STARTUP_REPORT_ENABLED` |
| `ops_monitor_admin_email` | str | "" | `EMILY_OPS_MONITOR_ADMIN_EMAIL` |

---

## 8. 非功能需求

### 8.1 可用性

- **fail-open**：OpsMonitor 初始化或执行失败不影响 PlanTaskScheduler 现有步骤
- **tick 隔离**：新增步骤异常不影响 plan_tasks 维度的 7 个已有步骤
- **冷却防抖**：进程重启不会产生告警风暴（首次 Tick 后冷却机制生效）

### 8.2 可观测性

- 卡滞检测和里程碑预警的每次执行结果记录到 debug 日志
- 告警推送记录到 `ops_alert_log` 表（如存在）
- OpsMonitor 子模块状态通过 `GET /api/v1/health` 暴露

### 8.3 性能

- 新增步骤在 60s Tick 内执行，串行追加
- 卡滞检测和里程碑预警为单次 SQL 查询，预计 <50ms
- Digest 统计为 COUNT + GROUP BY，预计 <100ms
- LLM 分析仅在触发条件满足时执行，不影响常规 Tick 延迟

### 8.4 安全

- OpsMonitor 不对外暴露 API 端点
- 不直接接受用户输入（告警模板无注入风险）
- LLM 分析的 prompt 不含用户消息，仅含系统统计数据

---

## 9. 文件变更清单

### 9.1 新增文件

| 文件 | 说明 |
|------|------|
| `emily_core/services/ops_monitor.py` | OpsMonitor 类：冷启动报告 + 卡滞检测 + 里程碑预警 + Digest + 按需 LLM |
| `需求文件/OpsMonitor/OpsMonitor-需求_V1.md` | 本需求文档 |

### 9.2 修改文件

| 文件 | 改动 |
|------|------|
| `emily_core/config.py` | +8 配置字段 |
| `emily_core/services/plan_task_scheduler.py` | `__init__` 注入 OpsMonitor；`_tick()` 追加步骤 8-11 |
| `emily_core/repositories/node_repo.py` | ProjectNodeRepo +4 查询方法 |
| `emily_core/session/session_agent.py` | `_load_session_prompt()` 注入 `{PROJECT_DIGEST}` |
| `emily_core/infrastructure/llm/prompt_loader.py` | load_prompt 支持 Digest 注入（或由 SessionAgent 自行处理） |
| `emily_core/__init__.py` | `_ensure_initialized()` 中创建 OpsMonitor + 调用 `startup_report()`；注入 OpsMonitor 到 PlanTaskScheduler |

---

## 10. 与废弃概念的对应

| 旧概念（ProjectAgent 需求） | 新定位（OpsMonitor） |
|---------------------------|---------------------|
| 独立 ProjectAgent 进程 | PlanTaskScheduler 的扩展步骤 |
| 独立 Tick 循环 + Advisory Lock | 复用 PlanTaskScheduler 的 `_tick()` |
| 4 阶段路线图（Phase 1-4） | 最终形态一次描述，实施顺序由计划阶段确定 |
| EmilyShell 运维终端 | 独立需求，不纳入 OpsMonitor |
| agent_shell/ 目录 | 独立需求，不纳入 OpsMonitor |
| ops_scheduler 探针框架 | Phase 1 不建——卡滞/里程碑直接在 `_tick()` 中实现。探针框架留待多个运维扫描项需要独立管理时再评估 |
| ops_* 5 张运维表 | 更新为 3 张：`ops_startup_report` + `ops_alert_log` + `project_digest` |
| project_agent_config.py | 配置直接放在 `config.py` |

---

*本需求规格基于以下来源整合编写：*
- *`需求文件/ProjectAgent/` 下 5 份设计文档*
- *`需求文件/运维模块/ops_scheduler_运维模块详细设计说明书.md`*
- *`需求文件/邮箱控制/邮箱控制模块-完整需求规格.md`*
- *`emily-data/prompts/project.md`*
- *`notebook.md` 中 project 思考笔记*
- *三轮架构讨论（ProjectAgent → Monitor + 按需 LLM → OpsMonitor）*
- *代码现状勘查：PlanTaskScheduler / ProjectNodeRepo / OutboundEventBus / SessionAgent*
