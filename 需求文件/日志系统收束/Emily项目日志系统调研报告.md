# Emily 项目日志系统调研报告

> **生成日期**：2026-06-27
> **调研范围**：Emily 全项目日志系统
> **文档版本**：v1.0

---

## 📋 目录

1. [日志系统概览](#1-日志系统概览)
2. [Python 应用日志](#2-python-应用日志)
3. [数据库持久化日志](#3-数据库持久化日志)
4. [文件型业务日志](#4-文件型业务日志)
5. [Docker 容器日志](#5-docker-容器日志)
6. [日志相关配置项](#6-日志相关配置项)
7. [日志目录结构](#7-日志目录结构)
8. [现状评估与建议](#8-现状评估与建议)

---

## 1. 日志系统概览

Emily 项目采用**四层日志架构**，覆盖从开发调试到业务审计的全场景：

| 层级 | 日志类型 | 存储方式 | 主要用途 |
|------|---------|---------|---------|
| **L1** | Python 应用日志 | 文件 + 控制台 | 系统调试、错误追踪、运行监控 |
| **L2** | 数据库业务日志 | PostgreSQL 表 | 业务审计、性能分析、问题定位 |
| **L3** | 文件型业务日志 | Markdown 文件 | 项目追踪、知识沉淀、问题清单 |
| **L4** | Docker 容器日志 | Docker json-file | 容器运维、排障诊断 |

---

## 2. Python 应用日志

### 2.1 核心配置

**配置位置**：`emily-core/emily_core/bootstrap.py` → `_setup_logging()`

| 配置项 | 值 | 说明 |
|--------|---|------|
| **日志框架** | Python 标准 `logging` 模块 | - |
| **根 Logger** | `emily` | 所有子 Logger 继承配置 |
| **日志级别** | `INFO` | 可通过 `Config.log_level` 配置 |
| **日志格式** | `%(asctime)s [%(levelname)s] %(name)s: %(message)s` | 时间 + 级别 + Logger名 + 消息 |
| **时间格式** | `%Y-%m-%d %H:%M:%S` | 北京时间 |
| **输出目标** | 控制台 + 文件 | 双输出通道 |
| **文件编码** | UTF-8 | 避免中文乱码 |
| **文件命名** | `emily_YYYYMMDD.log` | 按日期自动滚动 |

### 2.2 Logger 命名空间（约 50+ 个独立 Logger）

```
emily.core                  # Emily Core 核心
├── emily.project_agent      # ProjectAgent 项目级 Agent
├── emily.workitem_agent     # WorkItemAgent 任务执行
├── emily.session_agent      # SessionAgent 会话调度
├── emily.api               # API 服务层
│   └── emily.api.permission
├── emily.db                # 数据库层
├── emily.bus               # 消息总线
├── emily.scheduler         # 调度器
├── emily.injector          # 知识注入器
│
├── emily.tools.*           # 工具层（10+）
│   ├── emily.tool.event
│   ├── emily.tool.file
│   ├── emily.tool.meeting
│   ├── emily.tool.task
│   ├── emily.tool.plan_task
│   ├── emily.tool.query
│   ├── emily.tool.memory
│   └── ...
│
├── emily.permission.*      # 权限系统
│   ├── emily.permission.auth_engine
│   ├── emily.permission.cache
│   └── emily.permission.row_security
│
├── emily.service.*         # 业务服务层（15+）
│   ├── emily.service.event
│   ├── emily.service.file
│   ├── emily.service.meeting
│   ├── emily.service.query
│   ├── emily.service.task
│   ├── emily.service.user_binding
│   ├── emily.service.user_memory
│   ├── emily.service.workflow_integrator
│   └── ...
│
├── emily.repo.*            # 数据访问层（10+）
│   ├── emily.repo.user
│   ├── emily.repo.event
│   ├── emily.repo.meeting
│   ├── emily.repo.message
│   └── ...
│
└── emily.pipeline.*        # Pipeline 组件
    ├── emily.pipeline.hook
    └── emily.pipeline.registry
```

### 2.3 初始化流程

```python
# 调用链
init(config_data)
    └── _setup_logging(config)
          ├── 创建 Console Handler (DEBUG 级别)
          └── 如果 log_to_file=True
                ├── 创建 logs/ 目录
                ├── 创建按日期命名的 log 文件
                └── 添加 FileHandler (DEBUG 级别)
```

**容错机制**：文件日志创建失败时，仅打印 WARNING 警告，不阻断系统启动（fail-open 设计）。

---

## 3. 数据库持久化日志

### 3.1 已实现的表

| 表名 | 控制开关 | 说明 |
|------|---------|------|
| `agent_trace_logs` | `agent_trace_enabled` | Agent 推理过程追踪 |
| `llm_interaction_logs` | `llm_interaction_log_enabled` | LLM 交互日志（token、延迟、响应类型） |
| `tool_call_logs` | `tool_call_log_enabled` | 工具调用日志（工具名/参数/结果摘要） |
| `chat_archives` | `chat_archive_enabled` | 全量聊天记录归档（入站+出站双向） |
| `checkpoints` | `checkpoint_enabled` | Pipeline 执行检查点 |

### 3.2 Phase 2 规划中的表（HealthChecker）

| 表名 | 用途 | 状态 |
|------|------|------|
| `health_metrics` | 健康指标时间序列数据 | 待实现 |
| `health_alerts` | 健康告警记录 | 待实现 |
| `health_actions` | 自动修复动作记录 | 待实现 |
| `health_cold_start_reports` | 冷启动完整性检查报告 | 待实现 |
| `permission_audit_logs` | 权限审计日志 | 待实现 |

---

## 4. 文件型业务日志

### 4.1 业务日志清单

| 日志文件 | 控制开关 | 配置项 | 默认路径 | 格式 |
|---------|---------|--------|---------|------|
| 项目事件日志 | `journal_enabled` | `journal_path` | `tem_log/项目日志.md` | Markdown |
| 待解决问题清单 | `pending_issues_enabled` | `pending_issues_path` | `tem_log/待解决问题.md` | Markdown |
| 用户长期记忆 | `user_memory_enabled` | `user_memory_dir` | `memory/` 目录 | 每个用户独立 Markdown 文件 |

### 4.2 项目事件日志格式

```markdown
## 2026-06-27 10:30:00

### 事件类型：项目节点完成
- **节点**：1.3 项目投资收益测算
- **负责人**：财务部
- **完成人**：张三
- **备注**：测算已通过评审

---
```

### 4.3 待解决问题清单格式

```markdown
## 待解决问题清单

### [P0] 数据库连接不稳定 (2026-06-27)
- **上报人**：系统
- **状态**：处理中
- **描述**：连续 3 次数据库连接失败

---
```

---

## 5. Docker 容器日志

### 5.1 默认日志驱动

| 容器 | 日志驱动 | 说明 |
|------|---------|------|
| `emily-core` | `json-file` | Docker 默认，无额外配置 |
| `emily-postgres` | `json-file` | PostgreSQL 标准输出 |
| (其他容器) | `json-file` | 各自独立 |

### 5.2 常用日志操作命令

```bash
# 查看 emily-core 实时日志（最后 100 行）
docker logs emily-core --tail 100 -f

# 查看 postgres 日志
docker logs emily-postgres --tail 100

# 查看特定时间范围的日志
docker logs emily-core --since "2026-06-27T10:00:00" --until "2026-06-27T12:00:00"

# 只查看错误日志
docker logs emily-core 2>&1 | grep -i error
```

### 5.3 容器日志存储位置（宿主机）

```
/var/lib/docker/containers/
    └── [container_id]/
        └── [container_id]-json.log
```

---

## 6. 日志相关配置项

### 6.1 Config 类中的日志配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `log_level` | str | `"INFO"` | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `log_dir` | str | `"logs/"` | 日志文件目录 |
| `log_to_file` | bool | `True` | 是否写入日志文件 |

### 6.2 业务日志开关

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `journal_enabled` | bool | `True` | 项目事件日志开关 |
| `pending_issues_enabled` | bool | `True` | 待解决问题清单开关 |
| `user_memory_enabled` | bool | `True` | 用户长期记忆开关 |
| `chat_archive_enabled` | bool | `True` | 全量聊天记录存档开关 |
| `chat_archive_include_progress` | bool | `False` | 前导消息是否纳入归档 |

### 6.3 审计追踪开关

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `agent_trace_enabled` | bool | `True` | Agent 推理过程记录总开关 |
| `agent_trace_detail_level` | str | `"summary"` | 追踪级别：summary/full |
| `llm_interaction_log_enabled` | bool | `True` | LLM 交互日志开关 |
| `tool_call_log_enabled` | bool | `True` | 工具调用日志开关 |

### 6.4 环境变量映射

| 环境变量 | 配置项 | 说明 |
|---------|--------|------|
| - | - | 日志配置暂无独立环境变量，需通过 config_data 传入 |

---

## 7. 日志目录结构

### 7.1 运行时完整目录结构

```
emily/
│
├── logs/                           # L1: 应用日志目录（自动创建）
│   ├── emily_20260625.log
│   ├── emily_20260626.log
│   └── emily_20260627.log          # 按日期滚动，无大小限制
│
├── tem_log/                        # L3: 业务日志目录
│   ├── 项目日志.md                  # 项目事件流水账
│   └── 待解决问题.md                # 问题追踪清单
│
├── memory/                         # L3: 用户长期记忆目录
│   ├── user_001.md                 # 用户 001 的记忆
│   ├── user_002.md
│   └── ...
│
├── emily-data/                     # 其他数据目录
│   ├── baseknowledge/              # 知识库
│   ├── SOPrepository/              # SOP 仓库
│   └── files/                      # 上传/下载文件
│
└── /var/lib/docker/containers/     # L4: Docker 容器日志（宿主机）
    └── [container_id]/[container_id]-json.log
```

### 7.2 日志文件生命周期

| 日志类型 | 保留策略 | 清理机制 |
|---------|---------|---------|
| 应用日志 (.log) | 无限期保留 ❗ | ❌ 无自动清理 |
| 业务日志 (.md) | 无限期保留 | ❌ 无自动清理 |
| 用户记忆 | 最多 50 条/用户 | ✅ 自动裁剪（超出后删除旧条目） |
| 数据库日志 | 无限期保留 ❗ | ❌ 无自动清理 |
| Docker 日志 | 取决于 Docker 配置 | ⚠️ 默认无轮转 |

---

## 8. 现状评估与建议

### 8.1 ✅ 做得好的地方

| 优点 | 说明 |
|------|------|
| **模块化日志** | 每个组件有独立 Logger，便于过滤定位 |
| **双输出通道** | 控制台 + 文件同时输出，兼顾调试与持久化 |
| **精细开关控制** | 10+ 个独立开关控制各类业务日志 |
| **Fail-open 设计** | 文件日志失败不阻断系统启动 |
| **多层覆盖** | 从调试日志到业务审计，四层架构完整 |
| **UTF-8 编码** | 避免中文乱码问题 |

### 8.2 ⚠️ 存在的问题

| 问题 | 风险等级 | 说明 |
|------|---------|------|
| **无日志轮转** | ⚠️ 中 | 日志文件无限增长，可能耗尽磁盘空间 |
| **无保留期限** | ⚠️ 中 | 历史日志永久保留，缺乏合规性控制 |
| **无大小限制** | ⚠️ 中 | 单个日志文件可能过大，影响查询性能 |
| **Docker 日志无限制** | ⚠️ 中 | json-file 默认不轮转，长期运行占用大 |
| **日志配置不可配** | ⚠️ 低 | 日志格式、轮转策略等硬编码，无法通过配置调整 |
| **缺少告警集成** | ❓ 待定 | 错误日志未触发告警，依赖人工巡检 |

### 8.3 💡 改进建议

#### 建议 1：增加日志轮转（P1）

**使用 `TimedRotatingFileHandler` 替代 `FileHandler`：**

```python
from logging.handlers import TimedRotatingFileHandler

# 每天轮转一次，保留 30 天
handler = TimedRotatingFileHandler(
    log_file,
    when="D",           # 按天轮转
    interval=1,         # 每天
    backupCount=30,     # 保留 30 天
    encoding="utf-8"
)
```

#### 建议 2：增加按大小轮转（P2）

**组合使用时间 + 大小轮转：**

```python
from logging.handlers import RotatingFileHandler

# 按大小轮转，每个文件 100MB，保留 10 个
handler = RotatingFileHandler(
    log_file,
    maxBytes=100 * 1024 * 1024,  # 100MB
    backupCount=10,
    encoding="utf-8"
)
```

#### 建议 3：Docker 日志配置（P2）

**在 docker-compose.yml 中配置日志限制：**

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "100m"   # 单个文件最大 100MB
    max-file: "5"       # 保留 5 个文件
```

#### 建议 4：增加日志告警集成（P3）

```python
# ERROR 级别日志触发告警（通过 OutboundEventBus）
class AlertingLogHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            # 发送告警通知
            outbound_bus.publish_alert(
                title=f"[ERROR] {record.name}",
                message=record.getMessage(),
                level="critical"
            )
```

#### 建议 5：数据库日志表清理（P3）

**增加定期清理任务：**
- `agent_trace_logs`：保留 90 天
- `llm_interaction_logs`：保留 180 天
- `tool_call_logs`：保留 180 天
- `chat_archives`：根据合规要求确定保留期限

---

## 📎 附录

### 相关文件路径

| 文件 | 路径 | 说明 |
|------|------|------|
| 日志初始化 | `emily-core/emily_core/bootstrap.py` | `_setup_logging()` 函数 |
| 配置定义 | `emily-core/emily_core/config.py` | Config 数据类 |
| 事件日志服务 | `emily-core/emily_core/services/event_journal.py` | 项目事件日志实现 |
| 问题清单服务 | `emily-core/emily_core/services/pending_issues.py` | 待解决问题实现 |
| 用户记忆服务 | `emily-core/emily_core/services/user_memory_service.py` | 用户长期记忆实现 |

### 日志级别使用规范

| 级别 | 使用场景 |
|------|---------|
| `DEBUG` | 详细调试信息，开发阶段使用 |
| `INFO` | 正常运行的关键节点、状态变化 |
| `WARNING` | 异常但可恢复、非致命问题 |
| `ERROR` | 严重错误、功能失效、需要人工干预 |
| `CRITICAL` | 系统级崩溃、数据丢失等灾难性事件 |

---

**报告生成时间**：2026-06-27
**调研范围**：Emily v0.7.0-Phase1
