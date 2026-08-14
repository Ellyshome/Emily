-- ============================================================
-- seed_trace.sql — 信息溯源到人实验预埋数据（E2 场景四）
-- 编号体系：EVT-TRACE-9xxx / TSK-TRACE-9xxx / MTG-TRACE-9xxx
-- 幂等：先删后插（按编号识别）
-- 角色分离设计（关键）：
--   记录人(uploader) 张正宏 L3  ≠  认证人(confirmed_by) 李景利 L4
--   责任人(owner/host) 李景利/王建国
--   查询者：罗永强 L5（不参与数据链，跨角色追问的第三人）
-- ============================================================

BEGIN;

-- 清理旧预埋（按编号）
DELETE FROM events   WHERE event_no   LIKE 'EVT-TRACE-%';
DELETE FROM tasks    WHERE task_no    LIKE 'TSK-TRACE-%';
DELETE FROM meetings WHERE meeting_no LIKE 'MTG-TRACE-%';

-- ════════════════════════════════════════════════════════════
-- 一、事件 10 条（覆盖溯源字段三种组合）
--   组合A：记录人≠认证人（confirmed，4条：9001-9004）
--   组合B：记录人有、认证人空（pending，4条：9005-9008）
--   组合C：记录人=认证人同人（confirmed，2条：9009-9010）
-- ════════════════════════════════════════════════════════════

-- 组合A：记录人=张正宏，认证人=李景利
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9001', '施工动态', '成果提交',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '3#地块乔木种植完成',
    '3#地块乔木种植作业全部完成，共种植乔木 156 株，成活率待后续观察。',
    '2026-08-10T16:00:00+08:00', 'confirmed', '2026-08-10T17:20:00+08:00',
    (SELECT id FROM users WHERE username='李景利'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9002', '施工动态', '成果提交',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    'C区滨水步道硬质铺装完成约80平米',
    'C区滨水步道样板段硬质铺装施工完成约80平米，待监理验收确认。',
    '2026-08-11T16:00:00+08:00', 'confirmed', '2026-08-11T17:00:00+08:00',
    (SELECT id FROM users WHERE username='李景利'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9003', '质量安全', '成果提交',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '2#楼外墙保温施工完成',
    '2#楼外墙保温层施工全部完成，表面平整度自检合格。',
    '2026-08-11T15:00:00+08:00', 'confirmed', '2026-08-11T16:10:00+08:00',
    (SELECT id FROM users WHERE username='李景利'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9004', '施工动态', '成果提交',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '样板段石材铺装完成',
    '售楼处前广场样板段石材铺装完成，观感质量符合要求。',
    '2026-08-12T10:00:00+08:00', 'confirmed', '2026-08-12T11:00:00+08:00',
    (SELECT id FROM users WHERE username='李景利'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- 组合B：记录人=张正宏，认证人空（pending）
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9005', '质量安全', '待分类',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '基坑东侧发现渗水约2平米',
    '基坑东侧发现渗水，面积约2平米，已通知监理，待处理。',
    '2026-08-12T14:00:00+08:00', 'pending', now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9006', '施工动态', '待分类',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '5#楼三层钢筋绑扎完成',
    '5#楼三层梁板钢筋绑扎完成，待监理隐蔽验收。',
    '2026-08-12T15:00:00+08:00', 'pending', now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9007', '质量安全', '待分类',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '现场临时用电隐患整改完成',
    '现场临时用电隐患已整改完成，配电箱接地恢复。',
    '2026-08-12T16:00:00+08:00', 'pending', now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9008', '施工动态', '待分类',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '苗木进场验收待监理确认',
    '首批苗木进场，规格初验合格，待监理到场联合验收。',
    '2026-08-12T17:00:00+08:00', 'pending', now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- 组合C：记录人=认证人=王建国（同人）
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9009', '工作成果', '成果提交',
    p.id, (SELECT id FROM users WHERE username='王建国'),
    '景观施工图苗木审核报告完成',
    '景观施工图苗木使用方案审核完成，评分82分，报告已归档。',
    '2026-08-13T09:00:00+08:00', 'confirmed', '2026-08-13T09:30:00+08:00',
    (SELECT id FROM users WHERE username='王建国'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-TRACE-9010', '工作成果', '成果提交',
    p.id, (SELECT id FROM users WHERE username='王建国'),
    '周例会部署外墙检查工作',
    '周例会部署外墙检查专项工作，明确责任人与完成时限。',
    '2026-08-13T10:00:00+08:00', 'confirmed', '2026-08-13T10:20:00+08:00',
    (SELECT id FROM users WHERE username='王建国'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- ════════════════════════════════════════════════════════════
-- 二、任务 3 条（created_by=张正宏，owner 分离）
-- ════════════════════════════════════════════════════════════

INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
    status, due_date, created_by, created_at, updated_at)
SELECT
    gen_random_uuid()::text, 'TSK-TRACE-9001', p.id,
    '外墙检查报告',
    '完成2#楼外墙保温施工后的专项检查并出具报告。',
    (SELECT id FROM users WHERE username='李景利'), '李景利',
    'doing', '2026-08-15T18:00:00+08:00',
    (SELECT id FROM users WHERE username='张正宏'), now()::text, now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
    status, due_date, created_by, created_at, updated_at)
SELECT
    gen_random_uuid()::text, 'TSK-TRACE-9002', p.id,
    '苗木采购进场验收',
    '组织首批苗木的采购进场联合验收。',
    (SELECT id FROM users WHERE username='王建国'), '王建国',
    'todo', '2026-08-14T18:00:00+08:00',
    (SELECT id FROM users WHERE username='张正宏'), now()::text, now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
    status, due_date, created_by, created_at, updated_at)
SELECT
    gen_random_uuid()::text, 'TSK-TRACE-9003', p.id,
    '硬质铺装与园路施工',
    '推进C区滨水步道硬质铺装与园路施工。',
    (SELECT id FROM users WHERE username='李景利'), '李景利',
    'doing', '2026-08-20T18:00:00+08:00',
    (SELECT id FROM users WHERE username='张正宏'), now()::text, now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- ════════════════════════════════════════════════════════════
-- 三、会议 3 条（created_by=张正宏，host=王建国）
-- ════════════════════════════════════════════════════════════

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees,
    meeting_type, meeting_date, location, host_id, attendee_names,
    conclusion, action_items, status, created_by, created_at, updated_at, is_deleted)
SELECT
    gen_random_uuid()::text, 'MTG-TRACE-9001', p.id,
    '景观工程周例会',
    '景观工程周例会，部署苗木种植与铺装施工。', '[]',
    0, '2026-08-11T09:00:00+08:00', '项目部会议室',
    (SELECT id FROM users WHERE username='王建国'),
    '["王建国","李景利","张正宏"]',
    '部署3#地块乔木种植与C区铺装施工，明确责任分工。',
    '[{"item":"乔木种植","assignee":"张正宏"},{"item":"铺装施工","assignee":"李景利"}]',
    1, (SELECT id FROM users WHERE username='张正宏'), now()::text, now()::text, false
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees,
    meeting_type, meeting_date, location, host_id, attendee_names,
    conclusion, action_items, status, created_by, created_at, updated_at, is_deleted)
SELECT
    gen_random_uuid()::text, 'MTG-TRACE-9002', p.id,
    '苗木种植技术交底会',
    '苗木种植前技术交底，明确种植标准与验收要求。', '[]',
    3, '2026-08-12T14:00:00+08:00', '现场会议室',
    (SELECT id FROM users WHERE username='王建国'),
    '["王建国","张正宏"]',
    '完成苗木种植技术交底，明确种植穴深度与间距标准。',
    '[{"item":"按标准种植","assignee":"张正宏"}]',
    1, (SELECT id FROM users WHERE username='张正宏'), now()::text, now()::text, false
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees,
    meeting_type, meeting_date, location, host_id, attendee_names,
    conclusion, action_items, status, created_by, created_at, updated_at, is_deleted)
SELECT
    gen_random_uuid()::text, 'MTG-TRACE-9003', p.id,
    '样板段验收协调会',
    '协调样板段石材铺装验收事宜。', '[]',
    4, '2026-08-13T10:00:00+08:00', '项目部会议室',
    (SELECT id FROM users WHERE username='王建国'),
    '["王建国","李景利"]',
    '样板段石材铺装观感合格，安排正式验收。',
    '[{"item":"正式验收","assignee":"李景利"}]',
    1, (SELECT id FROM users WHERE username='张正宏'), now()::text, now()::text, false
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

COMMIT;

-- ════════════════════════════════════════════════════════════
-- 验证插入结果
-- ════════════════════════════════════════════════════════════
SELECT 'events(confirmed,记录≠认证)' AS grp, COUNT(*) FROM events WHERE event_no IN ('EVT-TRACE-9001','EVT-TRACE-9002','EVT-TRACE-9003','EVT-TRACE-9004') AND confirmed_by IS NOT NULL
UNION ALL SELECT 'events(pending,无认证)', COUNT(*) FROM events WHERE event_no IN ('EVT-TRACE-9005','EVT-TRACE-9006','EVT-TRACE-9007','EVT-TRACE-9008') AND confirmed_by IS NULL
UNION ALL SELECT 'events(同人)', COUNT(*) FROM events WHERE event_no IN ('EVT-TRACE-9009','EVT-TRACE-9010') AND user_id=confirmed_by
UNION ALL SELECT 'tasks', COUNT(*) FROM tasks WHERE task_no LIKE 'TSK-TRACE-%'
UNION ALL SELECT 'meetings', COUNT(*) FROM meetings WHERE meeting_no LIKE 'MTG-TRACE-%';
