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
UNION ALL SELECT 'messages(' || count(*)::text || ')' FROM messages
UNION ALL SELECT 'project_events(' || count(*)::text || ')' FROM project_events;

\echo ''
\echo '--- 运行时数据统计 ---'
SELECT 'pipeline_execution_logs(' || count(*)::text || ')' FROM pipeline_execution_logs WHERE pipeline_run_id LIKE 'SIM-%'
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

\echo ''
\echo '--- 统一项目事件 (project_events) 分布 ---'
SELECT event_kind || '(' || count(*)::text || ')' FROM project_events GROUP BY event_kind ORDER BY event_kind;

\echo ''
\echo '--- 临时节点 UNASSIGNED ---'
SELECT node_id, node_name, node_type FROM project_nodes WHERE node_id = 'UNASSIGNED';

\echo ''
\echo '--- 监理团队节点覆盖（分责任） ---'
SELECT u.username AS "监理", c.company_name AS "单位", COUNT(DISTINCT np.node_id) AS "覆盖节点数"
FROM node_participants np
JOIN users u ON np.user_id = u.id
LEFT JOIN company_info c ON u.company = c.id
WHERE u.username IN ('陈建华','钱卫东','何丽娟')
GROUP BY u.username, c.company_name
ORDER BY u.username;

\echo ''
\echo '--- 专业分包节点（幕墙/门窗/钢结构/二次结构/外檐） ---'
SELECT pn.node_id, pn.node_name, pn.node_type, pn.parent_node_id,
       (SELECT u.username FROM users u WHERE u.id = pn.responsible_user_id) AS "责任人"
FROM project_nodes pn
WHERE pn.node_id LIKE 'EMR-SG-01-06%' OR pn.node_id LIKE 'EMR-SG-01-07%'
   OR pn.node_id LIKE 'EMR-SG-01-08%' OR pn.node_id LIKE 'EMR-SG-01-09%'
   OR pn.node_id LIKE 'EMR-SG-01-10%'
ORDER BY pn.node_id;

\echo ''
\echo '--- 材料进场流转单（flow_type=3） ---'
SELECT flow_no, title, (metrics::json ->> 'node_id') AS node_id, status
FROM business_flow_orders
WHERE project_id = (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false)
  AND flow_type = 3
ORDER BY flow_no;

\echo ''
\echo '--- 材料进场事件（event_type=材料进场） ---'
SELECT event_no, title, (payload::json ->> 'node_id') AS node_id, status
FROM events
WHERE project_id = (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false)
  AND event_type = '材料进场'
ORDER BY event_no;

\echo ''
\echo '--- 材料事件在 project_events 的节点挂载 ---'
SELECT event_kind, node_id, count(*) FROM project_events
WHERE node_id LIKE 'EMR-SG-01-%'
  AND event_kind IN ('BUSINESS_FLOW','EVENT')
GROUP BY event_kind, node_id
ORDER BY node_id, event_kind;
