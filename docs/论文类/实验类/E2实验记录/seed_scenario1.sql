-- ===== 场景①预埋数据（幂等：先删后插）=====
BEGIN;

-- 清理旧预埋（用固定编号识别）
DELETE FROM events  WHERE event_no  IN ('EVT-PAPER-9001', 'EVT-PAPER-9002');
DELETE FROM tasks   WHERE task_no   IN ('TSK-PAPER-9001');
DELETE FROM meetings WHERE meeting_no IN ('MTG-PAPER-9001');

-- ① 会议记录：7月28日周例会，部署苗木审核工作
INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees,
    meeting_type, meeting_date, location, host_id, attendee_names,
    conclusion, action_items, status, created_by, created_at, updated_at, is_deleted)
SELECT
    gen_random_uuid()::text,
    'MTG-PAPER-9001',
    p.id,
    '翠湖庭院项目周例会（第30周）',
    '周例会，部署景观施工图苗木审核工作',
    '[]',
    0,
    '2026-07-28T15:00:00+08:00',
    '项目部会议室',
    NULL,
    '["王建国","李景利","张正宏","陈建华"]',
    '会议部署：景观施工图苗木使用方案需在本周内完成专项审核，由张正宏牵头组织设计院提交送审材料，李景利负责跟踪落实。',
    '[{"item":"景观施工图苗木审核","assignee":"张正宏","due":"2026-08-04"}]',
    1,
    NULL,
    now()::text,
    now()::text,
    false
FROM projects p WHERE p.code = 'EMERALD-01' AND p.is_deleted = false;

-- ② 任务：苗木审核任务（已完成）
INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
    status, due_date, created_by, created_at, updated_at)
SELECT
    gen_random_uuid()::text,
    'TSK-PAPER-9001',
    p.id,
    '景观施工图苗木使用方案审核',
    '依据公司苗木使用审核标准，对星湖湿地公园植物设计说明完成专项审核并输出评分报告。',
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    '张正宏',
    'done',
    '2026-08-04T18:00:00+08:00',
    (SELECT id FROM users WHERE username = '王建国' LIMIT 1),
    '2026-07-28T16:00:00+08:00',
    '2026-08-02T17:30:00+08:00'
FROM projects p WHERE p.code = 'EMERALD-01' AND p.is_deleted = false;

-- ③ 完工确认事件：张正宏提交审核报告完成
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, created_at)
SELECT
    gen_random_uuid()::text,
    'EVT-PAPER-9001',
    '工作成果',
    '成果提交',
    p.id,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    '景观施工图苗木审核报告完成',
    '星湖湿地公园植物设计说明已完成苗木使用审核，评分82分（修改后通过），报告已归档。',
    '2026-08-02T17:30:00+08:00',
    'confirmed',
    '2026-08-02T17:35:00+08:00',
    now()::text
FROM projects p WHERE p.code = 'EMERALD-01' AND p.is_deleted = false;

-- ④ 附加：一个待认证事件（供场景②使用）
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, created_at)
SELECT
    gen_random_uuid()::text,
    'EVT-PAPER-9002',
    '施工动态',
    '待分类',
    p.id,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    '景观硬质铺装样板段施工完成',
    'C区滨水步道样板段铺装完成约80平米，待监理验收确认。',
    '2026-08-11T16:00:00+08:00',
    'pending',
    now()::text
FROM projects p WHERE p.code = 'EMERALD-01' AND p.is_deleted = false;

COMMIT;

-- 验证插入结果
SELECT 'meeting' AS t, meeting_no, title, status FROM meetings WHERE meeting_no='MTG-PAPER-9001'
UNION ALL SELECT 'task', task_no, title, status FROM tasks WHERE task_no='TSK-PAPER-9001'
UNION ALL SELECT 'event', event_no, title, status FROM events WHERE event_no LIKE 'EVT-PAPER-%';
