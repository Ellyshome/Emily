-- ============================================================================
-- 005_create_panorama_tables.sql
-- 全景节点图 V2 — 5 张表 DDL（Phase 1-1）
-- 需求文档 §3.2–§3.6
-- 执行方式：docker exec -i emily-postgres psql -U emily -d emily < this_file.sql
-- ============================================================================

BEGIN;

-- 1. 节点主表
CREATE TABLE IF NOT EXISTS project_nodes (
    id              VARCHAR(100) PRIMARY KEY,
    project_id      VARCHAR(100) NOT NULL,
    node_id         VARCHAR(100) NOT NULL,
    node_name       VARCHAR(500) NOT NULL,
    owner_dept_id   VARCHAR(100) NOT NULL DEFAULT '项目总',
    related_company_id VARCHAR(100) NOT NULL DEFAULT '建设单位',
    deadline        VARCHAR(50) NOT NULL,
    remark          TEXT DEFAULT '',
    creator_id      VARCHAR(100) NOT NULL,
    created_at      VARCHAR(50) NOT NULL,
    approver_id     VARCHAR(100) DEFAULT '',
    approved_at     VARCHAR(50) DEFAULT '',
    completed_at    VARCHAR(50) DEFAULT '',
    is_discarded    BOOLEAN DEFAULT FALSE,
    status          VARCHAR(20) DEFAULT 'CONDITIONS_NOT_MET',
    updated_at      VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_project ON project_nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON project_nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_owner ON project_nodes(owner_dept_id);

-- 2. 前置依赖表
CREATE TABLE IF NOT EXISTS node_dependencies (
    id                          VARCHAR(100) PRIMARY KEY,
    node_id                     VARCHAR(100) NOT NULL,
    depends_on_deliverable_id   VARCHAR(100) NOT NULL,
    depends_on_node_id          VARCHAR(100) NOT NULL,
    dependency_type             VARCHAR(20) NOT NULL DEFAULT 'DELIVERABLE',
    weight                      VARCHAR(10) NOT NULL DEFAULT '1.0000',
    created_at                  VARCHAR(50) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dep_node_deliverable ON node_dependencies(node_id, depends_on_deliverable_id);
CREATE INDEX IF NOT EXISTS idx_ndep_node ON node_dependencies(node_id);
CREATE INDEX IF NOT EXISTS idx_ndep_deliverable ON node_dependencies(depends_on_deliverable_id);

-- 3. 产出成果表
CREATE TABLE IF NOT EXISTS node_deliverables (
    id              VARCHAR(100) PRIMARY KEY,
    deliverable_id  VARCHAR(100) NOT NULL,
    node_id         VARCHAR(100) NOT NULL,
    deliverable_name VARCHAR(500) NOT NULL,
    target_amount   VARCHAR(20) NOT NULL,
    current_amount  VARCHAR(20) NOT NULL DEFAULT '0.00',
    unit            VARCHAR(50) NOT NULL,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    file_id         VARCHAR(100) DEFAULT '',
    completed_at    VARCHAR(50) DEFAULT '',
    created_at      VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ndel_node ON node_deliverables(node_id);

-- 4. 节点可见文件中间表
CREATE TABLE IF NOT EXISTS node_accessible_files (
    id          VARCHAR(100) PRIMARY KEY,
    node_id     VARCHAR(100) NOT NULL,
    file_id     VARCHAR(100) NOT NULL,
    added_by    VARCHAR(100) NOT NULL,
    added_at    VARCHAR(50) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_naf_node_file ON node_accessible_files(node_id, file_id);
CREATE INDEX IF NOT EXISTS idx_naf_node ON node_accessible_files(node_id);
CREATE INDEX IF NOT EXISTS idx_naf_file ON node_accessible_files(file_id);

-- 5. 事件总线表
CREATE TABLE IF NOT EXISTS node_events (
    id          VARCHAR(100) PRIMARY KEY,
    event_id    VARCHAR(100) NOT NULL,
    node_id     VARCHAR(100) NOT NULL,
    event_type  VARCHAR(50) NOT NULL,
    old_value   TEXT DEFAULT '',
    new_value   TEXT DEFAULT '',
    operator_id VARCHAR(100) DEFAULT '',
    remark      TEXT DEFAULT '',
    created_at  VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nev_node ON node_events(node_id);
CREATE INDEX IF NOT EXISTS idx_nev_type ON node_events(event_type);
CREATE INDEX IF NOT EXISTS idx_nev_created ON node_events(created_at);

COMMIT;
