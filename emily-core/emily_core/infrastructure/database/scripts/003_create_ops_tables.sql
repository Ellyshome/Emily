-- 003_create_ops_tables.sql
-- Emily 运维模块 (ops_scheduler) — 5 张运维表
--
-- 执行方式:
--   docker exec -it emily-postgres psql -U emily -d emily \
--     -f /path/to/003_create_ops_tables.sql
--
-- 依赖: 无（独立新增，不修改已有表）

-- ============================================================
-- 1. ops_tick_log — Tick 执行日志（主表）
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_tick_log (
    tick_id             UUID PRIMARY KEY,
    tick_number         INTEGER NOT NULL DEFAULT 0,
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ,
    duration_ms         INTEGER DEFAULT 0,
    probes_executed     INTEGER DEFAULT 0,
    success             INTEGER DEFAULT 0,
    failed              INTEGER DEFAULT 0,
    total_findings      INTEGER DEFAULT 0,
    instance_id         VARCHAR(200) DEFAULT '',
    created_at          TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE ops_tick_log IS 'Tick 执行日志 — 记录每轮运维巡检的元数据';
COMMENT ON COLUMN ops_tick_log.tick_id IS '本轮 Tick 的唯一 UUID';
COMMENT ON COLUMN ops_tick_log.tick_number IS '累计 Tick 计数';
COMMENT ON COLUMN ops_tick_log.start_time IS 'Tick 开始时间（UTC）';
COMMENT ON COLUMN ops_tick_log.end_time IS 'Tick 结束时间（UTC）';
COMMENT ON COLUMN ops_tick_log.duration_ms IS 'Tick 总耗时（毫秒）';
COMMENT ON COLUMN ops_tick_log.probes_executed IS '本轮执行的探针总数';
COMMENT ON COLUMN ops_tick_log.success IS '成功探针数';
COMMENT ON COLUMN ops_tick_log.failed IS '失败探针数';
COMMENT ON COLUMN ops_tick_log.total_findings IS '发现的问-题总数';
COMMENT ON COLUMN ops_tick_log.instance_id IS 'Emily Core 实例标识';

CREATE INDEX IF NOT EXISTS idx_ops_tick_log_time ON ops_tick_log (start_time);


-- ============================================================
-- 2. ops_probe_execution — 探针执行记录
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_probe_execution (
    id              UUID PRIMARY KEY,
    tick_id         UUID NOT NULL REFERENCES ops_tick_log(tick_id),
    probe_name      VARCHAR(100) NOT NULL,
    status          VARCHAR(50) NOT NULL,
    duration_ms     INTEGER DEFAULT 0,
    findings_count  INTEGER DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE ops_probe_execution IS '探针执行记录 — 记录每个探针在每轮 Tick 中的执行情况';
COMMENT ON COLUMN ops_probe_execution.tick_id IS '关联的 Tick ID → ops_tick_log.tick_id';
COMMENT ON COLUMN ops_probe_execution.probe_name IS '探针名称';
COMMENT ON COLUMN ops_probe_execution.status IS '执行状态：SUCCESS / SKIPPED / FAILED';
COMMENT ON COLUMN ops_probe_execution.findings_count IS '发现的问-题数';
COMMENT ON COLUMN ops_probe_execution.error_message IS '失败时的错误信息';

CREATE INDEX IF NOT EXISTS idx_ops_probe_exec_tick ON ops_probe_execution (tick_id);
CREATE INDEX IF NOT EXISTS idx_ops_probe_exec_name ON ops_probe_execution (probe_name);


-- ============================================================
-- 3. ops_finding — 探针发现结果
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_finding (
    id              UUID PRIMARY KEY,
    tick_id         UUID NOT NULL REFERENCES ops_tick_log(tick_id),
    probe_name      VARCHAR(100) NOT NULL,
    finding_type    VARCHAR(100) NOT NULL,
    severity        VARCHAR(50) NOT NULL,
    target_id       VARCHAR(200) NOT NULL,
    message         TEXT,
    metadata_json   JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE ops_finding IS '探针发现结果 — 记录每个探针发现的具体问题';
COMMENT ON COLUMN ops_finding.tick_id IS '关联的 Tick ID → ops_tick_log.tick_id';
COMMENT ON COLUMN ops_finding.finding_type IS '发现类型：STALE_NODE / MILESTONE_WARNING / MAIL_COMMAND 等';
COMMENT ON COLUMN ops_finding.severity IS '严重程度：INFO / WARNING / CRITICAL';
COMMENT ON COLUMN ops_finding.target_id IS '关联目标 ID（如 node_id / mail_uid）';
COMMENT ON COLUMN ops_finding.metadata_json IS '额外元数据（JSONB）';

CREATE INDEX IF NOT EXISTS idx_ops_finding_tick ON ops_finding (tick_id);
CREATE INDEX IF NOT EXISTS idx_ops_finding_type ON ops_finding (finding_type);


-- ============================================================
-- 4. ops_mail_audit — 邮箱审计日志
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_mail_audit (
    id              UUID PRIMARY KEY,
    tick_id         UUID NOT NULL REFERENCES ops_tick_log(tick_id),
    mail_uid        VARCHAR(100) NOT NULL,
    mail_from       VARCHAR(200) DEFAULT '',
    mail_subject    VARCHAR(500) DEFAULT '',
    mail_date       TIMESTAMPTZ,
    command_text    TEXT,
    received_at     TIMESTAMPTZ DEFAULT now(),
    dispatched      BOOLEAN DEFAULT FALSE,
    dispatched_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_ops_mail_audit_uid UNIQUE (mail_uid)
);

COMMENT ON TABLE ops_mail_audit IS '邮箱审计日志 — 记录从邮箱接收到的运维命令';
COMMENT ON COLUMN ops_mail_audit.mail_uid IS 'IMAP UID（全局唯一，用于幂等去重）';
COMMENT ON COLUMN ops_mail_audit.mail_from IS '发件人邮箱地址';
COMMENT ON COLUMN ops_mail_audit.mail_subject IS '邮件主题';
COMMENT ON COLUMN ops_mail_audit.mail_date IS '邮件发送时间';
COMMENT ON COLUMN ops_mail_audit.command_text IS '解析出的命令文本';
COMMENT ON COLUMN ops_mail_audit.dispatched IS '是否已分派给 ProjectAgent 执行';

CREATE INDEX IF NOT EXISTS idx_ops_mail_from ON ops_mail_audit (mail_from);
CREATE INDEX IF NOT EXISTS idx_ops_mail_dispatched ON ops_mail_audit (dispatched);


-- ============================================================
-- 5. ops_startup_report — 冷启动报告
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_startup_report (
    id              UUID PRIMARY KEY,
    tick_id         UUID NOT NULL REFERENCES ops_tick_log(tick_id),
    startup_time    TIMESTAMPTZ NOT NULL,
    environment     VARCHAR(100) DEFAULT '',
    instance_id     VARCHAR(200) DEFAULT '',
    version         VARCHAR(100) DEFAULT '',
    db_status       BOOLEAN DEFAULT TRUE,
    llm_status      VARCHAR(50) DEFAULT '',
    maxkb_status    VARCHAR(50) DEFAULT '',
    email_status    VARCHAR(50) DEFAULT '',
    pipeline_status VARCHAR(50) DEFAULT '',
    projects_total  INTEGER DEFAULT 0,
    nodes_completed INTEGER DEFAULT 0,
    nodes_in_progress INTEGER DEFAULT 0,
    nodes_blocked   INTEGER DEFAULT 0,
    report_content  TEXT,
    sent_to_mail    BOOLEAN DEFAULT FALSE,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE ops_startup_report IS '冷启动报告 — 记录每次冷启动时生成的系统状态报告';
COMMENT ON COLUMN ops_startup_report.tick_id IS '关联的 Tick ID → ops_tick_log.tick_id';
COMMENT ON COLUMN ops_startup_report.startup_time IS '冷启动时间';
COMMENT ON COLUMN ops_startup_report.environment IS '运行环境：production / staging / dev';
COMMENT ON COLUMN ops_startup_report.db_status IS 'DB 连接状态（Boolean）';
COMMENT ON COLUMN ops_startup_report.llm_status IS 'LLM API 状态';
COMMENT ON COLUMN ops_startup_report.maxkb_status IS 'MaxKB 知识库服务状态';
COMMENT ON COLUMN ops_startup_report.email_status IS '邮箱服务状态';
COMMENT ON COLUMN ops_startup_report.pipeline_status IS 'Pipeline BUS 状态';
COMMENT ON COLUMN ops_startup_report.nodes_completed IS '已完成节点数';
COMMENT ON COLUMN ops_startup_report.nodes_in_progress IS '进行中节点数';
COMMENT ON COLUMN ops_startup_report.nodes_blocked IS '已阻塞节点数';
COMMENT ON COLUMN ops_startup_report.sent_to_mail IS '是否已通过邮件发送';
COMMENT ON COLUMN ops_startup_report.sent_at IS '邮件发送时间';

CREATE INDEX IF NOT EXISTS idx_ops_startup_created ON ops_startup_report (created_at);
