-- ============================================================================
-- 006_add_file_source_fields.sql
-- files 表新增全景节点溯源字段（Phase 1-4）
-- 需求文档 §6.1
-- ============================================================================

ALTER TABLE files ADD COLUMN IF NOT EXISTS source_module_id VARCHAR(100) DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS source_module_type VARCHAR(50) DEFAULT '';

COMMENT ON COLUMN files.source_module_id IS '来源模块ID（节点ID/其他业务对象ID）';
COMMENT ON COLUMN files.source_module_type IS '来源模块类型：NODE_STARTUP_DOC/NODE_WORKLOAD_DOC/NODE_DELIVERABLE_DOC/NODE_ATTACHMENT';
