-- ============================================================
-- 场景_布置_大区景观工作包.sql
-- 用途：为「L3 分解工作包 → L2 完工上报匹配」场景做临时数据布置
-- 配套：场景_测试用例.md、场景_回滚_大区景观工作包.sql
-- 参考：Issues/测试用例/L2完工记录上报测试（同结构、同口径）
-- 执行（PowerShell 下必须 docker cp 后 -f 执行，直接管道会损坏中文编码）：
--   docker cp "Issues/测试用例/L3工作包挂载与L2完工匹配测试/场景_布置_大区景观工作包.sql" emily-postgres:/tmp/lg_setup.sql
--   docker exec emily-postgres psql -U emily -d emily -f /tmp/lg_setup.sql
--   docker exec emily-postgres rm -f /tmp/lg_setup.sql
-- 幂等：重复执行不产生重复节点/成果/参与关系；
--       若 L3 已用对话成功建出工作包（编号可能不同），本脚本不会覆盖，见文末「可选清理」。
-- ============================================================
--
-- 【本布置解决什么】
--   被测能力分两段：
--     上半场  L3（张正宏）在大区景观工程下"分解任务/挂载工作包"——检验挂载录入能力可达性
--     下半场  L2（黄志强）上报「铺装面层完工 100 平方米」——检验能否准确匹配到该工作包
--   上半场是能力验证；若走不通，用本脚本兜底建好 3 个工作包，保证下半场仍可测。
--
-- 【布置出的结构】（深度上限 3，本布置只用到 2 层，留有 1 层余量）
--   EMR-LG-01 大区景观工程 (MILESTONE · 总包 · 责任人 张正宏)
--     ├─ EMR-LG-01-01 铺装面层        (WORK_PACKAGE · 责任人 张正宏) 成果：铺装面层 500 平方米
--     ├─ EMR-LG-01-02 车行路基础垫层  (WORK_PACKAGE · 责任人 张正宏) 成果：车行路基础垫层 300 平方米
--     └─ EMR-LG-01-03 人行路基础垫层  (WORK_PACKAGE · 责任人 张正宏) 成果：人行路基础垫层 200 平方米
--
-- 【可见性口径】参与单位/参与人沿用同专业节点 EMR-SG-01-05 的登记（含总包 中天建设集团），
--   保证张正宏（L3）与黄志强（L2）都能在 query_my_nodes 中看到这些节点。
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

-- ============================================================
-- 0. 上下文解析（项目 / 关键人员 / 总包单位）
-- ============================================================
CREATE TEMP TABLE _lg_ctx ON COMMIT DROP AS
SELECT
    (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false LIMIT 1) AS project_id,
    (SELECT id FROM users WHERE username = '张正宏' AND is_deleted = false LIMIT 1)   AS u_l3,      -- L3 参建管理（总包项目经理）
    (SELECT id FROM users WHERE username = '黄志强' AND is_deleted = false LIMIT 1)   AS u_l2,      -- L2 参建执行（景观施工员）
    (SELECT id FROM users WHERE username = '王建国' AND is_deleted = false LIMIT 1)   AS u_creator, -- L6 创建人
    (SELECT id FROM company_info WHERE company_name = '中天建设集团' LIMIT 1)          AS c_zongbao; -- 总包

DO $$
DECLARE c _lg_ctx;
BEGIN
    SELECT * INTO c FROM _lg_ctx;
    IF c.project_id IS NULL THEN RAISE EXCEPTION '布置失败：未找到项目 EMERALD-01'; END IF;
    IF c.u_l3 IS NULL       THEN RAISE EXCEPTION '布置失败：未找到用户 张正宏（L3）'; END IF;
    IF c.u_l2 IS NULL       THEN RAISE EXCEPTION '布置失败：未找到用户 黄志强（L2）'; END IF;
    IF c.c_zongbao IS NULL  THEN RAISE EXCEPTION '布置失败：未找到单位 中天建设集团'; END IF;
    IF NOT EXISTS (SELECT 1 FROM project_nodes WHERE node_id = 'EMR-SG-01-05') THEN
        RAISE EXCEPTION '布置失败：参照节点 EMR-SG-01-05 不存在，请先跑 .claude/tool/env-test 的 008 节点播种';
    END IF;
END $$;

-- ============================================================
-- 1. 新建「大区景观工程」里程碑节点（顶层，与施工总控同级）
--    注：L3 建不出里程碑节点（建节点是 L5+ 能力），故由布置提供；
--        "在其下分解工作包"才是 L3 被测的那一步
-- ============================================================
INSERT INTO project_nodes (
    id, project_id, node_id, node_name, owner_dept_id, related_company_id, deadline,
    remark, creator_id, created_at, responsible_user_id, node_type, visibility_mode,
    status, progress, parent_node_id, child_weight, updated_at, is_discarded
)
SELECT
    uuid_generate_v4()::text, c.project_id, 'EMR-LG-01', '大区景观工程',
    '', c.c_zongbao, '2026-11-30',
    '[场景布置] 大区级景观里程碑，用于 L3 在其下分解工作包',
    c.u_creator, NOW()::text, c.u_l3, 'MILESTONE', 'specific',
    'IN_PROGRESS', '0.00', NULL, '1.0000', NOW()::text, false
FROM _lg_ctx c
ON CONFLICT (node_id) DO NOTHING;

-- ============================================================
-- 2. 新建 3 个工作包（挂在 大区景观工程 之下）
--    若 L3 已用对话成功建出，本节按 node_id 幂等跳过
-- ============================================================
INSERT INTO project_nodes (
    id, project_id, node_id, node_name, owner_dept_id, related_company_id, deadline,
    remark, creator_id, created_at, responsible_user_id, node_type, visibility_mode,
    status, progress, parent_node_id, child_weight, updated_at, is_discarded
)
SELECT
    uuid_generate_v4()::text, c.project_id, v.node_id, v.node_name,
    '', c.c_zongbao, v.deadline, v.remark, c.u_creator, NOW()::text,
    c.u_l3, 'WORK_PACKAGE', 'specific', 'IN_PROGRESS', '0.00',
    'EMR-LG-01', '1.0000', NOW()::text, false
FROM _lg_ctx c,
(VALUES
    ('EMR-LG-01-01', '铺装面层',       '2026-10-31', '[场景布置] 由 L3 自 大区景观工程 分解出的工作包（铺装面层 500㎡）'),
    ('EMR-LG-01-02', '车行路基础垫层', '2026-10-20', '[场景布置] 由 L3 自 大区景观工程 分解出的工作包（车行路基础垫层 300㎡）'),
    ('EMR-LG-01-03', '人行路基础垫层', '2026-10-25', '[场景布置] 由 L3 自 大区景观工程 分解出的工作包（人行路基础垫层 200㎡）')
) AS v(node_id, node_name, deadline, remark)
ON CONFLICT (node_id) DO NOTHING;

-- ============================================================
-- 3. 工作包成果（带量纲与目标量）—— L2 上报匹配的唯一依据
--    说明：create_task_node 工具没有成果参数，L3 用对话建的工作包**可能没有成果**，
--          那样下半场的匹配就无从谈起。本节是匹配能力可测的前提。
-- ============================================================
INSERT INTO node_deliverables (
    id, deliverable_id, node_id, deliverable_name, target_amount, current_amount,
    unit, is_required, created_at, submission_status
)
SELECT
    uuid_generate_v4()::text, v.deliverable_id, v.node_id, v.deliverable_name,
    v.target_amount, '0.00', v.unit, true, NOW()::text, 'PENDING'
FROM (VALUES
    ('EMR-LG-01-01-DELV-001', 'EMR-LG-01-01', '铺装面层',       '500', '平方米'),
    ('EMR-LG-01-02-DELV-001', 'EMR-LG-01-02', '车行路基础垫层', '300', '平方米'),
    ('EMR-LG-01-03-DELV-001', 'EMR-LG-01-03', '人行路基础垫层', '200', '平方米')
) AS v(deliverable_id, node_id, deliverable_name, target_amount, unit)
WHERE NOT EXISTS (
    SELECT 1 FROM node_deliverables d
    WHERE d.node_id = v.node_id AND d.deliverable_name = v.deliverable_name
);

-- ============================================================
-- 4. 参与单位 / 参与人登记 —— 沿用同专业节点 EMR-SG-01-05 的登记
--    （保证张正宏、黄志强均可见、且属"企业参与节点"）
-- ============================================================
INSERT INTO node_participant_companies (id, node_id, company_id, added_by, added_at)
SELECT uuid_generate_v4()::text, t.node_id, s.company_id, 'lg_setup', NOW()::text
FROM (VALUES ('EMR-LG-01'), ('EMR-LG-01-01'), ('EMR-LG-01-02'), ('EMR-LG-01-03')) AS t(node_id)
JOIN node_participant_companies s ON s.node_id = 'EMR-SG-01-05'
ON CONFLICT (node_id, company_id) DO NOTHING;

INSERT INTO node_participants (id, node_id, user_id, participant_role, added_by, added_at)
SELECT uuid_generate_v4()::text, t.node_id, s.user_id, s.participant_role, 'lg_setup', NOW()::text
FROM (VALUES ('EMR-LG-01'), ('EMR-LG-01-01'), ('EMR-LG-01-02'), ('EMR-LG-01-03')) AS t(node_id)
JOIN node_participants s ON s.node_id = 'EMR-SG-01-05'
ON CONFLICT (node_id, user_id) DO NOTHING;

-- 兜底：确保两位当事人一定在参与名单内（L2 上报时服务层按"参与单位人员"校验）
INSERT INTO node_participants (id, node_id, user_id, participant_role, added_by, added_at)
SELECT uuid_generate_v4()::text, t.node_id, c.u_l2, 'participant', 'lg_setup', NOW()::text
FROM _lg_ctx c,
     (VALUES ('EMR-LG-01'), ('EMR-LG-01-01'), ('EMR-LG-01-02'), ('EMR-LG-01-03')) AS t(node_id)
ON CONFLICT (node_id, user_id) DO NOTHING;

-- ============================================================
-- 5. 校验输出
-- ============================================================
\echo '--- ① 大区景观工程及其工作包 ---'
SELECT p.node_id, p.node_name, p.node_type, p.status, p.parent_node_id,
       (SELECT username FROM users u WHERE u.id = p.responsible_user_id) AS 责任人
FROM project_nodes p
WHERE p.node_id LIKE 'EMR-LG-01%'
ORDER BY p.node_id;

\echo '--- ② 工作包成果（量纲与目标量）---'
SELECT node_id, deliverable_id, deliverable_name,
       target_amount || unit AS 目标量, current_amount AS 当前量, submission_status
FROM node_deliverables
WHERE node_id LIKE 'EMR-LG-01%'
ORDER BY node_id, deliverable_id;

\echo '--- ③ 黄志强（L2）视角：query_my_nodes 数据源 ---'
SELECT p.node_id, p.node_name, p.node_type, p.status,
       CASE WHEN p.responsible_user_id = c.u_l2 THEN 'responsible' ELSE 'participant' END AS role,
       COALESCE(string_agg(d.deliverable_name || ' ' || d.target_amount || d.unit, ' / '), '（无成果）') AS 成果
FROM project_nodes p
CROSS JOIN _lg_ctx c
LEFT JOIN node_deliverables d ON d.node_id = p.node_id
WHERE p.is_discarded = false AND p.node_id LIKE 'EMR-LG-01%'
  AND (p.responsible_user_id = c.u_l2
       OR EXISTS (SELECT 1 FROM node_participants np WHERE np.node_id = p.node_id AND np.user_id = c.u_l2))
GROUP BY p.node_id, p.node_name, p.node_type, p.status, p.responsible_user_id, c.u_l2
ORDER BY p.node_id;

\echo '--- ④ 布置生效确认（期望：节点 4、成果 3、参与单位 12）---'
SELECT
    (SELECT count(*) FROM project_nodes WHERE node_id LIKE 'EMR-LG-01%') AS 节点数,
    (SELECT count(*) FROM node_deliverables WHERE node_id LIKE 'EMR-LG-01%') AS 成果数,
    (SELECT count(*) FROM node_participant_companies WHERE node_id LIKE 'EMR-LG-01%') AS 参与单位数,
    (SELECT count(*) FROM node_participants WHERE node_id LIKE 'EMR-LG-01%') AS 参与人数;

COMMIT;

-- ============================================================
-- 可选清理：若上半场（L3 对话建包）成功建出了节点，且编号不是 EMR-LG-01-0X，
-- 会在库里留下重复工作包。确认后手动执行下面语句按名称清理（先查后删）。
-- ============================================================
-- SELECT node_id, node_name, parent_node_id, creator_id, created_at
-- FROM project_nodes
-- WHERE node_name IN ('铺装面层', '车行路基础垫层', '人行路基础垫层')
--   AND node_id NOT LIKE 'EMR-LG-01%'
--   AND is_discarded = false;
--
-- DELETE FROM node_deliverables WHERE node_id IN (
--     SELECT node_id FROM project_nodes
--     WHERE node_name IN ('铺装面层', '车行路基础垫层', '人行路基础垫层')
--       AND node_id NOT LIKE 'EMR-LG-01%');
-- DELETE FROM node_participants WHERE node_id IN (
--     SELECT node_id FROM project_nodes
--     WHERE node_name IN ('铺装面层', '车行路基础垫层', '人行路基础垫层')
--       AND node_id NOT LIKE 'EMR-LG-01%');
-- DELETE FROM node_participant_companies WHERE node_id IN (
--     SELECT node_id FROM project_nodes
--     WHERE node_name IN ('铺装面层', '车行路基础垫层', '人行路基础垫层')
--       AND node_id NOT LIKE 'EMR-LG-01%');
-- DELETE FROM project_nodes
-- WHERE node_name IN ('铺装面层', '车行路基础垫层', '人行路基础垫层')
--   AND node_id NOT LIKE 'EMR-LG-01%';
