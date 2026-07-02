# ProjectAgent — 项目级自主 Agent 需求规格

> **版本**：v1.0
> **状态**：待评审
> **最后更新**：2026-07-01
> **来源**：整合 6 份已有设计文档 + 代码现状勘查后编写

---

## 1. 背景与动机

### 1.1 当前缺口

Emily v0.6.0 有两层 Agent 覆盖，均是被动响应型：

| Agent | 作用域 | 触发方式 |
|-------|--------|----------|
| `SessionAgent` | 会话级（per-conversation） | 被动 — 用户发消息 |
| `WorkItemAgent` | 任务级（per-task） | 被动 — PipelineBUS 推给它 |

**项目级作用域无人值守**，具体表现为：

- 全景节点图 ~90 个节点全被动触发 — 节点卡滞 30 天无人知晓
- 没有项目健康度诊断 — 只看容器 `GET /health`，不看项目是否健康
- `PlanTaskScheduler` 只管 plan_task 实例的提醒/过期/归档，不碰状态机
- 无人主动扫描：依赖链断裂、里程碑逾期、数据完整性问题

### 1.2 目标

新增 **ProjectAgent**（项目级自主 Agent），填补第三层作用域：

```
项目级 → ProjectAgent   (新增 · 后台 Tick 循环 · 状态驱动)
会话级 → SessionAgent   (已有 · per-conversation)
任务级 → WorkItemAgent  (已有 · per-task)
```

ProjectAgent 是系统中唯一"不等人来问、自己看、自己决定要不要行动"的角色。

---

## 2. 架构定位

### 2.1 在 EmilyCore 中的位置

```
EmilyCore
  ├── PlanTaskScheduler     (时间驱动 — plan_task 实例生命周期)
  ├── SessionPoolManager    (会话池)
  ├── PipelineBUS           (任务总线)
  └── ProjectAgent           (状态驱动 — 项目级状态机维护 + 健康 + 自动运维)
        │
        ├── 复用 SMNodeRepository   (查询卡滞节点 / 里程碑)
        ├── 复用 OutboundEventBus   (推送告警消息)
        ├── 复用 Advisory Lock      (多进程 Tick 防重)
        └── Phase 3: 调用 PlanTaskService   (主动投递可追踪任务)
```

### 2.2 与 PlanTaskScheduler 的职责边界

| 触发源 | 归属 | 示例 |
|--------|------|------|
| 时间到了 | PlanTaskScheduler | "每周五的周报该交了" → 提醒 |
| 状态机异常 | **ProjectAgent** | "节点 3.5 卡了 30 天" → 排查任务 |

核心区分：
- **PlanTaskScheduler** — 时间驱动，管"到了该做什么"
- **ProjectAgent** — 状态驱动，管"现在出了什么问题"

### 2.3 职责三角

```
         ┌──────────────────┐
         │   状态机主动维护    │  ← 卡滞检测 / 延期预警 / 依赖校验 / 里程碑看守
         │   (Phase 1)        │
         └────────┬─────────┘
                  │
    ┌─────────────┼─────────────┐
    │             │             │
    ▼             ▼             ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│ 健康检查│  │  异常检测  │  │ 自动运维  │
│ Phase 2│  │  Phase 1  │  │ Phase 3  │
└────────┘  └──────────┘  └──────────┘
```

### 2.4 生命周期

```
CREATED → STARTING → ACTIVE → STOPPING → STOPPED
                    │
                    ├─ 按 tick_interval 循环 _tick()
                    │
                    └─ 异常 → DEGRADED（降级运行，不崩溃）
```

- **创建时机**：`EmilyCore._ensure_initialized()` → `_init_project_agent()` → `asyncio.ensure_future(start())`
- **默认 Tick 间隔**：300s（5 分钟），可配置
- **停止**：`shutdown()` → 完成当前 tick → 标记 STOPPED
- **初始化失败不阻塞 Core**（fail-open）：依赖未就绪时仅记录 warning 并跳过

---

## 3. 核心功能需求（四阶段路线图）

### 3.1 Phase 1 — 骨架 + 卡滞检测 + 里程碑预警

**目标**：最小可用 — 后台 Tick 循环 + 两项基础检测 + 告警推送。

#### 3.1.1 后台 Tick 循环

- 以可配置间隔（默认 300s）循环执行 `_do_tick()`
- 受 PostgreSQL Advisory Lock（`hashtext('project_agent:global_tick')`）保护，多进程互斥
- 每次 Tick 生成唯一 `tick_id`（UUID）
- Tick 异常不终止循环，记录错误后继续

#### 3.1.2 卡滞检测

- **查询条件**：`status IN ('IN_PROGRESS', 'BLOCKED', 'DELAYED') AND updated_at < now - N days`
- **可配置阈值**：`project_agent_stale_threshold_days`（默认 14 天）
- **告警冷却**：同一 `node_id:issue_type` 在 `alert_cooldown_hours`（默认 24h）内不重复推送
- **冷却存储**：Phase 1 用内存字典；Phase 3 可升级为 DB 持久化
- **输出**：通过 OutboundEventBus 推送告警消息到对应负责人

告警消息模板：
```
【节点卡滞预警】
节点「{node_name}」（{node_id}）处于「{status}」状态已超过 {days} 天未更新。
负责人：{owner}
所属阶段：Stage {stage_id}
最后更新时间：{updated_at}
请确认进度或更新状态。
```

#### 3.1.3 里程碑预警

- **查询条件**：`is_milestone=true AND planned_end_date 在 N 天内 AND status 非 COMPLETED`
- **可配置阈值**：`project_agent_deadline_warn_days`（默认 7 天）
- **输出**：同告警冷却机制，通过 OutboundEventBus 推送

预警消息模板：
```
【里程碑预警】
里程碑节点「{node_name}」（{node_id}）计划截止日期为 {planned_end_date}，距今不足 {days} 天。
当前状态：{status}
负责人：{owner}
请关注进度，确保按期完成。
```

#### 3.1.4 配置项

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `project_agent_enabled` | bool | true | 总开关 |
| `project_agent_tick_seconds` | int | 300 | Tick 间隔（秒） |
| `project_agent_stale_threshold_days` | int | 14 | 卡滞判定阈值（天） |
| `project_agent_deadline_warn_days` | int | 7 | 里程碑预警提前天数 |
| `project_agent_alert_cooldown_hours` | int | 24 | 同节点同问题冷却时间（小时） |

#### 3.1.5 不包含

- LLM 调用（纯规则/SQL）
- 健康指数计算
- 自动创建计划任务
- 周期性报告生成
- 数据完整性校验
- DB 持久化告警冷却

---

### 3.2 Phase 2 — 健康度检查

**目标**：将项目健康度量化为可追踪的指标，提供项目级"体检报告"。

#### 3.2.1 健康指数计算

```
项目健康指数 = F(
    完成率 × 0.30
  + 风险聚集度 × 0.25
  + 逾期率 × 0.20
  + 阻塞率 × 0.15
  + 数据完整性 × 0.10
)

输出：0-100 分
  >= 80：健康 🟢
  50-79：关注 🟡
  30-49：警告 🟠
  < 30：严重 🔴
```

**阶段级健康**：每个 Stage 独立计算子指数，支持细粒度定位问题阶段。

#### 3.2.2 数据完整性校验

| 校验项 | 说明 |
|--------|------|
| FK 有效性 | `project_id` 指向不存在的 project、事件关联不存在的 conversation |
| 节点-阶段一致性 | `sm_nodes.stage_id` 与 `sm_stages.stage_id` 对应 |
| 依赖链完整性 | `sm_node_dependencies` 中 from/to 节点均存在且未删除 |
| 导入覆盖率 | `全景节点.md` 定义的节点数 vs `sm_nodes` 实际导入数 |
| 孤立节点 | 在某阶段中无任何依赖关系的节点 |
| 循环依赖检测 | BFS/DFS 检测 `sm_node_dependencies` 图中的环 |
| 关键路径阻塞 | `critical_path` JSON 中任一节点处于 BLOCKED/DELAYED |

#### 3.2.3 输出形式

- 每次 Tick 静默执行检查
- 发现问题 → 通过 OutboundEventBus 推送告警（同 Phase 1 模式）
- 每天/每周生成健康报告 → 通过消息通道推送到指定群/人

---

### 3.3 Phase 3 — AI 自动运维 + 主动投递

**目标**：从"发现问题→告警"升级为"发现问题→投递任务→跟踪闭环"。

#### 3.3.1 主动投递

ProjectAgent 发现异常后，不是只发一条告警就完了，而是**创建一个正式的 PlanTask 实例**，交给 PlanTaskScheduler 接管后续生命周期：

```
ProjectAgent._do_tick()
  └── StaleDetector → 发现卡滞节点
        ├── Phase 1: OutboundEventBus → 一次性告警
        └── Phase 3: PlanTaskService.create_instance() → 正式任务 → PlanTaskScheduler 跟踪
```

| 触发条件 | 投递动作 |
|----------|----------|
| 节点卡滞 > 30 天 | 自动创建 plan_task → 分配给 owner |
| 里程碑逾期 | 自动创建升级任务 → 分配给 owner + supervisor |
| 项目健康指数 < 30 | 生成诊断报告 → 推送给 PM，附带建议任务 |
| 依赖链即将就绪 | 提前通知下游节点 owner |
| 依赖链断裂 | 创建修复任务 → 分配给 PM |

#### 3.3.2 周期性报告

| 频率 | 类型 | LLM？ | 说明 |
|------|------|-------|------|
| 每天 | 每日简报 | ❌ | 纯模板：完成/新增阻塞/新增延期 三项变化 |
| 每周 | 深度报告 | ✅ | LLM 分析瓶颈、风险趋势、建议措施 |

- 推送至指定群/人（通过 OutboundEventBus → SSE → IM）
- 每周深度报告另存为 Markdown 文件到 `logs/weekly_*.md`

#### 3.3.3 智能建议

- LLM 分析阻塞节点上下文（节点名称、依赖链、所属阶段、`block_reason`）
- 生成 2-3 条解除阻塞的建议措施
- 推送至节点负责人 + 阶段负责人

#### 3.3.4 自动升级

- 阻塞超过 N 天未解除 → 自动通知上级
- 里程碑逾期超过 M 天 → 自动提升优先级 + 通知 PM
- 复用 PlanTaskScheduler 的 `escalate_to_supervisor` 模式

#### 3.3.5 LLM 调用策略（成本控制）

| 频率 | 操作 | LLM？ | 说明 |
|------|------|-------|------|
| 每 5min | 卡滞检测 | ❌ | 纯 SQL，零 LLM 成本 |
| 每 5min | 里程碑预警 | ❌ | 纯 SQL，零 LLM 成本 |
| 每 5min | 数据完整性校验 | ❌ | 纯 SQL，零 LLM 成本 |
| 每小时 | 健康指数计算 | ❌ | 纯公式，零 LLM 成本 |
| 每天 | 每日简报 | ❌ | 纯模板，零 LLM 成本 |
| 每周 | 深度报告 | ✅ | LLM 分析瓶颈/趋势/建议 |
| 按需 | 智能建议 | ✅ | LLM 分析阻塞上下文 |
| 按需 | 主动投递决策 | ✅ | LLM 判断是否需要/如何投递 |

**原则**：高频操作全部纯规则/SQL，LLM 仅用于低频深度分析和按需触发。

---

### 3.4 Phase 4 — 多项目支持（远期规划）

- 激活 `projects` 表：创建 ProjectRepository / ProjectService / Project API
- 状态机 per-project 化：`sm_nodes` / `sm_stages` 增加 `project_id` FK 隔离
- ProjectAgent per-project 池化：每个活跃项目一个实例
- 跨项目聚合：Dashboard Agent 进行跨项目健康对比/资源调度

---

## 4. 运维调度模块（ops_scheduler）

### 4.1 定位

Tick 调度从 ProjectAgent 独立出来，成为可插拔的**探针框架**。ops_scheduler 只负责"发现"和"记录"，不负责"执行"。ProjectAgent 是它的协调者和消费者。

### 4.2 架构

```
ProjectAgent (协调者)
  └── ops_scheduler (探针框架)
        ├── Tick Scheduler (PG Advisory Lock + UUID per round)
        ├── Probe Registry (可插拔探针注册表)
        └── Probes:
              ├── StaleProbe     (卡滞检测)
              ├── HealthProbe    (健康度检查, Phase 2)
              ├── MailProbe      (邮箱轮询, 接收管理员 Order 命令)
              ├── DependencyProbe(依赖链校验, Phase 2)
              └── DataIntegrityProbe (数据完整性, Phase 2)
```

### 4.3 探针接口

每个探针实现统一接口：
- `run(ctx: TickContext) -> list[ProbeFinding]` — 执行检查，返回发现列表
- 失败隔离：一个探针失败不影响其他探针
- 支持独立设置执行频率（未来扩展）

### 4.4 ProbeFinding 结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `finding_type` | str | 发现类型：stale_node / milestone_warning / health_low / dependency_broken 等 |
| `severity` | str | INFO / WARNING / CRITICAL |
| `target_id` | str | 关联实体 ID（节点 ID、项目 ID 等） |
| `message` | str | 人类可读的描述 |
| `metadata` | dict | 附加结构化数据 |

### 4.5 优雅降级

- DB 不可达 → 探针结果写入本地 JSONL fallback 文件
- 邮箱不可达 → 跳过 MailProbe，不影响其他探针
- 本地文件写入失败 → 仅日志 warning，不中断 Tick

---

## 5. EmilyShell — 运维终端接口

### 5.1 定位

ProjectAgent 的**第二条人机交互通道**（补充 IM 消息和邮箱轮询），提供零依赖、实时、本地的运维终端。

### 5.2 通道对比

| 通道 | 实时性 | 可靠性 | 依赖 | 适用场景 |
|------|--------|--------|------|----------|
| IM 消息 | ⭐⭐⭐⭐ | ⭐⭐ | 第三方 IM | 用户日常交互 |
| 邮箱轮询 | ⭐⭐（延迟） | ⭐⭐⭐ | IMAP 服务器 | 日常告警、非紧急命令 |
| **EmilyShell** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 仅 Docker 可访问 | **紧急运维、调试、脚本** |

### 5.3 设计原则

| 原则 | 说明 |
|------|------|
| **零依赖** | 基于 Python `cmd.Cmd`，不引入第三方包 |
| **全权限** | 进入 Docker = 管理员，不做二次权限校验 |
| **双模式** | 交互 REPL（给人用）+ `-c` 单命令（给脚本/cron用） |
| **LLM 对话** | 用户输入自然语言 → DeepSeek 理解 → function calling 调用运维工具 |
| **分层风险** | 管理类命令需二次确认 |

### 5.4 运维工具集（6 个 Function Calling 工具）

| 工具名 | 功能 | 风险 |
|--------|------|------|
| `query_project_status` | 查询项目整体状态（节点分布/阶段/里程碑） | 只读 |
| `list_stale_nodes` | 列出卡滞节点（可指定天数阈值） | 只读 |
| `list_milestone_alerts` | 列出即将到期里程碑（可指定预警天数） | 只读 |
| `list_recent_findings` | 查看最近 N 条探针发现问题 | 只读 |
| `generate_weekly_report` | 生成 Markdown 周报并保存到 logs/ | 操作 |
| `show_system_info` | 显示 LLM/DB/节点等运行信息 | 只读 |

### 5.5 使用方式

```bash
# 交互 REPL 模式
docker exec -it emily-core python -m emily_core.project.agent_shell

# 单命令模式
docker exec emily-core python -m emily_core.project.agent_shell -c "项目进度怎么样？"

# Cron 定时调用
0 9 * * 1 docker exec emily-core python -m emily_core.project.agent_shell -c "生成周报"
```

### 5.6 内置命令

| 命令 | 功能 |
|------|------|
| `exit` / `quit` / `q` | 退出 |
| `!help` / `!h` | 显示帮助 |
| `!clear` / `!reset` | 清空对话记忆 |
| `!history` / `!hist` | 查看对话历史 |

---

## 6. 邮箱集成

### 6.1 需求

复用已有的邮箱控制模块（`emily_core/infrastructure/email/`）：

| 场景 | 触发条件 | 说明 |
|------|----------|------|
| 冷启动报告 | 系统启动完成 | ProjectAgent 用共享邮箱向管理员发送"Emily 实例已上线" |
| Order 轮询 | 定时（每 60s） | 检查共享邮箱收件箱，查找管理员发来的 `subject=order` 邮件，解析正文作为系统命令 |
| 告警通知 | CRITICAL 告警 | 当 IM/SSE 通道不可用时，作为 fallback 通知 |

### 6.2 约束

- ProjectAgent 与系统管理员默认共享邮箱
- `subject=order` 的邮件视为给 ProjectAgent 的系统命令
- 邮箱模块本身是无状态的（凭证由调用方提供）

---

## 7. 数据库需求

### 7.1 新增表

| 表名 | 用途 | Phase |
|------|------|-------|
| `ops_tick_log` | 每轮 Tick 的执行记录 | 1 |
| `ops_probe_execution` | 每个探针的执行详情 | 1 |
| `ops_finding` | 探针发现的问题 | 1 |
| `ops_mail_audit` | 邮件收发审计 | 1 |
| `ops_startup_report` | 系统冷启动报告 | 1 |

### 7.2 复用表

- `sm_nodes` — 状态机节点（SMNodeRepository 已有 `list_stale()` + `list_milestones_near_deadline()` 查询方法设计）
- `outbound_events` — 告警/通知消息推送

---

## 8. 非功能需求

### 8.1 可用性

- **fail-open**：ProjectAgent 初始化失败不阻塞 Core 启动
- **graceful degradation**：DB 不可达时探针结果落本地文件，不丢失
- **tick 异常恢复**：单次 Tick 异常不终止循环

### 8.2 可观测性

- 所有 Tick 执行记录可查询（`ops_tick_log`）
- 每个探针的执行详情可追溯（`ops_probe_execution`）
- ProjectAgent 状态通过 `GET /api/v1/health` 暴露

### 8.3 性能

- Tick 间隔默认 5min，每次 Tick 内探针串行执行
- 高频操作（卡滞检测、里程碑预警）纯 SQL 查询，不调用 LLM
- 告警冷却内存字典，单次 Tick 毫秒级完成

### 8.4 安全

- EmilyShell 进入 Docker 即管理员，不做二次权限校验
- 管理类命令（`purge_data`）需终端内二次确认
- 所有 Shell 操作可审计

---

## 9. 文件结构规划

```
emily-core/emily_core/project/
├── __init__.py                    # 包导出
├── project_agent.py               # ProjectAgent 主类（后台 Tick 循环）
├── project_agent_config.py        # ProjectAgentConfig dataclass
├── maintenance/
│   ├── __init__.py
│   └── stale_detector.py          # 卡滞检测 + 里程碑预警 + 告警冷却
├── health/
│   ├── __init__.py                # Phase 2 预留
│   ├── health_index.py            # Phase 2: 健康指数计算器
│   └── checks.py                  # Phase 2: 数据完整性校验
├── ops/
│   ├── __init__.py                # Phase 3 预留
│   ├── scheduler.py               # 探针调度器（Phase 1）
│   ├── config.py                  # OpsConfig
│   ├── probe_base.py              # 探针基类 + TickContext + ProbeFinding
│   ├── probe_registry.py          # 探针注册表
│   ├── probes/
│   │   ├── stale_probe.py         # 卡滞探针（Phase 1）
│   │   ├── health_probe.py        # 健康度探针（Phase 2）
│   │   ├── mailbox_probe.py       # 邮箱轮询探针（Phase 1）
│   │   └── dependency_probe.py    # 依赖链探针（Phase 2）
│   ├── models.py                  # ORM: OpsTickLog, OpsProbeExecution, OpsFinding, OpsMailAudit, OpsStartupReport
│   ├── repositories/
│   │   └── ops_repo.py            # OpsRepository: CRUD + 查询
│   └── persistence/
│       └── fallback_writer.py     # 本地 JSONL fallback
├── agent_shell/
│   ├── __init__.py                # 包导出
│   ├── __main__.py                # 启动入口（自举 Config/DB → REPL）
│   ├── shell.py                   # ProjectAgentShell(cmd.Cmd): LLM 对话 REPL + function calling
│   ├── tools.py                   # 6 个 Function Calling 工具定义 + ToolExecutor
│   └── formatter.py               # 终端输出格式化
```

---

## 10. 约束与兼容性

### 10.1 技术约束

| # | 约束 | 说明 |
|---|------|------|
| 1 | **分层不跳** | ProjectAgent → ops_scheduler → Repository → DB，同现有分层一致 |
| 2 | **sync Repository** | 遵循项目 `@staticmethod` + 可选 `session` 参数模式 |
| 3 | **业务内核独立** | 不 import `astrbot.*` |
| 4 | **复用现有组件** | SMNodeRepository、OutboundEventBus、Advisory Lock 均复用 |
| 5 | **Shell 独立进程** | EmilyShell 通过 Repository 直接访问 DB，不依赖 FastAPI 进程 |

### 10.2 与现有模块的关系

| 现有模块 | 关系 |
|----------|------|
| `PlanTaskScheduler` | 互补—时间驱动 vs 状态驱动；Phase 3 中 ProjectAgent 主动投递 PlanTask |
| `SessionAgent` | 无直接耦合—告警通过 OutboundEventBus 推送 |
| `WorkItemAgent` | 无直接耦合—ProjectAgent 不进入 PipelineBUS |
| `邮件控制模块` | 依赖—ProjectAgent 冷启动报告、Order 轮询、告警 fallback |
| `全景节点图 (SMNode)` | 依赖—SMNodeRepository 提供查询接口 |

---

## 11. 实施优先级建议

| 优先级 | 内容 | 理由 |
|--------|------|------|
| **P0** | Phase 1 骨架 + 卡滞检测 | 最小可用，立即解决"节点卡滞无人知"问题 |
| **P1** | Phase 1 里程碑预警 | 解决"里程碑过期无人提醒"问题 |
| **P2** | EmilyShell 基础版 | 提供紧急运维通道，不依赖 IM |
| **P3** | Phase 2 健康度检查 | 量化项目健康程度 |
| **P4** | Phase 3 主动投递 | 从"告警"升级到"闭环" |
| **P5** | Phase 4 多项目 | 远期扩展 |

---

*本需求规格基于以下文档整合编写：*
- *`需求文件/ProjectAgent/ProjectAgent_开发计划与实施报告.md`*
- *`需求文件/ProjectAgent/project-agentV2.md`*
- *`需求文件/ProjectAgent/project-agentV2_实施计划.md`*
- *`需求文件/ProjectAgent/project-agentV2_实施报告.md`*
- *`需求文件/运维模块/ops_scheduler_运维模块详细设计说明书.md`*
- *`需求文件/邮箱控制/邮箱控制模块-完整需求规格.md`*
- *`emily-data/prompts/project.md`*
