-- ============================================================
-- 005_seed_business_data.sql —— 业务数据种子
--   events / tasks / meetings / business_flow_orders / instruction_orders
--   project_plans / plan_items / conversations / messages / message_attachments
--
-- Precondition: 002 + 003 must be run first (users + companies + project + files)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 005_seed_business_data.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 0. Temp lookup tables (reuse pattern)
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _su AS
SELECT id, username, level FROM users WHERE is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sp AS
SELECT id, code FROM projects WHERE code = 'ECOCITY-26' AND is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sf AS
SELECT id, file_no, filename, file_category FROM files
WHERE project_id = (SELECT id FROM _sp LIMIT 1) AND is_deleted = false;

-- ============================================================
-- 1. Events —— project milestones and records (~10 items)
-- ============================================================
INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20250415-0001', '里程碑', '立项',
    p.id, u.id, NULL,
    '可行性研究报告通过评审',
    '生态城一期项目可行性研究报告经市发改委组织专家评审通过，项目估算总投资4.85亿元。',
    '2025-04-15', '[]', '评审意见：项目可行，建议加快推进。',
    '{"milestone": "feasibility_approved", "stage": "立项"}',
    'confirmed', '2025-04-15T10:00:00', '2025-04-15T10:00:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20250630-0001', '里程碑', '立项',
    p.id, u.id, NULL,
    '项目取得立项批复',
    '市发改委正式批复生态城一期项目立项，核准建设规模及投资。同步取得用地预审意见。',
    '2025-06-30', '[]', '批复文号：X发改投资[2025]128号',
    '{"milestone": "project_approved", "stage": "立项"}',
    'confirmed', '2025-06-30T14:30:00', '2025-06-30T14:30:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20250930-0001', '里程碑', '规划设计',
    p.id, u.id, NULL,
    '方案设计通过规划审批',
    '方案设计文本经市规划局审查通过，总建筑面积15.6万㎡，容积率2.5，绿化率35%。',
    '2025-09-30', '[]', '审批意见：方案符合控制性详细规划要求。',
    '{"milestone": "schematic_design_approved", "stage": "规划设计"}',
    'confirmed', '2025-09-30T16:00:00', '2025-09-30T16:00:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20251231-0001', '里程碑', '规划设计',
    p.id, u.id, NULL,
    '建设工程规划许可证取得',
    '取得建设工程规划许可证（建字第31000020250088号），项目正式进入施工准备阶段。',
    '2025-12-31', '[]', '同步取得建设用地规划许可证和不动产权证。',
    '{"milestone": "planning_permit_obtained", "stage": "规划设计"}',
    'confirmed', '2025-12-31T11:00:00', '2025-12-31T11:00:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260115-0001', '节点事件', '工程施工',
    p.id, u.id, NULL,
    '建筑工程施工许可证取得',
    '取得建筑工程施工许可证（施字第31000020260012号），项目正式开工。',
    '2026-01-15', '[]', '施工许可证有效期至2027-06-30。',
    '{"milestone": "construction_permit_obtained", "stage": "工程施工"}',
    'confirmed', '2026-01-15T09:00:00', '2026-01-15T09:00:00'
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260220-0001', '施工记录', '工程施工',
    p.id, u1.id, NULL,
    '样板段放线完成',
    '1号楼首层样板段放线完成，轴线偏差在规范允许范围内，监理复核合格。',
    '2026-02-20', '[]', '经监理确认，可进行下步工序。',
    '{"node_id": "ECOC-SG-01-02-02", "task": "setting_out"}',
    'confirmed', '2026-02-20T15:30:00', '2026-02-20T15:30:00'
FROM _sp p, _su u1 WHERE u1.username = '张正宏';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260315-0001', '验收', '工程施工',
    p.id, u1.id, NULL,
    '地基与基础分部工程验收通过',
    '桩基子分部+地下室结构子分部验收一次性通过。质量监督站全程监督，验收结论：合格。',
    '2026-03-15', '[]', '五方责任主体签认完毕。',
    '{"milestone": "foundation_accepted", "stage": "工程施工", "node_id": "ECOC-SG-01-01"}',
    'confirmed', '2026-03-15T16:00:00', '2026-03-15T16:00:00'
FROM _sp p, _su u1 WHERE u1.username = '陈建华';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260520-0001', '施工记录', '工程施工',
    p.id, u1.id, NULL,
    '1-2层主体结构混凝土浇筑完成',
    '1号楼1-2层框架柱、剪力墙、梁板混凝土浇筑完成，养护中。3层柱筋绑扎中。',
    '2026-05-20', '[]', '混凝土标号C40，坍落度检测合格。',
    '{"node_id": "ECOC-SG-01-02-02", "progress": 65}',
    'confirmed', '2026-05-20T17:00:00', '2026-05-20T17:00:00'
FROM _sp p, _su u1 WHERE u1.username = '张正宏';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260610-0001', '设计变更', '工程施工',
    p.id, u1.id, NULL,
    '结构设计变更——3层局部梁截面调整',
    '设计单位提出3层部分框架梁截面由300×600调整为350×700，经甲方和监理审核同意。',
    '2026-06-10', '[]', '变更编号：BG-2026-0012，不涉及造价增加。',
    '{"change_type": "design_change", "node_id": "ECOC-SG-01-02-02"}',
    'confirmed', '2026-06-10T10:00:00', '2026-06-10T10:00:00'
FROM _sp p, _su u1 WHERE u1.username = '赵明远';

INSERT INTO events (id, event_no, event_type, category, project_id, user_id, message_id,
    title, description, event_date, attachments, remarks, payload, status, created_at, confirmed_at)
SELECT uuid_generate_v4()::text, 'EVT-20260701-0001', '安全巡检', '工程施工',
    p.id, u1.id, NULL,
    '二季度安全文明施工检查通过',
    '市安监站二季度安全文明施工专项检查，项目评分92分（优秀），无重大安全隐患。',
    '2026-07-01', '[]', '检查组建议加强高温季节防暑降温措施。',
    '{"inspection_type": "safety", "score": 92}',
    'confirmed', '2026-07-01T14:00:00', '2026-07-01T14:00:00'
FROM _sp p, _su u1 WHERE u1.username = '陈建华';

-- ============================================================
-- 2. Tasks —— work assignments (~10 items)
-- ============================================================
INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260221-0001', p.id, NULL,
    '样板段钢筋绑扎及验收',
    '1号楼首层样板段墙柱钢筋绑扎，完成后报监理验收。钢筋规格及间距严格按图纸施工。',
    u1.id, '张工', 'done', '2026-02-28', '2月底前',
    u2.id, '2026-02-21T08:00:00', '2026-02-28T10:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260316-0001', p.id, NULL,
    '地下室防水施工',
    '地下室外墙及底板防水层施工，材料为SBS改性沥青防水卷材（3+4mm双层做法）。',
    u1.id, '孙师傅', 'done', '2026-03-31', '3月底前',
    u2.id, '2026-03-16T08:00:00', '2026-03-31T17:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '孙建国' AND u2.username = '张正宏';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260401-0001', p.id, NULL,
    '1层主体结构施工',
    '1层框架柱、剪力墙钢筋绑扎→模板支设→混凝土浇筑→养护。混凝土标号C40。',
    u1.id, '张工', 'done', '2026-04-30', '4月底前',
    u2.id, '2026-04-01T08:00:00', '2026-04-30T16:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260501-0001', p.id, NULL,
    '2层主体结构施工',
    '2层框架柱、剪力墙钢筋绑扎→模板支设→混凝土浇筑→养护。',
    u1.id, '张工', 'done', '2026-05-20', '5月20日前',
    u2.id, '2026-05-01T08:00:00', '2026-05-20T14:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260521-0001', p.id, NULL,
    '3层主体结构施工',
    '3层框架柱、剪力墙钢筋绑扎→模板支设→混凝土浇筑→养护。含设计变更（梁截面调整）执行。',
    u1.id, '张工', 'doing', '2026-07-20', '7月20日前',
    u2.id, '2026-05-21T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260702-0001', p.id, NULL,
    '高温季节防暑降温措施落实',
    '根据二季度安全检查意见，落实高温季节施工防暑降温措施：备足饮用水及防暑药品，调整午间作业时间。',
    u1.id, '孙师傅', 'doing', '2026-07-10', '7月10日前',
    u2.id, '2026-07-02T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '孙建国' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260705-0001', p.id, NULL,
    '3层结构验收准备',
    '3层混凝土浇筑完成后，整理钢筋隐蔽验收记录、混凝土试块报告、模板验收记录等，准备结构验收。',
    u1.id, '张工', 'todo', '2026-07-25', '7月25日前',
    u2.id, '2026-07-05T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260710-0001', p.id, NULL,
    '周进度报表编制',
    '编制本周（7月7日-7月11日）施工进度报表，含实际进度与计划对比分析、人材机投入统计。',
    u1.id, '孙师傅', 'todo', '2026-07-11', '本周五前',
    u2.id, '2026-07-10T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '孙建国' AND u2.username = '张正宏';

INSERT INTO tasks (id, task_no, project_id, source_message_id, title, description,
    owner_id, owner_text, status, due_date, due_text, created_by, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'TSK-20260712-0001', p.id, NULL,
    '4层施工准备——模板及支撑体系检查',
    '4层模板支撑架搭设前，检查已拆除的1-2层模板支撑体系完好性，补充损耗构件。',
    u1.id, '孙师傅', 'todo', '2026-07-18', '7月18日前',
    u2.id, '2026-07-12T08:00:00', '2026-07-13T12:00:00'
FROM _sp p, _su u1, _su u2 WHERE u1.username = '孙建国' AND u2.username = '张正宏';

-- ============================================================
-- 3. Meetings —— meeting records (~5 items)
-- ============================================================
INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260201-0001', p.id,
    '施工组织设计评审会',
    '对总包单位提交的施工组织设计进行评审，重点审查施工部署、总进度计划、资源配置、质量安全保证措施。',
    '["李景利", "张正宏", "陈建华", "赵明远"]', NULL,
    u.id, 4, '2026-02-01', '项目部会议室', u.id,
    '李经理, 张工, 陈监理, 赵工',
    '施工组织设计总体可行，需补充：1）雨季施工专项方案；2）深基坑监测方案细化。',
    '[{"item": "补充雨季施工方案", "responsible": "张工", "deadline": "2026-02-15"}, {"item": "细化深基坑监测方案", "responsible": "张工", "deadline": "2026-02-20"}]',
    '[]', 2, '2026-02-01T10:00:00', '2026-02-01T12:00:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260215-0001', p.id,
    '第一次工地例会',
    '建设、设计、总包、监理四方第一次工地例会。明确项目管理制度、沟通机制、审批流程。',
    '["李景利", "张正宏", "陈建华", "赵明远", "王建国"]', NULL,
    u.id, 0, '2026-02-15', '项目部会议室', u.id,
    '王总, 李经理, 张工, 陈监理, 赵工',
    '1）每周五下午2点召开周例会；2）工程变更审批流程：总包→监理→甲方→设计（如需）；3）材料进场报验提前24h通知监理。',
    '[{"item": "建立项目微信群沟通渠道", "responsible": "李经理", "deadline": "2026-02-16"}, {"item": "提交开工报告", "responsible": "张工", "deadline": "2026-02-20"}]',
    '[]', 2, '2026-02-15T09:00:00', '2026-02-15T11:30:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260316-0001', p.id,
    '地基与基础验收总结会',
    '桩基+地下室结构验收通过后的总结会。质量监督站反馈：观感质量优良，资料完整。下一步转入主体结构施工。',
    '["李景利", "张正宏", "陈建华", "孙建国"]', NULL,
    u.id, 1, '2026-03-16', '项目部会议室', u.id,
    '李经理, 张工, 陈监理, 孙师傅',
    '基础阶段质量优良，工期比计划提前12天。进入主体施工后重点关注：1）混凝土外观质量；2）高大模板支撑安全。',
    '[{"item": "编制主体结构施工专项方案", "responsible": "张工", "deadline": "2026-03-25"}, {"item": "模板支撑体系专项设计", "responsible": "赵工", "deadline": "2026-03-30"}]',
    '[]', 2, '2026-03-16T14:00:00', '2026-03-16T15:30:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260611-0001', p.id,
    '3层梁截面设计变更专题会',
    '设计单位提出3层局部框架梁截面调整方案，各方讨论确认变更范围、造价影响及工期评估。',
    '["李景利", "张正宏", "陈建华", "赵明远"]', NULL,
    u.id, 1, '2026-06-11', '项目部会议室', u.id,
    '李经理, 张工, 陈监理, 赵工',
    '同意设计变更（BG-2026-0012），梁截面由300×600调整为350×700。变更不增加造价，不影响总工期。',
    '[{"item": "出具正式设计变更通知单", "responsible": "赵工", "deadline": "2026-06-13"}, {"item": "更新施工图", "responsible": "赵工", "deadline": "2026-06-20"}]',
    '[]', 2, '2026-06-11T14:00:00', '2026-06-11T15:00:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees, source_message_id,
    created_by, meeting_type, meeting_date, location, host_id, attendee_names, conclusion,
    action_items, related_file_ids, status, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'MTG-20260704-0001', p.id,
    '二季度质量安全总结暨三季度计划会',
    '总结二季度质量安全管理情况（安全评分92分），部署三季度重点：1）应对高温及台风季节；2）4-6层主体结构施工启动准备。',
    '["王建国", "李景利", "张正宏", "陈建华", "孙建国"]', NULL,
    u.id, 0, '2026-07-04', '项目部会议室', u.id,
    '王总, 李经理, 张工, 陈监理, 孙师傅',
    '二季度安全质量受控。三季度目标：3层7月底前完成，4-5层9月底前完成。高温季节调整作业时间（11:00-15:00暂停室外高空作业）。',
    '[{"item": "落实防暑降温措施", "responsible": "孙师傅", "deadline": "2026-07-10"}, {"item": "4层施工准备", "responsible": "张工", "deadline": "2026-07-20"}, {"item": "三季度进度计划细化", "responsible": "李经理", "deadline": "2026-07-08"}]',
    '[]', 2, '2026-07-04T09:00:00', '2026-07-04T11:00:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

-- ============================================================
-- 4. Business Flow Orders (~5 items)
-- ============================================================
INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'FLW-20260613-0001', p.id,
    '设计变更——3层局部梁截面调整（BG-2026-0012）', 1,
    '{"change_scope": "3层框架梁", "cost_impact": 0, "schedule_impact": 0}',
    '2026-06-20', NULL, '总包确认', u1.id,
    '[{"node": "设计提出", "handler": "赵工", "time": "2026-06-10T10:00:00", "action": "提交变更方案"}, {"node": "监理审核", "handler": "陈监理", "time": "2026-06-10T16:00:00", "action": "技术审核通过"}, {"node": "甲方审批", "handler": "李经理", "time": "2026-06-11T10:00:00", "action": "同意变更"}, {"node": "专题会确认", "handler": "四方", "time": "2026-06-11T15:00:00", "action": "会议确认通过"}]',
    '["FIL-20260704-0014"]', '["MTG-20260611-0001"]', 1, 2,
    u2.id, '2026-06-13T08:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '赵明远';

INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'FLW-20260316-0001', p.id,
    '地基与基础分部工程验收', 4,
    '{"acceptance_type": "分部工程", "quality_rating": "优良"}',
    '2026-03-15', '2026-03-15', '完成', NULL,
    '[{"node": "总包自检", "handler": "张工", "time": "2026-03-10T08:00:00", "action": "自检合格，申请验收"}, {"node": "监理初验", "handler": "陈监理", "time": "2026-03-12T10:00:00", "action": "初验通过"}, {"node": "质监站监督验收", "handler": "质监站", "time": "2026-03-15T14:00:00", "action": "监督验收通过"}]',
    '["FIL-20260704-0011"]', '["MTG-20260316-0001"]', 2, 2,
    u.id, '2026-03-10T08:00:00', '2026-03-16T17:00:00', false
FROM _sp p, _su u WHERE u.username = '张正宏';

INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'FLW-20260708-0001', p.id,
    '钢筋进场验收（批次 GC-2026-0715）', 3,
    '{"material": "HRB400E钢筋", "spec": "Φ25/Φ20/Φ16/Φ12/Φ10", "quantity": "约180吨"}',
    '2026-07-12', NULL, '监理见证取样', u1.id,
    '[{"node": "材料报验", "handler": "孙师傅", "time": "2026-07-08T09:00:00", "action": "提交质保书及进场清单"}, {"node": "监理验收", "handler": "陈监理", "time": "2026-07-08T14:00:00", "action": "外观及尺寸检查通过，待取样送检"}]',
    '[]', '[]', 1, 2,
    u2.id, '2026-07-08T09:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '孙建国';

INSERT INTO business_flow_orders (id, flow_no, project_id, title, flow_type, metrics,
    planned_finish_time, actual_finish_time, current_node, current_handler_id,
    flow_records, related_file_ids, related_meeting_ids, status, priority,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'FLW-20260710-0001', p.id,
    '7月份进度款支付申请', 5,
    '{"amount": 3850000, "period": "2026年6月完成工程量"}',
    '2026-07-25', NULL, '监理审核', u1.id,
    '[{"node": "总包提交", "handler": "张工", "time": "2026-07-10T08:00:00", "action": "提交6月完成工程量清单及支付申请"}, {"node": "监理审核", "handler": "陈监理", "time": "2026-07-10T10:00:00", "action": "审核中"}]',
    '[]', '[]', 1, 2,
    u2.id, '2026-07-10T08:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '张正宏';

-- ============================================================
-- 5. Instruction Orders (~3 items)
-- ============================================================
INSERT INTO instruction_orders (id, instruction_no, project_id, title, content, instruction_type,
    issuer_id, executor_ids, deadline, actual_finish_time, feedback,
    related_file_ids, related_flow_id, message_id, status, creator_id,
    created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'INS-20260702-0001', p.id,
    '高温季节防暑降温工作安排',
    '根据市安监站二季度检查意见及三季度气候特点，即日起执行高温季节作业时间调整：每日11:00-15:00暂停室外高空及露天作业。各班组备足饮用水及防暑药品。安全员每日巡检。',
    0,
    u1.id,
    '["孙建国", "张正宏"]',
    '2026-07-03', NULL, '正在落实中',
    '[]', NULL, NULL, 1,
    u2.id, '2026-07-02T09:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '王建国';

INSERT INTO instruction_orders (id, instruction_no, project_id, title, content, instruction_type,
    issuer_id, executor_ids, deadline, actual_finish_time, feedback,
    related_file_ids, related_flow_id, message_id, status, creator_id,
    created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'INS-20260708-0001', p.id,
    '三季度进度计划细化要求',
    '请各单位在7月12日前提交三季度（7-9月）细化进度计划，含周分解节点、资源配置计划、风险预控措施。',
    0,
    u1.id,
    '["张正宏", "赵明远"]',
    '2026-07-12', NULL, NULL,
    '[]', NULL, NULL, 1,
    u2.id, '2026-07-08T10:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '王建国';

INSERT INTO instruction_orders (id, instruction_no, project_id, title, content, instruction_type,
    issuer_id, executor_ids, deadline, actual_finish_time, feedback,
    related_file_ids, related_flow_id, message_id, status, creator_id,
    created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'INS-20260405-0001', p.id,
    '1层混凝土外观质量整改',
    '1层东侧楼梯间剪力墙拆模后发现局部蜂窝麻面（面积约0.3㎡），请总包立即制定修补方案报监理审批，并在后续浇筑中加强振捣控制。',
    1,
    u1.id,
    '["张正宏"]',
    '2026-04-10', '2026-04-08', '已完成修补。采用高强无收缩灌浆料修补，表面平整度及色泽满足规范要求。监理复查通过。',
    '[]', NULL, NULL, 2,
    u2.id, '2026-04-05T14:00:00', '2026-04-08T16:00:00', false
FROM _sp p, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '李景利';

-- ============================================================
-- 6. Project Plan + Plan Items
-- ============================================================
INSERT INTO project_plans (id, plan_no, project_id, title, plan_type, start_date, end_date,
    creator_id, status, parent_plan_id, remark, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, 'PLN-20260101-0001', p.id,
    'ECOCITY-26 施工总进度计划', 0,
    '2026-01-15', '2027-06-30',
    u.id, 2, NULL,
    '合同工期730天。已考虑不可抗力宽限期30天。关键线路：桩基→地下室→主体→机电→装修→竣工。',
    '2026-01-15T08:00:00', '2026-07-13T12:00:00', false
FROM _sp p, _su u WHERE u.username = '李景利';

-- Capture plan ID
CREATE TEMP TABLE _splan AS
SELECT id, plan_no FROM project_plans WHERE plan_no = 'PLN-20260101-0001' AND is_deleted = false;

-- Plan items (15 items — root plan only)
INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 1, '施工准备（临建搭设、场地平整、施工许可证办理）',
    u1.id, u2.id, '2026-01-31', '2026-01-15', true, false, 100,
    '提前完成，施工许可证1月15日取得',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 2, '土方开挖及边坡支护',
    u1.id, u2.id, '2025-08-15', '2025-08-12', true, false, 100,
    '提前3天完成',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 3, '桩基施工（PHC管桩）',
    u1.id, u2.id, '2025-11-30', '2025-11-25', true, false, 100,
    '提前5天完成，静载试验合格',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 4, '地下室结构施工',
    u1.id, u2.id, '2026-01-31', '2026-01-28', true, false, 100,
    '地下二层结构封顶，防水完成',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 5, '地基与基础分部验收',
    u1.id, u2.id, '2026-03-15', '2026-03-15', true, false, 100,
    '一次性通过',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 6, '1层主体结构施工',
    u1.id, u2.id, '2026-04-30', '2026-04-28', true, false, 100,
    '提前2天完成',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 7, '2层主体结构施工',
    u1.id, u2.id, '2026-05-20', '2026-05-20', true, false, 100,
    '按期完成',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 8, '3层主体结构施工',
    u1.id, u2.id, '2026-07-20', NULL, false, false, 65,
    '1-2层浇筑完成，3层钢筋绑扎中（含设计变更执行）',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 9, '4层主体结构施工',
    u1.id, u2.id, '2026-08-31', NULL, false, false, 0,
    '待3层完成后启动',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 10, '5层主体结构施工',
    u1.id, u2.id, '2026-09-30', NULL, false, false, 0,
    NULL,
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 11, '6层主体结构施工',
    u1.id, u2.id, '2026-10-31', NULL, false, false, 0,
    NULL,
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '陈建华';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 12, '主体结构分部验收',
    u1.id, u2.id, '2026-12-31', NULL, false, false, 0,
    '全部主体结构完成后组织',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '陈建华' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 13, '机电安装施工',
    u1.id, u2.id, '2027-04-30', NULL, false, false, 0,
    '主体结构验收后方可大面积展开',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 14, '室内外装饰装修',
    u1.id, u2.id, '2027-05-31', NULL, false, false, 0,
    NULL,
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO plan_items (id, plan_id, item_no, content, responsible_id, reviewer_id,
    planned_date, actual_date, is_completed, is_covered, progress, remark,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text, pl.id, 15, '竣工验收及备案',
    u1.id, u2.id, '2027-06-30', NULL, false, false, 0,
    '含规划验收、消防验收、质监验收、档案验收',
    u2.id, '2026-01-15T08:00:00', '2026-01-31T17:00:00', false
FROM _splan pl, _su u1, _su u2 WHERE u1.username = '李景利' AND u2.username = '王建国';

-- ============================================================
-- 7. Conversations + Messages (IM simulation)
-- ============================================================

-- 7.1 Project group chat
INSERT INTO conversations (id, im_platform, conversation_type, conversation_id, group_id,
    title, project_id, takeover_mode, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'simulator', 'group', 'group_ecocity26_work', 'ECOCITY26_WORK',
    '生态城一期项目工作群', p.id, 'auto',
    '2026-01-15T08:00:00', '2026-07-13T12:00:00'
FROM _sp p;

CREATE TEMP TABLE _sconv_group AS
SELECT id FROM conversations WHERE conversation_id = 'group_ecocity26_work' LIMIT 1;

-- 7.2 Private conversations
INSERT INTO conversations (id, im_platform, conversation_type, conversation_id, group_id,
    title, project_id, takeover_mode, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'simulator', 'private', 'private_李景利', NULL,
    '李经理私聊', p.id, 'auto',
    '2026-01-15T08:00:00', '2026-07-13T12:00:00'
FROM _sp p;

CREATE TEMP TABLE _sconv_pm AS
SELECT id FROM conversations WHERE conversation_id = 'private_李景利' LIMIT 1;

INSERT INTO conversations (id, im_platform, conversation_type, conversation_id, group_id,
    title, project_id, takeover_mode, created_at, updated_at)
SELECT uuid_generate_v4()::text, 'simulator', 'private', 'private_张正宏', NULL,
    '张工私聊', p.id, 'auto',
    '2026-01-15T08:00:00', '2026-07-13T12:00:00'
FROM _sp p;

-- ============================================================
-- 8. Messages — simulate project communication (~25 messages)
-- ============================================================
-- Helper function for event_id
CREATE OR REPLACE FUNCTION _seed_event_id(seq int) RETURNS text AS $$
BEGIN
    RETURN 'EVTID-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || LPAD(seq::text, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- 8.1 Group chat messages (project work group)
INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at)
SELECT uuid_generate_v4()::text, _seed_event_id(1), c.id, u.id, 'sim_王建国',
    '各位好，生态城一期项目工作群正式启用。以后日常工作沟通、进度汇报、验收通知都在群里进行。请各单位主要人员确认收到。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-01-15T09:00:00'
FROM _sconv_group c, _su u WHERE u.username = '王建国';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(2), c.id, u.id, 'sim_李景利',
    '收到。王总。工程部全员已入群。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-01-15T09:02:00', '2026-01-15T09:02:00'
FROM _sconv_group c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(3), c.id, u.id, 'sim_张正宏',
    '收到。总包项目部已全员入群。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-01-15T09:03:00', '2026-01-15T09:03:00'
FROM _sconv_group c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(4), c.id, u.id, 'sim_陈建华',
    '收到。监理部已入群。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-01-15T09:04:00', '2026-01-15T09:04:00'
FROM _sconv_group c, _su u WHERE u.username = '陈建华';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(5), c.id, u.id, 'sim_李景利',
    '@Emily 帮我创建事件：样板段放线完成，1号楼首层样板段放线经监理复核合格，轴线偏差在规范允许范围。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-02-20T15:25:00', '2026-02-20T15:25:00'
FROM _sconv_group c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(6), c.id, u.id, 'sim_李景利',
    '@Emily 帮我创建一个任务：样板段钢筋绑扎及验收，负责人张工，截止日期2月28日。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-02-21T08:10:00', '2026-02-21T08:10:00'
FROM _sconv_group c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(7), c.id, u.id, 'sim_张正宏',
    '收到。样板段钢筋今天开始绑扎，周五前完成报验。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-02-21T08:15:00', '2026-02-21T08:15:00'
FROM _sconv_group c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(8), c.id, u.id, 'sim_张正宏',
    '@Emily 样板段钢筋绑扎已完成，今日报监理验收。请记录。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-02-26T14:00:00', '2026-02-26T14:00:00'
FROM _sconv_group c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(9), c.id, u.id, 'sim_陈建华',
    '监理今日验收样板段钢筋，绑扎质量合格，同意进入下一道工序。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-02-26T16:30:00', '2026-02-26T16:30:00'
FROM _sconv_group c, _su u WHERE u.username = '陈建华';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(10), c.id, u.id, 'sim_李景利',
    '@Emily 帮我查一下ECOCITY-26项目当前的施工进度。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-03-20T10:00:00', '2026-03-20T10:00:00'
FROM _sconv_group c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(11), c.id, u.id, 'sim_赵明远',
    '@Emily 结构设计变更——3层部分框架梁截面需要调整，请帮我发起一个变更审批流程。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-06-10T09:30:00', '2026-06-10T09:30:00'
FROM _sconv_group c, _su u WHERE u.username = '赵明远';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(12), c.id, u.id, 'sim_张正宏',
    '1-2层主体混凝土浇筑全部完成，目前正在养护。3层柱筋绑扎中，预计本周五完成柱筋。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-05-21T16:00:00', '2026-05-21T16:00:00'
FROM _sconv_group c, _su u WHERE u.username = '张正宏';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(13), c.id, u.id, 'sim_李景利',
    '@Emily 帮我创建会议：7月4日上午9点召开二季度质量安全总结暨三季度计划会，参加人：王总、李经理、张工、陈监理、孙师傅。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-07-01T14:00:00', '2026-07-01T14:00:00'
FROM _sconv_group c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(14), c.id, u.id, 'sim_孙建国',
    '李经理，钢筋进场报验已经提交系统了，HRB400E约180吨，质保书齐全。请监理安排验收。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-07-08T08:45:00', '2026-07-08T08:45:00'
FROM _sconv_group c, _su u WHERE u.username = '孙建国';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(15), c.id, u.id, 'sim_陈建华',
    '钢筋进场资料已收到。下午2点现场验收，请孙师傅安排人员配合取样送检。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-07-08T09:15:00', '2026-07-08T09:15:00'
FROM _sconv_group c, _su u WHERE u.username = '陈建华';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(16), c.id, u.id, 'sim_李景利',
    '@Emily 帮我查一下项目全景节点图目前哪些节点在施工中，哪些还没激活。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-07-10T15:30:00', '2026-07-10T15:30:00'
FROM _sconv_group c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(17), c.id, u.id, 'sim_张正宏',
    '@Emily 帮我查一下我这周有哪些待办任务。',
    'inbound', 'text', 0, '[]', '', NULL, 'ECOCITY26_WORK',
    '2026-07-13T08:30:00', '2026-07-13T08:30:00'
FROM _sconv_group c, _su u WHERE u.username = '张正宏';

-- 8.2 Private chat messages (李景利 with emily)
INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(18), c.id, u.id, 'sim_李景利',
    '@Emily 你好，我是李经理。帮我整理一下ECOCITY-26项目从开工到现在的重要里程碑事件。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-10T16:00:00', '2026-07-10T16:00:00'
FROM _sconv_pm c, _su u WHERE u.username = '李景利';

INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(19), c.id, u.id, 'sim_李景利',
    '@Emily 帮我把项目质量管理制度文件发给总包张工和监理陈工。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-11T09:00:00', '2026-07-11T09:00:00'
FROM _sconv_pm c, _su u WHERE u.username = '李景利';

-- 8.3 Private chat messages (孙建国 with emily)
INSERT INTO messages (id, event_id, conversation_id, sender_user_id, sender_im_id, content,
    direction, message_type, msg_type, attachments, file_url, receiver_id, group_id,
    created_at, updated_at)
SELECT uuid_generate_v4()::text, _seed_event_id(20), c.id, u.id, 'sim_孙建国',
    '@Emily 我的防暑降温任务已经完成了，饮水点和药品都配好了，帮我标记一下完成。',
    'inbound', 'text', 0, '[]', '', NULL, NULL,
    '2026-07-12T10:00:00', '2026-07-12T10:00:00'
FROM _sconv_pm c, _su u WHERE u.username = '孙建国';

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

SELECT '--- Messages ---' AS section;
SELECT COUNT(*) AS total_messages FROM messages;

-- Cleanup
DROP FUNCTION IF EXISTS _seed_event_id;

COMMIT;
