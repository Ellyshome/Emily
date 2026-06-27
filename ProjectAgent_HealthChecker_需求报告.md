# ProjectAgent HealthChecker 需求设计与实施需求规格说明书

> **文档版本**：v1.0
> **创建日期**：2026-06-27
> **归属模块**：ProjectAgent (Phase 2)
> **设计状态**：待评审通过
> **对应文档状态机模块**：健康检查与自动运维

---

## 📋 目录

1. [背景与目标](#1-背景与目标)
2. [核心需求清单](#2-核心需求清单)
3. [整体架构设计](#3-整体架构设计)
4. [数据模型设计](#4-数据模型设计)
5. [核心模块详细设计](#5-核心模块详细设计)
6. [告警规则定义](#6-告警规则定义)
7. [集成方案](#7-集成方案)
8. [实施计划](#8-实施计划)
9. [验收标准](#9-验收标准)

---

## 1. 背景与目标

### 1.1 背景

ProjectAgent 作为项目级自主 Agent，其核心职责之一是**自主化运维**。为了实现这一目标，它需要**实时掌握环境设施的运行状态。

当前状态：
- ✅ 业务节点状态监控已实现（StaleDetector）
- ❌ 基础设施健康状态监控缺失

**环境设施健康状况（数据库、Session 池、存储等）
**

**问题**：
1. 数据库离线、Session 池爆满、磁盘满等问题发生时，系统只能被动等待用户反馈
2. 缺乏历史趋势数据，无法进行根因分析
3. 冷启动时缺少完整性检查机制

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| **状态可观测** | 时间线性记录环境设施状态，支持历史复盘 |
| **异常可感知** | 超阈值主动告警，通知管理员 |
| **故障可自愈** | 典型故障场景下尝试自动修复 |
| **启动可验证** | 冷启动时执行完整性检查 |

---

## 2. 核心需求清单

### 需求 R1：状态记录与时间序列数据

| 项 | 详细要求 |
|----|---------|
| **R1.1** | 按时间线性记录环境设施状态 |
| **R1.2** | PG 断开连接的时间点与持续时长 |
| **R1.3** | 数据库流量指标（按时间切片） |
| **R1.4** | Session 池活跃数量变化 |
| **R1.5** | 存储容量使用率变化 |
| **R1.6** | 所有监控日志持久化存储，便于未来复盘分析 |

**非功能要求**：
- 时间片粒度：默认 60 秒
- 数据保留期：默认 30 天自动归档

### 需求 R2：阈值告警与自动修复

| 项 | 详细要求 |
|----|---------|
| **R2.1** | 支持多级别告警（INFO / WARNING / CRITICAL）
| **R2.2** | 连续 N 次触发才告警（防止抖动
| **R2.3** | 同一告警冷却机制（避免刷屏） |
| **R2.4** | 磁盘快满 → ProjectAgent 通知管理员人工处理 |
| **R2.5** | PG 离线重试 3 次无法恢复 → ProjectAgent 尝试介入修复 |
| **R2.6** | 告警记录持久化，支持确认/关闭

### 需求 R3：冷启动完整性检查

| 项 | 详细要求 |
|----|---------|
| **R3.1** | ProjectAgent 启动时执行一次性完整性检查 |
| **R3.2** | 检查数据库连接可用性 |
| **R3.3** | 检查消息总线连通性 |
| **R3.4** | 检查存储挂载路径可读写 |
| **R3.5** | 异常情况完整记录 |
| **R3.6** | 检查结果持久化，便于启动问题追溯 |

---

## 3. 整体架构设计

### 3.1 三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     ProjectAgent (Tick 循环)                   │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    HealthChecker (核心协调器)                  │ │
│  │                                                               │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │ │
│  │  │   Probes     │  │  Sliding Win  │  │  Rules Engine   │  │ │
│  │  │ (探针集合)   │  │ (滑动窗口)   │  │ (阈值/告警/修复)  │  │ │
│  │  │               │  │              │  │                   │  │ │
│  │  │ • DBProbe    │  │最近5次检测   │  │ • 阈值匹配        │  │ │
│  │  │ • SessionProbe│ │              │  │ • 告警决策        │  │ │
│  │  │ • DocProbe   │  │              │  │ • 修复动作编排    │  │ │
│  │  │ • StorageProbe│ │              │  │                   │  │ │
│  │  └──────────────┘  └──────────────┘  └───────────────────┘  │ │
│  │                           │                                   │ │
│  └───────────────────────────┼───────────────────────────────────┘ │
│                              │                                     │
│                              ▼                                     │
│                  ┌───────────────────────┐                        │
│                  │    持久化层 (PostgreSQL)│                        │
│                  │                       │                        │
│                  │  • health_metrics   (指标)  │                        │
│                  │  • health_alerts    (告警)  │                        │
│                  │  • health_actions  (修复)  │                        │
│                  └───────────────────────┘                        │
│                              │                                     │
│                              ▼                                     │
│                  ┌───────────────────────┐                        │
│                  │   消息总线        │                        │
│                  │  OutboundEventBus  │                        │
│                  └───────────────────────┘                        │
│                              │                                     │
│                              ▼                                     │
│                  ┌──────────────────────────┐                        │
│                  │  OpsRunner (Phase3) │  ← 自动创建运维任务    │
│                  └──────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 设计原则

| 原则 | 说明 |
|------|------|
| **内聚性** | 健康检查逻辑全部内聚在 HealthChecker 内部，不扩散到其他模块 |
| **轻量性** | 滑动窗口内存计算，不引入外部依赖 |
| **可扩展** | 新增探针只需添加一个 Probe 类，不修改核心流程 |
| **失败开放** | 告警规则可配置，无需重启即可 |

### 3.3 与现有模块的关系

```
ProjectAgent
    ├── StaleDetector (Phase1, 已完成) → 业务节点卡滞检测
    └── HealthChecker (Phase2, 新增) → 基础设施健康检测
        ├── Probes → 各类型探针
        │    ├── DBProbe          → 数据库
        │    ├── SessionPoolProbe → Session 池
        │    ├── StorageProbe     → 存储容量
        │    └── DocStoreProbe    → 文档仓库
        ├── MetricsSlidingWindow  → 内存滑动窗口
        ├── HealthRulesEngine → 规则引擎
        └── Repositories → 数据持久化
```

---

## 4. 数据模型设计

### 4.1 `health_metrics` — 时间序列指标表

**用途**：存储所有探针检测的原始指标数据（需求 R1）

```sql
CREATE TABLE health_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- 指标标识
    metric_name VARCHAR(100) NOT NULL,    -- "db_connection", "session_count", "storage_usage"
    component VARCHAR(100) NOT NULL,       -- "postgres", "session_pool", "document_store"
    
    -- 指标值 (支持多种类型)
    value_int INTEGER,                      -- 计数类
    value_float FLOAT,                      -- 百分比类
    value_bool BOOLEAN,                     -- 状态类
    value_json JSONB,                       -- 复杂结构 (流量明细)
    
    -- 时间维度 (时间切片)
    window_start TIMESTAMP NOT NULL,        -- 时间片起点
    window_end TIMESTAMP NOT NULL,          -- 时间片终点
    window_seconds INTEGER NOT NULL DEFAULT 60,  -- 时间片长度(秒)
    
    -- 辅助字段
    metadata JSONB DEFAULT '{}',            -- 额外信息 (错误信息, 连接串等)
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_health_metrics_time ON health_metrics(window_start);
CREATE INDEX idx_health_metrics_name ON health_metrics(metric_name);
CREATE INDEX idx_health_metrics_component ON health_metrics(component);
```

### 4.2 `health_alerts` — 告警记录表

**用途**：存储所有触发的告警（需求 R2）

```sql
CREATE TABLE health_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    alert_type VARCHAR(100) NOT NULL,       -- "storage_full", "db_connection_lost"
    severity VARCHAR(20) NOT NULL,          -- "INFO" / "WARNING" / "CRITICAL"
    message TEXT NOT NULL,
    
    -- 触发条件快照
    threshold_value FLOAT,                         -- 触发阈值
    actual_value FLOAT,                      -- 实际值
    consecutive_count INTEGER,               -- 连续触发次数
    
    -- 告警生命周期
    status VARCHAR(20) DEFAULT 'OPEN',       -- "OPEN" / "ACKNOWLEDGED" / "RESOLVED"
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMP,
    resolved_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_health_alerts_status ON health_alerts(status);
CREATE INDEX idx_health_alerts_severity ON health_alerts(severity);
CREATE INDEX idx_health_alerts_created ON health_alerts(created_at);
```

### 4.3 `health_actions` — 自动修复动作表

**用途**：记录自动修复动作的执行情况（需求 R2.5）

```sql
CREATE TABLE health_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    alert_id UUID REFERENCES health_alerts(id),
    action_type VARCHAR(100) NOT NULL,       -- "db_reconnect", "restart_service"
    action_params JSONB DEFAULT '{}',        -- 执行参数
    result_status VARCHAR(20),               -- "SUCCESS" / "FAILED" / "SKIPPED"
    result_message TEXT,
    
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_health_actions_alert ON health_actions(alert_id);
CREATE INDEX idx_health_actions_result ON health_actions(result_status);
```

### 4.4 `health_cold_start_reports` — 冷启动检查报告

**用途**：记录每次冷启动完整性检查结果（需求 R3）

```sql
CREATE TABLE health_cold_start_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    agent_instance_id VARCHAR(100),              -- 实例标识
    passed BOOLEAN NOT NULL,
    issues JSONB DEFAULT '[]',             -- 发现的问题列表
    check_summary TEXT,                          -- 检查摘要
    
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 5. 核心模块详细设计

### 5.1 目录结构

```
emily-core/emily_core/project/
├── __init__.py
├── project_agent.py              (修改: 集成 HealthChecker)
├── project_agent_config.py       (修改: 新增健康检查配置)
│
└── maintenance/
    ├── __init__.py
    ├── stale_detector.py       (已存在)
    └── health_checker/        ← 新增目录
        ├── __init__.py
        ├── health_checker.py    ← 核心协调器
        ├── probes.py             ← 探针集合
        ├── sliding_window.py    ← 滑动窗口
        ├── rules_engine.py    ← 规则引擎
        └── repositories.py    ← 数据持久化
```

### 5.2 Probes 探针模块

**职责**：执行具体的检测逻辑，返回标准化的检测结果

**基类设计**：

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ProbeResult:
    """单次探针检测结果"""
    component: str                    # 组件名称
    metric_name: str                # 指标名
    healthy: bool                    # 是否健康
    value_int: int | None = None     # 整数值
    value_float: float | None = None  # 浮点值
    value_bool: bool | None = None   # 布尔值
    metadata: dict | None = None
    error_message: str | None = None
    timestamp: datetime = None
```

**探针实现**：

| 探针类 | 检测内容 | 指标 |
|--------|---------|------|
| `DBProbe` | 数据库连接检测 | 连接状态、延迟、活跃连接数、查询流量 |
| `SessionPoolProbe` | Session 池 | 活跃会话数、空闲会话数、峰值 |
| `StorageProbe` | 存储容量 | 各路径使用率、剩余空间 |
| `DocStoreProbe` | 文档仓库 | 连通性、已用容量、总容量 |

### 5.3 MetricsSlidingWindow 滑动窗口

**职责**：内存中保留最近 N 次检测结果，用于趋势分析

**核心功能**：
```python
class MetricsSlidingWindow:
    - add(result: ProbeResult) →  添加检测结果
    - get_stats(component, metric) → 获取窗口统计

WindowStats:
    - consecutive_failures: int      连续失败次数
    - consecutive_successes: int    连续成功次数
    - trend: str                    improving / worsening / stable
    - avg_value / max_value / min_value
```

**趋势判断算法**：
- 比较窗口前三分之一与后三分之一的平均值
- 变化率 < 5% → stable
- 否则 → improving / worsening

### 5.4 HealthRulesEngine 规则引擎

**职责**：基于滑动窗口统计，判断是否触发告警

**规则定义**：

```python
@dataclass
class AlertRule:
    rule_id: str                           # 规则唯一标识
    component: str                          # 适用组件
    metric_name: str                       # 适用指标
    severity: str                          # INFO / WARNING / CRITICAL
    message_template: str                  # 消息模板
    condition: Callable[[WindowStats], bool]  # 触发条件
    consecutive_threshold: int = 1            # 连续 N 次才告警
    cooldown_minutes: int = 30             # 告警冷却时间
```

### 5.5 HealthChecker 核心协调器

**核心流程（每次 Tick 调用一次）：

```
Step 1: 执行所有探针检测
    ↓
Step 2: 更新内存滑动窗口
    ↓
Step 3: 持久化指标到数据库
    ↓
Step 4: 规则引擎评估告警
    ↓
Step 5: 持久化告警 + 推送通知
    ↓
Step 6: 尝试自动修复
```

---

## 6. 告警规则定义

### Phase 2 预置规则（后续可配置化扩展

| 规则 ID | 组件 | 指标 | 阈值 | 级别 | 连续次数 | 冷却 |
|---------|------|------|------|------|----------|------|
| db_connection_lost | postgres | connection | 失败 | CRITICAL | 3 次 | 10 分钟 |
| db_latency_high | postgres | latency | > 200ms | WARNING | 3 次 | 15 分钟 |
| disk_usage_warning | storage | disk_usage | >80% | WARNING | 2 次 | 60 分钟 |
| disk_usage_critical | storage | disk_usage | >90% | CRITICAL | 1 次 | 30 分钟 |
| session_pool_high | session_pool | active_sessions | >50 | WARNING | 3 次 | 15 分钟 |
| session_pool_critical | session_pool | active_sessions | >100 | CRITICAL | 2 次 | 10 分钟 |
| doc_store_offline | doc_store | connection | 失败 | WARNING | 2 次 | 30 分钟 |

---

## 7. 集成方案

### 7.1 与 ProjectAgent 的集成

```python
class ProjectAgent:
    def __init__(self, ...):
        # Phase 1 (已存在)
        self._stale_detector = StaleDetector(...)
        
        # Phase 2 新增
        self._health_checker = HealthChecker(
            probes=self._build_probes(),
            outbound_bus=outbound_bus,
            db_url=config.db_url,
            window_size=5,
        )
    
    async def _tick(self):
        """每次 Tick 执行检查"""
        # 1. Phase 1: 业务节点卡滞检测
        stale_result = await self._stale_detector.run()
        
        # 2. Phase 2: 健康检查 (新增)
        health_result = await self._health_checker.run_check()
        
        return {
            "stale_check": stale_result,
            "health_check": health_result,
        }
```

### 7.2 与消息总线的集成

告警推送格式：

```python
{
    "title": "[CRITICAL] 系统健康告警",
    "message": "数据库连续 3 次连接失败，请立即检查！",
    "metadata": {
        "rule_id": "db_connection_lost",
        "severity": "CRITICAL",
        "component": "postgres",
        "actual_value": 0,
        "timestamp": "2026-06-27T10:30:00"
    }
}
```

---

## 8. 实施计划

### Phase 2 阶段划分

| 阶段 | 任务 | 工作量 | 优先级 |
|------|------|--------|--------|
| **8.1 | 数据库表结构创建 | 0.5d | P0 |
| **8.2** | Probe 探针实现 | 1d | P0 |
| **8.3** | SlidingWindow 滑动窗口 | 0.5d | P0 |
| **8.4** | RulesEngine 规则引擎 | 0.5d | P0 |
| **8.5** | Repository 持久化层 | 0.5d | P1 |
| **8.6** | HealthChecker 协调器 | 0.5d | P0 |
| **8.7** | ProjectAgent 集成 | 0.5d | P0 |
| **8.8** | 冷启动完整性检查 | 0.5d | P1 |
| **8.9** | 单元测试 + 集成测试 | 1d | P1 |

**总计**：约 5.5 人天

### 实施依赖关系

```
8.1 (DB表)
   ↓
8.2 + 8.3 + 8.4 + 8.5 (并行)
   ↓
8.6 (HealthChecker 集成所有模块)
   ↓
8.7 + 8.8 (并行: Agent 集成 + 冷启动检查)
   ↓
8.9 (测试)
```

---

## 9. 验收标准

### 功能验收

| 验收项 | 通过标准 |
|---------|---------|
| R1.1 状态记录 | 能正确记录 PG 连接断开/恢复的时间点与时长 |
| R1.2 时间切片 | 数据库流量按 60 秒切片正确记录 |
| R1.3 Session 变化 | Session 活跃数量变化可历史可查 |
| R1.4 日志持久化 | 指标数据正确写入 health_metrics 表 |
| R2.1 多级告警 | INFO/WARNING/CRITICAL 三级告警正确区分 |
| R2.2 连续触发 | 连续 3 次失败才告警，避免单次抖动不误报 |
| R2.3 冷却机制 | 同一告警 30 分钟内不重复推送 |
| R2.4 通知管理员 | 告警通过消息总线正确推送 |
| R2.5 自动修复 | PG 离线重试 3 次后触发修复动作 |
| R3.1 冷启动检查 | 启动时执行 DB/总线/存储三项检查 |
| R3.2 异常记录 | 发现的问题完整记录在冷启动报告表 |

### 非功能验收

| 验收项 | 通过标准 |
|---------|---------|
| 性能影响 | 单次健康检查耗时 < 500ms |
| 资源占用 | 滑动窗口内存占用 < 10MB |
| 故障容错 | 单个探针失败不影响整体流程 |
| 数据保留 | 30 天数据自动清理归档 |

---

## 📎 附录：相关文档

| 文档 | 路径 |
|------|------|
| ProjectAgent 架构设计 | 需求文件/ProjectAgent/ |
| 全局状态机设计 | `需求文件/全局状态机/ |
| 权限系统设计 | `需求文件/权限管理系统/` |
