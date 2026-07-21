-- ============================================================
-- 002_seed_test_data_patch.sql —— 补充3名测试用户到现有用户池
--
-- Precondition: 002_seed_test_data.sql must be run first
--                (creates 王建国, 李景利, 张正宏, 陈建华,
--                 赵明远, 孙建国, 周文斌 + company_info)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 002_seed_test_data_patch.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 删除旧数据（幂等）
-- ============================================================
DELETE FROM user_im_bindings WHERE im_user_id IN (
    'sim_罗永强', 'sim_刘大勇', 'sim_黄志强'
);
DELETE FROM users WHERE username IN (
    '罗永强', '刘大勇', '黄志强'
);

-- ============================================================
-- 2. 创建临时表存储公司 ID
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _patch_company_ids AS
SELECT id, company_name, type FROM company_info WHERE is_deleted = false;

-- ============================================================
-- 3. 插入3个新用户
--    users 表字段: id, username, phone, email, status, is_admin,
--    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
--    perm_list, org_category, level, supervisor_id, company, position,
--    created_at, updated_at
-- ============================================================

-- 3.1 IT系统管理员 (level = 5, 建设单位)
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '罗永强',
    '13800000008', 'luoyongqiang@xxestate.com',
    'active', false, 1, '310101198801010008', '123456008', 'wx_it_luo',
    'IT系统管理员，负责系统运维和权限管理',
    '王建国', false, '["project.read","project.write","perm.manage","sop.manage"]',
    4, 5,
    (SELECT id FROM users WHERE username = '王建国' LIMIT 1),
    (SELECT id FROM _patch_company_ids WHERE type = '建设单位' LIMIT 1),
    '["IT系统管理员"]', NOW()::text, NOW()::text
);

-- 3.2 现场工长 (level = 2, 总包)
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '刘大勇',
    '13800000009', 'liudayong@zhongtian.com',
    'active', false, 1, '310101198901010009', '123456009', 'wx_刘大勇',
    '现场工长，负责施工班组日常管理',
    '王建国', false, '["task.read","progress.report"]',
    2, 2,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM _patch_company_ids WHERE type = '总包' LIMIT 1),
    '["现场工长"]', NOW()::text, NOW()::text
);

-- 3.3 景观施工员 (level = 2, 总包)
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '黄志强',
    '13800000010', 'huangzhiqiang@zhongtian.com',
    'active', false, 1, '310101199001010010', '123456010', 'wx_黄志强',
    '景观施工员，负责绿化景观施工',
    '王建国', false, '["task.read","progress.report"]',
    2, 2,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM _patch_company_ids WHERE type = '总包' LIMIT 1),
    '["景观施工员"]', NOW()::text, NOW()::text
);

-- ============================================================
-- 4. 插入用户 IM 绑定数据
-- ============================================================
INSERT INTO user_im_bindings (id, user_id, im_platform, im_user_id,
    im_display_name, status, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    u.id,
    'simulator',
    'sim_' || u.username,
    CASE u.username
        WHEN '罗永强' THEN '罗永强'
        WHEN '刘大勇' THEN '刘大勇'
        WHEN '黄志强' THEN '黄志强'
        ELSE u.username
    END,
    'active',
    NOW()::text,
    NOW()::text
FROM users u
WHERE u.username IN (
    '罗永强', '刘大勇', '黄志强'
);

-- ============================================================
-- 5. 验证查询
-- ============================================================

-- 5.1 总用户数（预期：10）
SELECT '--- 总用户数（预期10） ---' AS section;
SELECT COUNT(*) AS total_active_users FROM users WHERE is_deleted = false;

-- 5.2 各级别用户分布
SELECT '--- 各级别用户分布 ---' AS section;
SELECT
    level,
    CASE level
        WHEN 1 THEN '访客'
        WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理'
        WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员'
        WHEN 6 THEN '系统管理员'
        ELSE '未知'
    END AS level_name,
    COUNT(*) AS user_count
FROM users WHERE is_deleted = false
GROUP BY level ORDER BY level DESC;

-- 5.3 新增用户详情
SELECT '--- 新增用户详情 ---' AS section;
SELECT
    u.username AS "用户名",
    u.level AS "权限级别",
    CASE u.level
        WHEN 1 THEN '访客'
        WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理'
        WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员'
        WHEN 6 THEN '系统管理员'
        ELSE '未知'
    END AS "权限说明",
    c.company_name AS "所属单位",
    u.position AS "岗位"
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.username IN (
    '罗永强', '刘大勇', '黄志强'
)
ORDER BY u.level DESC;

-- 5.4 IM 绑定验证
SELECT '--- 新增 IM 绑定 ---' AS section;
SELECT u.username, b.im_user_id, b.im_display_name, b.im_platform
FROM user_im_bindings b
JOIN users u ON b.user_id = u.id
WHERE b.im_user_id IN (
    'sim_罗永强', 'sim_刘大勇', 'sim_黄志强'
);

-- 清理临时表
DROP TABLE IF EXISTS _patch_company_ids;

COMMIT;
