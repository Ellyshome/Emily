-- ============================================================
-- 场景模拟_回滚_景观任务链.sql
-- 用途：撤销「场景模拟_布置_景观任务链.sql」写入的全部临时数据
-- 配套：场景模拟_布置_景观任务链.sql、场景模拟_测试用例.md
-- 执行（PowerShell 下必须 docker cp 后 -f 执行，直接管道会损坏中文编码）：
--   docker cp "Issues/测试用例/场景模拟_回滚_景观任务链.sql" emily-postgres:/tmp/sim_rollback.sql
--   docker exec emily-postgres psql -U emily -d emily -f /tmp/sim_rollback.sql
--   docker exec emily-postgres rm -f /tmp/sim_rollback.sql
--
-- 【回滚范围】
--   ✓ 新建节点 EMR-SG-01-11 / EMR-SG-01-11-01 及其成果、参与单位、参与人
--   ✓ 布置新增的 4 条完工量型成果
--   ✓ 布置对 05-01 / 05-02 / 05-03 / 05 的状态与责任人改动（恢复到 seed 原值）
--   ✗ 测试过程中产生的业务留痕（events / project_events）**不删**：
--     留痕是本次测试的观察对象（node_id、会话、时间），删掉就没法复盘。
--     如需清理见文末「可选清理」。
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

-- ── 1. 删除布置新增的成果 ──
DELETE FROM node_deliverables
WHERE deliverable_id IN (
    'EMR-SG-01-05-02-DELV-002',   -- 园路面层铺装
    'EMR-SG-01-05-02-DELV-003',   -- 人行步道基层
    'EMR-SG-01-05-01-DELV-002',   -- 乔木灌木种植
    'EMR-SG-01-11-DELV-001',      -- 景观水景工程验收报告（里程碑汇总级，2026-09-21 新增）
    'EMR-SG-01-11-01-DELV-001'    -- 水景池壁石材铺装
);

-- ── 2. 删除新建节点的参与关系 ──
DELETE FROM node_participants
WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01');

DELETE FROM node_participant_companies
WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01');

-- ── 3. 删除新建节点的其他关联（依赖 / 共享文件 / 节点事件）──
DELETE FROM node_dependencies
WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01')
   OR depends_on_node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01');

DELETE FROM node_accessible_files
WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01');

-- 节点事件表（存量表，新链路已归口 project_events；此处仅为清理残留）
DELETE FROM node_events
WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01');

-- ── 4. 删除新建节点本身（先子后父）──
DELETE FROM project_nodes WHERE node_id = 'EMR-SG-01-11-01';
DELETE FROM project_nodes WHERE node_id = 'EMR-SG-01-11';

-- ── 5. 还原 seed 原值（责任人 / 状态）──
--    责任人：05-01 / 05-02 → 黄志强（seed 012 原值，本次布置未改，保底重设）
--            05 / 05-03 → 陈志远（seed 012 原值）
UPDATE project_nodes SET responsible_user_id =
    (SELECT id FROM users WHERE username = '黄志强' AND is_deleted = false LIMIT 1)
WHERE node_id IN ('EMR-SG-01-05-01', 'EMR-SG-01-05-02');

UPDATE project_nodes SET responsible_user_id =
    (SELECT id FROM users WHERE username = '陈志远' AND is_deleted = false LIMIT 1)
WHERE node_id IN ('EMR-SG-01-05', 'EMR-SG-01-05-03');

--    状态：05-03 恢复布置前的 IN_PROGRESS；05 恢复父状态滞后的原状 CONDITIONS_NOT_MET
UPDATE project_nodes SET status = 'IN_PROGRESS' WHERE node_id = 'EMR-SG-01-05-03';
UPDATE project_nodes SET status = 'CONDITIONS_NOT_MET' WHERE node_id = 'EMR-SG-01-05';

-- ── 6. 回滚校验（应全部为 0）──
\echo '--- 回滚结果（期望：新节点 0、新成果 0、新参与关系 0）---'
SELECT
    (SELECT count(*) FROM project_nodes WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01')) AS 残留新节点,
    (SELECT count(*) FROM node_deliverables WHERE deliverable_id IN (
        'EMR-SG-01-05-02-DELV-002', 'EMR-SG-01-05-02-DELV-003',
        'EMR-SG-01-05-01-DELV-002', 'EMR-SG-01-11-DELV-001',
        'EMR-SG-01-11-01-DELV-001')) AS 残留新成果,
    (SELECT count(*) FROM node_participant_companies
        WHERE node_id IN ('EMR-SG-01-11', 'EMR-SG-01-11-01')) AS 残留新参与单位;

COMMIT;

-- ============================================================
-- 可选清理：测试过程产生的业务留痕（默认不执行，确认不再需要复盘时手动放开）
-- ============================================================
-- BEGIN;
--   -- 事件主表：按布置/测试涉及节点或指定会话清理
--   DELETE FROM events WHERE title LIKE '%景观%铺装%';        -- 按标题特征（谨慎）
--   -- 统一事件流：落到临时节点或景观链的测试记录
--   DELETE FROM project_events WHERE node_id IN (
--       'EMR-SG-01-05-02', 'EMR-SG-01-05-01', 'EMR-SG-01-11-01', 'UNASSIGNED'
--   ) AND created_at >= '<测试开始时间>';
-- COMMIT;
