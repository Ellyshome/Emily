-- ============================================================
-- 场景_回滚_大区景观工作包.sql
-- 用途：撤销「场景_布置_大区景观工作包.sql」写入的临时数据
-- 配套：场景_布置_大区景观工作包.sql、场景_测试用例.md
-- 执行（PowerShell 下必须 docker cp 后 -f 执行）：
--   docker cp "Issues/测试用例/L3工作包挂载与L2完工匹配测试/场景_回滚_大区景观工作包.sql" emily-postgres:/tmp/lg_rollback.sql
--   docker exec emily-postgres psql -U emily -d emily -f /tmp/lg_rollback.sql
--   docker exec emily-postgres rm -f /tmp/lg_rollback.sql
--
-- 【回滚范围】
--   ✓ 新建节点 EMR-LG-01 / -01 / -02 / -03 及其成果、参与单位、参与人、节点关联
--   ✓ 可选：上半场由 L3 对话建出的同名工作包（按名称清理，默认注释）
--   ✗ 测试过程产生的业务留痕（events / project_events）不删——是本次观察对象
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

-- ── 1. 成果 ──
DELETE FROM node_deliverables WHERE node_id LIKE 'EMR-LG-01%';

-- ── 2. 参与关系 ──
DELETE FROM node_participants         WHERE node_id LIKE 'EMR-LG-01%';
DELETE FROM node_participant_companies WHERE node_id LIKE 'EMR-LG-01%';

-- ── 3. 其他节点关联（依赖 / 共享文件 / 节点事件）──
DELETE FROM node_dependencies
WHERE node_id LIKE 'EMR-LG-01%' OR depends_on_node_id LIKE 'EMR-LG-01%';

DELETE FROM node_accessible_files WHERE node_id LIKE 'EMR-LG-01%';
DELETE FROM node_events           WHERE node_id LIKE 'EMR-LG-01%';

-- ── 4. 节点本身（先子后父）──
DELETE FROM project_nodes WHERE node_id LIKE 'EMR-LG-01-%';
DELETE FROM project_nodes WHERE node_id = 'EMR-LG-01';

-- ── 5. 可选：清理上半场 L3 对话建出的同名工作包（默认注释，确认后放开）──
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

-- ── 6. 回滚校验（期望全部为 0）──
\echo '--- 回滚结果（期望：残留节点 0、残留成果 0、残留参与关系 0）---'
SELECT
    (SELECT count(*) FROM project_nodes WHERE node_id LIKE 'EMR-LG-01%') AS 残留节点,
    (SELECT count(*) FROM node_deliverables WHERE node_id LIKE 'EMR-LG-01%') AS 残留成果,
    (SELECT count(*) FROM node_participant_companies WHERE node_id LIKE 'EMR-LG-01%') AS 残留参与单位;

COMMIT;

-- ============================================================
-- 可选清理：测试留痕（默认不执行，确认不再复盘时放开）
-- ============================================================
-- BEGIN;
--   DELETE FROM project_events
--   WHERE node_id LIKE 'EMR-LG-01%'
--      OR (node_id = 'UNASSIGNED' AND created_at >= '<测试开始时间>');
-- COMMIT;
