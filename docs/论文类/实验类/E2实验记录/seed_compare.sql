-- ============================================================
-- seed_compare.sql — 对比实验（交叉验证能力）项目 A 预埋数据
-- 编号体系：*-CMP-9xxx（CMP = compare）
-- 幂等：先删后插（按编号识别）
--
-- 场景：集团景观条线负责人王建国 7/15 下达统一指令
--   "所有项目地被苗木与重点点睛苗木，7/20 前完成遮阴网覆盖，
--    遮光率≥70%、四周压实、无漏搭"
-- 项目 A（翠湖庭院，使用 Emily）留下的多渠道留痕：
--   ① 任务下发（王建国，指令下达人）
--   ② 全景节点（王建国，节点创建）
--   ③ 完工上报事件（张正宏，执行人工长）
--   ④ 认证记录（李景利，认证人，confirmed_by）
--   ⑤ 验收检查事件（李景利，第二来源，认证人本人留痕）
--   ⑥ 照片存放地址（张正宏，attachments 字段，不实际放图片）
--
-- 核心：多人（王建国≠张正宏≠李景利）× 多时间（7/15/7/18/7/19）
--       × 多渠道（任务/节点/事件×2），汇聚于同一项目底座
-- ============================================================

BEGIN;

-- 清理旧预埋
DELETE FROM events        WHERE event_no LIKE 'EVT-CMP-%';
DELETE FROM tasks         WHERE task_no  LIKE 'TSK-CMP-%';
DELETE FROM project_nodes WHERE node_id  LIKE 'SG-CMP-%';

-- ════════════════════════════════════════════════════════════
-- ① 任务下发（指令下达人 = 王建国，执行人 = 张正宏）
-- ════════════════════════════════════════════════════════════
INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
    status, due_date, created_by, created_at, updated_at)
SELECT
    gen_random_uuid()::text, 'TSK-CMP-9001', p.id,
    '地被苗木与重点点睛苗木遮阴网覆盖',
    '集团统一指令：所有在建项目地被苗木与重点点睛苗木，7月20日前完成遮阴网覆盖。'
    '遮阴网要求：遮光率≥70%、四周压实固定、无漏搭。',
    (SELECT id FROM users WHERE username='张正宏'), '张正宏',
    'done', '2026-07-20T18:00:00+08:00',
    (SELECT id FROM users WHERE username='王建国'), '2026-07-15T09:00:00+08:00',
    '2026-07-19T17:00:00+08:00'
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- ════════════════════════════════════════════════════════════
-- ② 全景节点（节点创建人 = 王建国，责任人 = 张正宏）
-- ════════════════════════════════════════════════════════════
INSERT INTO project_nodes (id, project_id, node_id, node_name, owner_dept_id,
    related_company_id, deadline, remark, creator_id, created_at, approver_id,
    approved_at, completed_at, is_discarded, status, responsible_user_id,
    node_type, visibility_mode, progress, parent_node_id, child_weight, updated_at)
SELECT
    gen_random_uuid()::text, p.id, 'SG-CMP-9001',
    '地被苗木与重点点睛苗木遮阴网覆盖',
    '景观工程部', '建设单位',
    '2026-07-20T18:00:00+08:00',
    '集团统一指令节点：遮光率≥70%、四周压实、无漏搭',
    (SELECT id FROM users WHERE username='王建国'), '2026-07-15T09:30:00+08:00',
    '', '', '2026-07-19T17:00:00+08:00', false, 'COMPLETED',
    (SELECT id FROM users WHERE username='张正宏'),
    'TASK', 'public', '100.00', '', '1.0000', '2026-07-19T17:00:00+08:00'
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- ════════════════════════════════════════════════════════════
-- ③ 完工上报事件（执行人 = 张正宏，7/18 上报）
--    照片地址存 attachments（模拟，不实际放图片）
-- ════════════════════════════════════════════════════════════
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, attachments, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-CMP-9001', '施工动态', '成果提交',
    p.id, (SELECT id FROM users WHERE username='张正宏'),
    '地被苗木与重点点睛苗木遮阴网覆盖完成',
    '3#地块地被苗木与重点点睛苗木已完成遮阴网覆盖，遮光率实测72%，四周压实固定，无漏搭。',
    '2026-07-18T16:00:00+08:00',
    '["file:///app/attachments/遮阴网覆盖完工照片.jpg"]',
    'confirmed', '2026-07-18T17:00:00+08:00',
    (SELECT id FROM users WHERE username='李景利'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

-- ════════════════════════════════════════════════════════════
-- ④ 验收检查事件（第二来源 = 李景利，7/19 认证人本人留痕）
--    形成「执行上报(张正宏 7/18) vs 验收检查(李景利 7/19)」可对照
-- ════════════════════════════════════════════════════════════
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
SELECT
    gen_random_uuid()::text, 'EVT-CMP-9002', '质量安全', '成果提交',
    p.id, (SELECT id FROM users WHERE username='李景利'),
    '遮阴网覆盖现场验收检查',
    '现场抽查地被苗木遮阴网覆盖情况，遮光率抽测达标，固定方式符合要求，无漏搭。',
    '2026-07-19T10:00:00+08:00',
    'confirmed', '2026-07-19T10:30:00+08:00',
    (SELECT id FROM users WHERE username='王建国'), now()::text
FROM projects p WHERE p.code='EMERALD-01' AND p.is_deleted=false;

COMMIT;

-- ════════════════════════════════════════════════════════════
-- 验证插入结果
-- ════════════════════════════════════════════════════════════
SELECT 'task'  AS grp, task_no AS no, title FROM tasks        WHERE task_no='TSK-CMP-9001'
UNION ALL SELECT 'node', node_id, node_name FROM project_nodes WHERE node_id='SG-CMP-9001'
UNION ALL SELECT 'event', event_no, title FROM events         WHERE event_no LIKE 'EVT-CMP-%';
