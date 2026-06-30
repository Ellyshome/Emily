-- ============================================================================
-- 004_cleanup_sm_tables.sql
-- 清理已废弃的旧 sm_* 全局状态机表（V2 重构前残留）
--
-- 废弃原因：全景节点模块 V2 全量重构，旧 sm_* 5 态节点级依赖模型
-- 已被 project_nodes/node_dependencies/node_deliverables/node_events
-- 四表文件级依赖模型替代。ORM 模型已在 models.py 中移除。
--
-- 执行方式：手动执行（⚠ 不可逆，执行前确认无业务依赖）
--   docker exec -it emily-postgres psql -U emily -d emily -f /path/to/004_cleanup_sm_tables.sql
--
-- 日期：2026-06-30
-- ============================================================================

BEGIN;

-- 按依赖顺序（先删子表/引用表，再删主表）防止 FK 约束报错
DROP TABLE IF EXISTS sm_simulation_results CASCADE;
DROP TABLE IF EXISTS sm_audit_logs CASCADE;
DROP TABLE IF EXISTS sm_status_history CASCADE;
DROP TABLE IF EXISTS sm_node_deliverables CASCADE;
DROP TABLE IF EXISTS sm_node_dependencies CASCADE;
DROP TABLE IF EXISTS sm_nodes CASCADE;
DROP TABLE IF EXISTS sm_stages CASCADE;

-- 验证：应返回 0 行
SELECT tablename FROM pg_tables
WHERE schemaname = 'public' AND tablename LIKE 'sm_%';

COMMIT;
