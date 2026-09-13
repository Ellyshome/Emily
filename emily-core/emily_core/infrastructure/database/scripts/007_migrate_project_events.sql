-- ============================================================
-- 007_migrate_project_events.sql —— 统一项目事件积累表数据回填
--
-- 将 7 类旧业务对象回填到统一基类表 project_events（单表继承）：
--   events / tasks / meetings / files(归档动作) / business_flow_orders
--   / node_deliverables / node_events
--
-- 设计约定：
--   · event_kind 判别列区分类型（EVENT/TASK/MEETING/FILE/BUSINESS_FLOW/DELIVERABLE/NODE_EVENT）
--   · event_no 历史数据保留旧编号（EVT-/TSK-/MTG-/FILE-/FLOW- 等），新数据用 PE- 统一编号
--   · node_id 统一挂全景节点；无法确定归属的事件挂临时节点 UNASSIGNED
--   · project_events.id 复用旧表 id（UUID，全局唯一，便于追溯）
--
-- Precondition: project_events 表已由 ORM create_all 创建（或手动执行建表）
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 007_migrate_project_events.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 临时节点 —— 暂不知去向的事件统一挂这里
-- ============================================================
INSERT INTO project_nodes
    (id, project_id, node_id, node_name, owner_dept_id, related_company_id,
     deadline, creator_id, created_at, responsible_user_id, node_type, updated_at,
     visibility_mode, status, progress, parent_node_id, child_weight,
     acknowledged_by, acknowledged_at, acknowledged_level)
VALUES
    (uuid_generate_v4()::text, 'UNASSIGNED', 'UNASSIGNED', '待归类事件（临时节点）',
     '', '', '', '', NOW()::text, '', 'UNASSIGNED', NOW()::text,
     'public', 'CONDITIONS_NOT_MET', '0.00', '', '1.0000', '', '', 0)
ON CONFLICT (node_id) DO NOTHING;

-- ============================================================
-- 2. events → EVENT
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, event_type, title, summary,
     status, actor_id, confirmed_by, confirmed_at, occurred_at, created_at,
     source_message_id, payload, category)
SELECT
    id, event_no, 'EVENT', project_id,
    COALESCE(NULLIF((NULLIF(payload, '')::json ->> 'node_id'), ''), 'UNASSIGNED'),
    COALESCE(NULLIF(event_type, ''), 'general'), title, COALESCE(description, ''),
    COALESCE(NULLIF(status, ''), 'pending'),
    user_id, confirmed_by, confirmed_at, event_date, created_at,
    message_id,
    json_build_object(
        'attachments', COALESCE(attachments, '[]'),
        'remarks', COALESCE(remarks, ''),
        'related_event_ids', COALESCE(related_event_ids, '[]'),
        'conversation_id', COALESCE(conversation_id, ''),
        'payload', COALESCE(payload, '{}')
    )::text,
    COALESCE(NULLIF(category, ''), '待分类')
FROM events
ON CONFLICT (event_no) DO NOTHING;

-- ============================================================
-- 3. tasks → TASK
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, title, summary,
     status, actor_id, occurred_at, created_at, source_message_id,
     payload, owner_id, due_date)
SELECT
    id, task_no, 'TASK', project_id, 'UNASSIGNED', title, COALESCE(description, ''),
    COALESCE(NULLIF(status, ''), 'todo'), created_by, due_date, created_at,
    source_message_id,
    json_build_object(
        'owner_text', COALESCE(owner_text, ''),
        'due_text', COALESCE(due_text, '')
    )::text,
    owner_id, due_date
FROM tasks
ON CONFLICT (event_no) DO NOTHING;

-- ============================================================
-- 4. meetings → MEETING
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, title, summary,
     status, actor_id, occurred_at, created_at, source_message_id,
     payload, meeting_type, meeting_date, location, host_id, conclusion)
SELECT
    id, meeting_no, 'MEETING', project_id, 'UNASSIGNED', COALESCE(title, ''),
    COALESCE(summary, ''),
    CASE status WHEN 0 THEN 'draft' WHEN 1 THEN 'confirmed' WHEN 2 THEN 'archived' ELSE 'confirmed' END,
    created_by, meeting_date, created_at, source_message_id,
    json_build_object(
        'attendees', COALESCE(attendees, '[]'),
        'attendee_names', COALESCE(attendee_names, '[]'),
        'action_items', COALESCE(action_items, '[]'),
        'related_file_ids', COALESCE(related_file_ids, '[]')
    )::text,
    COALESCE(meeting_type, 0), meeting_date, COALESCE(location, ''),
    host_id, COALESCE(conclusion, '')
FROM meetings
WHERE COALESCE(is_deleted, false) = false
ON CONFLICT (event_no) DO NOTHING;

-- ============================================================
-- 5. business_flow_orders → BUSINESS_FLOW
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, title, summary,
     status, actor_id, occurred_at, created_at, payload, flow_type, priority)
SELECT
    id, flow_no, 'BUSINESS_FLOW', project_id,
    COALESCE(NULLIF((NULLIF(metrics, '')::json ->> 'node_id'), ''), 'UNASSIGNED'),
    title, '',
    CASE status WHEN 0 THEN 'draft' WHEN 1 THEN 'processing' WHEN 2 THEN 'completed'
                WHEN 3 THEN 'rejected' WHEN 4 THEN 'cancelled' ELSE 'draft' END,
    creator_id, planned_finish_time, created_at,
    json_build_object(
        'metrics', COALESCE(metrics, '{}'),
        'flow_records', COALESCE(flow_records, '[]'),
        'related_file_ids', COALESCE(related_file_ids, '[]'),
        'related_meeting_ids', COALESCE(related_meeting_ids, '[]'),
        'current_node', COALESCE(current_node, ''),
        'current_handler_id', COALESCE(current_handler_id, ''),
        'actual_finish_time', COALESCE(actual_finish_time, '')
    )::text,
    COALESCE(flow_type, 0), COALESCE(priority, 1)
FROM business_flow_orders
WHERE COALESCE(is_deleted, false) = false
ON CONFLICT (event_no) DO NOTHING;

-- ============================================================
-- 6. node_deliverables → DELIVERABLE
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, title, summary,
     status, actor_id, confirmed_by, confirmed_at, occurred_at, created_at,
     payload, deliverable_id, submission_status, target_amount, current_amount, unit)
SELECT
    nd.id, LEFT('DELV-' || nd.deliverable_id, 50), 'DELIVERABLE',
    (SELECT pn.project_id FROM project_nodes pn WHERE pn.node_id = nd.node_id LIMIT 1),
    nd.node_id, nd.deliverable_name, '',
    COALESCE(NULLIF(nd.submission_status, ''), 'PENDING'),
    NULLIF(nd.submitted_by, ''), NULLIF(nd.confirmed_by, ''), NULLIF(nd.confirmed_at, ''),
    COALESCE(NULLIF(nd.submitted_at, ''), NULLIF(nd.completed_at, '')), nd.created_at,
    json_build_object(
        'is_required', nd.is_required,
        'file_id', COALESCE(nd.file_id, ''),
        'return_reason', COALESCE(nd.return_reason, ''),
        'attachment_file_id', COALESCE(nd.attachment_file_id, ''),
        'completed_at', COALESCE(nd.completed_at, '')
    )::text,
    nd.deliverable_id, nd.submission_status, nd.target_amount, nd.current_amount, nd.unit
FROM node_deliverables nd
ON CONFLICT (event_no) DO NOTHING;

-- ============================================================
-- 7. node_events → NODE_EVENT
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, event_type, title, summary,
     status, actor_id, occurred_at, created_at, payload, old_value, new_value)
SELECT
    ne.id, LEFT('NODE-' || ne.event_id, 50), 'NODE_EVENT',
    (SELECT pn.project_id FROM project_nodes pn WHERE pn.node_id = ne.node_id LIMIT 1),
    ne.node_id, ne.event_type, COALESCE(NULLIF(ne.remark, ''), ne.event_type), '',
    'logged', NULLIF(ne.operator_id, ''), ne.created_at, ne.created_at,
    json_build_object('remark', COALESCE(ne.remark, ''))::text,
    COALESCE(ne.old_value, ''), COALESCE(ne.new_value, '')
FROM node_events ne
ON CONFLICT (event_no) DO NOTHING;

-- ============================================================
-- 8. files → FILE（归档动作，files 表保留资源明细）
-- ============================================================
INSERT INTO project_events
    (id, event_no, event_kind, project_id, node_id, title, summary,
     status, actor_id, occurred_at, created_at, source_message_id,
     payload, file_id)
SELECT
    id, LEFT('FILE-' || file_no, 50), 'FILE', project_id,
    CASE WHEN COALESCE(source_module_type, '') LIKE 'NODE%' AND COALESCE(source_module_id, '') <> ''
         THEN source_module_id ELSE 'UNASSIGNED' END,
    filename, COALESCE(content_summary, ''),
    'archived', uploaded_by, created_at, created_at, message_id,
    json_build_object(
        'file_no', file_no,
        'file_type', COALESCE(file_type, ''),
        'file_category', COALESCE(file_category, 'OTHER'),
        'purpose', COALESCE(purpose, 'RECORD'),
        'confidentiality', COALESCE(confidentiality, 1),
        'version', COALESCE(version, 'V1.0')
    )::text,
    id
FROM files
WHERE COALESCE(is_deleted, false) = false
ON CONFLICT (event_no) DO NOTHING;

COMMIT;

-- ============================================================
-- 校验
-- ============================================================
SELECT '--- project_events 按类型分布 ---' AS section;
SELECT event_kind, count(*) FROM project_events GROUP BY event_kind ORDER BY event_kind;
SELECT '--- 临时节点 ---' AS section;
SELECT node_id, node_name, node_type FROM project_nodes WHERE node_id = 'UNASSIGNED';
