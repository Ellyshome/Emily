# ops_scheduler 运维模块 — AI 执行手册

> **角色**：你是一名资深 Python 后端工程师，任务是按本手册分阶段实现 Emily 项目的运维模块。
> **工作方式**：逐阶段执行。每阶段先理解目标→编写代码→运行自验收→写反思→确认通过后进入下一阶段。
> **关键原则**：每阶段验收不通过，不得进入下一阶段。反思如有发现，可微调后续阶段计划。
> **依据文档**：[ops_scheduler_运维模块详细设计说明书.md](需求文件/运维模块/ops_scheduler_运维模块详细设计说明书.md)

---

## 0. 总体指引（开始前必读）

### 0.1 项目编码规范

在编写任何代码之前，你必须了解以下项目约定（详见 `CLAUDE.md`）：

1. **Python 环境**：项目基于 `uv`，运行命令用 `uv run python ...` 而非裸 `python`
2. **Sync Repository 模式**：[Repository 全 sync](emily-core/emily_core/repositories/)，Service 层用 `asyncio.to_thread()` 包裹
3. **分层不可跳**：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`
4. **dataclass 配置**：所有配置类使用 `@dataclass`，与 `config.py` 中的 `Config` 一致
5. **ORM 模式**：使用 SQLAlchemy 2.0 风格，`Base` 基类，`Column()` 定义字段
6. **日志**：使用 `logging.getLogger("emily.xxx")`
7. **Docker 部署**：代码变更后需 `docker compose restart emily-core` + 清除 `__pycache__`

### 0.2 关键设计决策（与详细设计文档的差异）

| # | 设计文档原方案 | 本手册采用的修正方案 | 理由 |
|---|-------------|------------------|------|
| 1 | TickScheduler 独立 asyncio.Task | **嵌入 ProjectAgent._do_tick()** 作为 Phase 3 步骤 | 避免第3个后台线程；共用 advisory lock |
| 2 | TickScheduler 单例模式 `get_instance()` | **普通实例，EmilyCore 构造注入** | 与 PlanTaskScheduler/StaleDetector 一致 |
| 3 | MailboxProbe 用 raw `imaplib` | **复用 `EmailService.fetch_orders()`** | 已有异步 IMAP 基础设施 |
| 4 | Advisory lock 单独获取 | **复用 ProjectAgent 现有锁**，OpsScheduler 不获取/释放锁 | 原设计锁在函数返回前释放 |
| 5 | 冷启动判定 `_tick_count == 0` | **查 `ops_startup_report` 表**（24h 内有无记录） | 内存计数器重启归零 |
| 6 | `interval_seconds()` 是 `@abstractmethod` | 改为**普通方法**，默认返回 300 | 语义冲突修正 |
| 7 | 目录名 `project/ops_scheduler/` | **并入已有 `project/ops/`** | 避免与现有 Phase 3 占位冲突 |
| 8 | `mail_uid` 无约束 | **必须 UNIQUE** | 邮件幂等去重 |
| 9 | `bus_status` 字段 | 改为 `pipeline_status` + 新增 `maxkb_status`/`email_status` | 语义清晰 + 组件覆盖 |

### 0.3 目标目录结构

完成所有阶段后，`project/ops/` 目录应包含以下文件：

```
emily-core/emily_core/project/ops/
├── __init__.py              # 模块总入口
├── config.py                # OpsConfig dataclass
├── probe_base.py            # Probe ABC + ProbeFinding + TickContext
├── probe_registry.py        # Probe 注册器
├── scheduler.py             # OpsScheduler（同步，非独立 Task）
├── models.py                # 5 个 ORM 模型
├── startup_report.py        # 冷启动报告生成器
├── probes/
│   ├── __init__.py
│   ├── stale_probe.py       # 卡滞检测探针
│   ├── mailbox_probe.py     # 邮箱轮询探针
│   └── health_probe.py      # 健康检查探针（占位）
├── repositories/
│   ├── __init__.py
│   └── ops_repo.py          # 运维表 CRUD
└── persistence/
    ├── __init__.py
    └── fallback_writer.py   # 降级 MD/JSONL 写入
```

---

## 阶段一：核心骨架

> **目标**：建立运维模块的最小可运行骨架 — Probe 接口体系 + 调度执行器 + DB 表 + 降级写入 + EmilyCore 集成。

### 准备工作

在开始编码前，先阅读以下现有文件以理解项目模式：

```
emily-core/emily_core/config.py                    # [读] Config dataclass 模式
emily-core/emily_core/bootstrap.py                 # [读] 环境变量映射模式
emily-core/emily_core/__init__.py                   # [读] EmilyCore 初始化模式，找 _init_project_agent()
emily-core/emily_core/project/__init__.py           # [读] project 包当前的导出
emily-core/emily_core/project/project_agent.py      # [读] ProjectAgent 现有实现，重点关注 _do_tick()
emily-core/emily_core/project/project_agent_config.py # [读] ProjectAgentConfig dataclass
emily-core/emily_core/infrastructure/database/models.py # [读] ORM 模型模式
emily-core/emily_core/infrastructure/database/session.py # [读] DB session 获取方式
```

### 步骤清单

按顺序完成以下每一步。每步完成后确认文件存在且内容正确。

---

#### 步骤 1.1：创建 `project/ops/config.py`

**内容要求**：
- 定义 `OpsConfig` dataclass，包含以下字段（均为带默认值的简单类型）：
  - `enabled: bool = True`
  - `tick_interval_seconds: int = 300`
  - `stale_probe_enabled: bool = True`、`stale_threshold_days: int = 14`、`deadline_warn_days: int = 7`、`alert_cooldown_hours: int = 24`
  - `mailbox_enabled: bool = False`、`mail_imap_host: str = ""`、`mail_imap_port: int = 993`、`mail_username: str = ""`、`mail_password: str = ""`、`mail_sender_whitelist: str = ""`
  - `health_probe_enabled: bool = False`
  - `startup_report_enabled: bool = True`
  - `fallback_log_dir: str = "logs/"`
- 提供 `@classmethod from_global_config(cls, cfg: "Config") -> "OpsConfig"`，从全局 Config 提取同名字段

**参考模式**：`project_agent_config.py` 的 `ProjectAgentConfig`

**完成标志**：文件存在，导入 `OpsConfig` 无报错

---

#### 步骤 1.2：创建 `project/ops/probe_base.py`

**内容要求**：
- `ProbeFinding` dataclass：`finding_type: str`、`severity: str`、`target_id: str`、`message: str`、`metadata: dict = field(default_factory=dict)`
- `TickContext` dataclass：`tick_id: str`、`tick_number: int`、`start_time: datetime`，内部 `_last_runs: dict` 记录各 Probe 的上次运行时间。提供 `get_last_run_time(name)` 和 `set_last_run_time(name, dt)` 方法
- `Probe` 抽象基类（ABC）：
  - `name()` — `@abstractmethod`，返回 `str`
  - `run(ctx: TickContext) -> list[ProbeFinding]` — `@abstractmethod`
  - `enabled() -> bool` — 普通方法，默认 `True`
  - `interval_seconds() -> int` — **普通方法**（非 abstract），默认 `300`
  - `should_run(ctx) -> bool` — 普通方法，检查 enabled 和冷却

**完成标志**：`Probe` 是 ABC，`name()` 和 `run()` 有 `@abstractmethod`，`enabled()` 和 `interval_seconds()` 没有

---

#### 步骤 1.3：创建 `project/ops/probe_registry.py`

**内容要求**：
- `ProbeRegistry` 类，内部 `_probes: dict[str, Probe]`
- `register(probe)` — 注册，同名抛 `ValueError`
- `get_enabled_probes() -> list[Probe]` — 返回 `enabled()==True` 的
- `get_all() -> list[Probe]` — 返回全部

**完成标志**：重复注册同名 Probe 抛 `ValueError`

---

#### 步骤 1.4：创建 `project/ops/scheduler.py`

这是阶段一最核心的文件。**严格遵循以下约束**：

- ❌ **不要**定义 `_loop()` / `_task` / `start()` / `stop()`
- ❌ **不要**获取或释放 advisory lock（由 ProjectAgent 管理）
- ✅ 只提供同步方法 `run_tick(tick_id: str, tick_number: int) -> dict`

**内容要求**：

```python
import logging
from datetime import datetime, timezone
from uuid import uuid4
from .probe_base import TickContext
from .probe_registry import ProbeRegistry

logger = logging.getLogger("emily.ops")

class OpsScheduler:
    """运维调度执行器。由 ProjectAgent._do_tick() 在 advisory lock 保护下同步调用。"""

    def __init__(self, config: "OpsConfig", db_repo, fallback, email_service=None):
        self._config = config
        self._registry = ProbeRegistry()
        self._db_repo = db_repo
        self._fallback = fallback
        self._email_service = email_service
        self._consecutive_failures: dict[str, int] = {}

    def register_probe(self, probe):
        self._registry.register(probe)

    def run_tick(self, tick_id: str, tick_number: int) -> dict:
        ctx = TickContext(tick_id=tick_id, tick_number=tick_number,
                          start_time=datetime.now(timezone.utc))
        probe_results = []
        for probe in self._registry.get_enabled_probes():
            result = self._run_probe_safe(probe, ctx)
            probe_results.append(result)

        self._persist_results(ctx, probe_results)

        if self._is_cold_start():
            self._generate_startup_report(ctx, probe_results)

        return {
            "probes_run": len(probe_results),
            "findings_total": sum(r.get("findings_count", 0) for r in probe_results),
            "errors": [r for r in probe_results if r["status"] == "FAILED"],
        }

    def _run_probe_safe(self, probe, ctx):
        try:
            if not probe.should_run(ctx):
                return {"probe": probe.name(), "status": "SKIPPED"}
            findings = probe.run(ctx)
            ctx.set_last_run_time(probe.name(), datetime.now(timezone.utc))
            self._consecutive_failures[probe.name()] = 0
            return {"probe": probe.name(), "status": "SUCCESS",
                    "findings_count": len(findings) if findings else 0, "findings": findings}
        except Exception as e:
            failures = self._consecutive_failures.get(probe.name(), 0) + 1
            self._consecutive_failures[probe.name()] = failures
            if failures >= 3:
                logger.warning("Probe '%s' failed %d consecutive times", probe.name(), failures)
            return {"probe": probe.name(), "status": "FAILED", "error": str(e)}

    def _persist_results(self, ctx, results):
        try:
            self._db_repo.save_tick_results(ctx, results)
        except Exception as e:
            logger.error("DB persist failed, fallback to MD: %s", e)
            self._fallback.write_tick_results(ctx, results)

    def _is_cold_start(self) -> bool:
        """查 DB 判断（最近 24h 无启动报告），非内存计数器。"""
        try:
            return self._db_repo.get_latest_startup_report(hours=24) is None
        except Exception:
            return True

    def _generate_startup_report(self, ctx, probe_results):
        from .startup_report import generate_startup_report
        report = generate_startup_report(ctx, self._config)
        try:
            self._db_repo.save_startup_report(report)
        except Exception as e:
            logger.error("Save startup report failed, fallback to MD: %s", e)
            self._fallback.write_startup_report(report)

    def status(self) -> dict:
        return {
            "enabled": self._config.enabled,
            "probes_registered": len(self._registry.get_all()),
            "probes_enabled": len(self._registry.get_enabled_probes()),
            "consecutive_failures": dict(self._consecutive_failures),
        }
```

**完成标志**：
- 搜索 `_loop` 在文件中无匹配
- 搜索 `pg_try_advisory` 在文件中无匹配
- 搜索 `asyncio` 在文件中无匹配（`run_tick` 是纯同步）
- `run_tick` 不是 `async def`

---

#### 步骤 1.5：创建 `project/ops/persistence/fallback_writer.py`

**内容要求**：
- `FallbackWriter` 类，构造函数接收 `log_dir: str = "logs/"`
- 自动创建 `{log_dir}/ops_degraded/` 目录（用 `Path.mkdir(parents=True, exist_ok=True)`）
- `write_tick_results(ctx, results)` — 同时生成 `.md` 和 `.jsonl` 两个文件
- `write_startup_report(report)` — 生成 `.md` 文件
- `write_mail_error(ctx, error_msg)` — 生成 `.md` 文件
- 文件名格式：`ops_fallback_{type}_{tick_number}_{timestamp}.md`，其中 `{type}` 为 `tick` / `startup` / `mailbox`

**参考模式**：标准库 `pathlib.Path` + Python 文件写入

**完成标志**：调用 `write_tick_results()` 后 `logs/ops_degraded/` 目录下有对应的 `.md` 和 `.jsonl` 文件

---

#### 步骤 1.6：创建 `project/ops/repositories/ops_repo.py`

**内容要求**：
- `OpsRepository` 类，遵循项目 sync Repository 模式
- `save_tick_results(ctx, results, session=None)` — 写入 `ops_tick_log` + `ops_probe_execution` + `ops_finding`
- `get_latest_startup_report(hours=24, session=None)` — 查询最近 N 小时内的报告
- `save_startup_report(report, session=None)` — 写入 `ops_startup_report`
- `save_mail_audit(data, session=None)` — 写入 `ops_mail_audit`（基于 `mail_uid` 幂等，用 `ON CONFLICT DO NOTHING` 或先查后插）
- `mail_uid_exists(uid, session=None) -> bool` — 检查 uid 是否已存在
- `get_pending_mail_commands(session=None)` — 获取未分派的邮件命令
- `mark_command_dispatched(command_id, session=None)` — 标记已分派

**参考模式**：`repositories/sm_node_repo.py` 的 `SMNodeRepository`（静态方法 + 可选 session 参数）

**数据库连接**：使用 `get_session_raw()` 获取 session（参见 `infrastructure/database/session.py`）

**完成标志**：Repository 的方法可导入，基本 CRUD 逻辑正确

---

#### 步骤 1.7：创建 `project/ops/models.py`

**内容要求**：
定义 5 个 SQLAlchemy ORM 模型，全部继承 `Base`：

1. `OpsTickLog` — `__tablename__ = "ops_tick_log"`
   - `tick_id` UUID 主键、`tick_number` Integer、`start_time`/`end_time` DateTime、`duration_ms` Integer、`probes_executed/success/failed` Integer、`total_findings` Integer、`instance_id` String(200)、`created_at` DateTime

2. `OpsProbeExecution` — `__tablename__ = "ops_probe_execution"`
   - `id` UUID 主键、`tick_id` FK→ops_tick_log、`probe_name` String(100)、`status` String(50)、`duration_ms` Integer、`findings_count` Integer、`error_message` Text、`created_at` DateTime

3. `OpsFinding` — `__tablename__ = "ops_finding"`
   - `id` UUID 主键、`tick_id` FK→ops_tick_log、`probe_name` String(100)、`finding_type` String(100)、`severity` String(50)、`target_id` String(200)、`message` Text、`metadata` JSONB、`created_at` DateTime

4. `OpsMailAudit` — `__tablename__ = "ops_mail_audit"`
   - `id` UUID 主键、`tick_id` FK→ops_tick_log、**`mail_uid` String(100) unique=True**（注意必须有 unique）、`mail_from` String(200)、`mail_subject` String(500)、`mail_date` DateTime、`command_text` Text、`received_at` DateTime、`dispatched` Boolean、`dispatched_at` DateTime、`created_at` DateTime

5. `OpsStartupReport` — `__tablename__ = "ops_startup_report"`
   - `id` UUID 主键、`tick_id` FK→ops_tick_log、`startup_time` DateTime、`environment` String(100)、`instance_id` String(200)、`version` String(100)、**`db_status` Boolean**（不是 VARCHAR）、`llm_status` String(50)、**`maxkb_status` String(50)**（新增）、**`email_status` String(50)**（新增）、**`pipeline_status` String(50)**（不是 bus_status）、`projects_total` Integer、`nodes_completed/in_progress/blocked` Integer、`report_content` Text、`sent_to_mail` Boolean、`sent_at` DateTime、`created_at` DateTime

**参考模式**：打开 `infrastructure/database/models.py`，参考现有 ORM 模型的写法。注意 `Base` 的 import 路径。

**关键约束**：
- `OpsMailAudit.mail_uid` 必须有 `unique=True`
- `OpsStartupReport.db_status` 是 `Boolean`，不是 `VARCHAR`
- `OpsStartupReport` 有 `maxkb_status` 和 `email_status` 字段
- `OpsStartupReport` 的字段叫 `pipeline_status`，不是 `bus_status`

**完成标志**：5 个模型类可导入，`__tablename__` 正确，`mail_uid` 有 unique

---

#### 步骤 1.8：创建 `infrastructure/database/scripts/003_create_ops_tables.sql`

**内容要求**：
- 包含 5 张表的完整 `CREATE TABLE IF NOT EXISTS` DDL
- `ops_tick_log` — 主键 `tick_id UUID PRIMARY KEY`，索引 `idx_ops_tick_log_time ON (start_time)`
- `ops_probe_execution` — FK `REFERENCES ops_tick_log(tick_id)`，索引 `tick_id` 和 `probe_name`
- `ops_finding` — FK `REFERENCES ops_tick_log(tick_id)`，索引 `tick_id` 和 `finding_type`
- `ops_mail_audit` — FK `REFERENCES ops_tick_log(tick_id)`，**`UNIQUE (mail_uid)`**（必须），索引 `mail_from` 和 `dispatched`
- `ops_startup_report` — FK `REFERENCES ops_tick_log(tick_id)`，索引 `created_at`
- 每张表有 `COMMENT ON TABLE` 注释

**字段名必须与步骤 1.7 的 ORM 模型一致**（特别是 `pipeline_status` 而非 `bus_status`）。

**参考模式**：`scripts/002_seed_test_data.sql` 或现有 migration 脚本格式

**完成标志**：SQL 语法正确（可用 `docker exec emily-postgres psql -U emily -d emily -f /path/to/003.sql` 测试）

---

#### 步骤 1.9：修改 `config.py` — 新增全局配置字段

**操作**：打开 `emily-core/emily_core/config.py`

1. 找到 `# ---- 项目级 Agent (ProjectAgent) ----` 注释块
2. 在它后面新增如下字段：

```python
# ---- 运维模块 (Ops / ProjectAgent Phase 3) ----
ops_enabled: bool = True
"""运维调度模块总开关"""
ops_stale_probe_enabled: bool = True
"""卡滞节点检测探针开关"""
ops_mailbox_enabled: bool = False
"""邮箱轮询探针开关"""
ops_mail_imap_host: str = ""
ops_mail_imap_port: int = 993
ops_mail_username: str = ""
ops_mail_password: str = ""
ops_mail_sender_whitelist: str = ""
ops_health_probe_enabled: bool = False
"""健康度检查探针开关"""
ops_startup_report_enabled: bool = True
"""冷启动报告开关"""
ops_fallback_log_dir: str = "logs/"
"""降级备份文件目录"""
```

3. 找到现有 `email_poll_interval`（约第 293 行），将注释改为标记废弃：

```python
email_poll_interval: int = 60
"""[已废弃] 请使用 ops_mailbox_enabled + ops_tick_interval_seconds 替代。保留字段以兼容旧配置。"""
```

**完成标志**：`grep -c "ops_" config.py` 返回 >= 11（10 个新字段 + 废弃注释中的引用）

---

#### 步骤 1.10：修改 `bootstrap.py` — 新增环境变量映射

**操作**：打开 `emily-core/emily_core/bootstrap.py`

在现有环境变量映射之后新增：

```python
"EMILY_OPS_ENABLED": "ops_enabled",
"EMILY_OPS_MAILBOX_ENABLED": "ops_mailbox_enabled",
"EMILY_OPS_MAIL_IMAP_HOST": "ops_mail_imap_host",
"EMILY_OPS_MAIL_IMAP_PORT": "ops_mail_imap_port",
"EMILY_OPS_MAIL_USERNAME": "ops_mail_username",
"EMILY_OPS_MAIL_PASSWORD": "ops_mail_password",
"EMILY_OPS_MAIL_SENDER_WHITELIST": "ops_mail_sender_whitelist",
"EMILY_OPS_STARTUP_REPORT_ENABLED": "ops_startup_report_enabled",
```

**完成标志**：`grep "EMILY_OPS_" bootstrap.py` 返回 8 行

---

#### 步骤 1.11：修改 `project/__init__.py` — 导出 ops 符号

**操作**：打开 `emily-core/emily_core/project/__init__.py`

新增对 ops 模块核心类的导出（至少导出 `OpsScheduler` 和 `OpsConfig`）。如果 Stage 1 时 import 会因缺少依赖报错，可以延迟导入或先导出模块名。

**完成标志**：`from emily_core.project import OpsScheduler` 成功（或至少模块导入成功）

---

#### 步骤 1.12：修改 `__init__.py` (EmilyCore) — 集成 ops 模块

**操作**：打开 `emily-core/emily_core/__init__.py`

1. 在 `EmilyCore.__init__()` 中添加：
```python
self._ops_scheduler = None
```

2. 新增方法 `_init_ops_module(self)`：
```python
def _init_ops_module(self):
    """初始化运维模块。fail-open：异常不阻断 Core 启动。"""
    if not self._config.ops_enabled:
        return
    try:
        from emily_core.project.ops.config import OpsConfig
        from emily_core.project.ops.scheduler import OpsScheduler
        from emily_core.project.ops.repositories.ops_repo import OpsRepository
        from emily_core.project.ops.persistence.fallback_writer import FallbackWriter

        ops_config = OpsConfig.from_global_config(self._config)
        ops_repo = OpsRepository()
        fallback = FallbackWriter(log_dir=ops_config.fallback_log_dir)

        self._ops_scheduler = OpsScheduler(
            config=ops_config, db_repo=ops_repo,
            fallback=fallback, email_service=self._email_service,
        )
        if self._project_agent:
            self._project_agent.set_ops_scheduler(self._ops_scheduler)
        logger.info("Ops module initialized")
    except Exception as e:
        logger.warning("Ops module init failed (fail-open): %s", e)
```

3. 在 `_ensure_initialized()` 中添加调用（**关键**：必须在 `_init_project_agent()` 之后）：
```python
self._init_project_agent()
self._init_ops_module()           # ← 新增，必须在 ProjectAgent 之后
self._init_permission_module()
```

**完成标志**：
- `_init_ops_module` 方法存在
- `_ensure_initialized()` 调用顺序正确
- 整个 try/except 包裹（fail-open）

---

#### 步骤 1.13：修改 `project/project_agent.py` — 对接 OpsScheduler

**操作**：打开 `emily-core/emily_core/project/project_agent.py`

1. 在 `ProjectAgent.__init__()` 中添加：
```python
self._ops_scheduler = None  # OpsScheduler | None
```

2. 新增方法：
```python
def set_ops_scheduler(self, scheduler):
    """由 EmilyCore._init_ops_module() 调用，注入 OpsScheduler 实例。"""
    self._ops_scheduler = scheduler
```

3. 在 `_do_tick()` 方法末尾（Phase 1 stale_detector.run() 之后）添加：
```python
# Phase 3: 运维调度
if self._ops_scheduler:
    tick_id = str(uuid4())
    try:
        self._ops_scheduler.run_tick(tick_id, self._tick_count)
    except Exception as e:
        logger.error("Ops tick failed (degraded): %s", e)
```

4. 在 `status()` 方法返回的 dict 中添加：
```python
if self._ops_scheduler:
    result["ops"] = self._ops_scheduler.status()
```

**⚠ 关键约束**：Phase 1 的 `self._stale_detector.run()` 调用**不要删除**，保持原样。OpsScheduler 的 `run_tick` 是附加的，不替代原有逻辑。

**完成标志**：
- `set_ops_scheduler` 方法存在
- `_do_tick` 中调用了 `run_tick`
- `status()` 返回 dict 包含 `"ops"` 键
- `stale_detector.run()` 调用未被删除

---

#### 步骤 1.14：修改 `project/project_agent_config.py`

**操作**：打开 `emily-core/emily_core/project/project_agent_config.py`

新增字段：
```python
ops_enabled: bool = True
"""运维模块是否启用。False 时 OpsScheduler 不注入到 ProjectAgent。"""
```

**完成标志**：字段存在

---

#### 步骤 1.15：修改 `infrastructure/database/models.py`

**操作**：在文件末尾追加 5 个 ORM 模型的 import 或直接定义。根据项目习惯选择：
- 方式 A：在 `models.py` 中 import（如果 ops/models.py 已定义）
- 方式 B：直接在 `models.py` 中追加模型类

**推荐方式 A**：在 `models.py` 末尾添加 `from emily_core.project.ops.models import OpsTickLog, OpsProbeExecution, OpsFinding, OpsMailAudit, OpsStartupReport`

**完成标志**：`from emily_core.infrastructure.database.models import OpsTickLog` 成功

---

### 阶段一自验收

完成以上所有步骤后，按以下检查清单逐项验证。每一项都应该通过。

#### A. 文件存在性（用 Glob 检查）

```bash
# 在项目根目录执行，确认以下文件全部存在：
ls emily-core/emily_core/project/ops/__init__.py
ls emily-core/emily_core/project/ops/config.py
ls emily-core/emily_core/project/ops/probe_base.py
ls emily-core/emily_core/project/ops/probe_registry.py
ls emily-core/emily_core/project/ops/scheduler.py
ls emily-core/emily_core/project/ops/models.py
ls emily-core/emily_core/project/ops/repositories/ops_repo.py
ls emily-core/emily_core/project/ops/persistence/fallback_writer.py
ls emily-core/emily_core/infrastructure/database/scripts/003_create_ops_tables.sql
```

#### B. 接口正确性（用 Python 验证）

```bash
uv run python -c "
from emily_core.project.ops.probe_base import Probe, ProbeFinding, TickContext
from abc import ABC

# 1. Probe 是 ABC
assert issubclass(Probe, ABC), 'Probe must be ABC'

# 2. name() 和 run() 是 abstractmethod
assert hasattr(Probe.name, '__isabstractmethod__'), 'name() must be abstract'
assert hasattr(Probe.run, '__isabstractmethod__'), 'run() must be abstract'

# 3. enabled() 和 interval_seconds() 不是 abstractmethod
assert not hasattr(Probe.enabled, '__isabstractmethod__'), 'enabled() must be concrete'
assert not hasattr(Probe.interval_seconds, '__isabstractmethod__'), 'interval_seconds() must be concrete'

# 4. ProbeFinding 是 dataclass
from dataclasses import is_dataclass
assert is_dataclass(ProbeFinding), 'ProbeFinding must be @dataclass'

# 5. TickContext 是 dataclass
assert is_dataclass(TickContext), 'TickContext must be @dataclass'

print('All interface checks passed')
"
```

#### C. OpsScheduler 行为检查

```bash
uv run python -c "
from emily_core.project.ops.scheduler import OpsScheduler
import inspect

# 1. 检查不应存在的方法
source = inspect.getsource(OpsScheduler)
assert '_loop' not in source, 'OpsScheduler must NOT have _loop()'
assert 'pg_try_advisory' not in source, 'OpsScheduler must NOT acquire advisory lock'
assert 'asyncio' not in source, 'OpsScheduler must NOT use asyncio'

# 2. run_tick() 不是 async
assert not inspect.iscoroutinefunction(OpsScheduler.run_tick), 'run_tick() must be sync'

print('All OpsScheduler checks passed')
"
```

#### D. 数据库检查

```bash
# 1. 检查 SQL 文件中 mail_uid 的 UNIQUE 约束
grep -i 'unique.*mail_uid\|mail_uid.*unique' emily-core/emily_core/infrastructure/database/scripts/003_create_ops_tables.sql

# 2. 确认 5 张表都在 SQL 中
grep -c 'CREATE TABLE' emily-core/emily_core/infrastructure/database/scripts/003_create_ops_tables.sql
# 应输出 5

# 3. ORM 模型导入
uv run python -c "
from emily_core.project.ops.models import OpsTickLog, OpsProbeExecution, OpsFinding, OpsMailAudit, OpsStartupReport
# 检查 mail_uid unique
from sqlalchemy import Column
for col in OpsMailAudit.__table__.columns:
    if col.name == 'mail_uid':
        assert col.unique, 'mail_uid must have unique=True'
        print('mail_uid unique=True confirmed')
# 检查 __tablename__
assert OpsTickLog.__tablename__ == 'ops_tick_log'
assert OpsStartupReport.__tablename__ == 'ops_startup_report'
print('All ORM checks passed')
"
```

#### E. 配置集成检查

```bash
# config.py 中 ops_ 字段数量
grep -c 'ops_' emily-core/emily_core/config.py
# 应 >= 11

# bootstrap.py 中 EMILY_OPS 映射数量
grep -c 'EMILY_OPS_' emily-core/emily_core/bootstrap.py
# 应 == 8

# email_poll_interval 注释已更新
grep '已废弃' emily-core/emily_core/config.py
# 应有输出
```

#### F. EmilyCore 集成检查

```bash
uv run python -c "
import inspect
from emily_core import EmilyCore

# _init_ops_module 方法存在
assert hasattr(EmilyCore, '_init_ops_module'), 'EmilyCore must have _init_ops_module'

# 检查 _ensure_initialized 调用顺序
source = inspect.getsource(EmilyCore._ensure_initialized)
assert '_init_ops_module()' in source, '_ensure_initialized must call _init_ops_module'
print('EmilyCore integration checks passed')
"
```

#### G. ProjectAgent 集成检查

```bash
uv run python -c "
from emily_core.project.project_agent import ProjectAgent
import inspect

# set_ops_scheduler 方法存在
assert hasattr(ProjectAgent, 'set_ops_scheduler'), 'ProjectAgent must have set_ops_scheduler'

# _do_tick 中调用了 run_tick
source = inspect.getsource(ProjectAgent._do_tick)
assert 'run_tick' in source, '_do_tick must call run_tick'

# status() 包含 ops 键
status_source = inspect.getsource(ProjectAgent.status)
assert 'ops' in status_source, 'status() must include ops key'

print('ProjectAgent integration checks passed')
"
```

#### H. 降级写入检查

```bash
uv run python -c "
from emily_core.project.ops.persistence.fallback_writer import FallbackWriter
from pathlib import Path
import tempfile, os

# 测试自动创建目录和文件写入
with tempfile.TemporaryDirectory() as tmpdir:
    w = FallbackWriter(log_dir=tmpdir)
    degraded_dir = Path(tmpdir) / 'ops_degraded'
    assert degraded_dir.exists(), 'ops_degraded dir must be auto-created'
    
    # 模拟写入
    from emily_core.project.ops.probe_base import TickContext
    from datetime import datetime, timezone
    ctx = TickContext(tick_id='test-123', tick_number=1, start_time=datetime.now(timezone.utc))
    w.write_tick_results(ctx, [{'probe': 'test', 'status': 'SUCCESS'}])
    
    md_files = list(degraded_dir.glob('*.md'))
    jsonl_files = list(degraded_dir.glob('*.jsonl'))
    assert len(md_files) >= 1, 'MD file must be generated'
    assert len(jsonl_files) >= 1, 'JSONL file must be generated'
    print('FallbackWriter checks passed')
"
```

---

### 阶段一反思

完成所有验收后，回答以下问题（记录在本次对话中）：

1. **哪些步骤特别顺利？** 说明项目现有的哪些模式/文件帮助了你。
2. **哪些步骤遇到了困难？** 是依赖关系不清晰，还是参考代码不够？
3. **阶段一的产物是否符合你对"可运行骨架"的预期？** 如果不符合，缺少什么？
4. **后续阶段计划需要调整吗？** 例如：
   - 是否需要在阶段二中额外处理某个在阶段一中没考虑到的边界？
   - 阶段三的 `EmailService` 复用是否在阶段一中预留了正确的接口？
   - 阶段四的启动报告生成是否依赖了阶段一中尚未创建的模块？

**如果反思发现需要调整后续阶段，现在就在此记录你的调整决定。后续执行时以调整后的计划为准。**

---

## 阶段二：Stale Probe 迁移

> **目标**：将现有 `StaleDetector` 适配为 `StaleProbe`，通过 Probe 接口向 OpsScheduler 报告。**原有告警路径保持不变。**

### 准备工作

```
emily-core/emily_core/project/maintenance/stale_detector.py  # [读] StaleDetector.run() 的返回类型和字段
emily-core/emily_core/project/ops/probe_base.py               # [读] 确认 ProbeFinding 字段
emily-core/emily_core/__init__.py                              # [读] _init_ops_module() 当前代码
```

### 步骤清单

#### 步骤 2.1：创建 `project/ops/probes/__init__.py`

空文件或仅包含 docstring 的子包初始化。

#### 步骤 2.2：创建 `project/ops/probes/stale_probe.py`

**内容要求**：
- 继承 `Probe`
- 构造函数接收 `stale_detector: StaleDetector` 和 `config: OpsConfig`
- `name()` 返回 `"stale_probe"`
- `enabled()` 返回 `config.stale_probe_enabled`
- `interval_seconds()` 返回 `config.tick_interval_seconds`
- `run(ctx)` 调用 `self._detector.run()`，将返回的 `stale_nodes` 和 `milestone_warnings` 转换为 `ProbeFinding` 列表
  - `stale_node` → `finding_type="STALE_NODE"`, `severity="WARNING"`, `target_id=node.node_id`
  - `milestone_warning` → `finding_type="MILESTONE_WARNING"`, `severity="WARNING"`, `target_id=ms.node_id`

**⚠ 注意**：`StaleDetector.run()` 的返回类型是什么？打开 `stale_detector.py` 确认 `StaleDetectionResult` 的字段名。

#### 步骤 2.3：在 `_init_ops_module()` 中注册 StaleProbe

**操作**：打开 `emily-core/emily_core/__init__.py`，在 `_init_ops_module()` 中（创建 `self._ops_scheduler` 之后）添加：

```python
if ops_config.stale_probe_enabled and self._sm_node_repo:
    from emily_core.project.ops.probes.stale_probe import StaleProbe
    stale_probe = StaleProbe(self._stale_detector, ops_config)
    self._ops_scheduler.register_probe(stale_probe)
```

---

### 阶段二自验收

#### A. 适配正确性

```bash
uv run python -c "
from emily_core.project.ops.probes.stale_probe import StaleProbe
from emily_core.project.ops.probe_base import Probe, ProbeFinding

# 验证继承关系
assert issubclass(StaleProbe, Probe), 'StaleProbe must inherit Probe'

# 验证 name()
sp = StaleProbe.__new__(StaleProbe)  # 不行，需要真实实例
# 改为检查类定义
import inspect
source = inspect.getsource(StaleProbe.name)
assert 'stale_probe' in source, 'name() must return stale_probe'
print('StaleProbe adapter checks passed')
"
```

#### B. 无副作用检查

```bash
# 1. StaleDetector.run() 仍然存在于 project_agent.py 的 _do_tick 中
grep 'stale_detector.run()' emily-core/emily_core/project/project_agent.py
# 应有输出，证明未被删除

# 2. smoke_test 通过
uv run python scripts/smoke_test.py
# 应正常通过
```

#### C. 集成检查

```bash
uv run python -c "
from emily_core.project.ops.scheduler import OpsScheduler
from emily_core.project.ops.config import OpsConfig
from emily_core.project.ops.probe_registry import ProbeRegistry

# 模拟注册 StaleProbe
class MockStaleDetector:
    def run(self):
        class Result:
            stale_nodes = []
            milestone_warnings = []
        return Result()

from emily_core.project.ops.probes.stale_probe import StaleProbe
probe = StaleProbe(MockStaleDetector(), OpsConfig())
assert probe.name() == 'stale_probe'
findings = probe.run(None)  # ctx 可以为 None 如果 run 不使用它
assert isinstance(findings, list)

print('StaleProbe integration checks passed')
"
```

---

### 阶段二反思

1. `StaleDetector.run()` 的返回类型与 `ProbeFinding` 的映射是否完整？是否有遗漏的字段？
2. Phase 1 的告警和 StaleProbe 的 DB 持久化是否存在重复推送？（两者并存是设计意图，但需确认）
3. 是否需要调整阶段三/四的计划？

---

## 阶段三：邮箱 Probe

> **目标**：实现 `MailboxProbe`，**必须复用 `EmailService.fetch_orders()`**，不得使用 raw `imaplib`。

### 准备工作

```
emily-core/emily_core/services/email_service.py         # [读] EmailService 的 fetch_orders() 签名和返回类型
emily-core/emily_core/providers/email/base.py            # [读] EmailCredentials 和 EmailEnvelope 的字段
emily-core/emily_core/project/ops/probe_base.py          # [读] 确认 Probe/ProbeFinding
emily-core/emily_core/__init__.py                         # [读] _init_ops_module() 当前代码
```

### 步骤清单

#### 步骤 3.1：创建 `project/ops/probes/mailbox_probe.py`

**内容要求**：

- 继承 `Probe`
- `name()` → `"mailbox_probe"`
- `enabled()` → `config.mailbox_enabled`
- `interval_seconds()` → `300`
- `run(ctx)` → 桥接异步 `_run_async(ctx)`

**异步桥接方案**：因为 `ProjectAgent._do_tick()` 是同步方法，`run()` 需要调用异步的 `EmailService.fetch_orders()`。使用以下方案：

```python
import asyncio

def _run_async_in_sync(coro):
    """在同步上下文中运行异步协程。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # 在已有 event loop 中，使用 run_coroutine_threadsafe 或新线程
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        return asyncio.run(coro)
```

**`_run_async(ctx)` 逻辑**：
1. 构造 `EmailCredentials`（从 `config.mail_*` 读取）
2. 调用 `await self._email_service.fetch_orders(creds=creds)`
3. 解析白名单（`config.mail_sender_whitelist` 逗号分隔）
4. 遍历 orders：
   - 白名单外 → `MAIL_UNAUTHORIZED` finding
   - `mail_uid` 已存在 → 跳过（幂等）
   - 否则 → `MAIL_COMMAND` finding + 写入 `ops_mail_audit`
5. 异常 → `MAIL_ERROR` finding + `fallback.write_mail_error()`

**⚠ 严格约束**：代码中**不得出现 `import imaplib`**。必须通过 `self._email_service.fetch_orders()` 获取邮件。

#### 步骤 3.2：在 `_init_ops_module()` 中条件注册 MailboxProbe

```python
if ops_config.mailbox_enabled and self._email_service:
    from emily_core.project.ops.probes.mailbox_probe import MailboxProbe
    self._ops_scheduler.register_probe(
        MailboxProbe(ops_config, self._email_service, fallback, ops_repo)
    )
```

---

### 阶段三自验收

#### A. 正确复用 EmailService

```bash
# 确认 mailbox_probe.py 中没有 import imaplib
grep -i 'imaplib' emily-core/emily_core/project/ops/probes/mailbox_probe.py
# 应无输出

# 确认使用了 fetch_orders
grep 'fetch_orders' emily-core/emily_core/project/ops/probes/mailbox_probe.py
# 应有输出
```

#### B. 白名单/幂等逻辑检查

```bash
uv run python -c "
from emily_core.project.ops.config import OpsConfig

# 空白名单 → 放行
config = OpsConfig(mail_sender_whitelist='')
whitelist = [w.strip() for w in config.mail_sender_whitelist.split(',') if w.strip()]
assert whitelist == [], 'Empty whitelist should result in empty list'

# 有白名单
config = OpsConfig(mail_sender_whitelist='admin@co.com,ops@co.com')
whitelist = [w.strip() for w in config.mail_sender_whitelist.split(',') if w.strip()]
assert 'admin@co.com' in whitelist
assert 'ops@co.com' in whitelist
assert 'hacker@evil.com' not in whitelist

print('Whitelist logic checks passed')
"
```

#### C. 条件注册

```bash
uv run python -c "
from emily_core.project.ops.probes.mailbox_probe import MailboxProbe
import inspect

source = inspect.getsource(MailboxProbe)
# 确认 name() 返回 'mailbox_probe'
# 确认 enabled() 读取 config.mailbox_enabled
assert 'mailbox_probe' in source
assert 'mailbox_enabled' in source

print('MailboxProbe checks passed')
"
```

---

### 阶段三反思

1. 异步桥接方案在生产环境中是否可靠？你是否需要做额外处理？
2. `EmailService.fetch_orders()` 的返回格式是否完全匹配需求？是否需要额外字段映射？
3. 是否需要调整阶段四/五的计划？

---

## 阶段四：启动报告

> **目标**：实现冷启动报告生成器 — 首次 Tick 后生成包含组件状态和业务摘要的启动报告，邮件+IM 双通道发送。

### 准备工作

```
emily-core/emily_core/project/ops/scheduler.py       # [读] _generate_startup_report() 当前代码
emily-core/emily_core/repositories/sm_node_repo.py    # [读] 节点统计查询方法
emily-core/emily_core/outbound_bus.py                 # [读] OutboundEventBus 的 publish 方法
emily-core/emily_core/__init__.py                      # [读] EmilyCore 暴露的属性（db/llm/maxkb 状态）
```

### 步骤清单

#### 步骤 4.1：创建 `project/ops/startup_report.py`

**内容要求**：

1. `generate_startup_report(ctx: TickContext, config: OpsConfig) -> dict`
   - `startup_time` / `environment` / `instance_id` / `version`
   - 组件状态：`db_status` (Boolean) / `llm_status` / `maxkb_status` / `email_status` / `pipeline_status`（字符串，值为 `"OK"` 或具体错误信息）
   - 业务状态：`nodes_completed` / `nodes_in_progress` / `nodes_blocked` / `nodes_total`（Integer）
   - `report_content`：完整 Markdown 格式的报告文本

2. 辅助函数（模块内部）：
   - `_detect_environment()` — 从环境变量或配置文件推断（production/staging/dev）
   - `_get_instance_id()` — `socket.gethostname()`
   - `_get_version()` — 尝试从 `emily_core.__version__` 或 git describe 读取
   - `_check_db()` — 尝试 `SELECT 1`
   - `_check_llm()` — 尝试 ping LLM API
   - `_check_maxkb()` — 尝试 ping MaxKB
   - `_check_email()` — 尝试连接 SMTP
   - `_check_pipeline()` — 检查 BUS 状态
   - `_count_nodes(status)` — 通过 SMNodeRepository 查询
   - `_render_markdown(report)` — 渲染 Markdown

**实现策略**：
- 所有 `_check_*` 函数用 try/except 包裹，失败返回错误字符串而非抛异常
- `_count_nodes` 可以直接用 SQLAlchemy session 查询 `sm_nodes` 表
- Markdown 渲染用简单的字符串拼接（不引入外部模板库）

#### 步骤 4.2：修改 `project/ops/scheduler.py` — 扩展发送逻辑

**操作**：修改 `OpsScheduler.__init__()` 增加 `outbound_bus` 参数：

```python
def __init__(self, config, db_repo, fallback, email_service=None, outbound_bus=None):
    ...
    self._outbound_bus = outbound_bus
```

修改 `_generate_startup_report()` 增加双通道发送：

```python
def _generate_startup_report(self, ctx, probe_results):
    from .startup_report import generate_startup_report
    report = generate_startup_report(ctx, self._config)
    sent_any = False

    # 通道 1: 邮件
    if self._email_service and self._config.mailbox_enabled:
        try:
            _send_email_report_sync(self._email_service, report)
            sent_any = True
        except Exception as e:
            logger.warning("Startup report email send failed: %s", e)

    # 通道 2: IM
    if self._outbound_bus:
        try:
            self._outbound_bus.publish("reply", {"text": report["report_content"]})
            sent_any = True
        except Exception as e:
            logger.warning("Startup report IM send failed: %s", e)

    # 持久化到 DB
    try:
        self._db_repo.save_startup_report(report)
    except Exception as e:
        logger.error("Save startup report failed, fallback to MD: %s", e)
        self._fallback.write_startup_report(report)
        return

    # 标记已发送
    if sent_any:
        try:
            self._db_repo.mark_report_sent(report["tick_id"])
        except Exception:
            pass
```

#### 步骤 4.3：在 `_init_ops_module()` 中传递 outbound_bus

在 `EmilyCore._init_ops_module()` 中，构造 `OpsScheduler` 时传入 `self._outbound_bus`（如果存在）。

---

### 阶段四自验收

#### A. 报告内容完整性

```bash
uv run python -c "
from emily_core.project.ops.probe_base import TickContext
from emily_core.project.ops.startup_report import generate_startup_report
from emily_core.project.ops.config import OpsConfig
from datetime import datetime, timezone

ctx = TickContext(tick_id='test', tick_number=1, start_time=datetime.now(timezone.utc))
report = generate_startup_report(ctx, OpsConfig())

# 必需字段检查
required = ['startup_time', 'environment', 'instance_id', 'version',
            'db_status', 'llm_status', 'maxkb_status', 'email_status', 'pipeline_status',
            'nodes_completed', 'nodes_in_progress', 'nodes_blocked', 'nodes_total',
            'report_content']
for key in required:
    assert key in report, f'Missing key: {key}'

assert isinstance(report['report_content'], str)
assert len(report['report_content']) > 0

print('Startup report content checks passed')
"
```

#### B. 冷启动判定

```bash
# 代码审查确认 _is_cold_start() 不依赖内存计数器
grep -n '_is_cold_start\|_tick_count' emily-core/emily_core/project/ops/scheduler.py
# _is_cold_start 应调用 self._db_repo.get_latest_startup_report，而非读取 _tick_count
```

#### C. 双通道发送

```bash
# 确认 scheduler.py 中有独立的 try/except 包裹邮件和 IM 发送
grep -c 'except.*:' emily-core/emily_core/project/ops/scheduler.py
# 应该 > 3（至少包括邮件异常、IM异常、持久化异常各一个）
```

---

### 阶段四反思

1. 启动报告中的组件状态检测是否能真实反映系统健康？是否有哪些检查是"假阳性"（不准确）的？
2. 报告内容 Markdown 是否可读？信息密度是否足够？
3. 是否需要调整阶段五的计划？

---

## 阶段五：集成收尾

> **目标**：最终集成 — health 端点、文档同步、烟雾测试适配。

### 准备工作

```
scripts/smoke_test.py                # [读] 烟雾测试当前逻辑
docs/代码文件目录.md                  # [读] 当前文档内容
docs/业务模块与运转全景.md            # [读] 当前全景图
docs/数据库设计.md                    # [读] 当前数据库文档
docs/开发记录.md                      # [读] 当前开发记录
```

### 步骤清单

#### 步骤 5.1：health() 端点确认

`EmilyCore.health()` 应已自动包含 ops 状态（因为 `ProjectAgent.status()` 已在阶段一包含 ops 键）。**验证即可，无需额外修改。**

```bash
curl http://localhost:18080/api/v1/health | python -m json.tool | grep -A5 ops
```

如果 ops 键不存在，检查 `EmilyCore.health()` 是否正确调用了 `self._project_agent.status()`。

#### 步骤 5.2：烟雾测试适配

**操作**：打开 `scripts/smoke_test.py`

确保 Mock 模式下 ops 模块不会尝试连接真实 DB/邮箱。如果 smoke_test 不加载 EmilyCore，则无需修改。如果它调用了 `handle_message()` 触发全链路初始化，确保 Mock 配置中 ops 不会阻塞。

最简单的方案：在烟雾测试中检查 `ops_enabled` 是否为 True，如果是 Mock 模式则临时设为 False。

#### 步骤 5.3：更新 `.env.example`

在文件末尾追加：

```
# Ops Scheduler
EMILY_OPS_ENABLED=true
# EMILY_OPS_MAILBOX_ENABLED=true
# EMILY_OPS_MAIL_IMAP_HOST=imap.qq.com
# EMILY_OPS_MAIL_IMAP_PORT=993
# EMILY_OPS_MAIL_USERNAME=ops@company.com
# EMILY_OPS_MAIL_PASSWORD=your-password
# EMILY_OPS_MAIL_SENDER_WHITELIST=admin@company.com
```

#### 步骤 5.4：更新 `docs/代码文件目录.md`

新增以下条目（搜索 `project/` 相关的 section，在其下追加）：

```
| `project/ops/__init__.py` | 运维模块总入口 |
| `project/ops/probe_base.py` | Probe ABC + ProbeFinding + TickContext |
| `project/ops/probe_registry.py` | Probe 注册器 |
| `project/ops/scheduler.py` | OpsScheduler 调度执行器 |
| `project/ops/config.py` | OpsConfig 运维配置 |
| `project/ops/models.py` | 5 个 ORM 模型 |
| `project/ops/startup_report.py` | 冷启动报告生成器 |
| `project/ops/probes/stale_probe.py` | 卡滞检测探针 |
| `project/ops/probes/mailbox_probe.py` | 邮箱轮询探针 |
| `project/ops/probes/health_probe.py` | 健康检查探针 |
| `project/ops/repositories/ops_repo.py` | 运维表 CRUD |
| `project/ops/persistence/fallback_writer.py` | 优雅降级 MD/JSONL 写入 |
```

#### 步骤 5.5：更新 `docs/业务模块与运转全景.md`

在模块清单中新增运维模块条目，简述：
- 位置：`project/ops/`
- 职责：项目级运维调度（Probe 发现 + DB 持久化 + 降级）
- 挂载：作为 ProjectAgent 的 Phase 3，每 tick 300s 执行
- Probe 清单：stale_probe（卡滞检测）、mailbox_probe（邮箱轮询）、health_probe（占位）

#### 步骤 5.6：更新 `docs/数据库设计.md`

新增 5 张表的简要描述，引用 DDL 文件路径。

#### 步骤 5.7：更新 `docs/开发记录.md`

新增 ADR 条目，记录运维模块的关键设计决策：
- 嵌入 ProjectAgent 而非独立 tick
- 复用 EmailService 而非 raw imaplib
- Probe 接口模式（可插拔探针）

#### 步骤 5.8：更新 `CLAUDE.md`

在"关键文件索引"表中新增 ops 相关条目。

---

### 阶段五自验收

#### A. health 端点

```bash
curl -s http://localhost:18080/api/v1/health | python -c "
import json, sys
data = json.load(sys.stdin)
pa = data.get('project_agent', {})
ops = pa.get('ops', {})
assert 'enabled' in ops, 'ops must have enabled'
assert 'probes_registered' in ops, 'ops must have probes_registered'
assert 'probes_enabled' in ops, 'ops must have probes_enabled'
assert 'consecutive_failures' in ops, 'ops must have consecutive_failures'
print('health endpoint ops check passed')
"
```

#### B. 烟雾测试

```bash
uv run python scripts/smoke_test.py
# 应正常通过，无报错
```

#### C. 环境变量

```bash
grep 'EMILY_OPS_ENABLED' .env.example
# 应有输出
```

#### D. 文档完整性

```bash
# 确认 docs 中有 ops 相关内容
grep -l 'ops' docs/代码文件目录.md   # 应有输出
grep -l '运维模块\|OpsScheduler\|ops_scheduler' docs/业务模块与运转全景.md  # 应有输出
grep -l 'ops_tick_log' docs/数据库设计.md  # 应有输出
```

---

### 阶段五反思（终期反思）

1. **整体完成度**：5 个阶段的目标是否全部达成？哪些地方有妥协？
2. **代码质量**：是否遵循了项目的编码规范？与现有代码风格一致吗？
3. **已知限制**：
   - `MailboxProbe` 的异步桥接在极端并发场景下是否可靠？
   - `FallbackWriter` 的日志轮转是否足够？
   - 启动报告中的组件检测覆盖是否完整？
4. **后续建议**：如果你（AI）再次执行类似任务，这份手册有哪些可以改进的地方？

---

## 附录 A：文件变更总览

### 新增文件（16 个）

```
emily-core/emily_core/project/ops/__init__.py
emily-core/emily_core/project/ops/config.py
emily-core/emily_core/project/ops/probe_base.py
emily-core/emily_core/project/ops/probe_registry.py
emily-core/emily_core/project/ops/scheduler.py
emily-core/emily_core/project/ops/models.py
emily-core/emily_core/project/ops/startup_report.py
emily-core/emily_core/project/ops/probes/__init__.py
emily-core/emily_core/project/ops/probes/stale_probe.py
emily-core/emily_core/project/ops/probes/mailbox_probe.py
emily-core/emily_core/project/ops/probes/health_probe.py
emily-core/emily_core/project/ops/repositories/__init__.py
emily-core/emily_core/project/ops/repositories/ops_repo.py
emily-core/emily_core/project/ops/persistence/__init__.py
emily-core/emily_core/project/ops/persistence/fallback_writer.py
emily-core/emily_core/infrastructure/database/scripts/003_create_ops_tables.sql
```

### 修改文件（14 个）

```
emily-core/emily_core/config.py
emily-core/emily_core/bootstrap.py
emily-core/emily_core/__init__.py
emily-core/emily_core/project/__init__.py
emily-core/emily_core/project/project_agent.py
emily-core/emily_core/project/project_agent_config.py
emily-core/emily_core/infrastructure/database/models.py
scripts/smoke_test.py
.env.example
docs/代码文件目录.md
docs/业务模块与运转全景.md
docs/数据库设计.md
docs/开发记录.md
CLAUDE.md
```

---

## 附录 B：每阶段校验速查

| 阶段 | 核心产物 | 一键校验命令 |
|------|---------|-------------|
| 一 | Probe 接口 + Scheduler + DB 表 | `uv run python -c "from emily_core.project.ops.scheduler import OpsScheduler; print('OK')"` |
| 二 | StaleProbe 适配 | `grep 'stale_detector.run()' emily-core/emily_core/project/project_agent.py` |
| 三 | MailboxProbe（复用 EmailService） | `grep -L 'imaplib' emily-core/emily_core/project/ops/probes/mailbox_probe.py` |
| 四 | 启动报告 | `uv run python -c "from emily_core.project.ops.startup_report import generate_startup_report; print('OK')"` |
| 五 | 集成收尾 | `uv run python scripts/smoke_test.py && curl -s localhost:18080/api/v1/health \| grep ops` |
