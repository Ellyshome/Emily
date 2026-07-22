-- ============================================================
-- 003_seed_users_patch.sql —— 补充建设单位专业人员（设计+工程）
--
-- Precondition: 002 + 002_patch must be run first
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 003_seed_users_patch.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 删除旧数据（幂等）
-- ============================================================
DELETE FROM user_im_bindings WHERE im_user_id IN (
    'sim_林建辉', 'sim_周国栋', 'sim_马晓军', 'sim_陈志远'
);
DELETE FROM users WHERE username IN (
    '林建辉', '周国栋', '马晓军', '陈志远'
);

-- ============================================================
-- 2. 临时表存储建设单位 ID
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _patch3_company AS
SELECT id FROM company_info WHERE type = '建设单位' AND is_deleted = false LIMIT 1;

-- ============================================================
-- 3. 插入 4 名建设单位专业人员（level=3 参建管理）
--    直属上级：李景利（工程部经理, level=4）
-- ============================================================

-- 3.1 建筑设计师
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '林建辉',
    '13800000011', 'linjianhui@cuihuestate.com',
    'active', false, 1, '310101198203010011', '123456011', 'wx_林建辉',
    '建筑设计师，负责方案设计及规划报建',
    '王建国', false, '["design.read","design.upload","project.read"]',
    4, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    c.id,
    '["建筑设计师"]', NOW()::text, NOW()::text
FROM _patch3_company c;

-- 3.2 土建工程师
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '周国栋',
    '13800000012', 'zhouguodong@cuihuestate.com',
    'active', false, 1, '310101198507010012', '123456012', 'wx_周国栋',
    '土建工程师，负责地基基础、主体结构及场地平整管理',
    '王建国', false, '["task.read","task.write","progress.update","quality.check"]',
    4, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    c.id,
    '["土建工程师"]', NOW()::text, NOW()::text
FROM _patch3_company c;

-- 3.3 安装工程师
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '马晓军',
    '13800000013', 'maxiaojun@cuihuestate.com',
    'active', false, 1, '310101198811010013', '123456013', 'wx_马晓军',
    '安装工程师，负责水电预埋、电梯消防等机电安装管理',
    '王建国', false, '["task.read","task.write","progress.update","quality.check"]',
    4, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    c.id,
    '["安装工程师"]', NOW()::text, NOW()::text
FROM _patch3_company c;

-- 3.4 景观精装负责人（兼设计+工程双岗）
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '陈志远',
    '13800000014', 'chenzhiyuan@cuihuestate.com',
    'active', false, 1, '310101199103010014', '123456014', 'wx_陈志远',
    '景观精装负责人，兼景观设计与施工、精装设计与施工',
    '王建国', false, '["design.read","design.upload","task.read","task.write","progress.update"]',
    4, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    c.id,
    '["景观设计师","景观工程师","精装设计师","精装工程师"]', NOW()::text, NOW()::text
FROM _patch3_company c;

-- ============================================================
-- 4. 插入 IM 绑定
-- ============================================================
INSERT INTO user_im_bindings (id, user_id, im_platform, im_user_id,
    im_display_name, status, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    u.id,
    'simulator',
    'sim_' || u.username,
    CASE u.username
        WHEN '林建辉' THEN '林工'
        WHEN '周国栋' THEN '周工'
        WHEN '马晓军' THEN '马工'
        WHEN '陈志远' THEN '陈工'
        ELSE u.username
    END,
    'active',
    NOW()::text,
    NOW()::text
FROM users u
WHERE u.username IN ('林建辉', '周国栋', '马晓军', '陈志远');

-- ============================================================
-- 5. 验证
-- ============================================================
SELECT '--- 建设单位人员一览 ---' AS section;
SELECT u.username AS "姓名", u.level, u.position AS "岗位"
FROM users u
JOIN _patch3_company c ON u.company = c.id
WHERE u.is_deleted = false
ORDER BY u.username;

-- 清理
DROP TABLE IF EXISTS _patch3_company;

COMMIT;
