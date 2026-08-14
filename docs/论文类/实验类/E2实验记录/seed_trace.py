# -*- coding: utf-8 -*-
"""重新插入预埋数据，确保中文正确写入"""
import psycopg2
import uuid
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
now = datetime.now(TZ).isoformat()

ZHANG = '6131e468-5935-4b1f-9a1a-bb918321d07f'  # 张正宏
LI    = '76d6707d-5c70-430d-8521-eecfb50b9ca5'  # 李景利
WANG  = 'd1351471-c961-4e8f-9161-cc848531ca4f'  # 王建国
PROJ  = '9def49ba-f027-405a-946d-4b21367a47b9'  # EMERALD-01

conn = psycopg2.connect(host='127.0.0.1', port=25432, user='emily', password='emily_secret_2026', dbname='emily')
cur = conn.cursor()

# 清理
cur.execute("DELETE FROM events   WHERE event_no   LIKE 'EVT-TRACE-%'")
cur.execute("DELETE FROM tasks    WHERE task_no    LIKE 'TSK-TRACE-%'")
cur.execute("DELETE FROM meetings WHERE meeting_no LIKE 'MTG-TRACE-%'")

# 事件组合A：记录人=张正宏，认证人=李景利 (4条)
events_a = [
    ('EVT-TRACE-9001', '施工动态', '成果提交', '3#地块乔木种植完成',
     '3#地块乔木种植作业全部完成，共种植乔木 156 株，成活率待后续观察。',
     '2026-08-10T16:00:00+08:00', 'confirmed', '2026-08-10T17:20:00+08:00'),
    ('EVT-TRACE-9002', '施工动态', '成果提交', 'C区滨水步道硬质铺装完成约80平米',
     'C区滨水步道样板段硬质铺装施工完成约80平米，待监理验收确认。',
     '2026-08-11T16:00:00+08:00', 'confirmed', '2026-08-11T17:00:00+08:00'),
    ('EVT-TRACE-9003', '质量安全', '成果提交', '2#楼外墙保温施工完成',
     '2#楼外墙保温层施工全部完成，表面平整度自检合格。',
     '2026-08-11T15:00:00+08:00', 'confirmed', '2026-08-11T16:10:00+08:00'),
    ('EVT-TRACE-9004', '施工动态', '成果提交', '样板段石材铺装完成',
     '售楼处前广场样板段石材铺装完成，观感质量符合要求。',
     '2026-08-12T10:00:00+08:00', 'confirmed', '2026-08-12T11:00:00+08:00'),
]
for e in events_a:
    cur.execute("""INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
        title, description, event_date, status, confirmed_at, confirmed_by, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(uuid.uuid4()), e[0], e[1], e[2], PROJ, ZHANG, e[3], e[4], e[5], e[6], e[7], LI, now))

# 事件组合B：pending，认证人空 (4条)
events_b = [
    ('EVT-TRACE-9005', '质量安全', '待分类', '基坑东侧发现渗水约2平米',
     '基坑东侧发现渗水，面积约2平米，已通知监理，待处理。',
     '2026-08-12T14:00:00+08:00', 'pending'),
    ('EVT-TRACE-9006', '施工动态', '待分类', '5#楼三层钢筋绑扎完成',
     '5#楼三层梁板钢筋绑扎完成，待监理隐蔽验收。',
     '2026-08-12T15:00:00+08:00', 'pending'),
    ('EVT-TRACE-9007', '质量安全', '待分类', '现场临时用电隐患整改完成',
     '现场临时用电隐患已整改完成，配电箱接地恢复。',
     '2026-08-12T16:00:00+08:00', 'pending'),
    ('EVT-TRACE-9008', '施工动态', '待分类', '苗木进场验收待监理确认',
     '首批苗木进场，规格初验合格，待监理到场联合验收。',
     '2026-08-12T17:00:00+08:00', 'pending'),
]
for e in events_b:
    cur.execute("""INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
        title, description, event_date, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(uuid.uuid4()), e[0], e[1], e[2], PROJ, ZHANG, e[3], e[4], e[5], e[6], now))

# 事件组合C：同人=王建国 (2条)
events_c = [
    ('EVT-TRACE-9009', '工作成果', '成果提交', '景观施工图苗木审核报告完成',
     '景观施工图苗木使用方案审核完成，评分82分，报告已归档。',
     '2026-08-13T09:00:00+08:00', 'confirmed', '2026-08-13T09:30:00+08:00'),
    ('EVT-TRACE-9010', '工作成果', '成果提交', '周例会部署外墙检查工作',
     '周例会部署外墙检查专项工作，明确责任人与完成时限。',
     '2026-08-13T10:00:00+08:00', 'confirmed', '2026-08-13T10:20:00+08:00'),
]
for e in events_c:
    cur.execute("""INSERT INTO events (id, event_no, event_type, category, project_id, user_id,
        title, description, event_date, status, confirmed_at, confirmed_by, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(uuid.uuid4()), e[0], e[1], e[2], PROJ, WANG, e[3], e[4], e[5], e[6], e[7], WANG, now))

# 任务 3 条
tasks = [
    ('TSK-TRACE-9001', '外墙检查报告', '完成2#楼外墙保温施工后的专项检查并出具报告。',
     LI, '李景利', 'doing', '2026-08-15T18:00:00+08:00'),
    ('TSK-TRACE-9002', '苗木采购进场验收', '组织首批苗木的采购进场联合验收。',
     WANG, '王建国', 'todo', '2026-08-14T18:00:00+08:00'),
    ('TSK-TRACE-9003', '硬质铺装与园路施工', '推进C区滨水步道硬质铺装与园路施工。',
     LI, '李景利', 'doing', '2026-08-20T18:00:00+08:00'),
]
for t in tasks:
    cur.execute("""INSERT INTO tasks (id, task_no, project_id, title, description, owner_id, owner_text,
        status, due_date, created_by, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(uuid.uuid4()), t[0], PROJ, t[1], t[2], t[3], t[4], t[5], t[6], ZHANG, now, now))

# 会议 3 条
meetings = [
    ('MTG-TRACE-9001', '景观工程周例会', '景观工程周例会，部署苗木种植与铺装施工。',
     0, '2026-08-11T09:00:00+08:00', '项目部会议室',
     '["王建国","李景利","张正宏"]',
     '部署3#地块乔木种植与C区铺装施工，明确责任分工。',
     '[{"item":"乔木种植","assignee":"张正宏"},{"item":"铺装施工","assignee":"李景利"}]'),
    ('MTG-TRACE-9002', '苗木种植技术交底会', '苗木种植前技术交底，明确种植标准与验收要求。',
     3, '2026-08-12T14:00:00+08:00', '现场会议室',
     '["王建国","张正宏"]',
     '完成苗木种植技术交底，明确种植穴深度与间距标准。',
     '[{"item":"按标准种植","assignee":"张正宏"}]'),
    ('MTG-TRACE-9003', '样板段验收协调会', '协调样板段石材铺装验收事宜。',
     4, '2026-08-13T10:00:00+08:00', '项目部会议室',
     '["王建国","李景利"]',
     '样板段石材铺装观感合格，安排正式验收。',
     '[{"item":"正式验收","assignee":"李景利"}]'),
]
for m in meetings:
    cur.execute("""INSERT INTO meetings (id, meeting_no, project_id, title, summary, attendees,
        meeting_type, meeting_date, location, host_id, attendee_names,
        conclusion, action_items, status, created_by, created_at, updated_at, is_deleted)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(uuid.uuid4()), m[0], PROJ, m[1], m[2], '[]', m[3], m[4], m[5], WANG, m[6], m[7], m[8],
         1, ZHANG, now, now, False))

conn.commit()

# 验证
cur.execute("SELECT event_no, title, user_id IS NOT NULL, confirmed_by IS NOT NULL FROM events WHERE event_no LIKE 'EVT-TRACE-%' ORDER BY event_no")
print("=== events ===")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT task_no, title, owner_text FROM tasks WHERE task_no LIKE 'TSK-TRACE-%' ORDER BY task_no")
print("=== tasks ===")
for r in cur.fetchall():
    print(r)

cur.close()
conn.close()
print("DONE")
