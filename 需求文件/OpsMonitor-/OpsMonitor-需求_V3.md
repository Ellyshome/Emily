# OpsMonitor — 需求规格 V3

> **版本**：V3
> **状态**：待评审
> **最后更新**：2026-07-02

---

## 1. 定位

### 1.1 一句话

OpsMonitor 是 Emily Core 的运维感知模块。采用**定时唤醒**（非 Tick 轮询）的方式工作：凌晨 LLM 复盘项目风险 + 生成个人晨报、早晨推送晨报、以及为 SessionAgent 提供动态 Digest 注入。

### 1.2 不是什么

- **不是独立 Agent** — 不常驻 LLM 推理。LLM 仅在凌晨复盘时批量调用，结束后释放
- **不挂在 PlanTaskScheduler 上** — 独立的 asyncio 唤醒循环，与 PlanTaskScheduler 互不依赖
- **不是业务看板** — 不画图表。产出是 IM 晨报 + Digest 文本
- **不是 DevOps Agent** — 不处理脚本报错、不自我修复（DevOps Agent 为独立需求）
- **不做固定阈值报警** — 不设"X天未更新就报警"。风险判断全部交给 LLM 动态推理

---

## 2. 背景与动机

当前系统有两层 Agent——SessionAgent（会话级）和 WorkItemAgent（任务级），均被动响应。缺少一个主动感知项目状态、在每天工作开始前就告诉负责人"你今天该关心什么"的角色。

具体缺口：
- 没人告诉节点负责人：你的前置条件还差多少、按当前速度能不能赶上 deadline
- SessionAgent 对项目的认知依赖固定 `domain_knowledge.md`，不随项目进度更新
- 系统重启后管理员不知道当前状态

---

## 3. 架构

### 3.1 在 EmilyCore 中的位置

```
EmilyCore
  ├── PlanTaskScheduler (独立 Tick, plan_tasks 生命周期)
  └── OpsMonitor (独立唤醒循环)
        ├── 定时唤醒
        │     ├── 3:00 → _nightly_review()    复盘 + 生成晨报
        │     └── 9:00 → _deliver_briefs()    推送晨报
        ├── 一次性动作
        │     └── Core 初始化 → startup_report()   冷启动简报
        └── 懒加载
              └── SessionAgent → refresh_digest_if_stale()   Digest 提供
```

### 3.2 动作一览

| 动作 | 触发方式 | LLM？ |
|------|----------|-------|
| 冷启动报告 | Core 初始化完成，仅一次 | ❌ |
| 凌晨复盘 | 定时唤醒 3:00，每天一次 | ✅ 每节点一次 chat_json |
| 晨报合成 | 定时唤醒 9:00，每天一次 | ✅ 一次 chat（节点文案已有，仅做模板组装） |
| 晨报推送 | 紧接着晨报合成 | ❌ |
| Digest | SessionAgent 调用时按需刷新（>10min stale 则重新统计） | ❌ |

### 3.3 唤醒循环

不轮询，直接睡到目标时刻：

```
醒来 → 执行动作 → 计算下次醒来时间 → asyncio.sleep(s) → 醒来 → ...
```

每天最多醒来两次（3:00 + 9:00）。如果错过（系统当时挂了），Core 初始化时检测到今天还没复盘 → 立刻补执行。

### 3.4 与现有模块的关系

| 模块 | 关系 |
|------|------|
| `EmilyCore._ensure_initialized()` | **宿主** — 创建 OpsMonitor 实例 + 冷启动报告 + 启动唤醒循环 |
| `ProjectNodeRepo` | **依赖** — 读取节点数据 |
| `NodeDependencyRepo` | **依赖** — 读取前置依赖链（LLM 推理需要） |
| `EventRepository` | **依赖** — Digest 中"最近 7 天事件数"需要 |
| `OutboundEventBus` | **依赖** — 推送晨报 |
| `EmailService` | **依赖** — 冷启动报告邮件 |
| `LLM Client` | **依赖** — 复盘 + 晨报合成；复用 `EmilyCore._llm_client` |
| `SessionAgent` | **下游消费者** — 读取 Digest 注入 system prompt |
| `users` 表 | **依赖** — 读取用户的主力通讯途径字段，确定推送通道（默认 QQ） |

---

## 4. 核心功能

### 4.0 冷启动报告

Core 初始化完成后触发一次。采集环境信息 + 项目状态，写 DB 并发邮件给管理员。

**信息采集**：

| 类别 | 信息项 | 来源 |
|------|--------|------|
| 环境 | 启动时间、容器 hostname、Python 版本 | `platform` / `socket` / `os` |
| 环境 | DB 连接状态 | `SELECT 1` |
| 项目 | 活跃项目名称、节点总数、各状态分布 | `ProjectNodeRepo.count_all()` / `count_by_status()` |
| 项目 | 正在执行中的节点数量 | `ProjectNodeRepo.count_by_status("IN_PROGRESS")` |

**输出**：
1. 写入 `ops_startup_report` 表
2. 发送邮件给管理员（`ops_monitor_admin_email` 为空时仅写 DB）

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_startup_report_enabled` | bool | true | 冷启动报告开关 |
| `ops_monitor_admin_email` | str | "" | 管理员邮箱 |

---

### 4.1 凌晨复盘 + 个人晨报

核心功能。每天 3:00 执行。

#### 4.1.1 流程

```
3:00 唤醒

Phase 1 — 数据采集 (纯 SQL, 不调 LLM)
  ├── ProjectNodeRepo 拉活跃节点
  │     WHERE is_discarded=false AND status NOT IN ('COMPLETED','CONDITIONS_NOT_MET')
  ├── 逐个节点补全依赖链 → NodeDependencyRepo.get_dependency_chain()
  └── 组装每个节点的「节点档案」→ {node_context}

Phase 2 — LLM 批量评估
  ├── 逐个节点调 LLM chat_json (prompt: ops_monitor_review.md → {node_context})
  ├── 并行调用 (asyncio.gather)
  └── 每节点输出 → {risk_level, morning_brief_for_owner}

Phase 3 — 晨报生成
  ├── 按 owner_dept_id 分组
  ├── 每组调一次 LLM 合成完整晨报 (prompt: ops_monitor_brief.md)
  └── 写入 morning_briefs 表 (status='pending_delivery')
```

#### 4.1.2 复盘输入

每个节点的 LLM prompt 上下文（即 `{node_context}`，由 OpsMonitor 从数据库采集后注入 `ops_monitor_review.md`）：

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
  - 2025年同期项目：施工许可证办理 IN_PROGRESS→COMPLETED 平均45天

当前日期: 2026-07-02
```

#### 4.1.3 风险等级

| 等级 | 含义 | 晨报呈现 |
|------|------|----------|
| `none` | 无明显风险 — 不在晨报中出现 | 沉默 |
| `low` | 可关注 | "可以留意"段 |
| `medium` | 需要注意 | "建议关注"段 |
| `high` | 大概率延期，需立即行动 | "需立即行动"段 |
| `unknown` | 数据不足无法判断 | 晨报中列出并标注"数据不足，请人工判断" |

#### 4.1.4 晨报示例

```
早安，开发部。

今日是 2026年7月2日 星期三。

━━ 需立即行动 ━━
❗ 商品房预售许可证（3.8）：主体结构还需约45天封顶，预售许可证办理预估15-30天。最早完成日8/30已逼近9/1 deadline。建议立即启动材料准备。

━━ 你的工作总览 ━━
  进行中：3 个
  已完成：12 个
  本周截止：1 个（施工图审查完成）
```

#### 4.1.5 晨报推送

合成完成后立即通过 `OutboundEventBus.publish()` 推送。推送通道**从 `users` 表的 `primary_channel` 字段读取**（当前统一默认为 QQ，未来支持多通道自动判断）。推送后 `morning_briefs.status` → `delivered`。

#### 4.1.6 降级策略

| 场景 | 处理 |
|------|------|
| LLM 不可用 | 降级为纯规则摘要：节点总数/各状态数/本周到期列表。标注"⚠️ LLM 不可用，此为自动摘要" |
| 部分节点 LLM 超时 | 超时节点标记 `unknown`，不影响其他节点 |
| 推送时用户不在线 | 如用户配置了邮箱，降级为邮件发送 |
| 3:00 系统挂了 | Core 初始化时检测——今天尚未复盘 → 立即补执行 |
| 历史数据不足 | LLM 结果标注 `confidence: low`，晨报提示"此为系统初步评估" |

#### 4.1.7 配置项

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_enabled` | bool | true | 总开关 |
| `ops_monitor_review_hour` | int | 3 | 复盘触发小时 |
| `ops_monitor_brief_delivery_hour` | int | 9 | 晨报推送小时 |
| `ops_monitor_review_llm_timeout_seconds` | int | 120 | 单节点 LLM 超时 |

---

### 4.2 项目 Digest

给 SessionAgent 提供动态项目认知快照。

**触发**：SessionAgent 构建 system prompt 时调 `refresh_digest_if_stale()`。距上次刷新超过 10 分钟 → 重新统计；否则返回缓存。

**内容**（纯 SQL 统计，不调 LLM）：

```
当前项目状态（自动生成于 {generated_at}）：
- 节点总数 {total}，已完成 {completed}（{pct}%），进行中 {in_progress}
- 阻塞 {blocked} 个，延期 {delayed} 个
- 各阶段概况：Stage 1: {s1_done}/{s1_total} ...
- 本周到期节点：{list}
- 最近 7 天新增事件 {event_count} 条
```

**存储**：`project_digest` 表（按 project_id 单行 upsert）。缓存同时存在于 OpsMonitor 内存（`_digest_cache` + `_last_digest_at`），优先用内存缓存。

**配置项**：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `ops_monitor_digest_stale_minutes` | int | 10 | Digest 过期阈值（分钟） |

---

### 4.3 推送通道

主动推送信息（晨报、告警）时，从 `users` 表读取 `primary_channel` 字段确定通道：

| 字段值 | 通道 | 实现 |
|--------|------|------|
| `qq` (默认) | QQ 私聊 | `OutboundEventBus.publish("reply", ...)` |
| `email` | 邮件 | `EmailService.send()` |
| 未设置 | QQ（默认） | 同上 |

`primary_channel` 是 `users` 表新增字段，`VARCHAR(20) DEFAULT 'qq'`。当前不做自动判断（如"在线用QQ、离线用邮件"），这个逻辑留待未来。

---

### 4.4 （预留）DevOps Agent 触发

当 OpsMonitor 执行中出现系统级异常（Digest 连续失败、Repo 抛非预期异常），记录错误并置 `_devops_flag`，供未来 DevOps Agent 读取排查。

不在此需求范围内展开。

---

## 5. 与 SessionAgent 的集成

### 5.1 Digest 注入

`SessionAgent._build_system_prompt()` 在每次调用 LLM 前实时读取 Digest：

```python
def _build_system_prompt(self) -> str:
    base = _SESSION_SYSTEM_PROMPT  # 文件加载的模板，含 {PROJECT_DIGEST} 占位符
    digest_text = self._ops_monitor.refresh_digest_if_stale() or ""
    return base.replace("{PROJECT_DIGEST}", digest_text)
```

**注入时机**：`_build_system_prompt()` 每次调用的时刻，而非 `import` 时。这是因为 `_load_session_prompt()` 是模块级变量，import 时执行一次就不会再加载。Digest 必须在每次 LLM 调用前实时注入。

### 5.2 与 domain_knowledge.md 的关系

`domain_knowledge.md` 提供静态领域骨架（术语、组织架构），保留。Digest 提供动态项目快照。互补。

---

## 6. 数据库

### 6.1 新增表

| 表名 | 用途 | 关键列 |
|------|------|--------|
| `ops_startup_report` | 冷启动报告 | startup_time, instance_id, nodes_total, nodes_in_progress, nodes_blocked, summary_text |
| `morning_briefs` | 每日个人晨报 | brief_date, owner_dept_id, content, status(pending/delivered/failed), generated_at, delivered_at |

> `project_digest` 表保留（V2 中已有设计）。

### 6.2 users 表新增字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `primary_channel` | VARCHAR(20) | `'qq'` | 主力通讯途径。`qq` / `email`。未来扩展自动判断 |

### 6.3 Repository 新增方法

**ProjectNodeRepo**：

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `find_active(project_id)` | `list[ProjectNode]` | 活跃节点（非 COMPLETED / CONDITIONS_NOT_MET） |
| `count_all(project_id)` | `int` | 节点总数 |
| `count_by_status(project_id, status)` | `int` | 按状态计数 |
| `find_due_this_week(project_id)` | `list[ProjectNode]` | 本周到期节点 |

**NodeDependencyRepo**：

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `get_dependency_chain(node_id)` | `list[dict]` | 完整前置依赖链（递归，上限 10 层） |

**EventRepository**：

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `count_recent(project_id, days=7)` | `int` | 最近 N 天事件数 |

---

## 7. 配置项汇总

| 配置键 | 类型 | 默认值 | 环境变量 |
|--------|------|--------|----------|
| `ops_monitor_enabled` | bool | true | `EMILY_OPS_MONITOR_ENABLED` |
| `ops_monitor_startup_report_enabled` | bool | true | `EMILY_OPS_MONITOR_STARTUP_REPORT_ENABLED` |
| `ops_monitor_admin_email` | str | "" | `EMILY_OPS_MONITOR_ADMIN_EMAIL` |
| `ops_monitor_review_hour` | int | 3 | `EMILY_OPS_MONITOR_REVIEW_HOUR` |
| `ops_monitor_brief_delivery_hour` | int | 9 | `EMILY_OPS_MONITOR_BRIEF_DELIVERY_HOUR` |
| `ops_monitor_review_llm_timeout_seconds` | int | 120 | `EMILY_OPS_MONITOR_REVIEW_LLM_TIMEOUT` |
| `ops_monitor_digest_stale_minutes` | int | 10 | `EMILY_OPS_MONITOR_DIGEST_STALE_MINUTES` |

---

## 8. 非功能需求

### 8.1 可用性

- **fail-open**：OpsMonitor 初始化/执行失败不影响 Core 其余功能
- **复盘降级**：LLM 不可用 → 纯规则摘要，不中断服务
- **错过补执行**：系统在 3:00 挂了 → 重启时检测并补执行

### 8.2 可观测性

- 复盘执行记录到 info 日志（耗时、评估节点数、风险分布）
- 推送成功/失败记录到 `morning_briefs.status`
- OpsMonitor 状态通过 `/health` 端点暴露

### 8.3 性能

- 复盘：~30 个活跃节点 × 并行 LLM 调用，预计 30-60s（取决于 LLM API 并发）
- Digest：纯 SQL，< 100ms
- 唤醒循环：每天醒来 2 次，零 CPU 开销

### 8.4 安全

- 无对外暴露 API
- LLM prompt 仅含系统数据，不含用户消息
- 邮件凭证复用已有环境变量

---

## 9. 核心逻辑伪代码

```python
class OpsMonitor:
    def __init__(self, config, node_repo, dep_repo, event_repo,
                 llm_client, email_service, outbound_bus, user_repo):
        ...

    # ── 生命周期 ──

    async def startup_report(self):
        """Core 初始化后调用一次。"""
        ...

    async def start(self):
        """启动唤醒循环。"""
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while self._running:
            target, action = self._next_wakeup()
            wait = (target - datetime.now()).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)
            await action()

    def _next_wakeup(self):
        """计算下次醒来时间与动作。"""
        now = datetime.now()
        today_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)
        today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)

        if not self._review_done_today:
            target = max(now, today_3am)
            return target, self._nightly_review
        if not self._briefs_delivered_today and self._has_pending_briefs():
            target = max(now, today_9am)
            return target, self._deliver_briefs

        # 今天的活都干完了，睡到明天 3:00
        tomorrow_3am = today_3am + timedelta(days=1)
        return tomorrow_3am, self._nightly_review

    # ── 核心动作 ──

    async def _nightly_review(self):
        """凌晨复盘：数据采集 → LLM 批量评估 → 晨报生成。"""
        ...
        self._review_done_today = True

    async def _deliver_briefs(self):
        """早晨推送：读 morning_briefs → OutboundEventBus 推送 → 标记 delivered。"""
        ...
        self._briefs_delivered_today = True

    def refresh_digest_if_stale(self) -> str:
        """SessionAgent 调用。过期则重新统计，否则返回缓存。"""
        ...
```

---

## 10. 文件变更清单

### 10.1 新增文件

| 文件 | 说明 |
|------|------|
| `emily_core/services/ops_monitor.py` | OpsMonitor 类（唤醒循环 + 复盘 + 晨报 + Digest） |
| `emily-data/prompts/ops_monitor_review.md` | 凌晨复盘 LLM prompt |
| `emily-data/prompts/ops_monitor_brief.md` | 晨报组装 LLM prompt |
| `需求文件/OpsMonitor/OpsMonitor-需求_V3.md` | 本需求文档 |

### 10.2 修改文件

| 文件 | 改动 |
|------|------|
| `emily_core/config.py` | +7 配置字段 |
| `emily_core/repositories/node_repo.py` | ProjectNodeRepo +4 查询方法；NodeDependencyRepo +1 方法 |
| `emily_core/repositories/event_repo.py` | EventRepository +1 方法（count_recent） |
| `emily_core/session/session_agent.py` | `_build_system_prompt()` 注入 `{PROJECT_DIGEST}`；注入 OpsMonitor 引用 |
| `emily_core/infrastructure/database/models.py` | +2 ORM（`OpsStartupReport` / `MorningBrief`）；users 表 +1 字段（primary_channel） |
| `emily_core/__init__.py` | `_ensure_initialized()` 中创建 OpsMonitor → startup_report() → start()；注入到 SessionAgent |

---

*基于多轮架构讨论编写，覆盖以下结论：*
- *命名从 ProjectAgent → OpsMonitor*
- *架构从独立 Tick → 挂在 PlanTaskScheduler → 独立唤醒循环*
- *核心从卡滞检测 → 凌晨 LLM 复盘 + 个人晨报*
- *DevOps Agent 拆为独立需求*
- *推送通道基于 users.primary_channel 字段*
