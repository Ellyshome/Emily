-- ============================================================
-- 013_seed_node_participants.sql —— 按专业+层级分配节点参与人
--
-- Precondition:
--   - 002 + 002_patch + 003 用户已创建（14人）
--   - 008 节点树已创建（24个节点）
--   - 012 节点责任人已分配
--   - node_participants 表已创建（alembic migration a1b2c3d4e5f6）
--
-- 原则：
--   - 全员（除周文斌/访客）均按工作特性分配到相关节点
--   - 管理层（王建国/李景利/罗永强）覆盖全部节点
--   - 监理（陈建华）覆盖施工阶段全部节点
--   - 专业人员在各自领域节点中参与
--
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 013_seed_node_participants.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 幂等：先清旧数据
-- ============================================================
DELETE FROM node_participants;

-- ============================================================
-- 2. 临时映射表：username → node_id 范围
--    node_pattern 支持 LIKE 前缀匹配（如 'EMR-SG-01-05%' 匹配子节点）
-- ============================================================
CREATE TEMP TABLE _np_mapping (
    username       text NOT NULL,
    node_pattern   text NOT NULL,
    participant_role text NOT NULL DEFAULT 'participant'
);

-- ── 管理层：全部24个节点 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('王建国', 'EMR-%', 'observer'),
    ('李景利', 'EMR-%', 'participant'),
    ('罗永强', 'EMR-%', 'observer');

-- ── 总包项目经理：施工阶段全部节点 + 竣工验收 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('张正宏', 'EMR-SG-%', 'participant'),
    ('张正宏', 'EMR-JF-01', 'participant');

-- ── 监理工程师：施工阶段全部节点 + 竣工验收 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('陈建华', 'EMR-SG-%', 'observer'),
    ('陈建华', 'EMR-JF-01', 'observer');

-- ── 设计领域：立项 + 规划设计 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('赵明远', 'EMR-LX-%', 'participant'),
    ('赵明远', 'EMR-GH-%', 'participant'),
    ('林建辉', 'EMR-LX-%', 'participant'),
    ('林建辉', 'EMR-GH-%', 'participant');

-- ── 土建领域：地基基础 + 主体结构 + 场地平整 + 施工总控 + 竣工验收 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('周国栋', 'EMR-SG-01', 'participant'),
    ('周国栋', 'EMR-SG-01-01-%', 'participant'),
    ('周国栋', 'EMR-SG-01-02-%', 'participant'),
    ('周国栋', 'EMR-SG-01-04-%', 'participant'),
    ('周国栋', 'EMR-JF-01', 'participant'),
    ('孙建国', 'EMR-SG-01', 'participant'),
    ('孙建国', 'EMR-SG-01-01-%', 'participant'),
    ('孙建国', 'EMR-SG-01-02-%', 'participant'),
    ('孙建国', 'EMR-SG-01-04-%', 'participant'),
    ('刘大勇', 'EMR-SG-01', 'participant'),
    ('刘大勇', 'EMR-SG-01-01-%', 'participant'),
    ('刘大勇', 'EMR-SG-01-02-%', 'participant'),
    ('刘大勇', 'EMR-SG-01-04-%', 'participant');

-- ── 机电安装领域 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('马晓军', 'EMR-SG-01', 'participant'),
    ('马晓军', 'EMR-SG-01-03-%', 'participant'),
    ('马晓军', 'EMR-JF-01', 'participant');

-- ── 景观领域 ──
INSERT INTO _np_mapping (username, node_pattern, participant_role) VALUES
    ('陈志远', 'EMR-SG-01', 'participant'),
    ('陈志远', 'EMR-SG-01-05-%', 'participant'),
    ('陈志远', 'EMR-JF-01', 'participant'),
    ('黄志强', 'EMR-SG-01-05-%', 'participant');

-- ============================================================
-- 3. 展开 LIKE 匹配 → 实际 node_id，写入 node_participants
-- ============================================================
INSERT INTO node_participants (id, node_id, user_id, participant_role, added_by, added_at)
SELECT
    uuid_generate_v4()::text,
    pn.node_id,
    u.id,
    m.participant_role,
    'system_seed',
    NOW()::text
FROM _np_mapping m
JOIN project_nodes pn ON pn.node_id LIKE m.node_pattern
JOIN users u ON u.username = m.username AND u.is_deleted = false
WHERE pn.project_id = (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false LIMIT 1);

-- ============================================================
-- 4. 验证
-- ============================================================
SELECT '--- 节点参与人分配概览 ---' AS section;
SELECT
    u.username AS "参与人",
    u.level AS "级别",
    CASE u.level
        WHEN 1 THEN '访客'      WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理'  WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员'    WHEN 6 THEN '系统管理员'
    END AS "权限说明",
    c.company_name AS "所属单位",
    COUNT(DISTINCT np.node_id) AS "参与节点数",
    STRING_AGG(DISTINCT np.participant_role, ', ' ORDER BY np.participant_role) AS "角色"
FROM node_participants np
JOIN users u ON np.user_id = u.id
LEFT JOIN company_info c ON u.company = c.id
GROUP BY u.username, u.level, c.company_name
ORDER BY u.level DESC, COUNT(DISTINCT np.node_id) DESC;

SELECT '--- 各节点参与人数 ---' AS section;
SELECT
    pn.node_id AS "节点编号",
    pn.node_name AS "节点名称",
    pn.node_type AS "类型",
    COUNT(np.user_id) AS "参与人数"
FROM project_nodes pn
LEFT JOIN node_participants np ON pn.node_id = np.node_id
WHERE pn.project_id = (SELECT id FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false LIMIT 1)
GROUP BY pn.node_id, pn.node_name, pn.node_type, pn.sort_order
ORDER BY pn.sort_order;

SELECT '--- 未参与任何节点的用户（预期仅周文斌/访客） ---' AS section;
SELECT u.username, u.level, c.company_name
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.is_deleted = false
  AND u.id NOT IN (SELECT DISTINCT user_id FROM node_participants)
ORDER BY u.level;

-- 清理
DROP TABLE IF EXISTS _np_mapping;

COMMIT;
