-- seed_trace_uuid.sql — 用硬编码 UUID 预埋，避免中文子查询编码问题
BEGIN;

DELETE FROM events   WHERE event_no   LIKE 'EVT-TRACE-%';
DELETE FROM tasks    WHERE task_no    LIKE 'TSK-TRACE-%';
DELETE FROM meetings WHERE meeting_no LIKE 'MTG-TRACE-%';

-- 张正宏=6131e468-5935-4b1f-9a1a-bb918321d07f  李景利=76d6707d-5c70-430d-8521-eecfb50b9ca5
-- 王建国=d1351471-c961-4e8f-9161-cc848531ca4f  罗永强=5f4b74a9-e702-4e91-9240-6089401ad89f
-- 项目 EMERALD-01 = 9def49ba-f027-405a-946d-4b21367a47b9

-- 组合A：记录人=张正宏，认证人=李景利 (4条)
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
VALUES
(gen_random_uuid()::text, 'EVT-TRACE-9001', '施工动态', '成果提交',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '3#地块乔木种植完成',
 '3#地块乔木种植作业全部完成，共种植乔木 156 株，成活率待后续观察。',
 '2026-08-10T16:00:00+08:00', 'confirmed', '2026-08-10T17:20:00+08:00',
 '76d6707d-5c70-430d-8521-eecfb50b9ca5', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9002', '施工动态', '成果提交',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 'C区滨水步道硬质铺装完成约80平米',
 'C区滨水步道样板段硬质铺装施工完成约80平米，待监理验收确认。',
 '2026-08-11T16:00:00+08:00', 'confirmed', '2026-08-11T17:00:00+08:00',
 '76d6707d-5c70-430d-8521-eecfb50b9ca5', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9003', '质量安全', '成果提交',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '2#楼外墙保温施工完成',
 '2#楼外墙保温层施工全部完成，表面平整度自检合格。',
 '2026-08-11T15:00:00+08:00', 'confirmed', '2026-08-11T16:10:00+08:00',
 '76d6707d-5c70-430d-8521-eecfb50b9ca5', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9004', '施工动态', '成果提交',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '样板段石材铺装完成',
 '售楼处前广场样板段石材铺装完成，观感质量符合要求。',
 '2026-08-12T10:00:00+08:00', 'confirmed', '2026-08-12T11:00:00+08:00',
 '76d6707d-5c70-430d-8521-eecfb50b9ca5', now()::text);

-- 组合B：记录人=张正宏，认证人空（pending，4条）
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, created_at)
VALUES
(gen_random_uuid()::text, 'EVT-TRACE-9005', '质量安全', '待分类',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '基坑东侧发现渗水约2平米',
 '基坑东侧发现渗水，面积约2平米，已通知监理，待处理。',
 '2026-08-12T14:00:00+08:00', 'pending', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9006', '施工动态', '待分类',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '5#楼三层钢筋绑扎完成',
 '5#楼三层梁板钢筋绑扎完成，待监理隐蔽验收。',
 '2026-08-12T15:00:00+08:00', 'pending', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9007', '质量安全', '待分类',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '现场临时用电隐患整改完成',
 '现场临时用电隐患已整改完成，配电箱接地恢复。',
 '2026-08-12T16:00:00+08:00', 'pending', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9008', '施工动态', '待分类',
 '9def49ba-f027-405a-946d-4b21367a47b9', '6131e468-5935-4b1f-9a1a-bb918321d07f',
 '苗木进场验收待监理确认',
 '首批苗木进场，规格初验合格，待监理到场联合验收。',
 '2026-08-12T17:00:00+08:00', 'pending', now()::text);

-- 组合C：记录人=认证人=王建国（同人，2条）
INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
    title, description, event_date, status, confirmed_at, confirmed_by, created_at)
VALUES
(gen_random_uuid()::text, 'EVT-TRACE-9009', '工作成果', '成果提交',
 '9def49ba-f027-405a-946d-4b21367a47b9', 'd1351471-c961-4e8f-9161-cc848531ca4f',
 '景观施工图苗木审核报告完成',
 '景观施工图苗木使用方案审核完成，评分82分，报告已归档。',
 '2026-08-13T09:00:00+08:00', 'confirmed', '2026-08-13T09:30:00+08:00',
 'd1351471-c961-4e8f-9161-cc848531ca4f', now()::text),
(gen_random_uuid()::text, 'EVT-TRACE-9010', '工作成果', '成果提交',
 '9def49ba-f027-405a-946d-4b21367a47b9', 'd1351471-c961-4e8f-9161-cc848531ca4f',
 '周例会部署外墙检查工作',
 '周例会部署外墙检查专项工作，明确责任人与完成时限。',
 '2026-08-13T10:00:00+08:00', 'confirmed', '2026-08-13T10:20:00+08:00',
 'd1351471-c961-4e8f-9161-cc848531ca4f', now()::text);

-- 任务 3 条（created_by=张正宏，owner 分离）
INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
    status, due_date, created_by, created_at, updated_at)
VALUES
(gen_random_uuid()::text, 'TSK-TRACE-9001', '9def49ba-f027-405a-946d-4b21367a47b9',
 '外墙检查报告',
 '完成2#楼外墙保温施工后的专项检查并出具报告。',
 '76d6707d-5c70-430d-8521-eecfb50b9ca5', '李景利',
 'doing', '2026-08-15T18:00:00+08:00',
 '6131e468-5935-4b1f-9a1a-bb918321d07f', now()::text, now()::text),
(gen_random_uuid()::text, 'TSK-TRACE-9002', '9def49ba-f027-405a-946d-4b21367a47b9',
 '苗木采购进场验收',
 '组织首批苗木的采购进场联合验收。',
 'd1351471-c961-4e8f-9161-cc848531ca4f', '王建国',
 'todo', '2026-08-14T18:00:00+08:00',
 '6131e468-5935-4b1f-9a1a-bb918321d07f', now()::text, now()::text),
(gen_random_uuid()::text, 'TSK-TRACE-9003', '9def49ba-f027-405a-946d-4b21367a47b9',
 '硬质铺装与园路施工',
 '推进C区滨水步道硬质铺装与园路施工。',
 '76d6707d-5c70-430d-8521-eecfb50b9ca5', '李景利',
 'doing', '2026-08-20T18:00:00+08:00',
 '6131e468-5935-4b1f-9a1a-bb918321d07f', now()::text, now()::text);

-- 会议 3 条（created_by=张正宏，host=王建国）
INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees,
    meeting_type, meeting_date, location, host_id, attendee_names,
    conclusion, action_items, status, created_by, created_at, updated_at, is_deleted)
VALUES
(gen_random_uuid()::text, 'MTG-TRACE-9001', '9def49ba-f027-405a-946d-4b21367a47b9',
 '景观工程周例会',
 '景观工程周例会，部署苗木种植与铺装施工。', '[]',
 0, '2026-08-11T09:00:00+08:00', '项目部会议室',
 'd1351471-c961-4e8f-9161-cc848531ca4f',
 '["王建国","李景利","张正宏"]',
 '部署3#地块乔木种植与C区铺装施工，明确责任分工。',
 '[{"item":"乔木种植","assignee":"张正宏"},{"item":"铺装施工","assignee":"李景利"}]',
 1, '6131e468-5935-4b1f-9a1a-bb918321d07f', now()::text, now()::text, false),
(gen_random_uuid()::text, 'MTG-TRACE-9002', '9def49ba-f027-405a-946d-4b21367a47b9',
 '苗木种植技术交底会',
 '苗木种植前技术交底，明确种植标准与验收要求。', '[]',
 3, '2026-08-12T14:00:00+08:00', '现场会议室',
 'd1351471-c961-4e8f-9161-cc848531ca4f',
 '["王建国","张正宏"]',
 '完成苗木种植技术交底，明确种植穴深度与间距标准。',
 '[{"item":"按标准种植","assignee":"张正宏"}]',
 1, '6131e468-5935-4b1f-9a1a-bb918321d07f', now()::text, now()::text, false),
(gen_random_uuid()::text, 'MTG-TRACE-9003', '9def49ba-f027-405a-946d-4b21367a47b9',
 '样板段验收协调会',
 '协调样板段石材铺装验收事宜。', '[]',
 4, '2026-08-13T10:00:00+08:00', '项目部会议室',
 'd1351471-c961-4e8f-9161-cc848531ca4f',
 '["王建国","李景利"]',
 '样板段石材铺装观感合格，安排正式验收。',
 '[{"item":"正式验收","assignee":"李景利"}]',
 1, '6131e468-5935-4b1f-9a1a-bb918321d07f', now()::text, now()::text, false);

COMMIT;
