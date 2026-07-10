-- 006_evolution_tables.sql
-- 进化闭环模块：3 张新表

-- 1. 日洞察表
CREATE TABLE IF NOT EXISTS evolution_daily_insights (
    id VARCHAR PRIMARY KEY,
    insight_date VARCHAR NOT NULL UNIQUE,
    analysis_days INTEGER DEFAULT 1,
    total_messages INTEGER DEFAULT 0,
    total_pipeline_runs INTEGER DEFAULT 0,
    sop_hit_rate FLOAT DEFAULT 0.0,
    fallback_rate FLOAT DEFAULT 0.0,
    top_sop_ids TEXT DEFAULT '[]',
    feedback_summary TEXT DEFAULT '',
    anomaly_flags TEXT DEFAULT '[]',
    insight_text TEXT DEFAULT '',
    metrics_json TEXT DEFAULT '{}',
    health_score INTEGER DEFAULT 0,
    created_at VARCHAR DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_edi_date ON evolution_daily_insights (insight_date);

-- 2. 进化规则表
CREATE TABLE IF NOT EXISTS evolution_rules (
    id VARCHAR PRIMARY KEY,
    rule_no VARCHAR(20) NOT NULL UNIQUE,
    title VARCHAR(200) NOT NULL,
    description TEXT DEFAULT '',
    evidence_insight_ids TEXT DEFAULT '[]',
    category VARCHAR(30) DEFAULT '',
    confidence FLOAT DEFAULT 0.0,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    superseded_by VARCHAR(20) DEFAULT '',
    suggested_action TEXT DEFAULT '',
    impact_estimate VARCHAR(500) DEFAULT '',
    created_at VARCHAR DEFAULT '',
    confirmed_at VARCHAR DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_er_status ON evolution_rules (status);
CREATE INDEX IF NOT EXISTS idx_er_category ON evolution_rules (category);

-- 3. 进化补丁表
CREATE TABLE IF NOT EXISTS evolution_patches (
    id VARCHAR PRIMARY KEY,
    patch_no VARCHAR(20) NOT NULL UNIQUE,
    rule_no VARCHAR(20) DEFAULT '',
    target_type VARCHAR(30) DEFAULT '',
    target_path VARCHAR(500) DEFAULT '',
    patch_content TEXT DEFAULT '',
    patch_type VARCHAR(30) DEFAULT '',
    search_anchor VARCHAR(500) DEFAULT '',
    risk_level VARCHAR(10) DEFAULT '',
    risk_reasoning VARCHAR(500) DEFAULT '',
    validation_criteria TEXT DEFAULT '',
    expected_effect VARCHAR(500) DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    applied_at VARCHAR DEFAULT '',
    validated_at VARCHAR DEFAULT '',
    validation_result TEXT DEFAULT '',
    rollback_snapshot TEXT DEFAULT '',
    created_at VARCHAR DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ep_status ON evolution_patches (status);
CREATE INDEX IF NOT EXISTS idx_ep_rule ON evolution_patches (rule_no);
