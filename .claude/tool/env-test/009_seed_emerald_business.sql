-- ============================================================
-- 009_seed_emerald_business.sql —— EMERALD-01 翠湖庭院业务数据种子
--   events / tasks / meetings / business_flow_orders / instruction_orders
--   project_plans / plan_items / conversations / messages
--
-- Precondition: 002 + 003 must be run first (users + companies + project + files)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 009_seed_emerald_business.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 0. Temp lookup tables (reuse pattern)
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _su AS
SELECT id, username, level FROM users WHERE is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sp AS
SELECT id, code FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sf AS
SELECT id, file_no, filename, file_category FROM files
WHERE project_id = (SELECT id FROM _sp LIMIT 1) AND is_deleted = false;

-- ============================================================
-- 1. Events —— project milestones and records (~10 items)
-- ============================================================
INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20250701-0001', '里程碑', '立项',
    p.id, u.id, NULL,
    '项目正式立项',
    '翠湖庭院住宅小区项目经市发改委正式批复立项，核准总建筑面积9,850㎡，总投资2,600万元。',
    '2025-07-01', '[]', '批复文号：X发改投资[2025]256号',
    '{"milestone":"project_approved","stage":"立项"}',
    'confirmed', '2025-07-01T09:00:00', '2025-07-01T09:00:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20250815-0001', '里程碑', '规划设计',
    p.id, u.id, NULL,
    '方案设计通过规划审批',
    '方案设计文本经市规划局审查通过，总建筑面积9,850㎡，容积率1.59，绿化率38%，5栋5-6层住宅楼。',
    '2025-08-15', '[]', '审批意见：方案符合控制性详细规划要求。',
    '{"milestone":"schematic_design_approved","stage":"规划设计"}',
    'confirmed', '2025-08-15T15:00:00', '2025-08-15T15:00:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20251101-0001', '里程碑', '规划设计',
    p.id, u.id, NULL,
    '建设工程规划许可证取得',
    '取得建设工程规划许可证（建字第31000020250156号），同步取得建设用地规划许可证。',
    '2025-11-01', '[]', '项目正式进入施工准备阶段。',
    '{"milestone":"planning_permit_obtained","stage":"规划设计"}',
    'confirmed', '2025-11-01T10:30:00', '2025-11-01T10:30:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20251215-0001', '里程碑', '工程施工',
    p.id, u.id, NULL,
    '施工许可证取得，项目开工',
    '取得建筑工程施工许可证（施字第31000020260033号），翠湖庭院项目正式开工。',
    '2025-12-15', '[]', '施工许可证有效期至2026-10-31。',
    '{"milestone":"construction_permit_obtained","stage":"工程施工"}',
    'confirmed', '2025-12-15T08:00:00', '2025-12-15T08:00:00'
FROM _sp p, _su u WHERE u.username = '张正宏';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260220-0001', '里程碑', '工程施工',
    p.id, u.id, NULL,
    '地基与基础工程通过验收',
    '土方开挖+桩基施工+基础承台地梁全部完成，经监理单位组织验收，评定为合格。',
    '2026-02-20', '[]', '验收结论：地基承载力满足设计要求，桩基静载试验合格。',
    '{"milestone":"foundation_accepted","stage":"工程施工"}',
    'confirmed', '2026-02-20T14:00:00', '2026-02-20T14:00:00'
FROM _sp p, _su u WHERE u.username = '陈建华';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260515-0001', '里程碑', '工程施工',
    p.id, u.id, NULL,
    '主体结构全面封顶',
    '五栋住宅楼主体结构全部封顶，历时5个月。框架-剪力墙结构施工质量良好。',
    '2026-05-15', '[]', '1#-5#楼全部完成屋面结构浇筑。',
    '{"milestone":"structure_topped_out","stage":"工程施工"}',
    'confirmed', '2026-05-15T11:00:00', '2026-05-15T11:00:00'
FROM _sp p, _su u WHERE u.username = '张正宏';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260701-0001', '里程碑', '工程施工',
    p.id, u.id, NULL,
    '机电安装工程通过验收',
    '给排水+强弱电预埋+电梯安装+消防报警系统安装调试全部完成，验收合格。',
    '2026-07-01', '[]', '水电管线隐蔽验收+电梯调试报告+消防联动测试均已通过。',
    '{"milestone":"mep_accepted","stage":"工程施工"}',
    'confirmed', '2026-07-01T16:00:00', '2026-07-01T16:00:00'
FROM _sp p, _su u WHERE u.username = '陈建华';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260710-0001', '节点事件', '工程施工',
    p.id, u.id, NULL,
    '场地平整与压实完成',
    '建筑垃圾清运完毕，场地标高整平至设计高程±0.000以上0.30m，碾压压实度检测合格。',
    '2026-07-10', '[]', '场地已具备景观绿化进场条件。',
    '{"milestone":"site_leveled","stage":"工程施工"}',
    'confirmed', '2026-07-10T15:30:00', '2026-07-10T15:30:00'
FROM _sp p, _su u WHERE u.username = '刘大勇';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260710-0002', '问题', '工程施工',
    p.id, u.id, NULL,
    '3#楼外墙真石漆色差问题整改',
    '巡查发现3#楼南立面真石漆存在色差，责令总包进行整改。涉及面积约120㎡。',
    '2026-07-10', '[]', '整改要求：铲除问题区域重新施工，7月10日前完成。',
    '{"issue_type":"quality","severity":"medium"}',
    'confirmed', '2026-07-10T09:00:00', '2026-07-10T09:00:00'
FROM _sp p, _su u WHERE u.username = '陈建华';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260712-0001', '检查', '工程施工',
    p.id, u.id, NULL,
    '景观进场前安全技术交底',
    '组织总包+景观施工班组进行安全技术交底，明确绿化种植/硬质铺装/照明安装的工序与安全要求。',
    '2026-07-12', '[]', '交底内容包括：施工机械安全操作、临时用电规范、高空作业防护。',
    '{"check_type":"safety_tech_briefing"}',
    'pending', '2026-07-12T10:00:00', NULL
FROM _sp p, _su u WHERE u.username = '李景利';

-- ============================================================
-- 2. Tasks —— work assignments (~10 items)
-- ============================================================
INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20250701-001', p.id, NULL,
    '编制可行性研究报告',
    '编制翠湖庭院项目可行性研究报告，含市场需求分析、建设方案、投资估算与经济效益分析。',
    u1.id, '赵工', 'done', '2025-07-01', '2025年7月1日前',
    u2.id, '2025-06-15T08:00:00', '2025-06-28T16:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '赵明远' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20251001-001', p.id, NULL,
    '办理建设工程规划许可证',
    '收集整理报建资料，向市规划局提交建设工程规划许可申请，跟踪审批进度直至取证。',
    u1.id, '李经理', 'done', '2025-10-31', '2025年10月31日前',
    u2.id, '2025-10-01T08:00:00', '2025-11-01T10:30:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '王建国';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260120-001', p.id, NULL,
    '桩基检测与验收',
    '桩基施工完成后进行静载试验和低应变检测，整理检测报告报监理验收。',
    u1.id, '张工', 'done', '2026-01-31', '2026年1月31日前',
    u2.id, '2026-01-20T08:00:00', '2026-01-28T14:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260501-001', p.id, NULL,
    '主体结构封顶验收',
    '五栋住宅楼主体结构全部封顶后，整理钢筋隐蔽验收记录+混凝土试块报告，组织分部工程验收。',
    u1.id, '陈监理', 'done', '2026-05-15', '2026年5月15日前',
    u2.id, '2026-05-01T08:00:00', '2026-05-15T11:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260520-001', p.id, NULL,
    '1#-2#楼外墙真石漆施工',
    '1#-2#楼外立面真石漆施工，按照设计配合比施工，确保颜色均匀、无流坠。',
    u1.id, '刘工', 'done', '2026-06-30', '2026年6月30日前',
    u2.id, '2026-05-20T08:00:00', '2026-06-28T18:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '刘大勇' AND u2.username = '张正宏';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260701-001', p.id, NULL,
    '3#楼外立面真石漆色差整改',
    '3#楼南立面真石漆色差区域（约120㎡）铲除后重新施工，按原设计配合比，7月10日前完成并报监理复验。',
    u1.id, '张工', 'done', '2026-07-10', '2026年7月10日前',
    u2.id, '2026-07-01T08:00:00', '2026-07-10T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260710-001', p.id, NULL,
    '审核景观绿化施工方案',
    '审核景观班组提交的绿化种植/硬质铺装/景观照明施工方案，重点审查苗木品种规格、铺装材料及工序安排。',
    u1.id, '李经理', 'doing', '2026-07-18', '2026年7月18日前',
    u2.id, '2026-07-10T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '张正宏';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260715-001', p.id, NULL,
    '苗木采购进场验收',
    '首批苗木（桂花15株、香樟8株、红叶石楠200株、金叶女贞150株）采购进场，核对品种规格数量，查验检疫证明。',
    u1.id, '黄工', 'todo', '2026-07-25', '2026年7月25日前',
    u2.id, '2026-07-15T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '黄志强' AND u2.username = '张正宏';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260720-001', p.id, NULL,
    '景观绿化种植施工',
    '按照景观施工图进行绿化种植：桂花、香樟等乔木定点放线→树穴开挖→种植→支撑→浇水养护。',
    u1.id, '黄工', 'todo', '2026-08-30', '2026年8月30日前',
    u2.id, '2026-07-10T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '黄志强' AND u2.username = '张正宏';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260720-002', p.id, NULL,
    '硬质铺装与园路施工',
    '小区内部园路基础压实→碎石垫层→混凝土基层→面层铺装（透水砖/花岗岩），含路缘石安装。',
    u1.id, '黄工', 'todo', '2026-09-15', '2026年9月15日前',
    u2.id, '2026-07-10T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '黄志强' AND u2.username = '张正宏';

-- ============================================================
-- 3. Meetings —— meeting records (~5 items)
-- ============================================================
INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20251215-0001', p.id,
    '项目启动会',
    '建设单位/总包/监理/设计四方到场，明确项目分工与工期目标。确定施工总进度计划节点。',
    '["王建国","李景利","张正宏","陈建华","赵明远"]', NULL,
    u.id, 5, '2025-12-15', '项目部会议室', u.id,
    '王总, 李经理, 张工, 陈监理, 赵工',
    '四方确认项目组织架构与分工；总进度计划关键节点：桩基→基础→主体→机电→景观→竣工。',
    '[{"item": "编制施工总进度计划", "responsible": "李经理", "deadline": "2025-12-20"}, {"item": "提交开工报告", "responsible": "张工", "deadline": "2025-12-16"}]',
    '[]', 2, '2025-12-15T09:00:00', '2025-12-15T10:30:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260110-0001', p.id,
    '基础工程施工协调会',
    '部署冬季施工措施，明确桩基施工进度计划。协调钢材和混凝土供应保障。',
    '["张正宏","陈建华","孙建国","刘大勇"]', NULL,
    u.id, 0, '2026-01-10', '项目部会议室', u.id,
    '张工, 陈监理, 孙师傅, 刘工',
    '冬季施工混凝土需添加防冻剂，覆盖保温；钢材已备货150吨，商混站已锁定供应。',
    '[{"item": "编制冬季施工专项方案", "responsible": "张工", "deadline": "2026-01-13"}, {"item": "桩机进场调试", "responsible": "刘工", "deadline": "2026-01-12"}]',
    '[]', 2, '2026-01-10T14:00:00', '2026-01-10T15:30:00', false
FROM _sp p, _su u WHERE u.username = '张正宏';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260515-0001', p.id,
    '主体结构封顶验收总结',
    '五栋住宅楼全部封顶质量评定：混凝土强度+钢筋保护层+结构尺寸偏差均在合格范围内。安排机电安装进场。',
    '["王建国","李景利","张正宏","陈建华","赵明远"]', NULL,
    u.id, 4, '2026-05-15', '项目部会议室', u.id,
    '王总, 李经理, 张工, 陈监理, 赵工',
    '主体结构质量评定合格，同意进入机电安装和装饰装修阶段。',
    '[{"item": "安排电梯和消防安装进场", "responsible": "张工", "deadline": "2026-05-20"}, {"item": "编制外立面施工计划", "responsible": "刘工", "deadline": "2026-05-18"}]',
    '[]', 2, '2026-05-15T14:00:00', '2026-05-15T15:30:00', false
FROM _sp p, _su u WHERE u.username = '陈建华';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260705-0001', p.id,
    '外立面色差问题专题会',
    '3#楼南立面真石漆色差原因分析：施工时段不同导致面层颜色差异。确定整改方案：铲除问题区域重新施工。',
    '["陈建华","张正宏","刘大勇","赵明远"]', NULL,
    u.id, 1, '2026-07-05', '项目部会议室', u.id,
    '陈监理, 张工, 刘工, 赵工',
    '确认色差原因为施工时段和温湿度差异；同意铲除重做方案，7月10日前完成整改。',
    '[{"item": "铲除3#楼南立面色差区域", "responsible": "张工", "deadline": "2026-07-07"}, {"item": "按原配合比重做真石漆", "responsible": "刘工", "deadline": "2026-07-10"}, {"item": "监理复验确认", "responsible": "陈监理", "deadline": "2026-07-11"}]',
    '[]', 2, '2026-07-05T10:00:00', '2026-07-05T11:00:00', false
FROM _sp p, _su u WHERE u.username = '陈建华';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260712-0001', p.id,
    '景观工程进场部署会',
    '场地移交确认，绿化种植/硬质铺装/景观照明三大分项施工计划排布。明确苗木进场验收标准和养护要求。',
    '["李景利","张正宏","黄志强","刘大勇","陈建华"]', NULL,
    u.id, 0, '2026-07-12', '项目部会议室', u.id,
    '李经理, 张工, 黄工, 刘工, 陈监理',
    '场地平整已验收合格，即日起移交景观班组。施工顺序：先绿化种植→再硬质铺装→最后景观照明。',
    '[{"item": "提交景观绿化施工方案报审", "responsible": "黄工", "deadline": "2026-07-15"}, {"item": "苗木进场报验", "responsible": "黄工", "deadline": "2026-07-20"}, {"item": "审核施工方案", "responsible": "李经理", "deadline": "2026-07-18"}]',
    '[]', 2, '2026-07-12T10:00:00', '2026-07-12T11:30:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

-- ============================================================
-- 4. Business Flow Orders (~4 items)
-- ============================================================
INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'BFO-20260215-01', p.id,
    '设计变更——3#楼户型局部调整', 1,
    '{"change_scope": "3#楼户型阳台进深1.5m→1.8m", "cost_impact": 0, "structure_impact": false}',
    '2026-02-20', '2026-02-20', '完成', NULL,
    '[{"node": "设计提出", "handler": "赵工", "time": "2026-02-15T10:00:00", "action": "提交3#楼户型阳台进深调整方案，由1.5m调整为1.8m，不涉及结构受力体系变化"}, {"node": "甲方审批", "handler": "李经理", "time": "2026-02-18T14:00:00", "action": "审核同意"}, {"node": "监理确认", "handler": "陈监理", "time": "2026-02-20T16:00:00", "action": "确认变更不影响结构安全"}]',
    '[]', '[]', 2, 2,
    u.id, '2026-02-15T10:00:00', '2026-02-20T16:00:00', false
FROM _sp p, _su u WHERE u.username = '赵明远';

INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'BFO-20260501-01', p.id,
    '验收申请——主体结构分部验收', 4,
    '{"acceptance_type": "分部工程", "scope": "1#-5#楼主体结构"}',
    '2026-05-15', '2026-05-15', '完成', NULL,
    '[{"node": "总包申请", "handler": "张工", "time": "2026-05-01T09:00:00", "action": "提交主体结构分部验收申请，附混凝土试块强度报告+钢筋隐蔽验收记录+结构尺寸偏差检测报告"}, {"node": "监理审核", "handler": "陈监理", "time": "2026-05-10T14:00:00", "action": "资料审核通过，同意组织验收"}, {"node": "验收通过", "handler": "五方", "time": "2026-05-15T11:00:00", "action": "主体结构分部工程验收合格"}]',
    '[]', '["MTG-20260515-0001"]', 2, 2,
    u.id, '2026-05-01T09:00:00', '2026-05-15T11:00:00', false
FROM _sp p, _su u WHERE u.username = '张正宏';

INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'BFO-20260701-01', p.id,
    '材料进场报验——首批绿化苗木', 3,
    '{"material": "绿化苗木", "items": "桂花15株/香樟8株/红叶石楠200株/金叶女贞150株"}',
    '2026-07-05', '2026-07-03', '完成', NULL,
    '[{"node": "班组报验", "handler": "黄工", "time": "2026-07-01T14:00:00", "action": "提交首批苗木进场报验单，附苗木检疫证明"}, {"node": "监理验收", "handler": "陈监理", "time": "2026-07-03T10:00:00", "action": "苗木品种规格数量核对无误，检疫证明有效，验收通过"}]',
    '[]', '[]', 2, 2,
    u.id, '2026-07-01T14:00:00', '2026-07-03T10:00:00', false
FROM _sp p, _su u WHERE u.username = '黄志强';

INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'BFO-20260708-01', p.id,
    '进度款申请——主体结构完成节点', 5,
    '{"amount": 10400000, "ratio": "合同价40%", "period": "主体结构完成节点"}',
    '2026-07-25', NULL, '监理审核', u2.id,
    '[{"node": "总包提交", "handler": "张工", "time": "2026-07-08T09:00:00", "action": "提交主体结构完成节点进度款申请，金额1040万元，附已完工程量清单和监理审核确认单"}, {"node": "监理审核", "handler": "陈监理", "time": "2026-07-10T10:00:00", "action": "审核中"}]',
    '[]', '[]', 1, 2,
    u1.id, '2026-07-08T09:00:00', '2026-07-12T10:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

-- ============================================================
-- 5. Instruction Orders (~3 items)
-- ============================================================
INSERT INTO instruction_orders (id, instruction_no, project_id, title, content, instruction_type,
    issuer_id, executor_ids, deadline, actual_finish_time, feedback,
    related_file_ids, related_flow_id, message_id, status, creator_id,
    created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'INO-20260701-01', p.id,
    '场地平整与压实指令',
    '为满足景观绿化工程进场条件，要求于2026年7月10日前完成场地建筑垃圾清运、标高整平和碾压夯实。压实度≥0.93。',
    0,
    u1.id,
    '["张正宏"]',
    '2026-07-10', '2026-07-10', '场地平整已完成，压实度检测0.94，满足要求。',
    '[]', NULL, NULL, 2,
    u2.id, '2026-07-01T08:00:00', '2026-07-10T15:30:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '李景利';

INSERT INTO instruction_orders (id, instruction_no, project_id, title, content, instruction_type,
    issuer_id, executor_ids, deadline, actual_finish_time, feedback,
    related_file_ids, related_flow_id, message_id, status, creator_id,
    created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'INO-20260705-01', p.id,
    '3#楼外墙真石漆色差整改通知',
    '3#楼南立面真石漆出现明显色差，涉及面积约120㎡。要求铲除不合格区域，按原设计配合比重新施工，7月10日前完成整改并报监理复验。',
    1,
    u1.id,
    '["张正宏"]',
    '2026-07-10', '2026-07-10', '已完成整改，铲除问题区域后按原配合比重做真石漆，监理复验通过。',
    '[]', NULL, NULL, 2,
    u2.id, '2026-07-05T09:30:00', '2026-07-10T14:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '陈建华';

INSERT INTO instruction_orders (id, instruction_no, project_id, title, content, instruction_type,
    issuer_id, executor_ids, deadline, actual_finish_time, feedback,
    related_file_ids, related_flow_id, message_id, status, creator_id,
    created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'INO-20260710-01', p.id,
    '景观工程进场施工计划',
    '场地平整已经完成，景观工程即日起进场。按照先绿化种植、再硬质铺装、最后景观照明的顺序组织施工。每日施工前进行安全交底。',
    0,
    u1.id,
    '["黄志强"]',
    '2026-09-15', NULL, NULL,
    '[]', NULL, NULL, 1,
    u2.id, '2026-07-10T16:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '李景利';

-- ============================================================
-- 6. Project Plan + Plan Items
-- ============================================================
INSERT INTO project_plans (id, plan_no, project_id, title, plan_type, start_date, end_date,
    creator_id, status, parent_plan_id, remark, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'PLN-20251215-0001', p.id,
    '翠湖庭院施工总进度计划', 0,
    '2025-12-15', '2026-10-31',
    u.id, 2, NULL,
    '总工期320天。关键线路：基础→主体→机电→外立面→景观→竣工。',
    '2025-12-15T08:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

-- Capture plan ID
CREATE TEMP TABLE _splan AS
SELECT id, plan_no FROM project_plans WHERE plan_no = 'PLN-20251215-0001' AND is_deleted = false;

-- Plan items (18 items — root plan only)
INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 1, '临时设施搭建',
    u1.id, u2.id, '2025-12-25', '2025-12-25', true, false, 100,
    '按期完成',
    u2.id, '2025-12-15T08:00:00', '2025-12-25T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 2, '图纸会审与交底',
    u1.id, u2.id, '2025-12-31', '2025-12-30', true, false, 100,
    '提前完成',
    u2.id, '2025-12-15T08:00:00', '2025-12-30T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '赵明远' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 3, '土方开挖',
    u1.id, u2.id, '2026-01-15', '2026-01-14', true, false, 100,
    '提前1天完成',
    u2.id, '2025-12-15T08:00:00', '2026-01-14T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 4, '桩基施工',
    u1.id, u2.id, '2026-01-30', '2026-01-28', true, false, 100,
    '提前2天，静载试验合格',
    u2.id, '2025-12-15T08:00:00', '2026-01-28T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 5, '基础承台与地梁',
    u1.id, u2.id, '2026-02-15', '2026-02-15', true, false, 100,
    '按期完成',
    u2.id, '2025-12-15T08:00:00', '2026-02-15T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 6, '1#-2#楼主体结构',
    u1.id, u2.id, '2026-04-15', '2026-04-14', true, false, 100,
    '提前1天完成',
    u2.id, '2025-12-15T08:00:00', '2026-04-14T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 7, '3#楼主体结构',
    u1.id, u2.id, '2026-04-30', '2026-04-28', true, false, 100,
    '提前2天完成',
    u2.id, '2025-12-15T08:00:00', '2026-04-28T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 8, '4#-5#楼主体结构',
    u1.id, u2.id, '2026-05-10', '2026-05-10', true, false, 100,
    '按期完成',
    u2.id, '2025-12-15T08:00:00', '2026-05-10T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 9, '屋面工程（5栋）',
    u1.id, u2.id, '2026-05-20', '2026-05-18', true, false, 100,
    '提前2天完成',
    u2.id, '2025-12-15T08:00:00', '2026-05-18T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 10, '水电预埋与管线',
    u1.id, u2.id, '2026-05-31', '2026-05-31', true, false, 100,
    '按期完成',
    u2.id, '2025-12-15T08:00:00', '2026-05-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 11, '电梯及消防安装',
    u1.id, u2.id, '2026-06-30', '2026-06-30', true, false, 100,
    '按期完成，联动测试通过',
    u2.id, '2025-12-15T08:00:00', '2026-06-30T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 12, '1#-2#楼外立面',
    u1.id, u2.id, '2026-06-15', '2026-06-15', true, false, 100,
    '按期完成',
    u2.id, '2025-12-15T08:00:00', '2026-06-15T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '刘大勇' AND u2.username = '张正宏';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 13, '3#楼外立面（含整改）',
    u1.id, u2.id, '2026-07-10', '2026-07-10', true, false, 100,
    '含色差整改，已全部完成',
    u2.id, '2025-12-15T08:00:00', '2026-07-10T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '刘大勇' AND u2.username = '张正宏';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 14, '4#-5#楼外立面',
    u1.id, u2.id, '2026-06-30', '2026-06-28', true, false, 100,
    '提前2天完成',
    u2.id, '2025-12-15T08:00:00', '2026-06-28T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '刘大勇' AND u2.username = '张正宏';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 15, '场地平整与压实',
    u1.id, u2.id, '2026-07-10', '2026-07-10', true, false, 100,
    '压实度0.94，验收合格',
    u2.id, '2025-12-15T08:00:00', '2026-07-10T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '刘大勇' AND u2.username = '张正宏';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 16, '景观绿化施工',
    u1.id, u2.id, '2026-09-15', NULL, false, false, 0,
    '苗木已进场，待方案审核后开工',
    u2.id, '2025-12-15T08:00:00', '2026-07-13T12:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '黄志强' AND u2.username = '张正宏';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 17, '硬质铺装与园路',
    u1.id, u2.id, '2026-09-15', NULL, false, false, 0,
    '待景观绿化完成后启动',
    u2.id, '2025-12-15T08:00:00', '2026-07-13T12:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '黄志强' AND u2.username = '张正宏';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 18, '竣工验收与备案',
    u1.id, u2.id, '2026-10-31', NULL, false, false, 0,
    '含规划验收、消防验收、质监验收、档案验收',
    u2.id, '2025-12-15T08:00:00', '2026-07-13T12:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '王建国';

-- ============================================================
-- 7. Conversations + Messages (IM simulation)
-- ============================================================

-- 7.1 Project work group chat
INSERT INTO conversations (id, im_platform, conversation_type, conversation_id, group_id,
    title, project_id, takeover_mode, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'simulator', 'group', 'sim_emer_work', 'sim_emer_work',
    '翠湖庭院工作群', p.id, 'collaborate',
    '2025-12-15T08:00:00', '2026-07-13T12:00:00'
FROM _sp p;

CREATE TEMP TABLE _sconv_work AS
SELECT id FROM conversations WHERE conversation_id = 'sim_emer_work' LIMIT 1;

-- 7.2 Private chat (李景利↔张正宏)
INSERT INTO conversations (id, im_platform, conversation_type, conversation_id, group_id,
    title, project_id, takeover_mode, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'simulator', 'private', 'sim_emer_li_zhang', NULL,
    '李景利↔张正宏', p.id, 'collaborate',
    '2025-12-15T08:00:00', '2026-07-13T12:00:00'
FROM _sp p;

CREATE TEMP TABLE _sconv_li_zhang AS
SELECT id FROM conversations WHERE conversation_id = 'sim_emer_li_zhang' LIMIT 1;

-- 7.3 Quality management sub group
INSERT INTO conversations (id, im_platform, conversation_type, conversation_id, group_id,
    title, project_id, takeover_mode, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'simulator', 'group', 'sim_emer_quality', NULL,
    '翠湖庭院质量管理', p.id, 'collaborate',
    '2026-01-01T08:00:00', '2026-07-13T12:00:00'
FROM _sp p;

CREATE TEMP TABLE _sconv_quality AS
SELECT id FROM conversations WHERE conversation_id = 'sim_emer_quality' LIMIT 1;

-- ============================================================
-- 8. Messages — simulate project communication (~26 messages)
-- ============================================================
-- Helper function for event_id
CREATE OR REPLACE FUNCTION _seed_event_id(seq int) RETURNS text AS $$
BEGIN
    RETURN 'EVTID-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || LPAD(seq::text, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- 8.1 Work group chat messages (翠湖庭院工作群)
INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(1), c.id, u.id, 'sim_李景利',
    '张工，场地平整进度如何？明天能完成吗？',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-09T10:00:00'
FROM _sconv_work c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(2), c.id, u.id, 'sim_刘大勇',
    '李经理，场地平整今天下午就能完成，压实度检测正在进行。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-09T10:05:00'
FROM _sconv_work c, _su u WHERE u.username = '刘大勇';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(3), c.id, u.id, 'sim_李景利',
    '@Emily 帮我创建一个任务：审核景观绿化施工方案，负责人李景利，截止日期2026-07-18',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-09T10:10:00'
FROM _sconv_work c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(4), c.id, u.id, 'sim_黄志强',
    'EMERALD-01项目场地平整完成了，帮我更新进度',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-10T15:30:00'
FROM _sconv_work c, _su u WHERE u.username = '黄志强';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(5), c.id, u.id, 'sim_张正宏',
    '收到，景观方案已经发给李经理了，等待审核。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-10T15:32:00'
FROM _sconv_work c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(6), c.id, u.id, 'sim_李景利',
    '好的，我尽快审核。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-10T15:35:00'
FROM _sconv_work c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(7), c.id, u.id, 'sim_陈建华',
    '场地平整已验收，压实度0.94，符合要求。可以移交给景观班组。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-10T16:00:00'
FROM _sconv_work c, _su u WHERE u.username = '陈建华';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(8), c.id, u.id, 'sim_刘大勇',
    '收到，明天开始场地移交。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-10T16:02:00'
FROM _sconv_work c, _su u WHERE u.username = '刘大勇';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(9), c.id, u.id, 'sim_李景利',
    '@Emily 帮我查一下翠湖庭院项目目前还有哪些待办任务。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-11T09:00:00'
FROM _sconv_work c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(10), c.id, u.id, 'sim_黄志强',
    '首批苗木已经进场了，桂花15株、香樟8株，状态都不错。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-11T10:00:00'
FROM _sconv_work c, _su u WHERE u.username = '黄志强';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(11), c.id, u.id, 'sim_陈建华',
    '苗木进场报验单已经审核通过。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-11T14:00:00'
FROM _sconv_work c, _su u WHERE u.username = '陈建华';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(12), c.id, u.id, 'sim_李景利',
    '@Emily 帮我创建会议：7月12日上午10点召开景观工程进场部署会，参加人：李景利、张正宏、黄志远、刘建国、陈志明。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-11T16:00:00'
FROM _sconv_work c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(13), c.id, u.id, 'sim_张正宏',
    '进度款申请已经提交了，请各位领导审批。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-12T08:30:00'
FROM _sconv_work c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(14), c.id, u.id, 'sim_王建国',
    '收到，财务这边会尽快处理。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-12T08:35:00'
FROM _sconv_work c, _su u WHERE u.username = '王建国';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(15), c.id, u.id, 'sim_黄志强',
    '@Emily 翠湖庭院项目景观绿化施工需要哪些准备工作？',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-12T09:00:00'
FROM _sconv_work c, _su u WHERE u.username = '黄志强';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(16), c.id, u.id, 'sim_张正宏',
    '关于景观施工，我已经让黄工提供了详细的施工计划，大家看看。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-12T09:30:00'
FROM _sconv_work c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(17), c.id, u.id, 'sim_赵明远',
    '景观方案我看过了，绿化品种和布局都符合设计要求。',
    'inbound', 'text', 0, '[]', '', NULL, 'sim_emer_work',
    '2026-07-12T10:15:00'
FROM _sconv_work c, _su u WHERE u.username = '赵明远';

-- 8.2 Private chat messages (李景利↔张正宏)
INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(18), c.id, u.id, 'sim_李景利',
    '张工，3#楼外立面色差整改进展如何？',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-08T09:00:00'
FROM _sconv_li_zhang c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(19), c.id, u.id, 'sim_张正宏',
    '李经理，已经整改完成，监理昨天已经复验通过了。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-08T09:05:00'
FROM _sconv_li_zhang c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(20), c.id, u.id, 'sim_李景利',
    '好的，那这件事就闭环了。辛苦了。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-08T09:10:00'
FROM _sconv_li_zhang c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(21), c.id, u.id, 'sim_张正宏',
    '@Emily 帮我查一下这个月还有哪些验收节点。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-08T09:15:00'
FROM _sconv_li_zhang c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(22), c.id, u.id, 'sim_李景利',
    '对了张工，下周的进度款材料准备得怎么样了？',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-08T09:20:00'
FROM _sconv_li_zhang c, _su u WHERE u.username = '李景利';

-- 8.3 Quality management sub group messages (翠湖庭院质量管理)
INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(23), c.id, u.id, 'sim_陈建华',
    '3#楼外立面整改已完成，请各位确认。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-10T14:00:00'
FROM _sconv_quality c, _su u WHERE u.username = '陈建华';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(24), c.id, u.id, 'sim_张正宏',
    '已确认，整改区域真石漆颜色与周边一致。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-10T14:10:00'
FROM _sconv_quality c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(25), c.id, u.id, 'sim_赵明远',
    '确认，满足设计要求。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-10T14:15:00'
FROM _sconv_quality c, _su u WHERE u.username = '赵明远';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(26), c.id, u.id, 'sim_陈建华',
    '@Emily 帮我记录一下：3#楼外立面真石漆色差问题已完成整改并复验合格。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-10T14:20:00'
FROM _sconv_quality c, _su u WHERE u.username = '陈建华';

-- ============================================================
-- 9. Verify
-- ============================================================
SELECT '--- Events ---' AS section;
SELECT event_no, title, event_type, status FROM events WHERE project_id = (SELECT id FROM _sp LIMIT 1) ORDER BY event_no;

SELECT '--- Tasks ---' AS section;
SELECT task_no, title, status, owner_text FROM tasks WHERE project_id = (SELECT id FROM _sp LIMIT 1) ORDER BY task_no;

SELECT '--- Meetings ---' AS section;
SELECT meeting_no, title, meeting_type, status FROM meetings WHERE project_id = (SELECT id FROM _sp LIMIT 1) ORDER BY meeting_no;

SELECT '--- Business Flow Orders ---' AS section;
SELECT flow_no, title, flow_type, status FROM business_flow_orders WHERE project_id = (SELECT id FROM _sp LIMIT 1) ORDER BY flow_no;

SELECT '--- Instruction Orders ---' AS section;
SELECT instruction_no, title, instruction_type, status FROM instruction_orders WHERE project_id = (SELECT id FROM _sp LIMIT 1) ORDER BY instruction_no;

SELECT '--- Plan Items ---' AS section;
SELECT pl.plan_no, pi.item_no, pi.content, pi.progress FROM plan_items pi
JOIN project_plans pl ON pi.plan_id = pl.id
WHERE pl.project_id = (SELECT id FROM _sp LIMIT 1) ORDER BY pi.item_no;

SELECT '--- Messages ---' AS section;
SELECT COUNT(*) AS total_messages FROM messages;

-- Cleanup
DROP FUNCTION IF EXISTS _seed_event_id;

COMMIT;