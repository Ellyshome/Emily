-- ============================================================
-- 场景模拟_布置_景观任务链.sql
-- 用途：为「L2 施工员信息上报 → 节点归属引导」场景做临时数据布置
-- 配套：场景模拟.md（场景定义）、场景模拟_测试用例.md（观察口径）、
--       场景模拟_回滚_景观任务链.sql（回滚）
-- 执行（PowerShell 下必须 docker cp 后 -f 执行，直接管道会损坏中文编码）：
--   docker cp "Issues/测试用例/场景模拟_布置_景观任务链.sql" emily-postgres:/tmp/sim_setup.sql
--   docker exec emily-postgres psql -U emily -d emily -f /tmp/sim_setup.sql
--   docker exec emily-postgres rm -f /tmp/sim_setup.sql
-- 幂等：重复执行不产生重复节点/成果/参与关系（部分 UPDATE 为幂等赋值）
-- ============================================================
--
-- 【为什么需要这次布置】
--   现网翡翠湾项目的景观链只有「施工方案（份）」类成果，
--   而本场景上报的是「完工量」（面层铺装 10 平方米）。
--   成果名里没有任何工序/部位信息 ⇒ 系统必然判定"无承接本次成果的任务节点"
--   ⇒ 只能落候选档或临时节点。这不是引导能力问题，是数据缺位。
--   故布置补三类数据：① 完工量型成果 ② 企业参与/责任人 ③ 一条对照链。
--
-- 【布置后的任务链】（层级深度上限 3，见 node_service.MAX_PARENT_DEPTH=3）
--   EMR-SG-01 施工总控 (MILESTONE)
--     ├─ EMR-SG-01-05 景观绿化工程 (MILESTONE, 总包)
--     │    ├─ EMR-SG-01-05-01 绿化种植        (TASK, 责任人 黄志强) + 乔木灌木种植 120 株   ← 干扰项
--     │    ├─ EMR-SG-01-05-02 硬质铺装与园路  (TASK, 责任人 黄志强) + 园路面层铺装 500㎡    ← 本场景正解
--     │    │                                                        + 人行步道基层 800㎡
--     │    └─ EMR-SG-01-05-03 景观照明与小品  (TASK, 责任人 陈志远)（无铺装类成果）
--     └─ EMR-SG-01-11 景观水景工程 (MILESTONE, 总包, 责任人 陈志远)   ← 本次新建（对照链）
--          └─ EMR-SG-01-11-01 水景石材铺装 (TASK, 责任人 黄志强) + 水景池壁石材铺装 120㎡ ← 高干扰项
--
-- 【可见性口径】新建节点沿用 EMR-SG-01-05 的参与单位/参与人登记（含总包 中天建设集团），
--   保证黄志强（L2, 总包）在 query_my_nodes 中能看到，且属于其"企业参与节点"。
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

-- ============================================================
-- 0. 上下文解析（项目 / 关键人员 / 总包单位）
-- ============================================================
CREATE TEMP TABLE _sim_ctx ON COMMIT DROP AS
SELECT
    (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false LIMIT 1) AS project_id,
    (SELECT id FROM users WHERE username = '黄志强' AND is_deleted = false LIMIT 1)   AS u_huang,   -- L2 施工员（本场景被试人）
    (SELECT id FROM users WHERE username = '陈志远' AND is_deleted = false LIMIT 1)   AS u_chen,    -- L3 景观负责人
    (SELECT id FROM users WHERE username = '王建国' AND is_deleted = false LIMIT 1)   AS u_creator, -- L6 创建人
    (SELECT id FROM company_info WHERE company_name = '中天建设集团' LIMIT 1)          AS c_zongbao; -- 总包

-- 前置校验：关键上下文必须可解析，否则布置无意义
DO $$
DECLARE c _sim_ctx;
BEGIN
    SELECT * INTO c FROM _sim_ctx;
    IF c.project_id IS NULL THEN RAISE EXCEPTION '布置失败：未找到项目 EMERALD-01'; END IF;
    IF c.u_huang IS NULL   THEN RAISE EXCEPTION '布置失败：未找到用户 黄志强'; END IF;
    IF c.u_chen IS NULL    THEN RAISE EXCEPTION '布置失败：未找到用户 陈志远'; END IF;
    IF c.c_zongbao IS NULL THEN RAISE EXCEPTION '布置失败：未找到单位 中天建设集团'; END IF;
    IF NOT EXISTS (SELECT 1 FROM project_nodes WHERE node_id = 'EMR-SG-01-05') THEN
        RAISE EXCEPTION '布置失败：景观链根基节点 EMR-SG-01-05 不存在，请先跑 .claude/tool/env-test 的 008 节点播种';
    END IF;
END $$;

-- ============================================================
-- 1. 新建对照链节点：景观水景工程 → 水景石材铺装
--    （用于观察：候选过多时的选择行为；同名"铺装"的匹配精度；无承接时的降级）
-- ============================================================
-- 1.1 任务包级：景观水景工程
INSERT INTO project_nodes (
    id, project_id, node_id, node_name, owner_dept_id, related_company_id, deadline,
    remark, creator_id, created_at, responsible_user_id, node_type, visibility_mode,
    status, progress, parent_node_id, child_weight, updated_at, is_discarded
)
SELECT
    uuid_generate_v4()::text, c.project_id, 'EMR-SG-01-11', '景观水景工程',
    '', c.c_zongbao, '2026-10-15',
    '[场景模拟布置] 对照链任务包：成果不含园路铺装工序，用于检验候选区分能力',
    c.u_creator, NOW()::text, c.u_chen, 'MILESTONE', 'specific',
    'IN_PROGRESS', '0.00', 'EMR-SG-01', '1.0000', NOW()::text, false
FROM _sim_ctx c
ON CONFLICT (node_id) DO NOTHING;

-- 1.2 具体任务级：水景石材铺装（分配给总包，责任人 黄志强）
INSERT INTO project_nodes (
    id, project_id, node_id, node_name, owner_dept_id, related_company_id, deadline,
    remark, creator_id, created_at, responsible_user_id, node_type, visibility_mode,
    status, progress, parent_node_id, child_weight, updated_at, is_discarded
)
SELECT
    uuid_generate_v4()::text, c.project_id, 'EMR-SG-01-11-01', '水景石材铺装',
    '', c.c_zongbao, '2026-10-10',
    '[场景模拟布置] 高干扰项：名称含"铺装"，用于检验匹配精度',
    c.u_creator, NOW()::text, c.u_huang, 'TASK', 'specific',
    'IN_PROGRESS', '0.00', 'EMR-SG-01-11', '1.0000', NOW()::text, false
FROM _sim_ctx c
ON CONFLICT (node_id) DO NOTHING;

-- ============================================================
-- 2. 补「完工量型」成果 —— 让"承接本次成果的任务节点"这一档可判定
--    现网成果均为"施工方案（份）"，无法承接"完工 10 平方米"这类上报
-- ============================================================
INSERT INTO node_deliverables (
    id, deliverable_id, node_id, deliverable_name, target_amount, current_amount,
    unit, is_required, created_at, submission_status
)
SELECT
    uuid_generate_v4()::text, v.deliverable_id, v.node_id, v.deliverable_name,
    v.target_amount, '0.00', v.unit, true, NOW()::text, 'PENDING'
FROM (VALUES
    -- 【2026-09-21 补】里程碑状态已改为「以自身成果判定」，不再由子节点聚合
    --   （见 Issues/节点状态自证化/节点状态自证化_需求基线_V1.md）。
    --   无自有成果的里程碑会永远停在「条件不足」，故为 EMR-SG-01-11 补一条汇总级必需成果。
    --   ⚠️ 新增候选后需重跑 T1~T4，确认归属未被父节点抢走。
    ('EMR-SG-01-11-DELV-001',    'EMR-SG-01-11',    '景观水景工程验收报告', '1',   '份'),
    -- 正解：本场景「面层铺装完工 10 平方米」应归到这里
    ('EMR-SG-01-05-02-DELV-002', 'EMR-SG-01-05-02', '园路面层铺装',  '500', '平方米'),
    ('EMR-SG-01-05-02-DELV-003', 'EMR-SG-01-05-02', '人行步道基层',  '800', '平方米'),
    -- 干扰项：同一责任人、同一父节点，但不是铺装工序
    ('EMR-SG-01-05-01-DELV-002', 'EMR-SG-01-05-01', '乔木灌木种植',  '120', '株'),
    -- 高干扰项：成果名同样含"铺装"，考验匹配是否会被关键词带偏
    ('EMR-SG-01-11-01-DELV-001', 'EMR-SG-01-11-01', '水景池壁石材铺装', '120', '平方米')
) AS v(deliverable_id, node_id, deliverable_name, target_amount, unit)
WHERE NOT EXISTS (
    SELECT 1 FROM node_deliverables d
    WHERE d.node_id = v.node_id AND d.deliverable_name = v.deliverable_name
);

-- ============================================================
-- 3. 参与单位 / 参与人登记 —— 沿用同专业节点 EMR-SG-01-05 的登记
--    （保证新建节点对总包可见，从而进入黄志强的节点可见范围）
-- ============================================================
INSERT INTO node_participant_companies (id, node_id, company_id, added_by, added_at)
SELECT uuid_generate_v4()::text, t.node_id, s.company_id, 'sim_setup', NOW()::text
FROM (VALUES ('EMR-SG-01-11'), ('EMR-SG-01-11-01')) AS t(node_id)
JOIN node_participant_companies s ON s.node_id = 'EMR-SG-01-05'
ON CONFLICT (node_id, company_id) DO NOTHING;

INSERT INTO node_participants (id, node_id, user_id, participant_role, added_by, added_at)
SELECT uuid_generate_v4()::text, t.node_id, s.user_id, s.participant_role, 'sim_setup', NOW()::text
FROM (VALUES ('EMR-SG-01-11'), ('EMR-SG-01-11-01')) AS t(node_id)
JOIN node_participants s ON s.node_id = 'EMR-SG-01-05'
ON CONFLICT (node_id, user_id) DO NOTHING;

-- ============================================================
-- 4. 责任人归位（幂等赋值）
--    05-01 / 05-02 / 11-01 → 黄志强；05-03 / 11 → 陈志远
-- ============================================================
UPDATE project_nodes SET responsible_user_id = c.u_huang, updated_at = NOW()::text
FROM _sim_ctx c
WHERE node_id IN ('EMR-SG-01-05-01', 'EMR-SG-01-05-02', 'EMR-SG-01-11-01');

UPDATE project_nodes SET responsible_user_id = c.u_chen, updated_at = NOW()::text
FROM _sim_ctx c
WHERE node_id IN ('EMR-SG-01-05-03', 'EMR-SG-01-11');

-- ============================================================
-- 5. 状态就绪
--    叶子节点：有成果 + 无前置依赖 → 应为 IN_PROGRESS（成果未被依赖，故不受"文件级依赖"约束）
--    父节点：由子节点集体决定 → 有 IN_PROGRESS 子节点即为 IN_PROGRESS
--    注：现网 EMR-SG-01-05 父状态残留 CONDITIONS_NOT_MET（父状态重算滞后），
--        此处显式补正，测试文档"已知卡点"一节有记录
-- ============================================================
UPDATE project_nodes SET status = 'IN_PROGRESS', updated_at = NOW()::text
WHERE node_id IN ('EMR-SG-01-05', 'EMR-SG-01-11', 'EMR-SG-01-11-01');

-- ============================================================
-- 6. 布置结果校验
-- ============================================================
\echo '--- ① 链路与状态 ---'
SELECT node_id, node_name, node_type, status, parent_node_id,
       (SELECT username FROM users u WHERE u.id = p.responsible_user_id) AS 责任人
FROM project_nodes p
WHERE node_id LIKE 'EMR-SG-01-05%' OR node_id LIKE 'EMR-SG-01-11%' OR node_id = 'EMR-SG-01'
ORDER BY node_id;

\echo '--- ② 成果（含新增的完工量型）---'
SELECT node_id, deliverable_id, deliverable_name, target_amount || unit AS 目标量, submission_status
FROM node_deliverables
WHERE node_id LIKE 'EMR-SG-01-05%' OR node_id LIKE 'EMR-SG-01-11%'
ORDER BY node_id, deliverable_id;

\echo '--- ③ 黄志强视角：query_my_nodes 数据源（责任人 + 参与人）---'
SELECT p.node_id, p.node_name, p.node_type, p.status,
       CASE WHEN p.responsible_user_id = c.u_huang THEN 'responsible' ELSE 'participant' END AS role,
       COALESCE(string_agg(d.deliverable_name, ' / '), '（无成果）') AS 成果
FROM project_nodes p
CROSS JOIN _sim_ctx c
LEFT JOIN node_deliverables d ON d.node_id = p.node_id
WHERE p.is_discarded = false
  AND (p.responsible_user_id = c.u_huang
       OR EXISTS (SELECT 1 FROM node_participants np
                  WHERE np.node_id = p.node_id AND np.user_id = c.u_huang))
GROUP BY p.node_id, p.node_name, p.node_type, p.status, p.responsible_user_id, c.u_huang
ORDER BY p.node_id;

\echo '--- ④ 布置生效确认（应看到 11 / 11-01 两个新节点与 4 条新成果）---'
SELECT
    (SELECT count(*) FROM project_nodes WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01')) AS 新节点数,
    (SELECT count(*) FROM node_deliverables WHERE deliverable_id IN (
        'EMR-SG-01-05-02-DELV-002', 'EMR-SG-01-05-02-DELV-003',
        'EMR-SG-01-05-01-DELV-002', 'EMR-SG-01-11-01-DELV-001')) AS 新成果数,
    (SELECT count(*) FROM node_participant_companies WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01')) AS 新节点参与单位数;

COMMIT;
