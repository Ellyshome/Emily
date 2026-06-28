# Emily 运维模块 (ops_scheduler) 详细设计说明书

> **版本**：v1.0
> **最后更新**：2024-06-27
> **设计人**：架构师 / 资深运维工程师

---

## 📋 目录

1. [模块概述](#1-模块概述)
2. [架构设计](#2-架构设计)
3. [核心组件详细设计](#3-核心组件详细设计)
4. [数据库表结构](#4-数据库表结构)
5. [接口定义](#5-接口定义)
6. [优雅降级机制](#6-优雅降级机制)
7. [配置项说明](#7-配置项说明)
8. [实施计划](#8-实施计划)
9. [测试策略](#9-测试策略)
10. [运维手册](#10-运维手册)

---

## 1. 模块概述

### 1.1 背景

当前 Emily 项目的运维调度能力（Tick 循环）内嵌在 ProjectAgent 中，高内聚导致：
- 新增运维检查项需修改 ProjectAgent 核心代码
- 无法独立测试运维探针
- 每个检查项无法独立设置执行频率
- 缺少运维数据持久化和审计能力

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| **解耦** | Tick 调度从 ProjectAgent 独立出来，成为可插拔模块 |
| **可扩展** | 新增运维检查只需实现 Probe 接口，无需修改核心调度 |
| **可观测** | 所有运维操作持久化到数据库，支持审计和追溯 |
| **高可用** | 优雅降级，DB/邮箱不可用时本地落盘，不影响核心业务 |
| **多通道** | 支持邮箱作为运维命令输入通道 |

### 1.3 设计原则

1. **单一职责**：ops_scheduler 只负责"发现"和"记录"，不负责"执行"
2. **失败隔离**：一个 Probe 失败不影响其他 Probe 运行
3. **数据不丢**：任何渠道失败都有本地 MD 备份
4. **渐进式**：可与现有 ProjectAgent 共存，平滑迁移

---

## 2. 架构设计

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        ProjectAgent (协调者)                      │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                  ops_scheduler (本模块)                     │  │
│  │                                                             │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │                Tick Scheduler (核心)                  │  │  │
│  │  │  ┌───────────────────────────────────────────────┐   │  │  │
│  │  │  │ Advisory Lock (PostgreSQL)                    │   │  │  │
│  │  │  └───────────────────┬───────────────────────────┘   │  │  │
│  │  │                      │ Tick ID (UUID per round)      │  │  │
│  │  └──────────────────────┼────────────────────────────────┘  │  │
│  │                         │                                  │  │  │
│  │  ┌──────────────────────┼──────────────────────────────┐  │  │
│  │  │  Probe Registry      │                               │  │  │
│  │  │                      ▼                               │  │  │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │  │
│  │  │  │ Stale Probe │  │ Health Probe│  │ Mail Probe  │  │  │  │
│  │  │  │ (卡滞检测)  │  │ (健康检查)  │  │ (邮箱轮询)  │  │  │  │
│  │  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │  │  │
│  │  └─────────┼─────────────────┼─────────────────┼─────────┘  │  │
│  │            │                 │                 │           │  │
│  └────────────┼─────────────────┼─────────────────┼────────────┘  │
│               │                 │                 │              │
│               └─────────────────┴─────────────────┘              │
│                                 │                                 │
│  ┌─────────────────────────────▼───────────────────────────────┐ │
│  │                      统一事件分发层                            │ │
│  │                                                               │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌───────────────────────┐ │ │
│  │  │ DB 持久化   │  │ 邮件命令    │  │ 启动报告生成器        │ │ │
│  │  │ (正常路径)  │  │ 提交       │  │                      │ │ │
│  │  └─────────────┘  └─────────────┘  └───────────────────────┘ │ │
│  │         │                │                                       │ │
│  └─────────┼────────────────┼───────────────────────────────────────┘ │
│            │                │                                       │
│            ▼                ▼                                       │
│  ┌─────────────────┐  ┌─────────────────┐                          │
│  │ ops_* 数据库表  │  │  ProjectAgent    │                          │
│  │ (运维审计数据)  │  │  (实际执行层)    │                          │
│  └─────────────────┘  └─────────────────┘                          │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                      优雅降级 (Fallback)                       │ │
│  │                                                               │ │
│  │  DB 失败 → 本地 MD 落盘 (logs/ops_fallback_*.md)              │ │
│  │  邮件失败 → 本地 MD 缓存 + 指数退避重试                        │ │
│  └─────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
```

### 2.2 模块目录结构

```
emily-core/emily_core/project/
├── ops_scheduler/              # ✅ 新增：运维调度模块
│   ├── __init__.py
│   ├── scheduler.py            # Tick Scheduler 核心 + Advisory Lock
│   ├── tick_context.py         # Tick 上下文 + Tick ID 机制
│   ├── probe_base.py          # Probe 抽象基类
│   ├── probe_registry.py      # Probe 注册器
│   │
│   ├── probes/                 # 各探针实现
│   │   ├── __init__.py
│   │   ├── stale_probe.py     # 卡滞节点检测 (从 maintenance 迁入)
│   │   ├── health_probe.py    # 健康度检查 (Phase 2)
│   │   └── mailbox_probe.py   # 邮箱轮询
│   │
│   ├── persistence/           # 持久化层
│   │   ├── __init__.py
│   │   ├── db_repository.py   # DB 写入
│   │   └── fallback_writer.py # 优雅降级 MD 本地写入
│   │
│   ├── models.py              # 数据模型定义
│   ├── config.py              # 模块配置
│   └── startup_report.py      # 启动报告生成器
│
├── maintenance/               # 保留，迁移完成后可删除
│   └── stale_detector.py
├── health/                    # Phase 2
├── ops/                       # 执行层
└── project_agent.py           # 协调者，持有 Scheduler 实例
```

---

## 3. 核心组件详细设计

### 3.1 Tick Scheduler (核心调度器)

**职责**：
- 管理 Tick 循环（5 分钟间隔）
- 管理 PostgreSQL Advisory Lock（多实例互斥）
- 触发所有已注册 Probe 执行
- 生成 Tick ID（每轮唯一标识）
- 统一错误处理和降级触发

**关键代码骨架**：

```python
# ops_scheduler/scheduler.py

from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import List
from uuid import uuid4

from .tick_context import TickContext
from .probe_registry import ProbeRegistry
from .persistence.db_repository import DBRepository
from .persistence.fallback_writer import FallbackWriter

logger = logging.getLogger("emily.ops_scheduler")


class TickScheduler:
    """运维调度核心
    
    设计原则：
    - 单例模式（每个 Emily 实例一个 Scheduler）
    - 多实例互斥通过 Advisory Lock 保证
    - 每轮 Tick 生成唯一 Tick ID 用于追溯
    """

    _instance: "TickScheduler" = None

    @classmethod
    def get_instance(cls) -> "TickScheduler":
        if not cls._instance:
            cls._instance = TickScheduler()
        return cls._instance

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._registry = ProbeRegistry()
        self._db_repo = DBRepository()
        self._fallback = FallbackWriter()
        self._tick_count = 0
        self._consecutive_failures = {}  # probe_name -> count

    def register_probe(self, probe):
        """注册 Probe"""
        self._registry.register(probe)

    async def start(self):
        """启动调度循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TickScheduler started (interval=300s)")

    async def stop(self):
        """优雅停止"""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TickScheduler stopped")

    async def _loop(self):
        """主循环"""
        while self._running:
            try:
                await self._run_one_tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "TickScheduler loop error (degraded): %s",
                    e, exc_info=True,
                )
            await asyncio.sleep(300)  # 5 分钟

    async def _run_one_tick(self):
        """执行一轮 Tick（带锁保护）"""
        # 1. 尝试获取分布式锁
        if not await self._try_acquire_lock():
            return  # 其他实例已在执行

        # 2. 创建 Tick 上下文
        tick_id = str(uuid4())
        ctx = TickContext(
            tick_id=tick_id,
            tick_number=self._tick_count,
            start_time=datetime.utcnow(),
        )

        logger.info("Starting tick #%d (id=%s)", self._tick_count, tick_id[:8])

        # 3. 执行所有 Probe
        probe_results = []
        for probe in self._registry.get_enabled_probes():
            result = await self._run_probe_safe(probe, ctx)
            probe_results.append(result)

        # 4. 持久化结果（带优雅降级）
        await self._persist_results(ctx, probe_results)

        # 5. 检查是否为冷启动后的首轮 Tick
        if self._tick_count == 0:
            await self._generate_startup_report(ctx, probe_results)

        self._tick_count += 1

        logger.info(
            "Tick #%d completed (probes=%d, duration=%dms)",
            self._tick_count,
            len(probe_results),
            int((datetime.utcnow() - ctx.start_time).total_seconds() * 1000),
        )

    async def _run_probe_safe(self, probe, ctx: TickContext):
        """安全执行单个 Probe（错误隔离）"""
        probe_name = probe.name()
        start_time = datetime.utcnow()

        try:
            # 检查冷却时间
            if not probe.should_run(ctx):
                return {"probe": probe_name, "status": "SKIPPED"}

            # 执行 Probe
            findings = await probe.run(ctx)

            # 重置失败计数
            self._consecutive_failures[probe_name] = 0

            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            return {
                "probe": probe_name,
                "status": "SUCCESS",
                "findings_count": len(findings) if findings else 0,
                "findings": findings,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            # 累计失败次数
            failures = self._consecutive_failures.get(probe_name, 0) + 1
            self._consecutive_failures[probe_name] = failures

            # 连续失败 N 次后临时禁用
            if failures >= 3:
                logger.warning(
                    "Probe %s failed %d times, will skip for next hour",
                    probe_name, failures,
                )

            return {
                "probe": probe_name,
                "status": "FAILED",
                "error": str(e),
                "duration_ms": int((datetime.utcnow() - start_time).total_seconds() * 1000),
            }

    async def _persist_results(self, ctx: TickContext, results: List[dict]):
        """持久化 Tick 结果，失败则优雅降级到本地 MD"""
        try:
            await self._db_repo.save_tick_results(ctx, results)
        except Exception as e:
            logger.error("DB persistence failed, falling back to MD: %s", e)
            await self._fallback.write_tick_results(ctx, results)

    async def _generate_startup_report(self, ctx: TickContext, results: List[dict]):
        """首轮 Tick 后生成启动报告"""
        from .startup_report import generate_startup_report
        report = generate_startup_report(ctx, results)
        # 尝试发送邮件，失败则本地保存
        try:
            await self._send_startup_report(report)
        except Exception as e:
            logger.error("Send startup report failed, saving locally: %s", e)
            await self._fallback.write_startup_report(report)

    async def _try_acquire_lock(self) -> bool:
        """获取 PostgreSQL 分布式锁"""
        from ...infrastructure.database.session import get_session_raw
        from sqlalchemy import text

        session = get_session_raw()
        try:
            result = session.execute(
                text("SELECT pg_try_advisory_lock(hashtext('ops_scheduler:tick'))")
            )
            return result.scalar()
        except Exception:
            # DB 不可用，返回 False 不执行 Tick，但不崩溃
            return False
        finally:
            session.close()
```

---

### 3.2 Probe 抽象基类

```python
# ops_scheduler/probe_base.py

from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, List
from dataclasses import dataclass

from .tick_context import TickContext


@dataclass
class ProbeFinding:
    """Probe 发现的单个问题"""
    finding_type: str  # STALE_NODE / MILESTONE_WARNING / MAIL_COMMAND
    severity: str      # INFO / WARNING / CRITICAL
    target_id: str     # 节点 ID / 其他标识
    message: str
    metadata: dict = None


class Probe(ABC):
    """所有 Probe 的抽象基类"""

    @abstractmethod
    def name(self) -> str:
        """Probe 名称（唯一标识）"""
        pass

    @abstractmethod
    def enabled(self) -> bool:
        """是否启用（从配置读取）"""
        pass

    @abstractmethod
    def interval_seconds(self) -> int:
        """执行间隔（秒），可独立于全局 Tick"""
        return 300  # 默认 5 分钟

    @abstractmethod
    async def run(self, ctx: TickContext) -> List[ProbeFinding]:
        """执行检查，返回发现的问题列表"""
        pass

    def should_run(self, ctx: TickContext) -> bool:
        """是否应该在本轮 Tick 执行"""
        if not self.enabled():
            return False

        # 检查执行间隔
        last_run = ctx.get_last_run_time(self.name())
        if last_run and (ctx.start_time - last_run).total_seconds() < self.interval_seconds():
            return False

        return True
```

---

### 3.3 Mailbox Probe (邮箱轮询探针)

```python
# ops_scheduler/probes/mailbox_probe.py

from __future__ import annotations
import imaplib
import email
from datetime import datetime
from typing import List

from ..probe_base import Probe, ProbeFinding
from ..tick_context import TickContext


class MailboxProbe(Probe):
    """邮箱命令探针
    
    职责：
    - 每 5 分钟轮询运维邮箱
    - 验证发件人白名单
    - 解析 order 主题邮件
    - 提交给 ProjectAgent（只做到这一步）
    """

    def name(self) -> str:
        return "mailbox"

    def enabled(self) -> bool:
        from ..config import ops_config
        return ops_config.mailbox_enabled

    def interval_seconds(self) -> int:
        return 300  # 5 分钟

    async def run(self, ctx: TickContext) -> List[ProbeFinding]:
        findings = []

        try:
            # 1. 连接邮箱
            from ..config import ops_config
            mail = imaplib.IMAP4_SSL(ops_config.mail_imap_host, ops_config.mail_imap_port)
            mail.login(ops_config.mail_username, ops_config.mail_password)
            mail.select("INBOX")

            # 2. 搜索未读 + 主题包含 "order"
            _, data = mail.search(None, '(UNSEEN SUBJECT "order")')

            for num in data[0].split():
                # 3. 解析邮件
                _, msg_data = mail.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])

                # 4. 验证发件人白名单
                mail_from = self._extract_from(msg)
                if mail_from not in ops_config.mail_sender_whitelist:
                    findings.append(ProbeFinding(
                        finding_type="MAIL_UNAUTHORIZED",
                        severity="WARNING",
                        target_id=mail_from,
                        message=f"拒绝非白名单发件人: {mail_from}",
                    ))
                    continue

                # 5. 提取命令内容
                subject = msg["Subject"]
                body = self._extract_body(msg)

                # 6. 生成命令发现（后续由 ProjectAgent 处理）
                findings.append(ProbeFinding(
                    finding_type="MAIL_COMMAND",
                    severity="INFO",
                    target_id=subject,
                    message=body,
                    metadata={
                        "from": mail_from,
                        "date": msg["Date"],
                    },
                ))

                # 7. 标记已读
                mail.store(num, "+FLAGS", "\\Seen")

            mail.logout()

        except Exception as e:
            # 失败不抛出，降级处理
            findings.append(ProbeFinding(
                finding_type="MAIL_ERROR",
                severity="WARNING",
                target_id="connection",
                message=f"邮箱连接失败: {str(e)}",
            ))

        return findings

    def _extract_from(self, msg) -> str:
        """提取发件人邮箱"""
        from_header = msg["From"]
        if "<" in from_header:
            return from_header.split("<")[1].split(">")[0]
        return from_header

    def _extract_body(self, msg) -> str:
        """提取邮件正文"""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    return part.get_payload(decode=True).decode()
        else:
            return msg.get_payload(decode=True).decode()
        return ""
```

---

## 4. 数据库表结构

### 4.1 表总览

| 表名 | 用途 |
|------|------|
| `ops_tick_log` | 每轮 Tick 的总日志 |
| `ops_probe_execution` | 每个 Probe 的执行详情 |
| `ops_finding` | Probe 发现的问题明细 |
| `ops_mail_audit` | 邮箱命令审计记录 |
| `ops_startup_report` | 启动报告历史 |

---

### 4.2 DDL 语句

```sql
-- =====================================================
-- Emily Ops Scheduler 运维模块数据库表
-- 创建时间：2024-06-27
-- =====================================================

-- 1. Tick 总日志表
CREATE TABLE IF NOT EXISTS ops_tick_log (
    tick_id UUID PRIMARY KEY,
    tick_number INTEGER NOT NULL,              -- 第几轮 Tick
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    duration_ms INTEGER NOT NULL,
    probes_executed INTEGER NOT NULL,          -- 执行的 Probe 数量
    probes_success INTEGER NOT NULL,           -- 成功数量
    probes_failed INTEGER NOT NULL,            -- 失败数量
    total_findings INTEGER NOT NULL,           -- 发现问题总数
    instance_id VARCHAR(200),                  -- 实例 ID
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ops_tick_log_time ON ops_tick_log(start_time);


-- 2. Probe 执行详情表
CREATE TABLE IF NOT EXISTS ops_probe_execution (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tick_id UUID REFERENCES ops_tick_log(tick_id),
    probe_name VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,               -- SUCCESS / FAILED / SKIPPED
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    duration_ms INTEGER NOT NULL,
    findings_count INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ops_probe_execution_tick ON ops_probe_execution(tick_id);
CREATE INDEX idx_ops_probe_execution_name ON ops_probe_execution(probe_name);


-- 3. 发现的问题明细表
CREATE TABLE IF NOT EXISTS ops_finding (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tick_id UUID REFERENCES ops_tick_log(tick_id),
    probe_name VARCHAR(100) NOT NULL,
    finding_type VARCHAR(100) NOT NULL,        -- STALE_NODE / MILESTONE_WARNING / MAIL_COMMAND
    severity VARCHAR(50) NOT NULL,             -- INFO / WARNING / CRITICAL
    target_id VARCHAR(200),                    -- 节点 ID 或其他标识
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ops_finding_tick ON ops_finding(tick_id);
CREATE INDEX idx_ops_finding_type ON ops_finding(finding_type);


-- 4. 邮箱命令审计表
CREATE TABLE IF NOT EXISTS ops_mail_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tick_id UUID REFERENCES ops_tick_log(tick_id),
    mail_uid VARCHAR(100),
    mail_from VARCHAR(200) NOT NULL,
    mail_subject VARCHAR(500) NOT NULL,
    mail_date TIMESTAMP,
    command_text TEXT,                          -- 解析出的命令
    received_at TIMESTAMP NOT NULL,
    dispatched BOOLEAN DEFAULT false,           -- 是否已提交给 ProjectAgent
    dispatched_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ops_mail_audit_from ON ops_mail_audit(mail_from);
CREATE INDEX idx_ops_mail_audit_dispatched ON ops_mail_audit(dispatched);


-- 5. 启动报告历史表
CREATE TABLE IF NOT EXISTS ops_startup_report (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tick_id UUID REFERENCES ops_tick_log(tick_id),
    startup_time TIMESTAMP NOT NULL,
    environment VARCHAR(100) NOT NULL,         -- production / staging / dev
    instance_id VARCHAR(200),
    version VARCHAR(100),                      -- Emily 版本号

    -- 组件状态
    db_status VARCHAR(50),                     -- OK / FAILED
    llm_status VARCHAR(50),                    -- OK / NOT_CONFIGURED
    bus_status VARCHAR(50),                    -- OK / FAILED

    -- 业务状态
    projects_total INTEGER,
    nodes_completed INTEGER,
    nodes_in_progress INTEGER,
    nodes_blocked INTEGER,

    report_content TEXT,                       -- 完整报告内容（Markdown）
    sent_to_mail BOOLEAN DEFAULT false,        -- 是否已发送邮件
    sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ops_startup_report_time ON ops_startup_report(created_at);


-- =====================================================
-- 表注释
-- =====================================================
COMMENT ON TABLE ops_tick_log IS 'Tick 总日志表';
COMMENT ON TABLE ops_probe_execution IS 'Probe 执行详情表';
COMMENT ON TABLE ops_finding IS 'Probe 发现的问题明细表';
COMMENT ON TABLE ops_mail_audit IS '邮箱命令审计表';
COMMENT ON TABLE ops_startup_report IS '启动报告历史表';
```

---

## 5. 接口定义

### 5.1 与 ProjectAgent 的交互接口

```python
# ops_scheduler/__init__.py

from __future__ import annotations
from typing import List
from dataclasses import dataclass
from datetime import datetime


@dataclass
class MailCommand:
    """从邮箱解析出的命令"""
    id: str
    from_address: str
    subject: str
    body: str
    received_at: datetime


class OpsSchedulerFacade:
    """对外门面接口（供 ProjectAgent 调用）"""

    def __init__(self, scheduler):
        self._scheduler = scheduler

    async def get_pending_commands(self) -> List[MailCommand]:
        """ProjectAgent 拉取待执行的命令
        
        设计：拉取模式而非推送模式，避免耦合
        """
        # 从 ops_finding 表查询未处理的 MAIL_COMMAND
        pass

    async def mark_command_dispatched(self, command_id: str):
        """标记命令已提交给执行层"""
        pass

    async def get_startup_report(self, tick_id: str) -> dict:
        """获取启动报告"""
        pass
```

---

## 6. 优雅降级机制

### 6.1 降级策略矩阵

| 失败场景 | 降级动作 |
|---------|---------|
| **DB 连接失败** | 1. 所有 Tick 结果写入本地 MD 文件<br>2. 不阻塞 Tick 循环<br>3. DB 恢复后不自动补写（运维手动确认） |
| **邮箱接收失败** | 1. 本次跳过邮箱检查<br>2. 记录警告日志<br>3. 指数退避（下次延迟 10 分钟，最多 1 小时）<br>4. 不影响其他 Probe |
| **邮箱发送失败** | 1. 启动报告/告警内容写入本地 MD<br>2. 下次 Tick 重试发送（最多 3 次） |
| **单个 Probe 执行失败** | 1. 不影响其他 Probe<br>2. 错误记入日志<br>3. 连续失败 3 次 → 临时禁用 1 小时 |
| **Advisory Lock 获取失败** | 1. 静默跳过本轮 Tick<br>2. 不记错误（正常情况，多实例部署） |

### 6.2 本地 MD 备份格式

**文件名规则**：
```
logs/ops_fallback_YYYYMMDD_HHMMSS_{type}.md

其中 type：
- tick_#N      # 第 N 轮 Tick 结果
- startup      # 启动报告
- mailbox      # 邮箱命令备份
```

**文件格式示例**：

```markdown
# Emily 运维降级备份日志

## 基本信息
- 生成时间：2024-06-27 10:30:45
- 备份类型：tick_#1
- 失败原因：PostgreSQL connection refused
- 实例 ID：emily-core-abc123
- Tick ID：550e8400-e29b-41d4-a716-446655440000

## Tick 执行摘要
- 执行 Probe 数：3
- 成功：2
- 失败：1

## 各 Probe 详情

### 1. stale_probe (SUCCESS)

发现 3 个卡滞节点：

| 节点 ID | 节点名称 | 状态 | 卡滞天数 | 负责人 |
|---------|---------|------|---------|--------|
| 1.2 | 方案设计评审 | BLOCKED | 23 | 设计部 |
| 2.1 | 施工图审查 | IN_PROGRESS | 16 | 设计部 |
| 3.5 | 施工许可办理 | BLOCKED | 31 | 工程部 |

### 2. mailbox_probe (SUCCESS)

发现 1 条新命令：

| 发件人 | 主题 | 接收时间 |
|-------|------|---------|
| admin@company.com | [order] 导出项目周报 | 2024-06-27 10:25:00 |

命令内容：
```
请导出锦绣花园项目的本周周报，发送给项目组。
```

### 3. health_probe (FAILED)

错误信息：
```
ConnectionRefusedError: [Errno 111] Connection refused
```

--
本文件为自动生成的降级备份，DB 恢复后请确认数据是否已同步
联系：Emily 运维团队
```

---

## 7. 配置项说明

### 7.1 Config 类新增字段

```python
# emily_core/config.py 新增

@dataclass
class Config:
    # ... 已有配置 ...

    # ==========================================
    # Ops Scheduler 模块配置
    # ==========================================
    ops_enabled: bool = True
    """运维调度模块总开关"""

    ops_tick_interval_seconds: int = 300
    """全局 Tick 间隔（秒）"""

    # --- Stale Probe ---
    ops_stale_probe_enabled: bool = True
    """卡滞节点检测开关"""
    ops_stale_threshold_days: int = 14
    """卡滞判定阈值（天）"""

    # --- Mailbox Probe ---
    ops_mailbox_enabled: bool = False
    """邮箱轮询开关"""
    ops_mail_imap_host: str = ""
    """IMAP 服务器地址"""
    ops_mail_imap_port: int = 993
    """IMAP 服务器端口"""
    ops_mail_username: str = ""
    """邮箱账号"""
    ops_mail_password: str = ""
    """邮箱密码"""
    ops_mail_sender_whitelist: list[str] = field(default_factory=list)
    """允许发命令的邮箱白名单，如 ["admin@company.com"]"""

    # --- Health Probe (Phase 2) ---
    ops_health_probe_enabled: bool = False
    """健康检查开关"""

    # --- 优雅降级配置 ---
    ops_fallback_log_dir: str = "logs/"
    """降级备份文件存储目录"""
```

### 7.2 环境变量映射

```bash
# .env 示例

# 运维模块总开关
EMILY_OPS_ENABLED=true

# 邮箱配置
EMILY_OPS_MAILBOX_ENABLED=true
EMILY_OPS_MAIL_IMAP_HOST=imap.company.com
EMILY_OPS_MAIL_IMAP_PORT=993
EMILY_OPS_MAIL_USERNAME=emily-ops@company.com
EMILY_OPS_MAIL_PASSWORD=your-password-here
EMILY_OPS_MAIL_SENDER_WHITELIST=admin@company.com,ops@company.com

# 降级配置
EMILY_OPS_FALLBACK_LOG_DIR=logs/
```

---

## 8. 实施计划

### 阶段一：核心骨架（2 天）

- [ ] 创建 `ops_scheduler/` 目录结构
- [ ] 实现 `TickScheduler` 核心（循环 + 锁）
- [ ] 实现 `Probe` 抽象基类
- [ ] 实现 `ProbeRegistry` 注册器
- [ ] 实现 `TickContext` Tick 上下文
- [ ] 单元测试：调度器基本功能

### 阶段二：Stale Probe 迁移（1 天）

- [ ] 把 `stale_detector.py` 迁移为 `StaleProbe`
- [ ] 适配新的 Probe 接口
- [ ] ProjectAgent 集成：持有 Scheduler 实例
- [ ] 验证：迁移前后功能一致

### 阶段三：持久化 + 降级（1.5 天）

- [ ] 数据库 DDL 执行
- [ ] 实现 `DBRepository` 持久化层
- [ ] 实现 `FallbackWriter` MD 本地写入
- [ ] 测试：DB 失败时降级到 MD

### 阶段四：邮箱 Probe（1.5 天）

- [ ] 实现 `MailboxProbe`
- [ ] 配置项完善
- [ ] ProjectAgent 拉取命令接口
- [ ] 白名单验证测试

### 阶段五：启动报告（1 天）

- [ ] 实现 `StartupReportGenerator`
- [ ] 首轮 Tick 触发逻辑
- [ ] 邮件发送 + 失败降级

**总计：7 人天**

---

## 9. 测试策略

### 9.1 单元测试

| 测试项 | 重点 |
|--------|------|
| TickScheduler | 锁机制、失败隔离、Tick ID 生成 |
| Probe 基类 | 执行间隔、启用开关 |
| FallbackWriter | MD 格式正确性、目录权限处理 |
| MailboxProbe | 白名单验证、邮件解析 |

### 9.2 集成测试

| 场景 | 验证点 |
|------|-------|
| 正常流程 | Tick 正常执行，数据正确写入 DB |
| DB 不可用 | 优雅降级到 MD，Tick 继续运行 |
| 邮箱不可用 | MailboxProbe 失败，其他 Probe 正常 |
| 多实例部署 | Advisory Lock 正确互斥，只有一个实例执行 |
| 冷启动 | 首轮 Tick 后正确生成启动报告 |

### 9.3 边界测试

| 场景 | 验证点 |
|------|-------|
| Tick 内某 Probe 崩溃 | 其他 Probe 不受影响 |
| 连续 3 次失败 | Probe 被临时禁用 |
| 邮箱白名单外发件人 | 命令被拒绝，记录警告 |
| MD 写入目录无权限 | 记录错误日志，不崩溃 |

---

## 10. 运维手册

### 10.1 日常运维

**查看 Tick 执行情况**：
```sql
-- 最近 10 轮 Tick
SELECT
    tick_number,
    start_time,
    duration_ms,
    probes_success,
    probes_failed,
    total_findings
FROM ops_tick_log
ORDER BY start_time DESC
LIMIT 10;
```

**查看发现的问题**：
```sql
-- 最近 24 小时的卡滞节点
SELECT * FROM ops_finding
WHERE finding_type = 'STALE_NODE'
  AND created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;
```

**查看邮箱命令历史**：
```sql
SELECT
    mail_from,
    mail_subject,
    received_at,
    dispatched
FROM ops_mail_audit
ORDER BY created_at DESC
LIMIT 20;
```

### 10.2 故障排查

**现象：Tick 一直不执行**
```bash
# 1. 检查总开关
grep EMILY_OPS_ENABLED .env

# 2. 查看日志
docker logs emily-core | grep ops_scheduler

# 3. 检查是否其他实例持有锁
SELECT pg_advisory_unlock(hashtext('ops_scheduler:tick'));
-- （谨慎执行，确认是死锁情况）
```

**现象：MD 降级文件不断生成**
```bash
# 1. 查看 DB 连接状态
docker exec emily-core python -c "from emily_core.infrastructure.database.session import get_session_raw; s = get_session_raw(); s.execute('SELECT 1'); print('OK')"

# 2. 查看降级文件内容
cat logs/ops_fallback_*.md | grep "失败原因"
```

### 10.3 常用维护命令

**临时禁用某个 Probe**：
```bash
# 修改 .env
EMILY_OPS_STALE_PROBE_ENABLED=false

# 重启生效
docker restart emily-core
```

**手动清理旧数据**：
```sql
-- 保留最近 30 天
DELETE FROM ops_tick_log WHERE start_time < NOW() - INTERVAL '30 days';
-- 级联会自动删除子表数据
```

---

## ✅ 设计确认清单

| 项 | 状态 | 说明 |
|----|------|------|
| Tick 从 ProjectAgent 解耦 | ✅ | 已设计独立 Scheduler |
| Probe 可插拔 | ✅ | 两级开关（全局 + 每个 Probe） |
| 运维数据持久化 | ✅ | 5 张表完整设计 |
| 邮箱轮询只收不执行 | ✅ | MailboxProbe 只生成 Finding，ProjectAgent 拉取 |
| 优雅降级 MD 备份 | ✅ | 格式 + 策略已定义 |
| 冷启动报告 | ✅ | 首轮 Tick 触发 |
| Tick ID 追溯机制 | ✅ | 贯穿所有操作 |
| 失败隔离 | ✅ | 每个 Probe try/except 隔离 |

---

**文档完成时间**：2024-06-27
**文档状态**：待评审 → 可实施
