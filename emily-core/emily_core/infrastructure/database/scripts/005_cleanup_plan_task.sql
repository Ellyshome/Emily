-- ============================================================================
-- 005_cleanup_plan_task.sql
-- 清理已废弃的 PlanTask 系统 4 张表 + SOPCheckpoint 表
--
-- 替代者：
--   plan_task_templates/instances/logs/deliverables →
--     scheduler_jobs + scheduler_executions + Node Task 体系
--   sop_checkpoints → CheckpointService 已无调用方
--
-- ⚠ 不可逆，执行前确认无业务依赖
--
-- 执行方式：手动执行
--   docker exec -it emily-postgres psql -U emily -d emily -f /path/to/005_cleanup_plan_task.sql
--
-- 日期：2026-07-09
-- ============================================================================

BEGIN;

-- 备份确认（可选：取消注释以在删除前导出备份）
-- COPY plan_task_deliverables TO '/tmp/plan_task_deliverables.csv' WITH CSV HEADER;
-- COPY plan_task_logs TO '/tmp/plan_task_logs.csv' WITH CSV HEADER;
-- COPY plan_task_instances TO '/tmp/plan_task_instances.csv' WITH CSV HEADER;
-- COPY plan_task_templates TO '/tmp/plan_task_templates.csv' WITH CSV HEADER;
-- COPY sop_checkpoints TO '/tmp/sop_checkpoints.csv' WITH CSV HEADER;

-- 按依赖顺序（先删子表/引用表，再删主表）
DROP TABLE IF EXISTS plan_task_deliverables CASCADE;
DROP TABLE IF EXISTS plan_task_logs CASCADE;
DROP TABLE IF EXISTS plan_task_instances CASCADE;
DROP TABLE IF EXISTS plan_task_templates CASCADE;

-- 清理 CheckpointService 废弃表
DROP TABLE IF EXISTS sop_checkpoints CASCADE;

COMMIT;
