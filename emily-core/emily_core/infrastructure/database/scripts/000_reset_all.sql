-- ============================================================
-- 000_reset_all.sql —— 清空所有业务表，保留表结构
-- FK dependency safe order: deletes child tables first
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 000_reset_all.sql
-- ============================================================

BEGIN;

-- ============================================================
-- Step 1: Disable triggers (so FK checks don't block TRUNCATE)
-- ============================================================
-- Postgres TRUNCATE CASCADE auto-handles FKs but we list explicitly for safety

-- Log / audit tables (no inbound FKs from business tables)
TRUNCATE TABLE permission_audit_log CASCADE;
TRUNCATE TABLE pipeline_execution_logs CASCADE;
TRUNCATE TABLE hook_execution_logs CASCADE;
TRUNCATE TABLE tool_call_logs CASCADE;
TRUNCATE TABLE llm_interaction_logs CASCADE;
TRUNCATE TABLE rag_retrieval_logs CASCADE;
TRUNCATE TABLE evolution_llm_interaction_logs CASCADE;
TRUNCATE TABLE agent_reasoning_logs CASCADE;
TRUNCATE TABLE sop_routing_logs CASCADE;
TRUNCATE TABLE session_lifecycle_logs CASCADE;
TRUNCATE TABLE scheduler_job_logs CASCADE;
TRUNCATE TABLE user_feedback_signals CASCADE;
TRUNCATE TABLE business_event_logs CASCADE;

-- Evolution tables
TRUNCATE TABLE evolution_patches CASCADE;
TRUNCATE TABLE evolution_rules CASCADE;
TRUNCATE TABLE evolution_daily_insights CASCADE;

-- Permission tables
TRUNCATE TABLE permission_requests CASCADE;
TRUNCATE TABLE permission_grants CASCADE;
TRUNCATE TABLE permission_review_tasks CASCADE;
TRUNCATE TABLE sop_permission_bindings CASCADE;
TRUNCATE TABLE pending_data CASCADE;
TRUNCATE TABLE data_masking_rules CASCADE;
TRUNCATE TABLE permission_def CASCADE;
TRUNCATE TABLE public_field_registry CASCADE;
TRUNCATE TABLE sop_business_flows CASCADE;
TRUNCATE TABLE permission_groups CASCADE;

-- Scheduler tables
TRUNCATE TABLE scheduler_executions CASCADE;
TRUNCATE TABLE scheduler_jobs CASCADE;

-- Node graph tables (V2 panorama)
TRUNCATE TABLE node_events CASCADE;
TRUNCATE TABLE node_accessible_files CASCADE;
TRUNCATE TABLE node_dependencies CASCADE;
TRUNCATE TABLE node_deliverables CASCADE;
TRUNCATE TABLE project_nodes CASCADE;

-- Plan tables
TRUNCATE TABLE plan_items CASCADE;
TRUNCATE TABLE project_plans CASCADE;

-- Flow / instruction tables
TRUNCATE TABLE instruction_orders CASCADE;
TRUNCATE TABLE business_flow_orders CASCADE;

-- Project business tables
TRUNCATE TABLE message_attachments CASCADE;
TRUNCATE TABLE files CASCADE;
TRUNCATE TABLE meetings CASCADE;
TRUNCATE TABLE tasks CASCADE;
TRUNCATE TABLE events CASCADE;
TRUNCATE TABLE project_indicator_details CASCADE;
TRUNCATE TABLE session_archives CASCADE;
TRUNCATE TABLE session_accessible_files CASCADE;

-- Core tables
TRUNCATE TABLE messages CASCADE;
TRUNCATE TABLE conversations CASCADE;
TRUNCATE TABLE project_world_books CASCADE;
TRUNCATE TABLE projects CASCADE;
TRUNCATE TABLE user_im_bindings CASCADE;
TRUNCATE TABLE system_descriptions CASCADE;
TRUNCATE TABLE tool_registry CASCADE;
TRUNCATE TABLE users CASCADE;
TRUNCATE TABLE company_info CASCADE;

-- PlanTask deprecated tables (if still exist)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'plan_task_deliverables') THEN
        TRUNCATE TABLE plan_task_deliverables CASCADE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'plan_task_logs') THEN
        TRUNCATE TABLE plan_task_logs CASCADE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'plan_task_instances') THEN
        TRUNCATE TABLE plan_task_instances CASCADE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'plan_task_templates') THEN
        TRUNCATE TABLE plan_task_templates CASCADE;
    END IF;
END $$;

COMMIT;

-- ============================================================
-- Verification
-- ============================================================
SELECT 'All tables truncated successfully. Ready for seed data.' AS status;
