# 计划：拆分 OpsMonitor 需求 V3

## Context

OpsMonitor 需求 V3 是一份独立模块需求，但经过分析，其功能点按触发方式分属不同类别：
- **时间驱动型**（凌晨复盘、晨报推送、冷启动报告）→ 适合作为 Scheduler Handler
- **按需服务型**（Digest）→ 用户要求移除
- **基础设施型**（推送通道）→ 跨模块共享

因此将 OpsMonitor V3 按需求类别拆分为多个独立需求文件，每个文件自包含，明确与 Scheduler 框架（模块拆分改造需求.md 中定义）的挂载关系。

## 拆分方案

### 文件 1：`需求文件/OpsMonitor-/凌晨复盘与晨报需求.md`

**来源**：OpsMonitor V3 §4.1 + §4.1.1~§4.1.7 + §6.1(morning_briefs 表) + §7(相关配置项)

**内容要点**：
- 定位：NightlyReviewHandler + MorningBriefDeliveryHandler 的完整业务规格
- 挂载关系：Scheduler 框架（模块拆分改造需求.md §4），action_type = `generate_morning_report`，CRON: `0 3 * * *`（复盘）+ `0 9 * * *`（推送）
- 凌晨复盘三阶段流程（数据采集 → LLM 批量评估 → 晨报生成）
- 复盘输入（节点档案上下文格式）
- 风险等级定义（none/low/medium/high/unknown + 晨报呈现规则）
- 晨报格式与示例
- 晨报推送流程（OutboundEventBus + 状态流转 pending→delivered/failed）
- 降级策略（LLM 不可用、部分超时、推送失败、系统挂了补执行、数据不足）
- morning_briefs 表结构
- LLM prompt 模板需求（ops_monitor_review.md / ops_monitor_brief.md）
- 配置项（review_hour、brief_delivery_hour、llm_timeout 等）
- 文件变更清单

### 文件 2：`需求文件/OpsMonitor-/冷启动报告需求.md`

**来源**：OpsMonitor V3 §4.0 + §6.1(ops_startup_report 表) + §7(相关配置项)

**内容要点**：
- 定位：StartupReportHandler 的业务规格
- 挂载关系：Scheduler 框架，action_type = `startup_report`，ONCE 类型，Core init 时 `trigger_job_manually` 触发
- 信息采集清单（环境 + 项目状态）
- 输出：写 ops_startup_report 表 + 可选发邮件
- ops_startup_report 表结构
- 配置项（startup_report_enabled、admin_email）
- 文件变更清单

### 文件 3：`需求文件/OpsMonitor-/推送通道需求.md`

**来源**：OpsMonitor V3 §4.3 + §6.2(users 新增 primary_channel 字段)

**内容要点**：
- 定位：跨模块推送通道基础设施，供所有需要主动推送的 Handler/Service 调用
- PushChannelResolver 接口设计
- users 表新增 primary_channel 字段
- 当前支持通道（QQ / Email）及默认值
- 未来扩展预留
- 文件变更清单

### 移除的内容

| 内容 | 原因 |
|---|---|
| §4.2 项目 Digest（整章） | 用户要求移除 |
| §5 与 SessionAgent 的集成（整章） | 依赖 Digest，Digest 移除后此章无独立意义 |
| §3.1 架构总图中的 OpsMonitor 模块 | 不再作为独立模块存在 |
| §3.3 唤醒循环伪代码 | 改由 SchedulerEngine 统一管理 |

### 每个文件的通用结构

```
1. 定位与挂载关系（明确 Scheduler Handler 还是基础设施）
2. 业务逻辑详述
3. 数据库变更
4. 配置项
5. 降级/容错策略
6. 文件变更清单
7. 与其他需求文档的引用关系
```

## 执行步骤

1. 创建 `凌晨复盘与晨报需求.md` — 从 V3 提取 §4.1 全部内容，重写挂载关系
2. 创建 `冷启动报告需求.md` — 从 V3 提取 §4.0 全部内容，重写挂载关系
3. 创建 `推送通道需求.md` — 从 V3 提取 §4.3 + §6.2，重写为基础设施规格
4. 原文件 `OpsMonitor-需求_V3.md` 保留不动（作为历史参考）

## 验证

- 对比三份新文档与 V3 原文，确认无业务逻辑遗漏（Digest 除外）
- 确认每个 Handler 的 action_type 与模块拆分改造需求.md §4.4.3 内置 Handler 列表一致
- 确认配置项、表结构、字段定义完整迁移
