-- 011_seed_world_book_patch.sql —— 补全世界书 tier 推进所需数据
-- 
-- 目的：确保种子数据满足 InitializationChecker 的 T2/T3 检查项，
--       让世界书能推进到 tier≥3 并激活。
-- 
-- 执行时机：在 010_seed_runtime_data.sql 之后、build_world_book.py 之前
-- 幂等性：可重复执行（使用 ON CONFLICT / WHERE 条件保证）

BEGIN;

-- ── 1. T2_chief_supervisor：任命总监理工程师 ──
-- 陈建华 已有 "监理工程师" 职位，补上 "总监理工程师"
-- 使用 Unicode 转义避免 PowerShell 管道编码问题
UPDATE users
SET position = '["\u603b\u76d1\u7406\u5de5\u7a0b\u5e08","\u76d1\u7406\u5de5\u7a0b\u5e08","\u8d28\u91cf\u76d1\u7763\u5458"]'::jsonb
WHERE username = '陈建华'
  AND is_deleted = false;

-- ── 2. T3_node_tree_created + T3_milestone_deadlines：设立里程碑节点 ──
-- 将高层级节点（非子任务）标记为 MILESTONE
UPDATE project_nodes
SET node_type = 'MILESTONE'
WHERE node_id IN ('EMR-LX-01', 'EMR-GH-01', 'EMR-SG-01', 'EMR-JF-01')
  AND node_type != 'MILESTONE';

COMMIT;
