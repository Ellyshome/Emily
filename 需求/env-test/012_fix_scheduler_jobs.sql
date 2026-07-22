-- ============================================================
-- 012_fix_scheduler_jobs.sql —— 调度器半接线修复：存量作业 action_type 对齐
--   背景：3 个种子作业 action_type/handler_module/params 为旧架构残留，
--   无对应 handler，每周期触发 "No handler"（见 0722调度器半接线修复计划.md）。
--   本脚本对齐 action_type 到已注册 handler；JOB-003 因 files 表无过期字段、
--   handler 未实现，先置 INACTIVE。
--
-- 幂等：按旧 action_type 匹配更新，改后旧值不再命中，可重复执行。
-- 注意：JOB-001 的 project_id 取第一个未删除项目；若需指定项目请改子查询。
--       修复后 next_execution_at 若已 past-due，下个 tick 会立即触发作业并
--       由 reschedule 推进到下个周期（符合验证预期）。
--
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 012_fix_scheduler_jobs.sql
-- ============================================================

BEGIN;

-- JOB-001: 每周进度汇报任务创建 → create_periodic_node
UPDATE scheduler_jobs
SET action_type = 'create_periodic_node',
    handler_module = 'scheduler.jobs.periodic_node',
    action_params = '{"project_id":"' || (SELECT id FROM projects WHERE is_deleted = false ORDER BY created_at LIMIT 1) || '","node_name":"本周进度汇报","owner_dept_id":"项目总","creator_id":"' || (SELECT id FROM users WHERE username = '王建国' AND is_deleted = false LIMIT 1) || '"}',
    updated_at = NOW()::text
WHERE action_type = 'create_task_node';

-- JOB-002: 每日晨报 → generate_morning_report
UPDATE scheduler_jobs
SET action_type = 'generate_morning_report',
    handler_module = 'scheduler.jobs.morning_report',
    action_params = '{"push_to_group":"项目群"}',
    updated_at = NOW()::text
WHERE action_type = 'morning_report';

-- JOB-003: 文件过期提醒 → check_file_expiry（handler 未实现，files 表无过期字段，先停用）
UPDATE scheduler_jobs
SET action_type = 'check_file_expiry',
    handler_module = 'scheduler.jobs.file_expiry',
    status = 'INACTIVE',
    updated_at = NOW()::text
WHERE action_type = 'file_expiry_reminder';

-- 验证
SELECT job_no, action_type, handler_module, status, next_execution_at
FROM scheduler_jobs
WHERE job_no IN ('JOB-001','JOB-002','JOB-003')
ORDER BY job_no;

COMMIT;
