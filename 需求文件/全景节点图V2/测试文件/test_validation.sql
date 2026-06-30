-- ========================================
-- 全景节点图V2测试数据验证SQL脚本
-- 用法: docker exec -i emily-postgres psql -U emily -d emily < test_validation.sql
-- ========================================

\timing on
\set QUIET 1

\echo ========================================
\echo 全景节点图V2测试数据验证报告
\echo ========================================
\echo

-- 设置项目ID
\set project_id 'project-xiongan-001'

-- ========================================
-- 1. 基本数据统计
-- ========================================
\echo 【1. 基本数据统计】
\echo ------------------------

SELECT 
    'project_nodes' as table_name,
    COUNT(*) as record_count,
    '节点主表' as description
FROM project_nodes
WHERE project_id = :'project_id'

UNION ALL

SELECT 
    'node_deliverables',
    COUNT(*),
    '成果表'
FROM node_deliverables
WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :'project_id')

UNION ALL

SELECT 
    'node_dependencies',
    COUNT(*),
    '依赖表'
FROM node_dependencies
WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :'project_id')

UNION ALL

SELECT 
    'node_events',
    COUNT(*),
    '事件表'
FROM node_events
WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :'project_id')

UNION ALL

SELECT 
    'node_accessible_files',
    COUNT(*),
    '文件关联表'
FROM node_accessible_files
WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :'project_id')

ORDER BY table_name;

\echo

-- ========================================
-- 2. 节点状态分布
-- ========================================
\echo 【2. 节点状态分布】
\echo ------------------------

SELECT 
    status,
    COUNT(*) as node_count,
    ROUND(COUNT(*)*100.0/SUM(COUNT(*))OVER(), 1) as percentage
FROM project_nodes
WHERE project_id = :'project_id'
GROUP BY status
ORDER BY status;

\echo

-- ========================================
-- 3. 成果完成率统计
-- ========================================
\echo 【3. 成果完成率统计】
\echo ------------------------

SELECT 
    n.node_id,
    LEFT(n.node_name, 20) as node_name,
    n.status,
    n.progress,
    COUNT(d.id) as total_deliverables,
    SUM(CASE WHEN d.is_required THEN 1 ELSE 0 END) as required_deliverables,
    SUM(CASE WHEN CAST(d.current_amount AS DECIMAL) >= CAST(d.target_amount AS DECIMAL) 
             THEN 1 ELSE 0 END) as completed_deliverables,
    ROUND(SUM(CASE WHEN CAST(d.current_amount AS DECIMAL) >= CAST(d.target_amount AS DECIMAL) 
                   THEN 1 ELSE 0 END)*100.0/NULLIF(COUNT(d.id), 0), 1) as completion_rate
FROM project_nodes n
LEFT JOIN node_deliverables d ON n.node_id = d.node_id
WHERE n.project_id = :'project_id'
GROUP BY n.node_id, n.node_name, n.status, n.progress
ORDER BY completion_rate DESC, n.node_id
LIMIT 20;

\echo

-- ========================================
-- 4. 依赖关系统计
-- ========================================
\echo 【4. 依赖关系统计】
\echo ------------------------

SELECT 
    d.node_id,
    LEFT(n.node_name, 20) as node_name,
    COUNT(*) as total_dependencies,
    MIN(CAST(d.weight AS DECIMAL)) as min_weight,
    MAX(CAST(d.weight AS DECIMAL)) as max_weight,
    BOOL_OR(CAST(d.weight AS DECIMAL) >= 999) as has_blocking
FROM node_dependencies d
JOIN project_nodes n ON d.node_id = n.node_id
WHERE n.project_id = :'project_id'
GROUP BY d.node_id, n.node_name
ORDER BY total_dependencies DESC
LIMIT 15;

\echo

-- ========================================
-- 5. 事件审计日志统计
-- ========================================
\echo 【5. 事件审计日志统计】
\echo ------------------------

SELECT 
    event_type,
    COUNT(*) as event_count,
    MIN(created_at) as first_event,
    MAX(created_at) as last_event
FROM node_events
WHERE node_id IN (SELECT node_id FROM project_nodes WHERE project_id = :'project_id')
GROUP BY event_type
ORDER BY event_count DESC;

\echo

-- ========================================
-- 6. 父子层级结构
-- ========================================
\echo 【6. 父子层级结构】
\echo ------------------------

WITH RECURSIVE node_tree AS (
    SELECT node_id, node_name, parent_node_id, 0 as level,
           ARRAY[node_id] as path
    FROM project_nodes
    WHERE project_id = :'project_id' AND parent_node_id = ''
    
    UNION ALL
    
    SELECT n.node_id, n.node_name, n.parent_node_id, nt.level + 1,
           nt.path || n.node_id
    FROM project_nodes n
    JOIN node_tree nt ON n.parent_node_id = nt.node_id
    WHERE n.project_id = :'project_id'
      AND n.node_id != ALL(nt.path)
)
SELECT 
    LPAD('', level*2, ' ') || node_id as indented_id,
    LEFT(node_name, 25) as node_name,
    level as depth_level,
    ARRAY_LENGTH(path, 1) - 1 as depth
FROM node_tree
ORDER BY path
LIMIT 30;

\echo

-- ========================================
-- 7. 数据一致性检查
-- ========================================
\echo 【7. 数据一致性检查】
\echo ------------------------

\echo 7.1 状态为COMPLETED但存在未完成的必需成果:

SELECT 
    n.node_id,
    n.node_name,
    n.status,
    COUNT(*) as incomplete_required
FROM project_nodes n
JOIN node_deliverables d ON n.node_id = d.node_id
WHERE n.project_id = :'project_id'
  AND n.status = 'COMPLETED'
  AND d.is_required = true
  AND CAST(d.current_amount AS DECIMAL) < CAST(d.target_amount AS DECIMAL)
GROUP BY n.node_id, n.node_name, n.status
HAVING COUNT(*) > 0;

\echo

\echo 7.2 父节点进度与子节点加权平均不一致:

SELECT 
    parent.node_id as parent_id,
    LEFT(parent.node_name, 20) as parent_name,
    parent.progress as parent_progress,
    ROUND(SUM(CAST(child.progress AS DECIMAL) * CAST(child.child_weight AS DECIMAL)) / 
          NULLIF(SUM(CAST(child.child_weight AS DECIMAL)), 0), 2) as calculated_progress,
    ABS(CAST(parent.progress AS DECIMAL) - 
        ROUND(SUM(CAST(child.progress AS DECIMAL) * CAST(child.child_weight AS DECIMAL)) / 
              NULLIF(SUM(CAST(child.child_weight AS DECIMAL)), 0), 2)) as diff
FROM project_nodes parent
JOIN project_nodes child ON parent.node_id = child.parent_node_id
WHERE parent.project_id = :'project_id'
GROUP BY parent.node_id, parent.node_name, parent.progress
HAVING ABS(CAST(parent.progress AS DECIMAL) - 
           ROUND(SUM(CAST(child.progress AS DECIMAL) * CAST(child.child_weight AS DECIMAL)) / 
                 NULLIF(SUM(CAST(child.child_weight AS DECIMAL)), 0), 2)) > 0.1;

\echo

-- ========================================
-- 8. 最新节点列表
-- ========================================
\echo 【8. 最新节点列表 (TOP 15)】
\echo ------------------------

SELECT 
    node_id,
    LEFT(node_name, 30) as node_name,
    status,
    progress || '%' as progress,
    LEFT(deadline, 19) as deadline,
    owner_dept_id,
    is_discarded
FROM project_nodes
WHERE project_id = :'project_id'
ORDER BY created_at DESC
LIMIT 15;

\echo
\echo ========================================
\echo 验证报告完成
\echo ========================================
