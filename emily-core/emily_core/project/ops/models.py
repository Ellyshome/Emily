"""运维模块 ORM 模型 —— 5 张运维表。

表清单：
    ops_tick_log         — Tick 执行日志（主表）
    ops_probe_execution  — 探针执行记录
    ops_finding          — 探针发现结果
    ops_mail_audit       — 邮箱审计日志
    ops_startup_report   — 冷启动报告
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from emily_core.infrastructure.database.models import Base, _new_uuid, _utc_now


class OpsTickLog(Base):
    """Tick 执行日志表 —— 记录每轮 Tick 的元数据。

    每轮 Tick 写入一条记录，是 ops_probe_execution 和 ops_finding 的关联主表。
    """
    __tablename__ = "ops_tick_log"

    tick_id = Column(UUID(as_uuid=False), primary_key=True)
    tick_number = Column(Integer, nullable=False, default=0)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True))
    duration_ms = Column(Integer, default=0)
    probes_executed = Column(Integer, default=0)
    success = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    total_findings = Column(Integer, default=0)
    instance_id = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), default=_utc_now)


class OpsProbeExecution(Base):
    """探针执行记录表 —— 记录每个探针在每轮 Tick 中的执行情况。"""
    __tablename__ = "ops_probe_execution"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    tick_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ops_tick_log.tick_id"),
        nullable=False,
    )
    probe_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    duration_ms = Column(Integer, default=0)
    findings_count = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_utc_now)


class OpsFinding(Base):
    """探针发现结果表 —— 记录每个探针发现的具体问题。"""
    __tablename__ = "ops_finding"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    tick_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ops_tick_log.tick_id"),
        nullable=False,
    )
    probe_name = Column(String(100), nullable=False)
    finding_type = Column(String(100), nullable=False)
    severity = Column(String(50), nullable=False)
    target_id = Column(String(200), nullable=False)
    message = Column(Text)
    metadata_json = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), default=_utc_now)


class OpsMailAudit(Base):
    """邮箱审计日志表 —— 记录从邮箱接收到的运维命令。

    关键约束：mail_uid 有 UNIQUE 约束，确保邮件幂等去重。
    """
    __tablename__ = "ops_mail_audit"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    tick_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ops_tick_log.tick_id"),
        nullable=False,
    )
    mail_uid = Column(String(100), unique=True, nullable=False)
    mail_from = Column(String(200), default="")
    mail_subject = Column(String(500), default="")
    mail_date = Column(DateTime(timezone=True))
    command_text = Column(Text)
    received_at = Column(DateTime(timezone=True), default=_utc_now)
    dispatched = Column(Boolean, default=False)
    dispatched_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utc_now)


class OpsStartupReport(Base):
    """冷启动报告表 —— 记录每次冷启动时生成的系统状态报告。

    字段命名：
      • pipeline_status（不是 bus_status）—— Pipeline BUS 的状态
      • maxkb_status — MaxKB 知识库服务状态
      • email_status — 邮箱服务状态
      • db_status 是 Boolean 类型
    """
    __tablename__ = "ops_startup_report"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    tick_id = Column(
        UUID(as_uuid=False),
        ForeignKey("ops_tick_log.tick_id"),
        nullable=False,
    )
    startup_time = Column(DateTime(timezone=True), nullable=False)
    environment = Column(String(100), default="")
    instance_id = Column(String(200), default="")
    version = Column(String(100), default="")
    db_status = Column(Boolean, default=True)
    llm_status = Column(String(50), default="")
    maxkb_status = Column(String(50), default="")
    email_status = Column(String(50), default="")
    pipeline_status = Column(String(50), default="")
    projects_total = Column(Integer, default=0)
    nodes_completed = Column(Integer, default=0)
    nodes_in_progress = Column(Integer, default=0)
    nodes_blocked = Column(Integer, default=0)
    report_content = Column(Text)
    sent_to_mail = Column(Boolean, default=False)
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utc_now)


