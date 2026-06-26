# ProjectAgent — 项目级自主 Agent 开发计划与实施报告

> **最后更新**：2026-06-27 | **版本**：v0.7.0-Phase1 | **状态**：Phase 1 完成，Phase 2/3/4 规划中

***

## 1. 背景与动机

### 1.1 问题

Emily v0.6.0 有两层 Agent 覆盖：

| Agent           | 作用域                   | 触发方式                |
| --------------- | --------------------- | ------------------- |
| `SessionAgent`  | 会话级（per-conversation） | 被动——用户发消息           |
| `WorkItemAgent` | 任务级（per-task）         | 被动——PipelineBUS 推给它 |

**项目级作用域无人值守**。具体表现为：

- 状态机 \~90 个节点全被动触发——节点卡滞 30 天无人知晓
- 没有项目健康度诊断——只看容器 `GET /health`，不看项目是否健康
- PlanTaskScheduler 只管 plan\_task 实例的提醒/过期/归档，不碰状态机
- 无人主动扫描：依赖链断裂、里程碑逾期、数据完整性问题

### 1.2 决策

新增 **ProjectAgent**（项目级自主 Agent），填补第三层作用域。决策记录见 [开发记录 ADR-E08](../../docs/开发记录.md#adr-e08新增项目级-agentprojectagent-三层作用域补齐)。

***

## 2. 架构设计

### 2.1 三层 Agent 作用域

```
项目级  → ProjectAgent   (新增 · 后台 Tick 循环 · 状态机全貌)
会话级  → SessionAgent   (已有 · per-conversation · TTL 10min)
任务级  → WorkItemAgent  (已有 · global singleton · 4-node PipelineBUS)
```

### 2.2 职责三角

```
         ┌──────────────────┐
         │   状态机主动维护    │  ← 卡滞检测 / 延期预警 / 依赖校验 / 里程碑看守
         │   (Phase 1 ✅)    │
         └────────┬─────────┘
                  │
    ┌─────────────┼─────────────┐
    │             │             │
    ▼             ▼             ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│ 健康检查│  │  异常检测  │  │ 自动运维  │
│ Phase 2│  │  Phase 1  │  │ Phase 3  │
│ 预留   │  │  ✅ 完成  │  │  预留    │
└────────┘  └──────────┘  └──────────┘
```

### 2.3 与现有组件的关系

```
EmilyCore
  ├── PlanTaskScheduler    (时间驱动 — plan_task 实例生命周期)
  ├── SessionPoolManager   (会话池)
  ├── PipelineBUS          (任务总线)
  └── ProjectAgent          (状态驱动 — 项目级状态机维护 + 健康 + 自动运维)
        │
        ├── 复用 SMNodeRepository   (查询卡滞节点 / 里程碑)
        ├── 复用 OutboundEventBus   (推送告警消息)
        ├── 复用 advisory-lock      (多进程 Tick 防重)
        └── Phase 3: 调用 PlanTaskService.create_instance() (主动投递可追踪任务)
```

**与 PlanTaskScheduler 的职责边界**：

| 触发源   | 归属                | 示例                      |
| ----- | ----------------- | ----------------------- |
| 时间到了  | PlanTaskScheduler | "每周五的周报该交了" → 提醒        |
| 状态机异常 | **ProjectAgent**  | "节点 3.5 卡了 30 天" → 排查任务 |

### 2.4 生命周期

```
CREATED → STARTING → ACTIVE → STOPPING → STOPPED
                    │
                    ├─ 按 tick_interval 循环 _tick()
                    │
                    └─ 异常 → DEGRADED（降级运行，不崩溃）
```

- **创建时机**：`EmilyCore._ensure_initialized()` → `_init_project_agent()` → `asyncio.ensure_future(start())`
- **Tick 间隔**：300s（5 分钟），可按需配置
- **停止**：`shutdown()` → 完成当前 tick → 标记 STOPPED

***

## 3. Phase 1 — 骨架 + 卡滞检测（✅ 已完成）

> 实施日期：2026-06-26

### 3.1 交付物

#### 3.1.1 新增文件（7 个）

```
emily-core/emily_core/project/
├── __init__.py                    # 包导出
├── project_agent.py               # ProjectAgent 主类（后台 Tick 循环）
├── project_agent_config.py        # ProjectAgentConfig dataclass
├── maintenance/
│   ├── __init__.py
│   └── stale_detector.py          # 卡滞检测 + 里程碑预警
├── health/
│   └── __init__.py                # Phase 2 预留
└── ops/
    └── __init__.py                # Phase 3 预留
```

#### 3.1.2 修改文件（5 个）

| 文件                                        | 改动摘要                                                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `emily_core/config.py`                    | +5 配置字段（project\_agent\_enabled / tick\_seconds / stale\_threshold\_days / deadline\_warn\_days / alert\_cooldown\_hours） |
| `emily_core/repositories/sm_node_repo.py` | +2 查询方法（list\_stale / list\_milestones\_near\_deadline）                                                                   |
| `emily_core/__init__.py`                  | +`_project_agent` 属性 + `_init_project_agent()` + `health()` 集成                                                            |
| `docs/代码文件目录.md`                          | +project/ 包文档条目                                                                                                           |
| `docs/业务模块与运转全景.md`                       | +5.12 ProjectAgent 模块清单                                                                                                   |
| `docs/开发记录.md`                            | +ADR-E08 + 版本历史更新                                                                                                         |

### 3.2 配置项（config.py）

```python
# ---- 项目级 Agent (ProjectAgent) ----
project_agent_enabled: bool = True
"""项目级 Agent 总开关（状态机主动维护 + 健康度检查 + AI 自动运维）"""

project_agent_tick_seconds: int = 300
"""ProjectAgent 调度循环间隔（秒），默认 300 秒（5 分钟）"""

project_agent_stale_threshold_days: int = 14
"""节点卡滞判定阈值（天）。IN_PROGRESS/BLOCKED 超过此天数未变化视为卡滞"""

project_agent_deadline_warn_days: int = 7
"""milestone 节点到期前 N 天开始预警"""

project_agent_alert_cooldown_hours: int = 24
"""同一节点同一问题的告警冷却时间（小时），避免重复推送"""
```

### 3.3 核心逻辑

#### 3.3.1 ProjectAgent 主循环

```
_tick()  ← 每 300s 执行一次，受 PostgreSQL Advisory Lock 保护
  │
  └── _do_tick()
        │
        ├── StaleDetector.run()
        │     ├── _detect_stale_nodes()
        │     │     └── SMNodeRepository.list_stale(
        │     │           statuses=["IN_PROGRESS","BLOCKED","DELAYED"],
        │     │           older_than_iso=now - 14 days
        │     │         )
        │     │         → 推送告警 (含 24h 冷却)
        │     │
        │     └── _detect_milestone_warnings()
        │           └── SMNodeRepository.list_milestones_near_deadline(
        │                 now_iso=now, warn_before_days=7
        │               )
        │               → 推送预警 (含 24h 冷却)
        │
        ├── Phase 2: health checker   ← TODO
        └── Phase 3: ops runner       ← TODO
```

#### 3.3.2 StaleDetector 详情

**卡滞检测**（`_detect_stale_nodes`）：

- 查询条件：`status IN ('IN_PROGRESS', 'BLOCKED', 'DELAYED') AND updated_at < now - 14d`
- 生成告警消息模板：

```
【节点卡滞预警】
节点「地块尽职调查与可研启动」（1.1）处于「进行中」状态已超过 30 天未更新。
负责人：投资部
所属阶段：阶段1
最后更新时间：2026-05-27T10:30:00
请确认进度或更新状态。
```

**里程碑预警**（`_detect_milestone_warnings`）：

- 查询条件：`is_milestone=true AND planned_end_date 在 7 天内 AND status 非 COMPLETED`
- 生成预警消息模板：

```
【里程碑预警】
里程碑节点「施工图审查完成」（3.8）计划截止日期为 2026-07-03，距今不足 7 天。
当前状态：进行中
负责人：设计部
请关注进度，确保按期完成。
```

**告警冷却**：

- 内存级冷却字典：`{ "stale:1.1" → "2026-06-26T10:00:00" }`
- 同一 `node_id:issue_type` 24h 内不重复推送
- 进程重启后冷却重置（Phase 1 可接受；Phase 3 可升级为 DB 持久化）

#### 3.3.3 SMNodeRepository 新增方法

```python
@staticmethod
def list_stale(*, statuses: list[str], older_than_iso: str,
               session: Optional[Session] = None) -> list[SMNode]:
    """Find nodes stuck in given statuses whose updated_at is older than threshold."""

@staticmethod
def list_milestones_near_deadline(*, now_iso: str, warn_before_days: int,
                                  session: Optional[Session] = None) -> list[SMNode]:
    """Find milestone nodes whose planned_end_date is within warn_before_days of now."""
```

### 3.4 集成点

```python
# emily_core/__init__.py — _ensure_initialized() 调用顺序
self._init_phase_b_deps()
self._init_phase_c_deps()
self._init_plan_task_module()
self._init_state_machine_module()     # ← ProjectAgent 的依赖
self._init_project_agent()            # ← 新增：状态机之后，权限之前
self._init_permission_module()
self._build_pipeline_bus()
self._build_session_pool()

# health() 返回增加 project_agent 状态
def health(self) -> dict:
    result = { ... }
    if self._project_agent is not None:
        result["project_agent"] = self._project_agent.status()
    return result
```

**初始化失败不阻塞 Core**（fail-open）：如果 `_sm_node_repo` 未就绪，仅记录 warning 并跳过。

### 3.5 Phase 1 不包含的内容

- LLM 调用（所有检测纯规则/SQL）
- 健康指数计算
- 自动创建计划任务
- 周期性报告生成
- 数据完整性校验
- DB 持久化告警冷却记录

***

## 4. Phase 2 — 健康度检查（规划中）

### 4.1 目标

将项目健康度量化为可追踪的指标，提供项目级"体检报告"。

### 4.2 计划模块

#### 4.2.1 健康指数计算（`health/health_index.py`）

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

#### 4.2.2 数据完整性校验（`health/checks.py`）

| 校验项      | 说明                                                |
| -------- | ------------------------------------------------- |
| FK 有效性   | `project_id` 指向不存在的 project、事件关联不存在的 conversation |
| 节点-阶段一致性 | `sm_nodes.stage_id` 对应 `sm_stages.stage_id` 存在    |
| 依赖链完整性   | `sm_node_dependencies` 中 from/to 节点均存在且未删除        |
| 导入覆盖率    | `全景节点.md` 定义的节点数 vs `sm_nodes` 实际导入数              |
| 孤立节点     | 在某阶段中无任何依赖关系的节点                                   |
| 循环依赖检测   | BFS/DFS 检测 sm\_node\_dependencies 图中的环            |
| 关键路径阻塞   | `critical_path` JSON 中任一节点处于 BLOCKED/DELAYED      |

#### 4.2.3 依赖链校验（`maintenance/dependency_validator.py`）

- 孤立节点检测（无前置依赖且无后续依赖的非起点/终点节点）
- 循环依赖检测（DFS 着色法）
- 缺失依赖检测（依赖的节点 ID 在 sm\_nodes 中不存在）

### 4.3 输出形式

- 每次 Tick 静默执行检查
- 发现问题 → 通过 OutboundEventBus 推送告警（同 Phase 1 模式）
- 每天/每周生成健康报告 → 通过消息通道推送到指定群/人

***

## 5. Phase 3 — AI 自动运维 + 主动投递（规划中）

### 5.1 目标

从"发现问题→告警"升级为"发现问题→投递任务→跟踪闭环"。

### 5.2 计划模块

#### 5.2.1 周期性报告生成（`ops/report_generator.py`）

- **每日简报**（纯规则，成本极低）：完成/新增阻塞/新增延期 三项变化
- **每周深度报告**（LLM 驱动）：瓶颈分析、风险趋势、建议措施
- 推送至指定群/人（通过 OutboundEventBus → SSE → IM）

#### 5.2.2 智能建议（`ops/suggestion.py`）

- LLM 分析阻塞节点上下文（节点名称、依赖链、所属阶段、`block_reason`）
- 生成解除阻塞的 2-3 条建议措施
- 推送至节点负责人 + 阶段负责人

#### 5.2.3 自动升级（`ops/escalation.py`）

- 阻塞超过 N 天未解除 → 自动通知上级
- 里程碑逾期超过 M 天 → 自动提升优先级 + 通知 PM
- 复用 PlanTaskScheduler 的 `escalate_to_supervisor` 模式

#### 5.2.4 主动投递（`ops/task_dispatcher.py`）

> **核心创新**：ProjectAgent 是系统中唯一"不等人来问、自己看、自己决定要不要行动"的角色。

**触发源 → 投递策略**：

| 触发条件        | 投递动作                                       |
| ----------- | ------------------------------------------ |
| 节点卡滞 >30 天  | 自动创建 plan\_task："请确认节点「X」当前进度" → 分配给 owner |
| 里程碑逾期       | 自动创建升级任务 → 分配给 owner + supervisor          |
| 项目健康指数 < 30 | 生成诊断报告 → 推送给 PM，附带建议任务                     |
| 依赖链即将就绪     | 提前通知下游节点 owner："前置节点即将完成，请准备启动"            |
| 依赖链断裂       | 创建修复任务 → 分配给 PM："节点 X 的依赖节点 Y 不存在/已删除"     |

**实现路径**：

```
ProjectAgent._do_tick()
  │
  └── StaleDetector → 发现卡滞节点
        │
        ├── Phase 1 (当前): OutboundEventBus.publish("reply", ...) → 一次性告警
        │
        └── Phase 3 (规划): PlanTaskService.create_instance(...) → 正式计划任务
              │
              └── PlanTaskScheduler 接管后续 → 提醒 / 逾期 / 归档 / 升级
```

**职责分工**：

- **ProjectAgent**：发现 + 决策 + 创建任务（只做一次）
- **PlanTaskScheduler**：接管任务生命周期（持续跟踪）

#### 5.2.5 预判性操作（`ops/anticipator.py`）

- 当前置依赖全部进入 COMPLETED → 提前提醒下游节点 owner 准备
- 阶段完成率 > 90% → 提醒下一阶段负责人提前筹备
- 多个并行节点同步延期 → 分析是否关键路径瓶颈

### 5.3 LLM 调用策略

| 频率             | 操作      | LLM？ | 说明              |
| -------------- | ------- | ---- | --------------- |
| 每次 tick (5min) | 卡滞检测    | ❌    | 纯 SQL，零 LLM 成本  |
| 每次 tick        | 里程碑预警   | ❌    | 纯 SQL，零 LLM 成本  |
| 每次 tick        | 数据完整性校验 | ❌    | 纯 SQL，零 LLM 成本  |
| 每小时            | 健康指数计算  | ❌    | 纯公式，零 LLM 成本    |
| 每天             | 每日简报    | ❌    | 纯模板，零 LLM 成本    |
| 每周             | 深度报告    | ✅    | LLM 分析瓶颈/趋势/建议  |
| 按需（发现异常时）      | 智能建议    | ✅    | LLM 分析阻塞上下文     |
| 按需             | 主动投递决策  | ✅    | LLM 判断是否需要/如何投递 |

**成本控制**：高频操作全部纯规则/SQL，LLM 仅用于低频深度分析和按需触发。

***

## 6. Phase 4 — 多项目支持（远期规划）

### 6.1 背景

当前 `projects` 表存在但未使用（无 Repository/Service/API），状态机为全局单实例。当系统需要同时管理多个项目时，需要：

### 6.2 计划

1. **激活 projects 表**：创建 ProjectRepository / ProjectService / Project API
2. **状态机 per-project 化**：`sm_nodes` / `sm_stages` 增加 `project_id` FK 隔离
3. **ProjectAgent per-project 池化**：每个活跃项目一个 ProjectAgent 实例，共享 Tick 循环或独立循环
4. **跨项目聚合**：Dashboard Agent（或 ProjectAgent 的上级 Agent）进行跨项目健康对比/资源调度

***

## 7. 文件清单

### 7.1 新增文件

| 文件                                                            | 行数  | 说明                                             |
| ------------------------------------------------------------- | --- | ---------------------------------------------- |
| `emily-core/emily_core/project/__init__.py`                   | 18  | 包导出                                            |
| `emily-core/emily_core/project/project_agent.py`              | 152 | ProjectAgent 主类：Tick 循环 + advisory-lock + 生命周期 |
| `emily-core/emily_core/project/project_agent_config.py`       | 44  | ProjectAgentConfig dataclass：从全局 Config 提取     |
| `emily-core/emily_core/project/maintenance/__init__.py`       | 14  | maintenance 子模块导出                              |
| `emily-core/emily_core/project/maintenance/stale_detector.py` | 255 | 卡滞检测 + 里程碑预警 + 告警冷却                            |
| `emily-core/emily_core/project/health/__init__.py`            | 7   | Phase 2 预留                                     |
| `emily-core/emily_core/project/ops/__init__.py`               | 8   | Phase 3 预留                                     |

**总计**：7 文件，\~500 行

### 7.2 修改文件

| 文件                                        | 新增行数 | 说明                |
| ----------------------------------------- | ---- | ----------------- |
| `emily_core/config.py`                    | +11  | 5 个配置字段           |
| `emily_core/repositories/sm_node_repo.py` | +62  | 2 个查询方法           |
| `emily_core/__init__.py`                  | +37  | 初始化集成 + health 扩展 |
| `docs/代码文件目录.md`                          | +15  | package 文档        |
| `docs/业务模块与运转全景.md`                       | +14  | 5.12 模块清单         |
| `docs/开发记录.md`                            | +13  | ADR-E08           |

***

## 8. 配置参考

```json
{
  "project_agent_enabled": true,
  "project_agent_tick_seconds": 300,
  "project_agent_stale_threshold_days": 14,
  "project_agent_deadline_warn_days": 7,
  "project_agent_alert_cooldown_hours": 24
}
```

**环境变量覆盖**（标准 env→config 映射）：

| 环境变量                                       | 配置项                                  |
| ------------------------------------------ | ------------------------------------ |
| `EMILY_PROJECT_AGENT_ENABLED`              | `project_agent_enabled`              |
| `EMILY_PROJECT_AGENT_TICK_SECONDS`         | `project_agent_tick_seconds`         |
| `EMILY_PROJECT_AGENT_STALE_THRESHOLD_DAYS` | `project_agent_stale_threshold_days` |

***

## 9. 运维命令

```bash
# 检查 ProjectAgent 运行状态
curl http://localhost:18080/api/v1/health | jq '.project_agent'

# 关闭 ProjectAgent（设置环境变量后重启容器）
EMILY_PROJECT_AGENT_ENABLED=false docker compose restart emily-core

# 查看 ProjectAgent 日志
docker logs --tail 50 emily-core 2>&1 | grep project_agent
```

***

## 10. 相关文档

| 文档            | 路径                                                         |
| ------------- | ---------------------------------------------------------- |
| ADR 决策记录      | [docs/开发记录.md](../../docs/开发记录.md)                         |
| 代码文件目录        | [docs/代码文件目录.md](../../docs/代码文件目录.md)                     |
| 业务模块全景        | [docs/业务模块与运转全景.md](../../docs/业务模块与运转全景.md)               |
| 全局状态机需求       | [需求文件/全局状态机/全局状态机需求-架构师完善版.md](../全局状态机/全局状态机需求-架构师完善版.md) |
| 项目主 CLAUDE.md | [CLAUDE.md](../../CLAUDE.md)                               |

