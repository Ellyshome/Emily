-- ============================================================
-- 012_seed_node_responsible.sql —— 按专业+单位分配节点责任人
--
-- Precondition: 003_seed_users_patch.sql must be run first
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 012_seed_node_responsible.sql
-- ============================================================

BEGIN;

-- ============================================================
-- 里程碑大节点（6个）→ 王建国（系统管理员, level=6, 建设单位）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '王建国' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-LX-01',     -- 项目立项
    'EMR-LX-01-01',  -- 可行性研究
    'EMR-LX-01-02',  -- 立项审批
    'EMR-GH-01',     -- 规划报建
    'EMR-SG-01',     -- 施工总控
    'EMR-JF-01'      -- 竣工验收
);

-- ============================================================
-- 方案设计（1个）→ 赵明远（设计单位, L3 建筑设计师）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '赵明远' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-GH-01-01'  -- 方案设计
);

-- ============================================================
-- 规划报建子节点（1个）→ 林建辉（建设单位, L3 建筑设计师）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '林建辉' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-GH-01-02'  -- 规划许可证办理
);

-- ============================================================
-- 土建大节点（3个）→ 周国栋（建设单位, L3 土建工程师）
-- 监理 WP 层级，具体 TASK 由总包执行
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '周国栋' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-01', -- 地基与基础工程
    'EMR-SG-01-02', -- 主体结构工程
    'EMR-SG-01-04'  -- 室外场地平整
);

-- ============================================================
-- 土建执行节点（2个）→ 张正宏（总包, L3 项目经理）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '张正宏' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-01-01', -- 土方开挖
    'EMR-SG-01-04-01'  -- 场地平整与压实
);

-- ============================================================
-- 主体结构执行（2个）→ 孙建国（总包, L2 施工员）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '孙建国' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-02-01', -- 1#-2#楼主体结构
    'EMR-SG-01-02-03'  -- 4#-5#楼主体结构
);

-- ============================================================
-- 主体+基础执行（2个）→ 刘大勇（总包, L2 现场工长）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '刘大勇' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-01-02', -- 桩基与基础施工
    'EMR-SG-01-02-02'  -- 3#楼主体结构
);

-- ============================================================
-- 机电安装（3个）→ 马晓军（建设单位, L3 安装工程师）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '马晓军' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-03',    -- 机电安装工程
    'EMR-SG-01-03-01', -- 水电预埋与管线
    'EMR-SG-01-03-02'  -- 电梯及消防安装
);

-- ============================================================
-- 景观管理（2个）→ 陈志远（建设单位, L3 景观精装负责人）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '陈志远' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-05',    -- 景观绿化工程
    'EMR-SG-01-05-03'  -- 景观照明与小品
);

-- ============================================================
-- 景观施工（2个）→ 黄志强（总包, L2 景观施工员）
-- ============================================================
UPDATE project_nodes SET responsible_user_id = (
    SELECT id FROM users WHERE username = '黄志强' AND is_deleted = false LIMIT 1
)
WHERE node_id IN (
    'EMR-SG-01-05-01', -- 绿化种植
    'EMR-SG-01-05-02'  -- 硬质铺装与园路
);

-- ============================================================
-- 验证
-- ============================================================
SELECT u.username AS "责任人",
       c.company_name AS "所属单位",
       u.level AS "级别",
       COUNT(*) AS "负责节点数"
FROM project_nodes pn
JOIN users u ON pn.responsible_user_id = u.id
LEFT JOIN company_info c ON u.company = c.id
GROUP BY u.username, c.company_name, u.level
ORDER BY u.level DESC, COUNT(*) DESC;

COMMIT;
