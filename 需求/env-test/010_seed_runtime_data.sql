-- ============================================================
-- 010_seed_runtime_data.sql —— 运行时/进化/调度种子数据
--   补全 pipeline_execution_logs / scheduler / evolution / routing /
--   RAG / feedback / permission_runtime / attachments / node_files
--
-- Precondition: 002 + 002_patch + 007 + 008 + 009 + 006 must be run first
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 010_seed_runtime_data.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 0. Temp lookup tables
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _su AS
SELECT id, username, level FROM users WHERE is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sp AS
SELECT id, code FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sf AS
SELECT id, file_no, filename, file_category FROM files
WHERE project_id = (SELECT id FROM _sp LIMIT 1) AND is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sm AS
SELECT id, event_id, sender_user_id, conversation_id, content, created_at FROM messages;

CREATE TEMP TABLE IF NOT EXISTS _sconv AS
SELECT id, conversation_id FROM conversations;

-- ============================================================
-- 0.1 Idempotent cleanup
-- ============================================================
DELETE FROM node_accessible_files WHERE node_id LIKE 'EMR-%';
DELETE FROM message_attachments WHERE id IN (
    SELECT ma.id FROM message_attachments ma
    JOIN _sm m ON ma.message_id = m.id
);
DELETE FROM pending_data WHERE pending_no LIKE 'PND-2026%';
DELETE FROM permission_requests WHERE request_no LIKE 'PRQ-2026%';
DELETE FROM user_feedback_signals WHERE signal_type IN ('repeat_request','explicit_correction','positive','abandonment');
DELETE FROM rag_retrieval_logs WHERE query_text LIKE '【模拟】%';
DELETE FROM sop_routing_logs WHERE message_content LIKE '【模拟】%';
DELETE FROM scheduler_job_logs WHERE action_type IN ('create_task_node','morning_report','file_expiry_reminder','create_periodic_node','generate_morning_report','check_file_expiry');
DELETE FROM scheduler_executions WHERE execution_no LIKE 'SEX-2026%';
DELETE FROM scheduler_jobs WHERE job_no LIKE 'JOB-%';
DELETE FROM evolution_patches WHERE patch_no LIKE 'EP-%';
DELETE FROM evolution_rules WHERE rule_no LIKE 'R-%';
DELETE FROM evolution_daily_insights WHERE insight_date LIKE '2026-%';
DELETE FROM pipeline_execution_logs WHERE pipeline_run_id LIKE 'SIM-%';

-- ============================================================
-- 1. Pipeline Execution Logs (10 records)
--    先于 user_feedback_signals / rag_retrieval_logs
--    columns: id, pipeline_run_id, conversation_id, user_id, user_name,
--    user_level, matched_sop_id, match_confidence, is_compound, is_fallback,
--    intent_reasoning, final_status, abort_reason, result_text,
--    tool_calls_json, step_results_json, hook_decisions_json,
--    was_blocked, block_hook_name, started_at, completed_at,
--    elapsed_ms, node1_ms, node2_ms, node3_ms, node4_ms, created_at
-- ============================================================
CREATE TEMP TABLE _spel AS
SELECT uuid_generate_v4()::text AS id,
       'SIM-RUN-' || LPAD(gs::text, 4, '0') AS pipeline_run_id,
       NOW()::text AS created_at
FROM generate_series(1, 10) gs;

-- 10 simulated pipeline runs, varying status/confidence/fallback
-- Use row_number() to get deterministic sequence for each row in _spel
INSERT INTO pipeline_execution_logs (id, pipeline_run_id, conversation_id, user_id,
    user_name, user_level, matched_sop_id, match_confidence, is_compound, is_fallback,
    intent_reasoning, final_status, abort_reason, result_text,
    tool_calls_json, step_results_json, hook_decisions_json,
    was_blocked, block_hook_name, started_at, completed_at,
    elapsed_ms, node1_ms, node2_ms, node3_ms, node4_ms, created_at)
SELECT
    t.id, t.pipeline_run_id,
    (SELECT id FROM _sconv WHERE conversation_id = 'sim_emer_work' LIMIT 1),
    u.id, u.username, u.level,
    CASE (t.rn % 5)
        WHEN 0 THEN 'SOP-001' WHEN 1 THEN 'SOP-002'
        WHEN 2 THEN 'SOP-003' WHEN 3 THEN 'SOP-001'
        WHEN 4 THEN ''
    END,
    CASE (t.rn % 5)
        WHEN 0 THEN 'high' WHEN 1 THEN 'medium'
        WHEN 2 THEN 'high' WHEN 3 THEN 'low'
        WHEN 4 THEN 'none'
    END,
    (t.rn % 3 = 0), (t.rn % 5 = 4),
    CASE (t.rn % 5)
        WHEN 0 THEN '用户明确要求创建事件，意图清晰'
        WHEN 1 THEN '用户请求查询项目进度'
        WHEN 2 THEN '用户请求创建任务'
        WHEN 3 THEN '用户意图模糊，可能是查询或创建'
        WHEN 4 THEN '未匹配到已知SOP，走通用对话'
    END,
    CASE (t.rn % 4)
        WHEN 0 THEN 'completed' WHEN 1 THEN 'completed'
        WHEN 2 THEN 'completed' WHEN 3 THEN 'aborted'
    END,
    CASE WHEN (t.rn % 4 = 3) THEN 'permission_denied' ELSE '' END,
    CASE (t.rn % 4)
        WHEN 0 THEN '事件已创建：EVT-模拟-已确认。'
        WHEN 1 THEN '当前项目进度：主体结构施工中，3层完成65%。'
        WHEN 2 THEN '任务已创建并分配给张工，截止日期已设。'
        WHEN 3 THEN ''
    END,
    CASE (t.rn % 4)
        WHEN 0 THEN '{"tools":["create_event"],"count":1}'
        WHEN 1 THEN '{"tools":["query_project_progress"],"count":1}'
        WHEN 2 THEN '{"tools":["create_task","notify_user"],"count":2}'
        WHEN 3 THEN '{"tools":[],"count":0}'
    END,
    '{"planning":"ok","execution":"ok","guardian":"ok"}',
    '{}',
    false, '',
    ('2026-07-' || LPAD((t.rn + 1)::text, 2, '0') || 'T09:' || LPAD((t.rn * 5 % 60)::text, 2, '0') || ':00')::text,
    ('2026-07-' || LPAD((t.rn + 1)::text, 2, '0') || 'T09:' || LPAD(((t.rn * 5 + t.rn % 3 + 1) % 60)::text, 2, '0') || ':00')::text,
    (800 + t.rn * 150), (200 + t.rn * 30), (300 + t.rn * 40), (200 + t.rn * 35), (100 + t.rn * 20),
    t.created_at
FROM (
    SELECT *, row_number() OVER (ORDER BY pipeline_run_id) AS rn FROM _spel
) t
JOIN _su u ON u.username = CASE (t.rn % 5)
    WHEN 0 THEN '李景利' WHEN 1 THEN '张正宏'
    WHEN 2 THEN '陈建华' WHEN 3 THEN '孙建国'
    WHEN 4 THEN '赵明远'
END;

-- Cleanup and rebuild _spel with actual data
DROP TABLE _spel;
CREATE TEMP TABLE _spel AS
SELECT id, pipeline_run_id, conversation_id, user_id FROM pipeline_execution_logs WHERE pipeline_run_id LIKE 'SIM-%';

-- ============================================================
-- 2. Scheduler Jobs (3 records)
--    columns: id, job_no, name, description, job_type, cron_expression,
--    interval_seconds, deadline_rule, action_type, handler_module,
--    action_params, status, last_executed_at, next_execution_at,
--    creator_id, created_at, updated_at
-- ============================================================
CREATE TEMP TABLE _sjobs AS
SELECT uuid_generate_v4()::text AS id, 'JOB-001' AS job_no,
       '每周进度汇报任务创建' AS name,
       '每周一09:00自动生成进度汇报待办任务' AS description,
       'CRON' AS job_type, '0 9 * * 1' AS cron_expression,
       0 AS interval_seconds, '' AS deadline_rule,
       'create_periodic_node' AS action_type,
       'scheduler.jobs.periodic_node' AS handler_module,
       '{"project_id":"' || (SELECT id FROM _sp LIMIT 1) || '","node_name":"本周进度汇报","owner_dept_id":"项目总","creator_id":"' || (SELECT id FROM _su WHERE username = '王建国' LIMIT 1) || '"}' AS action_params,
       'ACTIVE' AS status,
       '2026-07-14T09:00:00' AS last_executed_at,
       '2026-07-21T09:00:00' AS next_execution_at,
       NOW()::text AS created_at
UNION ALL
SELECT uuid_generate_v4()::text, 'JOB-002',
       '每日晨报',
       '每日08:00自动汇总前一日项目动态并推送晨报',
       'CRON', '0 8 * * *',
       0, '',
       'generate_morning_report',
       'scheduler.jobs.morning_report',
       '{"push_to_group":"项目群"}',
       'ACTIVE',
       '2026-07-20T08:00:00',
       '2026-07-21T08:00:00',
       NOW()::text
UNION ALL
SELECT uuid_generate_v4()::text, 'JOB-003',
       '文件过期提醒',
       '每24小时检查一次临近过期的许可/证照文件并发送提醒',
       'INTERVAL', '',
       86400, '文件有效期<30天时触发',
       'check_file_expiry',
       'scheduler.jobs.file_expiry',
       '{"threshold_days":30,"check_categories":["PROJECT_LICENSE"]}',
       'INACTIVE',
       '2026-07-20T06:00:00',
       '2026-07-21T06:00:00',
       NOW()::text;

INSERT INTO scheduler_jobs (id, job_no, name, description, job_type, cron_expression,
    interval_seconds, deadline_rule, action_type, handler_module, action_params,
    status, last_executed_at, next_execution_at, creator_id, created_at, updated_at)
SELECT
    s.id, s.job_no, s.name, s.description, s.job_type, s.cron_expression,
    s.interval_seconds, s.deadline_rule, s.action_type, s.handler_module,
    s.action_params, s.status, s.last_executed_at, s.next_execution_at,
    u.id, s.created_at, s.created_at
FROM _sjobs s, _su u WHERE u.username = '王建国';

-- ============================================================
-- 3. Scheduler Executions (14 records, ~2 weeks)
--    columns: id, job_id, execution_no, period_key, status,
--    started_at, finished_at, error_message, result_summary, created_at
-- ============================================================
CREATE TEMP TABLE _sjobs_real AS
SELECT id, job_no, action_type, action_params FROM scheduler_jobs WHERE job_no IN ('JOB-001','JOB-002','JOB-003');

INSERT INTO scheduler_executions (id, job_id, execution_no, period_key, status,
    started_at, finished_at, error_message, result_summary, created_at)
SELECT
    uuid_generate_v4()::text,
    j.id,
    'SEX-2026-' || TO_CHAR(CURRENT_DATE, 'MMDD') || '-' || LPAD(s.rn::text, 3, '0'),
    '2026-07-' || LPAD((s.gs + 1)::text, 2, '0'),
    CASE (s.gs % 10)
        WHEN 0 THEN 'FAILED' WHEN 9 THEN 'FAILED'
        ELSE 'SUCCESS'
    END,
    ('2026-07-' || LPAD((s.gs + 1)::text, 2, '0') || 'T' ||
     CASE j.job_no
         WHEN 'JOB-001' THEN '09:00:00'
         WHEN 'JOB-002' THEN '08:00:00'
         WHEN 'JOB-003' THEN '06:00:00'
     END)::text,
    ('2026-07-' || LPAD((s.gs + 1)::text, 2, '0') || 'T' ||
     CASE j.job_no
         WHEN 'JOB-001' THEN '09:00:30'
         WHEN 'JOB-002' THEN '08:00:25'
         WHEN 'JOB-003' THEN '06:00:10'
     END)::text,
    CASE (s.gs % 10) WHEN 0 THEN '数据库连接超时' WHEN 9 THEN 'handler模块加载失败' ELSE '' END,
    CASE (s.gs % 10)
        WHEN 0 THEN '' WHEN 9 THEN ''
        ELSE CASE j.job_no
            WHEN 'JOB-001' THEN '创建任务成功: TSK-自动-2026'
            WHEN 'JOB-002' THEN '晨报已推送给2名接收人'
            WHEN 'JOB-003' THEN '检查18个文件, 0个即将过期'
        END
    END,
    NOW()::text
FROM (
    SELECT
        row_number() OVER (ORDER BY job_no, gs) AS rn,
        gs, job_no
    FROM _sjobs_real
    CROSS JOIN generate_series(1, 14) gs
    WHERE (job_no = 'JOB-001' AND gs IN (1, 8))
       OR (job_no = 'JOB-002' AND gs IN (2,3,4,5,6,7,9,10,11,12))
       OR (job_no = 'JOB-003' AND gs IN (2, 7))
) s
JOIN _sjobs_real j ON j.job_no = s.job_no;

-- ============================================================
-- 4. Scheduler Job Logs (14 records, 1:1 with executions)
--    columns: id, job_id, action_type, params_json, success, summary,
--    elapsed_ms, error_detail, started_at, completed_at, created_at
-- ============================================================
INSERT INTO scheduler_job_logs (id, job_id, action_type, params_json, success, summary,
    elapsed_ms, error_detail, started_at, completed_at, created_at)
SELECT
    uuid_generate_v4()::text,
    e.job_id,
    j.action_type,
    j.action_params,
    (e.status = 'SUCCESS'),
    e.result_summary,
    CASE j.job_no
        WHEN 'JOB-001' THEN 520 WHEN 'JOB-002' THEN 380 WHEN 'JOB-003' THEN 210
    END,
    e.error_message,
    e.started_at,
    e.finished_at,
    NOW()::text
FROM scheduler_executions e
JOIN _sjobs_real j ON e.job_id = j.id;

-- ============================================================
-- 5. Evolution Daily Insights (5 records, 2026-02 ~ 2026-07, monthly)
--    columns: id, insight_date, analysis_days, total_messages, total_pipeline_runs,
--    sop_hit_rate, fallback_rate, top_sop_ids, feedback_summary, anomaly_flags,
--    insight_text, metrics_json, health_score, created_at
-- ============================================================
INSERT INTO evolution_daily_insights (id, insight_date, analysis_days, total_messages,
    total_pipeline_runs, sop_hit_rate, fallback_rate, top_sop_ids, feedback_summary,
    anomaly_flags, insight_text, metrics_json, health_score, created_at)
VALUES
(uuid_generate_v4()::text, '2026-02-01', 30, 156, 42,
 0.72, 0.18, '[{"sop_id":"SOP-001","count":18},{"sop_id":"SOP-003","count":9}]',
 '用户对事件创建功能满意度高，查询类请求偶有歧义',
 '["low_rag_hit"]',
 '本月SOP命中率72%，较上月提升5个百分点。主要命中SOP为事件创建(SOP-001)和任务管理(SOP-003)。RAG检索命中率偏低，建议补充项目制度类知识文档。',
 '{"avg_latency_ms":1850,"avg_tokens":420,"peak_concurrent":3}',
 78, NOW()::text),
(uuid_generate_v4()::text, '2026-03-01', 28, 142, 38,
 0.76, 0.14, '[{"sop_id":"SOP-001","count":16},{"sop_id":"SOP-002","count":10}]',
 '进度查询准确率提升，用户开始主动使用@Emily进行日常汇报',
 '[]',
 '本月运行平稳，SOP命中率76%。进度查询(SOP-002)使用量增加，说明用户对项目透明度需求增强。',
 '{"avg_latency_ms":1720,"avg_tokens":395,"peak_concurrent":4}',
 82, NOW()::text),
(uuid_generate_v4()::text, '2026-04-01', 31, 168, 45,
 0.80, 0.10, '[{"sop_id":"SOP-001","count":20},{"sop_id":"SOP-003","count":12}]',
 '任务分配和进度跟踪功能被高频使用，主体结构施工阶段交互密集',
 '[]',
 '四月进入主体施工高峰，交互量上升。SOP命中率突破80%。建议优化会议创建(SOP-004)的意图识别逻辑。',
 '{"avg_latency_ms":1680,"avg_tokens":450,"peak_concurrent":5}',
 85, NOW()::text),
(uuid_generate_v4()::text, '2026-05-01', 30, 135, 36,
 0.78, 0.15, '[{"sop_id":"SOP-001","count":17},{"sop_id":"SOP-002","count":8}]',
 '偶有用户反馈查询结果不够精确，需进一步细化SOP路由规则',
 '["high_fallback"]',
 '五月fallback率回升至15%，主要因为景观相关查询未有效匹配。建议注册景观类SOP。',
 '{"avg_latency_ms":1910,"avg_tokens":480,"peak_concurrent":4}',
 76, NOW()::text),
(uuid_generate_v4()::text, '2026-06-01', 30, 148, 40,
 0.83, 0.08, '[{"sop_id":"SOP-001","count":19},{"sop_id":"SOP-004","count":11}]',
 '机电安装阶段，文件查询和会议管理SOP用量显著增长',
 '[]',
 '六月SOP命中率创新高83%。文件查询SOP(SOP-004)用量增长明显，RAG检索质量同步改善。系统整体健康度良好。',
 '{"avg_latency_ms":1650,"avg_tokens":410,"peak_concurrent":6}',
 88, NOW()::text),
(uuid_generate_v4()::text, '2026-07-01', 20, 98, 28,
 0.85, 0.06, '[{"sop_id":"SOP-001","count":14},{"sop_id":"SOP-004","count":8}]',
 '景观进场阶段，交底和验收类SOP使用频繁，用户整体满意度高',
 '[]',
 '七月上半月运行状态优良，SOP命中率85%，fallback仅6%。系统自进化规则R-001已确认，建议尽快应用。',
 '{"avg_latency_ms":1580,"avg_tokens":430,"peak_concurrent":7}',
 90, NOW()::text);

-- ============================================================
-- 6. Evolution Rules (2 records)
--    columns: id, rule_no, title, description, evidence_insight_ids,
--    category, confidence, status, superseded_by, suggested_action,
--    impact_estimate, created_at, confirmed_at
-- ============================================================
INSERT INTO evolution_rules (id, rule_no, title, description, evidence_insight_ids,
    category, confidence, status, superseded_by, suggested_action,
    impact_estimate, created_at, confirmed_at)
SELECT
    uuid_generate_v4()::text, 'R-001',
    '主体结构按楼栋编号拆分任务',
    '当用户创建主体结构施工任务时，自动识别是否涉及多栋楼，若涉及则按楼栋拆分为独立子任务以提高跟踪精度。',
    '["' || (SELECT id FROM evolution_daily_insights WHERE insight_date = '2026-04-01') || '"]',
    'task_optimization', 0.85, 'CONFIRMED', '',
    '修改SOP-003的Prompt模板，增加楼栋编号识别与拆分逻辑。',
    '预计提升任务跟踪粒度，减少30%的"任务过大"反馈。',
    NOW()::text, NOW()::text
FROM evolution_daily_insights WHERE insight_date = '2026-04-01' LIMIT 1;

INSERT INTO evolution_rules (id, rule_no, title, description, evidence_insight_ids,
    category, confidence, status, superseded_by, suggested_action,
    impact_estimate, created_at, confirmed_at)
SELECT
    uuid_generate_v4()::text, 'R-002',
    '景观进场前必须完成场地验收',
    '根据五月fallback分析，景观类查询占比上升。建议在SOP流程中增加前置检查：景观施工SOP激活前，验证场地平整节点是否已完成。',
    '["' || (SELECT id FROM evolution_daily_insights WHERE insight_date = '2026-05-01') || '"]',
    'process_guard', 0.72, 'DRAFT', '',
    '注册景观施工SOP，并在其Guardian节点增加场地平整节点状态检查。',
    '防止景观施工在场地未整备的情况下启动，避免返工。',
    NOW()::text, ''
FROM evolution_daily_insights WHERE insight_date = '2026-05-01' LIMIT 1;

-- ============================================================
-- 7. Evolution Patches (1 record)
--    columns: id, patch_no, rule_no, target_type, target_path, patch_content,
--    patch_type, search_anchor, risk_level, risk_reasoning, validation_criteria,
--    expected_effect, status, applied_at, validated_at, validation_result,
--    rollback_snapshot, created_at
-- ============================================================
INSERT INTO evolution_patches (id, patch_no, rule_no, target_type, target_path,
    patch_content, patch_type, search_anchor, risk_level, risk_reasoning,
    validation_criteria, expected_effect, status, applied_at, validated_at,
    validation_result, rollback_snapshot, created_at)
SELECT
    uuid_generate_v4()::text, 'EP-001', 'R-001',
    'prompt', 'sops/SOP-003/prompt_template.md',
    '# 原模板
你是一个任务管理助手...

# 新增指令
若任务涉及多栋楼（如1#-5#楼），请自动拆分为每栋楼的独立子任务，格式为"[楼栋号] 任务描述"。',
    'append', '# 任务描述格式要求',
    'low', '仅修改Prompt模板追加部分，不影响核心逻辑，变更范围可控。',
    '拆分子任务后，用户确认任务时显示"已按楼栋拆分"提示即为通过。',
    '任务按楼栋拆分后，进度跟踪更精确，预计单任务反馈减少30%。',
    'APPLIED', NOW()::text, NOW()::text,
    '{"status":"pass","metrics":{"split_accuracy":0.92,"user_acceptance":0.88}}',
    '# 原模板
你是一个任务管理助手，根据用户输入创建任务并分配给负责人。
# 任务描述格式要求
任务描述应包含：工作内容、执行标准、验收要求。',
    NOW()::text
FROM evolution_rules WHERE rule_no = 'R-001' LIMIT 1;

-- ============================================================
-- 8. SOP Routing Logs (30 records, ~1 month)
--    columns: id, log_date, log_time, user_id, conversation_id, message_id,
--    message_content, matched_sop_id, is_hit, match_confidence,
--    fallback_action, llm_reasoning, execution_result, created_at
-- ============================================================
INSERT INTO sop_routing_logs (id, log_date, log_time, user_id, conversation_id,
    message_id, message_content, matched_sop_id, is_hit, match_confidence,
    fallback_action, llm_reasoning, execution_result, created_at)
SELECT
    uuid_generate_v4()::text,
    '2026-07-' || LPAD((gs % 20 + 1)::text, 2, '0'),
    TO_CHAR((8 + gs % 10)::int, 'FM00') || ':' || TO_CHAR((gs * 7 % 60)::int, 'FM00') || ':00',
    u.id,
    (SELECT id FROM _sconv WHERE conversation_id = 'sim_emer_work' LIMIT 1),
    CASE WHEN gs >= 5 AND gs <= 9 THEN m.id ELSE NULL END,
    CASE (gs % 6)
        WHEN 0 THEN '【模拟】@Emily 帮我把今天的混凝土浇筑记录创建成事件'
        WHEN 1 THEN '【模拟】@Emily 查一下翠湖庭院项目目前的施工进度'
        WHEN 2 THEN '【模拟】@Emily 帮我创建一个任务：外墙真石漆施工，负责人刘大勇，下周五前完成'
        WHEN 3 THEN '【模拟】@Emily 查一下地基验收报告在哪个文件里'
        WHEN 4 THEN '【模拟】景观绿化什么时候可以进场？场地平整完了吗？'
        WHEN 5 THEN '【模拟】@Emily 下周三开个景观交底会，你帮我安排一下'
    END,
    CASE (gs % 6)
        WHEN 0 THEN 'SOP-001' WHEN 1 THEN 'SOP-002'
        WHEN 2 THEN 'SOP-003' WHEN 3 THEN 'SOP-004'
        WHEN 4 THEN '' WHEN 5 THEN 'SOP-005'
    END,
    (gs % 6 != 4),
    CASE (gs % 6)
        WHEN 0 THEN 'high' WHEN 1 THEN 'high'
        WHEN 2 THEN 'high' WHEN 3 THEN 'medium'
        WHEN 4 THEN 'none' WHEN 5 THEN 'medium'
    END,
    CASE WHEN (gs % 6 = 4) THEN 'general_chat' ELSE '' END,
    CASE (gs % 6)
        WHEN 0 THEN '用户明确要求创建事件，关键词"创建事件"精准命中SOP-001'
        WHEN 1 THEN '用户要求查询进度，"施工进度"命中SOP-002查询类关键词'
        WHEN 2 THEN '用户明确要求创建任务，匹配SOP-003'
        WHEN 3 THEN '用户要求查找文件"验收报告"，匹配SOP-004文件查询'
        WHEN 4 THEN '用户询问景观进场条件，未匹配到已注册SOP，fallback到通用对话'
        WHEN 5 THEN '用户要求安排会议，"开个会"匹配SOP-005会议创建'
    END,
    CASE (gs % 6)
        WHEN 4 THEN 'fallback' ELSE 'completed'
    END,
    NOW()::text
FROM generate_series(1, 30) gs
JOIN _su u ON u.username = CASE (gs % 5)
    WHEN 0 THEN '李景利' WHEN 1 THEN '张正宏'
    WHEN 2 THEN '陈建华' WHEN 3 THEN '孙建国'
    WHEN 4 THEN '赵明远'
END
LEFT JOIN _sm m ON m.id = (SELECT id FROM _sm ORDER BY RANDOM() LIMIT 1);

-- ============================================================
-- 9. RAG Retrieval Logs (20 records)
--    columns: id, pipeline_run_id, conversation_id, user_id, query_text,
--    provider, hit_count, top_score, avg_score, results_summary,
--    was_used_by_llm, latency_ms, error_summary, created_at
-- ============================================================
INSERT INTO rag_retrieval_logs (id, pipeline_run_id, conversation_id, user_id,
    query_text, provider, hit_count, top_score, avg_score, results_summary,
    was_used_by_llm, latency_ms, error_summary, created_at)
SELECT
    uuid_generate_v4()::text,
    (SELECT pipeline_run_id FROM _spel ORDER BY RANDOM() LIMIT 1),
    (SELECT id FROM _sconv WHERE conversation_id = 'sim_emer_work' LIMIT 1),
    u.id,
    CASE (gs % 5)
        WHEN 0 THEN '【模拟】地基验收标准和规范要求'
        WHEN 1 THEN '【模拟】混凝土C40配合比和养护要求'
        WHEN 2 THEN '【模拟】外墙真石漆施工工艺'
        WHEN 3 THEN '【模拟】项目质量管理制度内容'
        WHEN 4 THEN '【模拟】翠湖庭院合同主要条款'
    END,
    CASE (gs % 3) WHEN 0 THEN 'maxkb' WHEN 1 THEN 'maxkb' WHEN 2 THEN 'local_fallback' END,
    CASE (gs % 3) WHEN 0 THEN (3 + gs % 3) WHEN 1 THEN (1 + gs % 3) WHEN 2 THEN 0 END,
    CASE (gs % 3) WHEN 0 THEN (0.75 + gs * 0.01) WHEN 1 THEN (0.60 + gs * 0.01) WHEN 2 THEN 0.00 END,
    CASE (gs % 3) WHEN 0 THEN (0.65 + gs * 0.01) WHEN 1 THEN (0.50 + gs * 0.01) WHEN 2 THEN 0.00 END,
    CASE (gs % 3)
        WHEN 0 THEN '检索到3-5条相关文档片段，相关性较好'
        WHEN 1 THEN '检索到1-3条相关文档片段，部分相关'
        WHEN 2 THEN '本地检索无结果，可能缺少相关知识文档'
    END,
    (gs % 3 != 2),
    (120 + gs * 20),
    CASE (gs % 3) WHEN 2 THEN 'local_fallback: 索引为空或查询无匹配' ELSE '' END,
    NOW()::text
FROM generate_series(1, 20) gs
JOIN _su u ON u.username = CASE (gs % 5)
    WHEN 0 THEN '李景利' WHEN 1 THEN '张正宏'
    WHEN 2 THEN '陈建华' WHEN 3 THEN '赵明远'
    WHEN 4 THEN '孙建国'
END;

-- ============================================================
-- 10. User Feedback Signals (5 records)
--     columns: id, pipeline_run_id, conversation_id, user_id, signal_type,
--     signal_strength, trigger_message, context_summary, created_at
-- ============================================================
INSERT INTO user_feedback_signals (id, pipeline_run_id, conversation_id, user_id,
    signal_type, signal_strength, trigger_message, context_summary, created_at)
SELECT
    uuid_generate_v4()::text,
    pel.pipeline_run_id,
    pel.conversation_id,
    pel.user_id,
    CASE gs
        WHEN 1 THEN 'repeat_request' WHEN 2 THEN 'explicit_correction'
        WHEN 3 THEN 'positive' WHEN 4 THEN 'abandonment'
        WHEN 5 THEN 'positive'
    END,
    CASE gs
        WHEN 1 THEN 0.75 WHEN 2 THEN 0.60
        WHEN 3 THEN 0.90 WHEN 4 THEN 0.40
        WHEN 5 THEN 0.85
    END,
    CASE gs
        WHEN 1 THEN '@Emily 我说的是查进度，不是创建事件！'
        WHEN 2 THEN '@Emily 不对，我说的是3#楼，不是1#楼'
        WHEN 3 THEN '@Emily 很好，就是这样，谢谢！'
        WHEN 4 THEN '(用户发送第一条消息后无回应，2分钟内未继续对话)'
        WHEN 5 THEN '@Emily 这个总结写得不错，很全面'
    END,
    CASE gs
        WHEN 1 THEN '用户首次请求被误识别为SOP-001创建事件，实际意图为查询进度'
        WHEN 2 THEN 'LLM回复中的楼栋编号错误，用户明确指出应为3#楼'
        WHEN 3 THEN '用户对事件创建结果表示满意'
        WHEN 4 THEN '用户在Pipeline执行完成前放弃等待'
        WHEN 5 THEN '用户对会议纪要总结表示认可'
    END,
    NOW()::text
FROM generate_series(1, 5) gs
JOIN _spel pel ON pel.pipeline_run_id = ('SIM-RUN-' || LPAD((gs + 2)::text, 4, '0'))
WHERE EXISTS (SELECT 1 FROM _spel WHERE pipeline_run_id = ('SIM-RUN-' || LPAD((gs + 2)::text, 4, '0')));

-- ============================================================
-- 11. Permission Requests (3 records)
--     columns: id, request_no, requester_id, perm_code, request_type, reason,
--     status, current_approver_id, approval_level, priority, expire_at,
--     approved_at, approver_id, approval_remark, source_data, agent_issue_id,
--     created_at, updated_at, is_deleted
-- ============================================================
INSERT INTO permission_requests (id, request_no, requester_id, perm_code, request_type,
    reason, status, current_approver_id, approval_level, priority, expire_at,
    approved_at, approver_id, approval_remark, source_data, agent_issue_id,
    created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, 'PRQ-20260701-001',
    u1.id, 'project.write', 'TEMP_GRANT',
    '景观施工员黄志强需要临时写入权限以记录绿化施工进度',
    'APPROVED',
    u2.id, 1, 'NORMAL',
    '2026-08-01T00:00:00',
    '2026-07-02T10:00:00', u2.id,
    '同意，授予至8月1日。到期后系统自动回收。',
    '{"module":"project","action":"write","scope":"EMERALD-01"}',
    NULL,
    '2026-07-01T09:00:00', '2026-07-02T10:00:00', false
FROM _su u1, _su u2
WHERE u1.username = '黄志强' AND u2.username = '李景利'
UNION ALL
SELECT
    uuid_generate_v4()::text, 'PRQ-20260705-001',
    u1.id, 'perm.manage', 'LEVEL_UP',
    'IT管理员罗永强申请将权限级别从L5提升至L6，以便管理全系统权限配置',
    'PENDING',
    u2.id, 2, 'HIGH',
    NULL,
    NULL, NULL, '',
    '{"from_level":5,"to_level":6}',
    NULL,
    '2026-07-05T14:00:00', '2026-07-05T14:00:00', false
FROM _su u1, _su u2
WHERE u1.username = '罗永强' AND u2.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, 'PRQ-20260708-001',
    u1.id, 'sop.manage', 'TEMP_GRANT',
    '监理陈工需要临时SOP管理权限以注册新的质量验收SOP模板',
    'REJECTED',
    u2.id, 1, 'NORMAL',
    NULL,
    '2026-07-09T16:00:00', u2.id,
    'SOP模板注册应由建设单位工程部统一管理，监理方可在现有模板框架内使用。如有需要请提交具体验收标准，由工程部统一录入。',
    '{"sop_type":"quality_inspection"}',
    NULL,
    '2026-07-08T11:00:00', '2026-07-09T16:00:00', false
FROM _su u1, _su u2
WHERE u1.username = '陈建华' AND u2.username = '李景利';

-- ============================================================
-- 12. Permission Audit Log (10 records)
--     columns: log_id(BIGSERIAL), event_time, grantor_id, grantee_id,
--     perm_code, grant_type, duration, session_id, operation_type,
--     client_ip, user_agent, remark
-- ============================================================
INSERT INTO permission_audit_log (event_time, grantor_id, grantee_id, perm_code,
    grant_type, duration, session_id, operation_type, client_ip, user_agent, remark)
SELECT
    ('2026-07-' || LPAD((gs % 10 + 1)::text, 2, '0') || 'T' ||
     TO_CHAR((8 + gs % 8)::int, '00') || ':00:00')::text,
    CASE (gs % 3) WHEN 0 THEN (SELECT id FROM _su WHERE username = '王建国')
                  WHEN 1 THEN (SELECT id FROM _su WHERE username = '李景利')
                  WHEN 2 THEN (SELECT id FROM _su WHERE username = '罗永强') END,
    CASE (gs % 4) WHEN 0 THEN (SELECT id FROM _su WHERE username = '张正宏')
                  WHEN 1 THEN (SELECT id FROM _su WHERE username = '孙建国')
                  WHEN 2 THEN (SELECT id FROM _su WHERE username = '黄志强')
                  WHEN 3 THEN (SELECT id FROM _su WHERE username = '罗永强') END,
    CASE (gs % 5) WHEN 0 THEN 'project.read' WHEN 1 THEN 'project.write'
                  WHEN 2 THEN 'task.read' WHEN 3 THEN 'task.write'
                  WHEN 4 THEN 'perm.manage' END,
    CASE (gs % 4) WHEN 0 THEN 'AUTO' WHEN 1 THEN 'AUTO'
                  WHEN 2 THEN 'TEMP' WHEN 3 THEN 'PERMANENT' END,
    CASE (gs % 4) WHEN 2 THEN 86400 ELSE NULL END,
    'SESSION-' || LPAD(gs::text, 6, '0'),
    CASE (gs % 4) WHEN 0 THEN 'ACCESS_CHECK' WHEN 1 THEN 'ACCESS_CHECK'
                  WHEN 2 THEN 'GRANT' WHEN 3 THEN 'GRANT'
           WHEN 4 THEN 'REVOKE' WHEN 5 THEN 'ACCESS_DENIED'
           WHEN 6 THEN 'ACCESS_CHECK' WHEN 7 THEN 'ACCESS_CHECK'
           WHEN 8 THEN 'GRANT' WHEN 9 THEN 'ACCESS_DENIED' END,
    '172.18.0.1',
    'Emily-Core/1.0 (internal)',
    CASE (gs % 5) WHEN 0 THEN '用户查看项目信息' WHEN 1 THEN '用户更新任务状态'
                  WHEN 2 THEN '临时权限自动分配' WHEN 3 THEN '权限手动授予'
                  WHEN 4 THEN '管理员权限审核' END
FROM generate_series(1, 10) gs;

-- ============================================================
-- 13. Pending Data (1 record)
--     columns: id, pending_no, user_id, data_type, data_content,
--     exception_reason, target_node_id, approver_id, status, expire_time,
--     request_id, created_at, updated_at, is_deleted
-- ============================================================
INSERT INTO pending_data (id, pending_no, user_id, data_type, data_content,
    exception_reason, target_node_id, approver_id, status, expire_time,
    request_id, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, 'PND-20260710-001',
    u1.id, 'event_create',
    '{"title":"景观进场道路硬化完成","event_type":"施工记录","category":"工程施工","description":"翠湖庭院小区内部道路路基硬化完成，景观大苗可进场。"}',
    '用户景观施工员黄志强(L2)越权尝试在EMR-SG-01-05节点下创建事件，该节点要求L3+权限',
    'EMR-SG-01-05',
    u2.id, 'PENDING',
    '2026-07-17T00:00:00',
    NULL,
    '2026-07-10T16:00:00', '2026-07-10T16:00:00', false
FROM _su u1, _su u2
WHERE u1.username = '黄志强' AND u2.username = '李景利';

-- ============================================================
-- 14. Message Attachments (6 records)
--     columns: id, message_id, file_id, attachment_type, file_url,
--     local_path, file_size, mime_type, thumbnail_url, created_at
-- ============================================================
INSERT INTO message_attachments (id, message_id, file_id, attachment_type, file_url,
    local_path, file_size, mime_type, thumbnail_url, created_at)
SELECT
    uuid_generate_v4()::text,
    m.id,
    f.id,
    CASE (gs % 2) WHEN 0 THEN 1 WHEN 1 THEN 2 END,
    CASE (gs % 2) WHEN 0 THEN 'https://example.com/images/site_photo_' || gs || '.jpg'
                  WHEN 1 THEN 'https://example.com/files/doc_' || gs || '.pdf' END,
    CASE (gs % 2) WHEN 0 THEN 'mock/EMERALD-01/PROCESS_DOC/site_photo_' || gs || '.jpg'
                  WHEN 1 THEN 'mock/EMERALD-01/PROCESS_DOC/doc_' || gs || '.pdf' END,
    CASE (gs % 2) WHEN 0 THEN 204800 WHEN 1 THEN 102400 END,
    CASE (gs % 2) WHEN 0 THEN 'image/jpeg' WHEN 1 THEN 'application/pdf' END,
    '',
    NOW()::text
FROM generate_series(1, 6) gs
JOIN LATERAL (SELECT id FROM _sm ORDER BY id OFFSET gs - 1 LIMIT 1) m ON true
JOIN LATERAL (SELECT id FROM _sf ORDER BY id OFFSET gs LIMIT 1) f ON true;

-- ============================================================
-- 15. Node Accessible Files (12 records)
--     columns: id, node_id, file_id, added_by, added_at
-- ============================================================
-- Bind delivery files to their corresponding nodes
INSERT INTO node_accessible_files (id, node_id, file_id, added_by, added_at)
SELECT
    uuid_generate_v4()::text,
    node_id,
    f.id,
    (SELECT id FROM _su WHERE username = '李景利' LIMIT 1),
    NOW()::text
FROM (VALUES
    ('EMR-SG-01-01', '地基与基础分部验收报告.pdf'),
    ('EMR-SG-01-02', '主体结构分部验收报告.pdf'),
    ('EMR-SG-01-03', '机电安装分部验收报告.pdf'),
    ('EMR-SG-01-04-01', '场地平整压实验收记录.pdf'),
    ('EMR-GH-01-01', '方案设计文本.pdf'),
    ('EMR-GH-01-01', '施工图设计文件.pdf'),
    ('EMR-SG-01', '建筑工程施工许可证.pdf'),
    ('EMR-SG-01', '建设工程施工总承包合同.pdf'),
    ('EMR-SG-01', '项目质量管理制度.pdf'),
    ('EMR-SG-01', '图纸会审记录.pdf'),
    ('EMR-SG-01-02', '设计变更通知单（结构）.pdf'),
    ('EMR-SG-01-05', '绿化景观施工图设计文件.pdf')
) AS v(node_id, filename)
JOIN _sf f ON f.filename = v.filename
WHERE NOT EXISTS (
    SELECT 1 FROM node_accessible_files naf
    WHERE naf.node_id = v.node_id AND naf.file_id = f.id
);

-- ============================================================
-- 16. Verify
-- ============================================================
SELECT '--- Pipeline Executions ---' AS section;
SELECT COUNT(*) AS total FROM pipeline_execution_logs WHERE pipeline_run_id LIKE 'SIM-%';

SELECT '--- Scheduler ---' AS section;
SELECT 'jobs(' || COUNT(*)::text || ')' FROM scheduler_jobs
UNION ALL SELECT 'executions(' || COUNT(*)::text || ')' FROM scheduler_executions
UNION ALL SELECT 'logs(' || COUNT(*)::text || ')' FROM scheduler_job_logs;

SELECT '--- Evolution ---' AS section;
SELECT 'insights(' || COUNT(*)::text || ')' FROM evolution_daily_insights WHERE insight_date LIKE '2026-%'
UNION ALL SELECT 'rules(' || COUNT(*)::text || ')' FROM evolution_rules WHERE rule_no LIKE 'R-%'
UNION ALL SELECT 'patches(' || COUNT(*)::text || ')' FROM evolution_patches WHERE patch_no LIKE 'EP-%';

SELECT '--- Routing & RAG ---' AS section;
SELECT 'sop_routing(' || COUNT(*)::text || ')' FROM sop_routing_logs WHERE message_content LIKE '【模拟】%'
UNION ALL SELECT 'rag_retrieval(' || COUNT(*)::text || ')' FROM rag_retrieval_logs WHERE query_text LIKE '【模拟】%';

SELECT '--- Feedback & Permission ---' AS section;
SELECT 'feedback(' || COUNT(*)::text || ')' FROM user_feedback_signals WHERE context_summary LIKE '%'
UNION ALL SELECT 'perm_requests(' || COUNT(*)::text || ')' FROM permission_requests WHERE request_no LIKE 'PRQ-2026%'
UNION ALL SELECT 'audit_logs(' || COUNT(*)::text || ')' FROM permission_audit_log
UNION ALL SELECT 'pending_data(' || COUNT(*)::text || ')' FROM pending_data WHERE pending_no LIKE 'PND-2026%';

SELECT '--- Attachments & Node Files ---' AS section;
SELECT 'message_attachments(' || COUNT(*)::text || ')' FROM message_attachments
UNION ALL SELECT 'node_accessible_files(' || COUNT(*)::text || ')' FROM node_accessible_files WHERE node_id LIKE 'EMR-%';

-- Cleanup
DROP TABLE IF EXISTS _spel;
DROP TABLE IF EXISTS _sjobs_real;
DROP TABLE IF EXISTS _sjobs;

COMMIT;
