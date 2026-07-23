-- verify_data.sql —— 测试环境数据完整性验证
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < verify_data.sql

\echo '--- 核心数据统计 ---'
SELECT 'users(' || count(*)::text || ')' FROM users WHERE is_deleted = false
UNION ALL SELECT 'company_info(' || count(*)::text || ')' FROM company_info WHERE is_deleted = false
UNION ALL SELECT 'projects(' || count(*)::text || ')' FROM projects WHERE is_deleted = false
UNION ALL SELECT 'project_nodes(' || count(*)::text || ')' FROM project_nodes
UNION ALL SELECT 'node_deliverables(' || count(*)::text || ')' FROM node_deliverables
UNION ALL SELECT 'node_dependencies(' || count(*)::text || ')' FROM node_dependencies
UNION ALL SELECT 'files(' || count(*)::text || ')' FROM files WHERE is_deleted = false
UNION ALL SELECT 'events(' || count(*)::text || ')' FROM events
UNION ALL SELECT 'tasks(' || count(*)::text || ')' FROM tasks
UNION ALL SELECT 'meetings(' || count(*)::text || ')' FROM meetings
UNION ALL SELECT 'business_flow_orders(' || count(*)::text || ')' FROM business_flow_orders
UNION ALL SELECT 'instruction_orders(' || count(*)::text || ')' FROM instruction_orders
UNION ALL SELECT 'plan_items(' || count(*)::text || ')' FROM plan_items
UNION ALL SELECT 'conversations(' || count(*)::text || ')' FROM conversations
UNION ALL SELECT 'messages(' || count(*)::text || ')' FROM messages;

\echo ''
\echo '--- 运行时数据统计 ---'
SELECT 'pipeline_execution_logs(' || count(*)::text || ')' FROM pipeline_execution_logs WHERE pipeline_run_id LIKE 'SIM-%'
UNION ALL SELECT 'scheduler_jobs(' || count(*)::text || ')' FROM scheduler_jobs
UNION ALL SELECT 'scheduler_executions(' || count(*)::text || ')' FROM scheduler_executions
UNION ALL SELECT 'evolution_daily_insights(' || count(*)::text || ')' FROM evolution_daily_insights WHERE insight_date LIKE '2026-%'
UNION ALL SELECT 'evolution_rules(' || count(*)::text || ')' FROM evolution_rules WHERE rule_no LIKE 'R-%'
UNION ALL SELECT 'evolution_patches(' || count(*)::text || ')' FROM evolution_patches WHERE patch_no LIKE 'EP-%'
UNION ALL SELECT 'sop_routing_logs(' || count(*)::text || ')' FROM sop_routing_logs
UNION ALL SELECT 'rag_retrieval_logs(' || count(*)::text || ')' FROM rag_retrieval_logs
UNION ALL SELECT 'user_feedback_signals(' || count(*)::text || ')' FROM user_feedback_signals
UNION ALL SELECT 'permission_requests(' || count(*)::text || ')' FROM permission_requests WHERE request_no LIKE 'PRQ-2026%'
UNION ALL SELECT 'permission_audit_log(' || count(*)::text || ')' FROM permission_audit_log
UNION ALL SELECT 'message_attachments(' || count(*)::text || ')' FROM message_attachments
UNION ALL SELECT 'node_accessible_files(' || count(*)::text || ')' FROM node_accessible_files WHERE node_id LIKE 'EMR-%';

\echo ''
\echo '--- 用户权限一览 ---'
SELECT u.username, u.level,
    CASE u.level
        WHEN 1 THEN 'visit' WHEN 2 THEN 'executor'
        WHEN 3 THEN 'manager' WHEN 4 THEN 'supervisor'
        WHEN 5 THEN 'admin' WHEN 6 THEN 'sysadmin'
        ELSE 'unknown'
    END AS level_name,
    b.im_display_name AS display_name
FROM users u
LEFT JOIN user_im_bindings b ON u.id = b.user_id AND b.im_platform = 'napcat'
WHERE u.is_deleted = false
ORDER BY u.level DESC;

\echo ''
\echo '--- storage_path 检查 ---'
SELECT count(*) AS absolute_path_count FROM files WHERE storage_path LIKE '/%' AND is_deleted = false;
