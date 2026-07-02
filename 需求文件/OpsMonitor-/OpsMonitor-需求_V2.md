# OpsMonitor — 运维巡检模块需求规格

> **版本**：V2
> **状态**：待评审
> **最后更新**：2026-07-01

---

## 1. 定位

### 1.1 一句话

OpsMonitor 是 **PlanTaskScheduler 的扩展模块**，提供三个定时动作：凌晨 LLM 复盘评估风险并生成个人晨报、周期性刷新项目 Digest 供 SessionAgent 注入、系统冷启动时生成环境与项目状态简报发邮件给管理员。

### 1.2 不是什么

- **不是独立 Agent** — 不新建进程、不新建 Tick 循环、不常驻 LLM 推理
- **不是业务看板** — 不生成管理驾驶舱、不画图表。复盘结果以内嵌晨报文本和告警消息的形式存在
- **不是 DevOps Agent** — 不处理脚本报错、不自我修复。那个是独立需求（§4.5 预留接口）
- **不替代卡滞检测** — 不设固定阈值做"X天未更新就报警"。风险判断全部交给 LLM 动态推理

### 1.3 命名

"Monitor" 而非 "Agent"：本模块不做自主决策——它定时收集数据、交给 LLM 评估、生成文本推送出去。决策者是 LLM（推理风险）+ 用户（阅读晨报后行动）。

---

## 2. 背景与动机

### 2.1 当前缺口

PlanTaskScheduler 的 `_tick()` 循环只扫描 `plan_tasks` 表，不感知项目全景节点图（`project_nodes`）。导致：

- 没有人主动告诉每个节点负责人"你负责的节点前置条件还差多少、按当前速度能不能赶上 deadline"
- SessionAgent 对项目的认知依赖固定的 `domain_knowledge.md`，无法随项目推进更新
- 系统重启后管理员不知道当前项目处于什么状态

### 2.2 为什么不是独立 Agent

PlanTaskScheduler 已有成熟的调度循环，另起 Tick 会导致锁竞争和代码重复。OpsMonitor 的所有定时动作挂在 PlanTaskScheduler 的 `_tick()` 中，按各自间隔执行，不新建锁。

---

## 3. 架构

### 3.1 在 EmilyCore 中的位置

```
EmilyCore
  ├── PlanTaskScheduler (时间驱动调度循环, 60s tick)
  │     ├── _tick() 现有步骤 (plan_tasks 维度)
  │     └── _tick() 新增 OpsMonitor 检查点:
  │           ├── 凌晨3:00 → 全项目LLM复盘 + 生成个人晨报
  │           ├── 每10min  → Digest 刷新
  │           └── 每30min  → (预留: DevOps Agent 触发检查)
  ├── OpsMonitor (一次性启动动作)
  │     └── startup_report() → 冷启动简报 + 邮件
  ├── OutboundEventBus → SSE → IM (晨报推送)
  └── SessionAgent (消费 Digest)
```

### 3.2 动作一览

| 动作 | 触发方式 | 频率 | LLM？ |
|------|----------|------|-------|
| 冷启动报告 | Core 初始化完成时，仅一次 | 每次重启 | ❌ 纯数据采集 + 模板 |
| 凌晨复盘 + 晨报生成 | 定时 (3:00 AM) | 每天一次 | ✅ 核心 LLM 推理 |
| 晨报推送 | 定时 (从公司制度读取上班时间，默认 9:00 AM) | 每天一次 | ❌ 仅推送已生成内容 |
| Digest 刷新 | Tick 计数，每 10min | ~10min | ❌ 纯统计 |

### 3.3 与现有模块的关系

| 模块 | 关系 |
|------|------|
| `PlanTaskScheduler` | **宿主** — OpsMonitor 的定时动作在其 `_tick()` 中检查并触发 |
| `EmilyCore._ensure_initialized()` | **宿主** — 冷启动报告在此触发 |
| `ProjectNodeRepo` | **依赖** — 读取节点数据 |
| `NodeDependencyRepo` | **依赖** — 读取前置依赖链（LLM 推理需要） |
| `OutboundEventBus` | **依赖** — 推送晨报/告警 |
| `EmailService` | **依赖** — 冷启动报告邮件 |
| `SessionAgent` | **下游消费者** — 读取 Digest 注入 system prompt |
| DevOps Agent (未来) | **被触发方** — OpsMonitor 检测到系统性异常时拉起（§4.5 预留） |

---

## 4. 核心功能

### 4.0 冷启动报告

**定位**：EmilyCore 初始化完成后触发一次，感知当前系统环境和项目状态，生成简报写入 DB 并发邮件给管理员。

**触发**：仅一次 — `EmilyCore._ensure_initialized()` 中，`_build_session_pool()` 之前。

**信息采集**：

| 类别 | 信息项 | 来源 |
|------|--------|------|
| 环境 | 启动时间、容器 hostname、Python 版本 | `platform` / `socket` / `os` |
| 环境 | DB 连接状态 | `SELECT 1` 探测 |
| 项目 | 是否有活跃项目 | `ProjectNodeRepo.count_all()` |
| 项目 | 正在执行中的节点数量 | `ProjectNodeRepo.count_by_status("IN_PROGRESS")` |
| 状态概述 | 节点总数、各状态分布 | `ProjectNodeRepo` 按 status 计数 |

**输出**：
1. 写入 `ops_startup_report` 表（审计）
2. 通过 `EmailService.send()` 发邮件给管理员（`ops_monitor_admin_email` 未配置时仅写 DB）

**报告格式**（Markdown 邮件）：

```
Emily 冷启动报告
═══════════════

启动时间：2026-07-01 14:30:00 UTC+8
实例 ID：emily-core-a1b2c3d4
Python：3.12.x
数据库：可达 ✅

项目：锦绣花园
  节点总数：42
  进行中：5  |  阻塞：3  |  已完成：28

当前状态概述：
  项目整体进度 66.7%，有 3 个阻塞节点需要关注。
  最近一条事件记录于 2026-06-30。
```

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_startup_report_enabled` | bool | true | 冷启动报告开关 |
| `ops_monitor_admin_email` | str | "" | 管理员邮箱（空 = 不发送，仅写 DB） |

---

### 4.1 凌晨 LLM 复盘 + 个人晨报

这是 OpsMonitor 的核心功能。

#### 4.1.1 时机

```
Tick 循环每分钟检查一次当前时刻：
  - 03:00 → 触发复盘
  - 从 company_config 读取上班时间（默认 09:00）→ 触发晨报推送
```

复盘和推送之间至少间隔 6 小时（03:00 到 09:00），确保 LLM 推理有充足的时间窗口。如果 LLM 调用较慢或临时不可用，在此期间可以重试。

#### 4.1.2 复盘输入

LLM 接收的上下文（按节点逐个评估）：

```
节点 ID: 3.8
节点名称: 商品房预售许可证办理
当前状态: IN_PROGRESS
Deadline: 2026-09-01
负责人: 开发部（owner_dept_id）
所属阶段: Stage 3

前置依赖链:
  - 3.6 主体结构封顶 (IN_PROGRESS, 进度30%, 预计完工: 2026-08-15)
    └── 3.4 施工图审查 (COMPLETED)
    └── 3.5 施工许可证 (COMPLETED)

历史参考（同类节点完成耗时，如有）:
  - 2025年同期项目：施工许可证办理 从IN_PROGRESS到COMPLETED 平均45天

当前日期: 2026-07-01
```

#### 4.1.3 LLM 推理输出

```json
{
  "node_id": "3.8",
  "risk_level": "high",
  "risk_summary": "主体结构还需约45天封顶，预售许可证办理预估15-30天，最早完成日8/30已逼近9/1 deadline",
  "suggested_action": "建议现在就开始准备预售许可证材料，不等主体封顶",
  "suggested_warn_at": "2026-07-15",
  "morning_brief_for_owner": "预售许可证（3.8）存在较高延误风险。前置节点主体结构封顶目前仅完成30%，按当前速度预计8月中旬才能成为前置条件，届时办理时间非常紧张。建议立即启动预售许可证材料准备工作。"
}
```

**风险等级含义**：

| 等级 | 含义 | 晨报呈现 |
|------|------|----------|
| `none` | 无明显风险 — 前置条件充足，时间充裕 | 不在晨报中出现（沉默 = 好消息） |
| `low` | 可关注 — 有余量但建议留意 | "以下节点建议关注" |
| `medium` | 需要注意 — 前置条件偏紧或历史数据不乐观 | "以下节点需要您的关注" |
| `high` | 高风险 — 大概率延期，需立即行动 | "❗以下节点存在较高延误风险" |

#### 4.1.4 晨报生成

LLM 完成全节点评估后，按 `owner_dept_id` 分组，为每个负责人合成一份个人晨报：

```
早安，开发部。

今日是 2026年7月2日 星期三。

━━ 需立即行动 ━━
❗ 商品房预售许可证（3.8）：主体结构还需约45天封顶，预售许可证办理预估15-30天。最早完成日8/30已逼近9/1 deadline。建议立即启动材料准备。

━━ 建议关注 ━━
（无）

━━ 你的工作节点总览 ━━
  进行中：3 个
  已完成：12 个
  本周截止：1 个（施工图审查完成）
```

生成后，每条晨报存入 `morning_briefs` 表，标记 `status='pending_delivery'`。

#### 4.1.5 晨报推送

上班时间（默认 9:00）触发推送：读取 `morning_briefs` 表中 `status='pending_delivery'` 的记录，按 `owner_dept_id` 逐个通过 `OutboundEventBus.publish("reply", ...)` 推送。推送成功后标记 `status='delivered'`。

> 如果某用户不在线（无活跃 SSE 连接），消息在 OutboundEventBus 队列中等待，或降级为邮件发送（见 §4.1.6）。

#### 4.1.6 降级策略

| 场景 | 处理 |
|------|------|
| LLM 不可用（API key 缺失或调用失败） | 降级为纯规则摘要：节点总数/各状态数量/本周到期节点列表。标注"⚠️ 今日为自动生成摘要，LLM 风险评估不可用" |
| LLM 超时（>120s） | 单节点评估失败不影响其他节点。超时的节点标记为 `risk_level: "unknown"`，晨报中列出但不给判断 |
| 推送时用户不在线 | 降级：如果用户配置了邮箱，通过 `EmailService` 发送邮件版晨报 |
| 凌晨 3:00 未触发（系统当时挂了） | Core 初始化完成时检查——如果今天还没生成晨报且当前时间 > 3:00，立即执行复盘（推迟但不错过） |

#### 4.1.7 配置项

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_review_enabled` | bool | true | 凌晨复盘开关 |
| `ops_monitor_review_hour` | int | 3 | 复盘触发小时（24h制） |
| `ops_monitor_brief_delivery_hour` | int | 9 | 晨报推送小时（从公司制度读取，此为兜底） |
| `ops_monitor_review_llm_timeout_seconds` | int | 120 | 单节点 LLM 评估超时 |

---

### 4.2 项目摘要 Digest

**目的**：给 SessionAgent 提供随项目推进而自动刷新的认知快照，替代固定的 `domain_knowledge.md`。

**触发**：每 10 个 Tick（约 10 分钟）刷新一次。

**内容**（纯统计模板，不调用 LLM）：

```
当前项目状态（自动生成于 {generated_at}）：
- 节点总数 {total}，已完成 {completed}（{pct}%），进行中 {in_progress}
- 阻塞 {blocked} 个，延期 {delayed} 个
- 各阶段概况：Stage 1: {s1_done}/{s1_total} ...
- 本周到期节点：{list}
- 最近 7 天新增事件 {event_count} 条
```

**存储**：写入 `project_digest` 表（按 project_id 单行 upsert）。

**注入时机**（见 §5.4）：`SessionAgent` 在每次构建 system prompt 时实时读取 `project_digest` 表，注入 `{PROJECT_DIGEST}` 占位符。

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_digest_refresh_ticks` | int | 10 | 每 N 个 Tick 刷新一次 Digest |

---

### 4.3 （预留）DevOps Agent 触发接口

当 OpsMonitor 的执行过程中出现系统级异常（如 Digest 刷新连续失败、Repo 方法抛非预期异常），记录错误日志并设置一个 `_devops_flag`。此标记由 PlanTaskScheduler 的 Tick 检查——如果置位，拉起 DevOps Agent 排查。

**不在此需求范围内展开**，仅预留：
- `ops_monitor._devops_flag: bool`
- `PlanTaskScheduler._tick()` 中对标记的检查点
- 触发 DevOps Agent 的调用接口（CLI 命令或 Python API，待 DevOps Agent 需求确定）

---

## 5. 与 SessionAgent 的集成

### 5.1 Digest 注入路径

```
PlanTaskScheduler._tick()
  └── _refresh_digest()
        └── ProjectNodeRepo 查询 + 统计
        └── upsert project_digest 表

SessionAgent._build_system_prompt()
  └── 读取 project_digest 表最新一条
  └── 将 digest_text 注入 {PROJECT_DIGEST} 占位符
  └── 调用 LLM
```

### 5.2 与 domain_knowledge.md 的关系

`domain_knowledge.md` — 静态领域骨架（术语、组织架构），保留。Digest — 动态项目快照，互补。

### 5.3 Digest 注入时机

`_load_session_prompt()` 是 import 时执行的模块级变量，只加载一次。因此 `{PROJECT_DIGEST}` 不能在文件加载时注入，必须在每次构建 system prompt 时实时读取 `project_digest` 表并注入：

```python
def _build_system_prompt(self) -> str:
    base = _SESSION_SYSTEM_PROMPT
    digest_text = self._read_latest_digest() or ""
    return base.replace("{PROJECT_DIGEST}", digest_text)
```

---

## 6. 数据库需求

### 6.1 新增表

| 表名 | 用途 | 关键列 |
|------|------|--------|
| `ops_startup_report` | 冷启动报告 | startup_time, instance_id, project_id, project_name, nodes_total, nodes_in_progress, nodes_blocked, db_reachable, summary_text |
| `morning_briefs` | 每日个人晨报 | brief_date, owner_dept_id, content, risk_summary, status(pending_delivery/delivered/delivery_failed), generated_at, delivered_at |
| `project_digest` | 项目摘要缓存 | project_id (PK), digest_text, generated_at |

> `ops_alert_log` 不再需要——卡滞检测已取消，固定阈值报警不再存在。LLM 风险评估的结果直接体现在晨报中，不需要单独的告警记录表。

### 6.2 ProjectNodeRepo 新增方法

| 方法 | SQL 逻辑 | 返回值 |
|------|----------|--------|
| `find_active_with_dependencies(project_id)` | 查询状态非 COMPLETED/CONDITIONS_NOT_MET 的节点 + 其前置依赖链 | `list[ProjectNode]`（每个节点附带依赖列表） |
| `count_all(project_id)` | `SELECT COUNT(*) WHERE is_discarded=false` | `int` |
| `count_by_status(project_id, status)` | `SELECT COUNT(*) WHERE status=:s AND is_discarded=false` | `int` |
| `find_nodes_due_this_week(project_id)` | `WHERE deadline BETWEEN now AND now+7d AND status != COMPLETED` | `list[ProjectNode]` |

### 6.3 NodeDependencyRepo 新增方法

| 方法 | SQL 逻辑 | 返回值 |
|------|----------|--------|
| `get_dependency_chain(node_id)` | 递归查询该节点的所有前置依赖节点，含每个前置节点的当前 status 和进度 | `list[dict]` |

---

## 7. 配置项汇总

| 配置键 | 类型 | 默认值 | 环境变量 |
|--------|------|--------|----------|
| `ops_monitor_enabled` | bool | true | `EMILY_OPS_MONITOR_ENABLED` |
| `ops_monitor_startup_report_enabled` | bool | true | `EMILY_OPS_MONITOR_STARTUP_REPORT_ENABLED` |
| `ops_monitor_admin_email` | str | "" | `EMILY_OPS_MONITOR_ADMIN_EMAIL` |
| `ops_monitor_review_enabled` | bool | true | `EMILY_OPS_MONITOR_REVIEW_ENABLED` |
| `ops_monitor_review_hour` | int | 3 | `EMILY_OPS_MONITOR_REVIEW_HOUR` |
| `ops_monitor_brief_delivery_hour` | int | 9 | `EMILY_OPS_MONITOR_BRIEF_DELIVERY_HOUR` |
| `ops_monitor_review_llm_timeout_seconds` | int | 120 | `EMILY_OPS_MONITOR_REVIEW_LLM_TIMEOUT` |
| `ops_monitor_digest_refresh_ticks` | int | 10 | `EMILY_OPS_MONITOR_DIGEST_REFRESH_TICKS` |

---

## 8. 非功能需求

### 8.1 可用性

- **fail-open**：OpsMonitor 执行失败不影响 PlanTaskScheduler 现有步骤
- **tick 隔离**：新增步骤异常不影响 plan_tasks 的已有步骤
- **复盘降级**：LLM 不可用时降级为纯规则摘要，不中断服务

### 8.2 可观测性

- 每次复盘执行记录到 info 日志（耗时、评估节点数、风险分布）
- 晨报推送成功/失败记录到 `morning_briefs.status`
- OpsMonitor 状态通过 `GET /api/v1/health` 暴露

### 8.3 性能

- 凌晨复盘：1 次总结性 LLM 调用。按 ~30 个活跃节点，预计 1-3 分钟完成（含所有节点的 LLM 评估）
- Digest 统计：纯 SQL，< 100ms
- 晨报推送：按 `owner_dept_id` 逐条 publish，< 1s

### 8.4 安全

- 无对外暴露的 API 端点
- LLM prompt 中不含用户个人消息，仅含系统数据
- 邮件凭证复用已有环境变量，不新增敏感配置

---

## 9. 文件变更清单

### 9.1 新增文件

| 文件 | 说明 |
|------|------|
| `emily_core/services/ops_monitor.py` | OpsMonitor 类：冷启动报告 + 凌晨复盘 + 晨报生成 + Digest |
| `需求文件/OpsMonitor/OpsMonitor-需求_V2.md` | 本需求文档 |

### 9.2 修改文件

| 文件 | 改动 |
|------|------|
| `emily_core/config.py` | +8 配置字段 |
| `emily_core/services/plan_task_scheduler.py` | `__init__` 注入 OpsMonitor；`_tick()` 中新增时段检查点 |
| `emily_core/repositories/node_repo.py` | ProjectNodeRepo +4 查询方法 |
| `emily_core/repositories/node_repo.py` | NodeDependencyRepo +1 查询方法 |
| `emily_core/session/session_agent.py` | `_build_system_prompt()` 注入 `{PROJECT_DIGEST}` |
| `emily_core/__init__.py` | `_ensure_initialized()` 中创建 OpsMonitor + 调用 `startup_report()`；注入 OpsMonitor 到 PlanTaskScheduler |
| `emily_core/infrastructure/database/models.py` | +3 ORM（`OpsStartupReport` / `MorningBrief` / `ProjectDigest`） |

---

*本需求规格基于以下来源整合编写：*
- *`需求文件/ProjectAgent/` 下 5 份设计文档*
- *`需求文件/运维模块/ops_scheduler_运维模块详细设计说明书.md`*
- *`需求文件/邮箱控制/邮箱控制模块-完整需求规格.md`*
- *`emily-data/prompts/project.md`*
- *`notebook.md` 中 project 思考笔记*
- *多轮架构讨论（ProjectAgent → Monitor + 按需 LLM → OpsMonitor → 凌晨复盘 + 动态阈值）*
- *代码现状勘查：PlanTaskScheduler / ProjectNodeRepo / OutboundEventBus / SessionAgent / EmailService*
